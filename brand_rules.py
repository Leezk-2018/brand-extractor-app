import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class BrandRule:
    name: str
    aliases: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    case_sensitive: bool = False


@dataclass
class BrandMatchDetail:
    name: str
    alias: str
    source: str
    excluded_by: str | None = None


def load_brand_rules(path: str | Path) -> dict[str, BrandRule]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = build_rules_from_payload(payload)
    return {rule.name.lower(): rule for rule in rules}


def build_rules_for_names(
    brand_names: Iterable[str],
    rule_map: dict[str, BrandRule] | None = None,
) -> list[BrandRule]:
    compiled: list[BrandRule] = []
    for raw_name in brand_names:
        name = (raw_name or "").strip()
        if not name:
            continue
        if rule_map and name.lower() in rule_map:
            compiled.append(rule_map[name.lower()])
        else:
            compiled.append(BrandRule(name=name, aliases=[name]))
    return compiled


def build_brand_rules_payload(brand_names: Iterable[str]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for raw_name in brand_names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        payload.append(
            {
                "name": name,
                "aliases": [],
                "exclude": [],
            }
        )
    return payload


def parse_brand_rules_json(text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"高级品牌规则 JSON 解析失败：{exc.msg}（第 {exc.lineno} 行，第 {exc.colno} 列）") from exc
    return normalize_brand_rules_payload(payload)


def normalize_brand_rules_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("高级品牌规则必须是 JSON 数组。")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 条规则必须是对象。")

        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"第 {index} 条规则缺少有效的 name。")

        aliases = _normalize_string_list(item.get("aliases", []), field_name=f"第 {index} 条规则的 aliases")
        exclude = _normalize_string_list(item.get("exclude", []), field_name=f"第 {index} 条规则的 exclude")
        case_sensitive = item.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise ValueError(f"第 {index} 条规则的 case_sensitive 必须是 true/false。")

        normalized_item: dict[str, Any] = {
            "name": name,
            "aliases": aliases,
            "exclude": exclude,
        }
        if case_sensitive:
            normalized_item["case_sensitive"] = True
        normalized.append(normalized_item)

    return normalized


def build_rules_from_payload(payload: Any) -> list[BrandRule]:
    normalized = normalize_brand_rules_payload(payload)
    return [_rule_from_dict(item) for item in normalized]


def match_brands(text: str, rules: list[BrandRule]) -> list[str]:
    return [detail.name for detail in explain_brand_matches(text, rules, source="text")]


def explain_brand_matches(
    text: str,
    rules: list[BrandRule],
    source: str,
) -> list[BrandMatchDetail]:
    return [detail for detail in evaluate_brand_matches(text, rules, source=source) if detail.excluded_by is None]


def evaluate_brand_matches(
    text: str,
    rules: list[BrandRule],
    source: str,
) -> list[BrandMatchDetail]:
    if not text:
        return []

    matched: list[BrandMatchDetail] = []
    for rule in rules:
        detail = _match_rule(text, rule, source=source)
        if detail is not None:
            matched.append(detail)
    return matched


def _match_rule(text: str, rule: BrandRule, source: str) -> BrandMatchDetail | None:
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    aliases = rule.aliases or [rule.name]
    excludes = rule.exclude or []

    for blocked in excludes:
        if re.search(_word_pattern(blocked), text, flags):
            return BrandMatchDetail(name=rule.name, alias="", source=source, excluded_by=blocked)

    for alias in aliases:
        if re.search(_word_pattern(alias), text, flags):
            return BrandMatchDetail(name=rule.name, alias=alias, source=source)
    return None


def _word_pattern(term: str) -> str:
    return r"\b" + re.escape(term) + r"\b"


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是字符串数组。")
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _rule_from_dict(payload: dict) -> BrandRule:
    name = str(payload["name"]).strip()
    aliases = [str(item).strip() for item in payload.get("aliases", []) if str(item).strip()]
    exclude = [str(item).strip() for item in payload.get("exclude", []) if str(item).strip()]
    case_sensitive = bool(payload.get("case_sensitive", False))
    if not aliases:
        aliases = [name]
    return BrandRule(
        name=name,
        aliases=aliases,
        exclude=exclude,
        case_sensitive=case_sensitive,
    )
