from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.news import NewsQuery, execute_news_query, parse_news_query
from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan

RSS_PAYLOAD = """<rss><channel>
<item><title>Story One</title><link>https://example.invalid/1</link><pubDate>Tue, 07 Apr 2026 15:00:00 GMT</pubDate></item>
<item><title>Story Two</title><link>https://example.invalid/2</link><pubDate>Tue, 07 Apr 2026 16:00:00 GMT</pubDate></item>
</channel></rss>"""


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class NewsTests(unittest.TestCase):
    @patch(
        "oracle_app.news.get_news_settings",
        return_value={
            "sources": {
                "npr": {"label": "NPR", "url": "https://example.invalid/npr.xml"},
                "bbc": {"label": "BBC News", "url": "https://example.invalid/bbc.xml"},
            },
            "timeout_seconds": 8,
            "max_headlines": 3,
        },
    )
    def test_parse_news_query_matches_configured_source_key(self, _mock_settings) -> None:
        query = parse_news_query("give me bbc headlines")

        self.assertEqual(query, NewsQuery(source="bbc", original_text="give me bbc headlines"))

    @patch(
        "oracle_app.news.get_news_settings",
        return_value={
            "sources": {
                "npr": {"label": "NPR", "url": "https://example.invalid/npr.xml"},
                "bbc": {"label": "BBC News", "url": "https://example.invalid/bbc.xml"},
            },
            "timeout_seconds": 8,
            "max_headlines": 3,
        },
    )
    def test_parse_news_query_matches_configured_source_label(self, _mock_settings) -> None:
        query = parse_news_query("what are the latest bbc news headlines")

        self.assertEqual(query, NewsQuery(source="bbc", original_text="what are the latest bbc news headlines"))

    @patch(
        "oracle_app.news.get_news_settings",
        return_value={
            "sources": {
                "npr": {"label": "NPR", "url": "https://example.invalid/npr.xml"},
            },
            "timeout_seconds": 8,
            "max_headlines": 3,
        },
    )
    def test_parse_news_query_does_not_match_ap_inside_other_words(self, _mock_settings) -> None:
        query = parse_news_query("give me recap headlines")

        self.assertEqual(query, NewsQuery(source=None, original_text="give me recap headlines"))

    @patch(
        "oracle_app.news.get_news_settings",
        return_value={
            "sources": {
                "npr": {"label": "NPR", "url": "https://example.invalid/npr.xml"},
            },
            "timeout_seconds": 8,
            "max_headlines": 3,
        },
    )
    def test_execute_news_query_keeps_requested_source_label_when_unavailable(self, _mock_settings) -> None:
        result = execute_news_query(NewsQuery(source="reuters", original_text="reuters headlines"))

        self.assertEqual(result["error"], "news_source_unavailable")
        self.assertEqual(result["source"], "reuters")
        self.assertEqual(result["source_label"], "Reuters")

    @patch("oracle_app.provider_bridges.rss_news.request.urlopen")
    @patch(
        "oracle_app.news.get_news_settings",
        return_value={
            "news_provider": "rss",
            "sources": {
                "npr": {"label": "NPR", "url": "https://example.invalid/npr.xml", "provider": "rss"},
            },
            "timeout_seconds": 8,
            "max_headlines": 1,
        },
    )
    def test_execute_news_query_uses_rss_bridge_and_limits_headlines(self, _mock_settings, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse(RSS_PAYLOAD)

        result = execute_news_query(NewsQuery(source="npr", original_text="npr headlines"))

        self.assertEqual(result["source"], "npr")
        self.assertEqual(result["source_label"], "NPR")
        self.assertEqual(len(result["headlines"]), 1)
        self.assertEqual(result["headlines"][0]["title"], "Story One")

    def test_news_reply_uses_requested_source_label_when_no_headlines_are_available(self) -> None:
        dispatch = DispatchPlan(
            target="news",
            hook="news.execute",
            payload={"text": "reuters headlines"},
            status="executed",
            result={
                "action": "headlines",
                "source": "reuters",
                "source_label": "Reuters",
                "headlines": [],
                "error": "news_source_unavailable",
            },
        )

        self.assertEqual(
            build_reply_text(dispatch),
            "I couldn't find any current headlines from Reuters.",
        )
