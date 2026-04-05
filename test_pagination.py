import unittest

from brand_rules import BrandRule
from extractor_core import search_channel_brand_mentions


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
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self.responses[len(self.calls) - 1])


class FakeVideosResource:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self.responses[len(self.calls) - 1])


class FakeVideoCategoriesResource:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self.responses[len(self.calls) - 1])


class FakeYoutube:
    def __init__(self, channels_response, search_responses, video_responses, category_responses):
        self.channels_response = channels_response
        self.search_resource = FakeSearchResource(search_responses)
        self.videos_resource = FakeVideosResource(video_responses)
        self.video_categories_resource = FakeVideoCategoriesResource(category_responses)

    def channels(self):
        return FakeChannelsResource(self.channels_response)

    def search(self):
        return self.search_resource

    def videos(self):
        return self.videos_resource

    def videoCategories(self):
        return self.video_categories_resource


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.sony_rules = [BrandRule(name="Sony", aliases=["Sony"])]

    def test_search_channel_brand_mentions_fetches_all_pages(self):
        youtube = FakeYoutube(
            {"items": []},
            [
                {"items": [{"snippet": {"channelId": "UC1234567890123456789012"}}]},
                {
                    "items": [
                        {
                            "id": {"videoId": "video-1"},
                            "snippet": {
                                "title": "Sony review part 1",
                                "description": "",
                                "publishedAt": "2025-01-01T00:00:00Z",
                            },
                        }
                    ],
                    "nextPageToken": "PAGE_2",
                },
                {
                    "items": [
                        {
                            "id": {"videoId": "video-2"},
                            "snippet": {
                                "title": "Sony review part 2",
                                "description": "",
                                "publishedAt": "2025-01-02T00:00:00Z",
                            },
                        }
                    ]
                },
            ],
            [
                {
                    "items": [
                        {
                            "id": "video-1",
                            "snippet": {"categoryId": "28", "tags": ["Sony", "Camera"]},
                            "contentDetails": {"duration": "PT10M"},
                            "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"},
                        },
                        {
                            "id": "video-2",
                            "snippet": {"categoryId": "28", "tags": ["Sony"]},
                            "contentDetails": {"duration": "PT8M"},
                            "statistics": {"viewCount": "800", "likeCount": "40", "commentCount": "4"},
                        },
                    ]
                }
            ],
            [
                {"items": [{"id": "28", "snippet": {"title": "Science & Technology"}}]}
            ],
        )
        page_updates = []

        result = search_channel_brand_mentions(
            youtube,
            kol="@example",
            search_query="camera",
            brands=self.sony_rules,
            published_after=None,
            enable_full_search=True,
            enable_deep_search=True,
            page_progress=lambda page_number, total_items, has_next: page_updates.append(
                (page_number, total_items, has_next)
            ),
        )

        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(len(youtube.search_resource.calls), 3)
        self.assertEqual(len(youtube.videos_resource.calls), 1)
        self.assertEqual(len(youtube.video_categories_resource.calls), 1)
        self.assertNotIn("pageToken", youtube.search_resource.calls[1])
        self.assertEqual(youtube.search_resource.calls[2]["pageToken"], "PAGE_2")
        self.assertEqual(page_updates, [(1, 1, True), (2, 2, False)])

    def test_search_channel_brand_mentions_stops_after_first_page_by_default(self):
        youtube = FakeYoutube(
            {"items": []},
            [
                {"items": [{"snippet": {"channelId": "UC1234567890123456789012"}}]},
                {
                    "items": [
                        {
                            "id": {"videoId": "video-1"},
                            "snippet": {
                                "title": "Sony review part 1",
                                "description": "",
                                "publishedAt": "2025-01-01T00:00:00Z",
                            },
                        }
                    ],
                    "nextPageToken": "PAGE_2",
                },
            ],
            [],
            [],
        )
        page_updates = []

        result = search_channel_brand_mentions(
            youtube,
            kol="@example",
            search_query="camera",
            brands=self.sony_rules,
            published_after=None,
            page_progress=lambda page_number, total_items, has_next: page_updates.append(
                (page_number, total_items, has_next)
            ),
        )

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(youtube.search_resource.calls), 2)
        self.assertEqual(len(youtube.videos_resource.calls), 0)
        self.assertEqual(len(youtube.video_categories_resource.calls), 0)
        self.assertEqual(page_updates, [(1, 1, True)])

    def test_deep_search_matches_brand_from_tags(self):
        youtube = FakeYoutube(
            {"items": []},
            [
                {"items": [{"snippet": {"channelId": "UC1234567890123456789012"}}]},
                {
                    "items": [
                        {
                            "id": {"videoId": "video-3"},
                            "snippet": {
                                "title": "Creator desk setup",
                                "description": "No brand in title or description",
                                "publishedAt": "2025-01-03T00:00:00Z",
                            },
                        }
                    ]
                },
            ],
            [
                {
                    "items": [
                        {
                            "id": "video-3",
                            "snippet": {"categoryId": "28", "tags": ["Sony", "creator"]},
                            "contentDetails": {"duration": "PT5M"},
                            "statistics": {"viewCount": "300", "likeCount": "20", "commentCount": "2"},
                        }
                    ]
                }
            ],
            [
                {"items": [{"id": "28", "snippet": {"title": "Science & Technology"}}]}
            ],
        )

        result = search_channel_brand_mentions(
            youtube,
            kol="@example",
            search_query="camera",
            brands=self.sony_rules,
            published_after=None,
            enable_deep_search=True,
        )

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(youtube.videos_resource.calls), 1)
        self.assertTrue(any("命中标签" in str(value) for value in result.rows[0].values()))

    def test_match_scope_can_disable_title_description_and_keep_tags(self):
        youtube = FakeYoutube(
            {"items": []},
            [
                {"items": [{"snippet": {"channelId": "UC1234567890123456789012"}}]},
                {
                    "items": [
                        {
                            "id": {"videoId": "video-4"},
                            "snippet": {
                                "title": "Desk setup",
                                "description": "No brand here",
                                "publishedAt": "2025-01-04T00:00:00Z",
                            },
                        }
                    ]
                },
            ],
            [
                {
                    "items": [
                        {
                            "id": "video-4",
                            "snippet": {"categoryId": "28", "tags": ["Sony"]},
                            "contentDetails": {"duration": "PT6M"},
                            "statistics": {"viewCount": "500", "likeCount": "30", "commentCount": "3"},
                        }
                    ]
                }
            ],
            [
                {"items": [{"id": "28", "snippet": {"title": "Science & Technology"}}]}
            ],
        )

        result = search_channel_brand_mentions(
            youtube,
            kol="@example",
            search_query="camera",
            brands=self.sony_rules,
            published_after=None,
            enable_deep_search=True,
            match_title=False,
            match_description=False,
            match_tags=True,
        )
        self.assertEqual(len(result.rows), 1)

        youtube_without_tags = FakeYoutube(
            {"items": []},
            [
                {"items": [{"snippet": {"channelId": "UC1234567890123456789012"}}]},
                {
                    "items": [
                        {
                            "id": {"videoId": "video-4"},
                            "snippet": {
                                "title": "Desk setup",
                                "description": "No brand here",
                                "publishedAt": "2025-01-04T00:00:00Z",
                            },
                        }
                    ]
                },
            ],
            [
                {
                    "items": [
                        {
                            "id": "video-4",
                            "snippet": {"categoryId": "28", "tags": ["Sony"]},
                            "contentDetails": {"duration": "PT6M"},
                            "statistics": {"viewCount": "500", "likeCount": "30", "commentCount": "3"},
                        }
                    ]
                }
            ],
            [
                {"items": [{"id": "28", "snippet": {"title": "Science & Technology"}}]}
            ],
        )

        result_without_tags = search_channel_brand_mentions(
            youtube_without_tags,
            kol="@example",
            search_query="camera",
            brands=self.sony_rules,
            published_after=None,
            enable_deep_search=True,
            match_title=False,
            match_description=False,
            match_tags=False,
        )
        self.assertEqual(len(result_without_tags.rows), 0)


if __name__ == "__main__":
    unittest.main()
