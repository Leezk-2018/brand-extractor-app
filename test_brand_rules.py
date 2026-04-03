import json
import unittest
from pathlib import Path

from extractor_core import _build_result_row, explain_brand_matches_for_video, extract_brands, load_selected_brand_rules
from brand_rules import evaluate_brand_matches


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

        details = explain_brand_matches_for_video("Surface Laptop review", "No mention here", rules)
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
        self.assertIn("Sony <- Sony (title)", row["匹配详情"])
        self.assertIn("Sony <- Sony (description)", row["匹配详情"])


if __name__ == "__main__":
    unittest.main()
