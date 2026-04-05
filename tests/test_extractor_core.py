import datetime
import unittest

from brand_rules import BrandRule
from extractor_core import build_published_after, extract_brands, resolve_channel_id


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeChannelsResource:
    def __init__(self, response):
        self.response = response

    def list(self, **kwargs):
        return FakeRequest(self.response)


class FakeSearchResource:
    def __init__(self, response):
        self.response = response

    def list(self, **kwargs):
        return FakeRequest(self.response)


class FakeYoutube:
    def __init__(self, channels_response, search_response):
        self._channels_response = channels_response
        self._search_response = search_response

    def channels(self):
        return FakeChannelsResource(self._channels_response)

    def search(self):
        return FakeSearchResource(self._search_response)


class ExtractorCoreTests(unittest.TestCase):
    def test_build_published_after_formats_date(self):
        self.assertEqual(
            build_published_after(datetime.date(2025, 1, 2)),
            "2025-01-02T00:00:00Z",
        )

    def test_build_published_after_accepts_none(self):
        self.assertIsNone(build_published_after(None))

    def test_extract_brands_is_case_insensitive(self):
        matched = extract_brands(
            "Shot on SONY and Logitech gear",
            [BrandRule(name="Sony", aliases=["Sony"]), BrandRule(name="Logitech", aliases=["Logitech"])],
        )
        self.assertCountEqual(matched, ["Sony", "Logitech"])

    def test_extract_brands_respects_word_boundaries(self):
        matched = extract_brands("Credit cards are unrelated here", [BrandRule(name="Red", aliases=["Red"])])
        self.assertEqual(matched, [])

    def test_resolve_channel_id_returns_literal_channel_id(self):
        youtube = FakeYoutube({"items": []}, {"items": []})
        self.assertEqual(
            resolve_channel_id(youtube, "UC1234567890123456789012"),
            "UC1234567890123456789012",
        )

    def test_resolve_channel_id_falls_back_to_search(self):
        youtube = FakeYoutube(
            {"items": []},
            {"items": [{"snippet": {"channelId": "UCSEARCHRESULT1234567890"}}]},
        )
        self.assertEqual(
            resolve_channel_id(youtube, "@example"),
            "UCSEARCHRESULT1234567890",
        )


if __name__ == "__main__":
    unittest.main()
