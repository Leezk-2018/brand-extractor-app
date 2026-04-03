import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


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
    rules = [_rule_from_dict(item) for item in payload]
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
