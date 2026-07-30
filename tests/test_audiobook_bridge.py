from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.provider_bridges.audiobookshelf_audiobook import (
    AudiobookshelfAudiobookBridge,
    normalize_audiobook_item,
    normalize_audiobook_playback_session,
)


class _FakeResponse:
    def __init__(self, payload: dict | str = "") -> None:
        self._payload = payload

    def read(self) -> bytes:
        if isinstance(self._payload, str):
            return self._payload.encode("utf-8")
        return json.dumps(self._payload).encode("utf-8")


class AudiobookshelfAudiobookBridgeTests(unittest.TestCase):
    def test_playback_session_normalization_hides_provider_field_names(self) -> None:
        session = normalize_audiobook_playback_session(
            {
                "id": "session-1",
                "libraryItemId": "book-1",
                "displayTitle": "Dune",
                "displayAuthor": "Frank Herbert",
                "duration": 1000,
                "currentTime": 125,
                "audioTracks": [{"contentUrl": "/audio/1", "mimeType": "audio/mpeg"}],
            }
        )

        self.assertEqual(session["provider_session_id"], "session-1")
        self.assertEqual(session["library_item_id"], "book-1")
        self.assertEqual(session["tracks"][0]["content_url"], "/audio/1")
        self.assertNotIn("audioTracks", session)
        self.assertNotIn("libraryItemId", session)

    def test_item_normalization_hides_provider_progress_shape(self) -> None:
        item = normalize_audiobook_item(
            {
                "id": "book-1",
                "media": {"duration": 1000, "metadata": {"title": "Dune", "authors": [{"name": "Frank Herbert"}]}},
                "userMediaProgress": {"libraryItemId": "book-1", "currentTime": 125, "isFinished": False},
            }
        )

        self.assertEqual(item["title"], "Dune")
        self.assertEqual(item["progress"]["current_time_seconds"], 125.0)
        self.assertNotIn("userMediaProgress", item)

    @patch(
        "oracle_app.provider_bridges.audiobookshelf_audiobook.get_audiobook_connection_settings",
        return_value={
            "base_url": "https://abs.example",
            "api_key": "token",
            "library_id": "library-1",
            "timeout_seconds": 8,
            "configured": True,
        },
    )
    @patch("oracle_app.provider_bridges.audiobookshelf_audiobook.request.urlopen")
    def test_sync_session_preserves_position_reporting_payload(self, mock_urlopen, _mock_settings) -> None:
        mock_urlopen.return_value = _FakeResponse("")

        AudiobookshelfAudiobookBridge().sync_session(
            "session-1",
            current_time=123.45,
            time_listened=-7.0,
            duration=456.0,
            user_id="reader_one",
        )

        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://abs.example/api/session/session-1/sync")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Authorization"], "Bearer token")
        self.assertEqual(
            json.loads(req.data.decode("utf-8")),
            {
                "currentTime": 123.45,
                "timeListened": 0.0,
                "duration": 456.0,
            },
        )

    @patch(
        "oracle_app.provider_bridges.audiobookshelf_audiobook.get_audiobook_connection_settings",
        return_value={
            "base_url": "https://abs.example",
            "api_key": "token",
            "library_id": "library-1",
            "timeout_seconds": 8,
            "configured": True,
        },
    )
    @patch("oracle_app.provider_bridges.audiobookshelf_audiobook.request.urlopen")
    def test_fetch_current_progress_keeps_latest_unfinished_book_progress(self, mock_urlopen, _mock_settings) -> None:
        mock_urlopen.return_value = _FakeResponse(
            {
                "mediaProgress": [
                    {
                        "libraryItemId": "finished",
                        "mediaItemType": "book",
                        "currentTime": 900,
                        "lastUpdate": 40,
                        "isFinished": True,
                    },
                    {
                        "libraryItemId": "older",
                        "mediaItemType": "book",
                        "currentTime": 100,
                        "lastUpdate": 50,
                    },
                    {
                        "libraryItemId": "latest",
                        "mediaItemType": "book",
                        "currentTime": 200,
                        "lastUpdate": 60,
                    },
                ]
            }
        )

        progress = AudiobookshelfAudiobookBridge().fetch_current_progress(user_id="reader_one")

        self.assertIsNotNone(progress)
        self.assertEqual(progress["library_item_id"], "latest")
        self.assertEqual(progress["current_time_seconds"], 200.0)
        self.assertNotIn("libraryItemId", progress)

    @patch(
        "oracle_app.provider_bridges.audiobookshelf_audiobook.get_audiobook_connection_settings",
        return_value={
            "base_url": "https://abs.example",
            "api_key": "token",
            "library_id": "library-1",
            "timeout_seconds": 8,
            "configured": True,
        },
    )
    @patch("oracle_app.provider_bridges.audiobookshelf_audiobook.request.urlopen")
    def test_search_titles_normalizes_audiobookshelf_results(self, mock_urlopen, _mock_settings) -> None:
        mock_urlopen.return_value = _FakeResponse(
            {
                "book": [
                    {
                        "libraryItem": {
                            "id": "book-1",
                            "media": {
                                "duration": 321.0,
                                "metadata": {
                                    "title": "Dune",
                                    "subtitle": "Book One",
                                    "authors": [{"name": "Frank Herbert"}],
                                    "narratorName": "Simon Vance",
                                    "series": [{"name": "Dune", "sequence": "1"}],
                                },
                            },
                        }
                    }
                ]
            }
        )

        candidates = AudiobookshelfAudiobookBridge().search_titles("dune", user_id="reader_one")

        self.assertEqual(
            candidates,
            [
                {
                    "library_item_id": "book-1",
                    "title": "Dune",
                    "subtitle": "Book One",
                    "author": "Frank Herbert",
                    "narrator": "Simon Vance",
                    "series": [{"name": "Dune", "sequence": "1"}],
                    "duration": 321.0,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
