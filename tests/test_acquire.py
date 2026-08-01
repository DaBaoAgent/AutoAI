from __future__ import annotations

import unittest

from dabo_kb.acquire import (
    ROUTER_DATA_MARKER,
    _duration_matches,
    extract_media_candidates,
)
from dabo_kb.routing import route_title, transcript_needs_visual_review


class RoutingTests(unittest.TestCase):
    def test_github_title_uses_small(self) -> None:
        result = route_title("GitHub 本周开源热榜：owner/repo")
        self.assertEqual(result["lane"], "small")
        self.assertTrue(result["needs_name_review"])

    def test_general_chinese_tutorial_uses_base(self) -> None:
        result = route_title("三个技巧让 AI 视频的声音无缝衔接")
        self.assertEqual(result["lane"], "base")

    def test_english_dense_transcript_needs_visual_review(self) -> None:
        text = "Alpha Beta Gamma Delta Epsilon Zeta Theta Kappa"
        self.assertTrue(transcript_needs_visual_review(text))


class CaptureParserTests(unittest.TestCase):
    def test_router_data_marker_is_stable_literal(self) -> None:
        self.assertEqual(ROUTER_DATA_MARKER, "window._ROUTER_DATA = ")

    def test_extracts_nested_audio_and_video_urls(self) -> None:
        payload = {
            "aweme_list": [
                {
                    "aweme_id": "7667234100725501238",
                    "desc": "GitHub 榜单",
                    "duration": 61000,
                    "music": {
                        "play_url": {
                            "url_list": ["https://audio.example/a.m4a"]
                        }
                    },
                    "video": {
                        "play_addr": {
                            "url_list": ["https://video.example/v.mp4"]
                        }
                    },
                }
            ]
        }
        items = extract_media_candidates(payload)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "7667234100725501238")
        self.assertEqual(items[0].duration_ms, 61000)
        self.assertEqual(
            items[0].audio_urls,
            ["https://audio.example/a.m4a"],
        )
        self.assertEqual(
            items[0].video_urls,
            ["https://video.example/v.mp4"],
        )

    def test_rejects_non_aweme_ids(self) -> None:
        payload = {
            "user": {
                "id": "123",
                "video": {"play_addr": {"url_list": ["https://bad"]}},
            }
        }
        self.assertEqual(extract_media_candidates(payload), [])

    def test_duration_validation_has_bounded_tolerance(self) -> None:
        self.assertTrue(_duration_matches(60.5, 60000))
        self.assertFalse(_duration_matches(42, 60000))


if __name__ == "__main__":
    unittest.main()
