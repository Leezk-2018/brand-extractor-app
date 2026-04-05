import json
import unittest
from pathlib import Path

from extractor_core import _build_result_row, explain_brand_matches_for_video, extract_brands, load_selected_brand_rules
from brand_rules import (
    BrandRule,
    build_brand_rules_payload,
    build_rules_from_payload,
    evaluate_brand_matches,
    normalize_brand_rules_payload,
    parse_brand_rules_json,
)


class BrandRuleTests(unittest.TestCase):
    def test_extract_brands_supports_aliases_from_rules_file(self):
        rules_path = Path(__file__).with_name("_test_brand_rules_alias.json")
        try:
            rules_path.write_text(
                json.dumps(
                    [{"name": "Microsoft", "aliases": ["Microsoft", "Surface"], "exclude": []}]
                ),
                encoding="utf-8",
            )
            rules = load_selected_brand_rules(["Microsoft"], rules_path=rules_path)
        finally:
            if rules_path.exists():
                rules_path.unlink()

        matched = extract_brands("Surface Laptop review", rules)
        self.assertEqual(matched, ["Microsoft"])

    def test_extract_brands_supports_exclude_terms_from_rules_file(self):
        rules_path = Path(__file__).with_name("_test_brand_rules_exclude.json")
        try:
            rules_path.write_text(
                json.dumps(
                    [{"name": "Red", "aliases": ["RED"], "exclude": ["credit", "reddit"]}]
                ),
                encoding="utf-8",
            )
            rules = load_selected_brand_rules(["Red"], rules_path=rules_path)
        finally:
            if rules_path.exists():
                rules_path.unlink()

        matched = extract_brands("Credit tips for creators using RED workflows", rules)
        self.assertEqual(matched, [])

    def test_explain_brand_matches_reports_alias_and_source(self):
        rules_path = Path(__file__).with_name("_test_brand_rules_detail.json")
        try:
            rules_path.write_text(
                json.dumps(
                    [{"name": "Microsoft", "aliases": ["Microsoft", "Surface"], "exclude": []}]
                ),
                encoding="utf-8",
            )
            rules = load_selected_brand_rules(["Microsoft"], rules_path=rules_path)
        finally:
            if rules_path.exists():
                rules_path.unlink()

        details = explain_brand_matches_for_video("Surface Laptop review", "No mention here", None, rules)
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].name, "Microsoft")
        self.assertEqual(details[0].alias, "Surface")
        self.assertEqual(details[0].source, "title")

    def test_evaluate_brand_matches_reports_excluded_by(self):
        rules_path = Path(__file__).with_name("_test_brand_rules_blocked.json")
        try:
            rules_path.write_text(
                json.dumps(
                    [{"name": "Red", "aliases": ["RED"], "exclude": ["credit", "reddit"]}]
                ),
                encoding="utf-8",
            )
            rules = load_selected_brand_rules(["Red"], rules_path=rules_path)
        finally:
            if rules_path.exists():
                rules_path.unlink()

        details = evaluate_brand_matches("Credit tips for creators using RED workflows", rules, source="description")
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].name, "Red")
        self.assertEqual(details[0].excluded_by, "credit")
        self.assertEqual(details[0].source, "description")

    def test_result_row_dedupes_brand_names_but_keeps_detail_rows(self):
        rules_path = Path(__file__).with_name("_test_brand_rules_dedupe.json")
        try:
            rules_path.write_text(
                json.dumps(
                    [{"name": "Sony", "aliases": ["Sony"], "exclude": []}]
                ),
                encoding="utf-8",
            )
            rules = load_selected_brand_rules(["Sony"], rules_path=rules_path)
        finally:
            if rules_path.exists():
                rules_path.unlink()

        row = _build_result_row(
            "demo_kol",
            {
                "snippet": {
                    "title": "Sony camera review",
                    "description": "Best Sony setup for travel",
                    "publishedAt": "2025-01-02T10:00:00Z",
                },
                "id": {"videoId": "abc123"},
            },
            rules,
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["提及的品牌"], "Sony")
        self.assertIn("Sony：命中标题（Sony）", row["匹配详情"])
        self.assertIn("Sony：命中描述（Sony）", row["匹配详情"])

    def test_build_brand_rules_payload_initializes_empty_aliases_and_exclude(self):
        payload = build_brand_rules_payload(["Sony", " Canon ", ""])
        self.assertEqual(
            payload,
            [
                {"name": "Sony", "aliases": [], "exclude": []},
                {"name": "Canon", "aliases": [], "exclude": []},
            ],
        )

    def test_parse_brand_rules_json_normalizes_whitespace(self):
        payload = parse_brand_rules_json(
            json.dumps(
                [
                    {
                        "name": " Sony ",
                        "aliases": [" Sony Camera ", ""],
                        "exclude": [" reddit "],
                    }
                ]
            )
        )
        self.assertEqual(
            payload,
            [{"name": "Sony", "aliases": ["Sony Camera"], "exclude": ["reddit"]}],
        )

    def test_parse_brand_rules_json_rejects_invalid_top_level(self):
        with self.assertRaisesRegex(ValueError, "JSON 数组"):
            parse_brand_rules_json('{"name": "Sony"}')

    def test_normalize_brand_rules_payload_rejects_non_boolean_case_sensitive(self):
        with self.assertRaisesRegex(ValueError, "case_sensitive"):
            normalize_brand_rules_payload(
                [{"name": "Sony", "aliases": [], "exclude": [], "case_sensitive": "yes"}]
            )

    def test_build_rules_from_payload_uses_name_when_aliases_empty(self):
        rules = build_rules_from_payload([{"name": "Sony", "aliases": [], "exclude": []}])
        self.assertEqual(rules, [BrandRule(name="Sony", aliases=["Sony"], exclude=[], case_sensitive=False)])


if __name__ == "__main__":
    unittest.main()
