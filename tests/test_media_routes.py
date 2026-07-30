from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib import error as urlerror

from fastapi import FastAPI, HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.media_routes import register_media_routes, ui_audio_audiobook_art, ui_audio_music_art


class _FakeResponse:
    def __init__(self, *, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class MediaRoutesTests(unittest.TestCase):
    def test_register_media_routes_mounts_expected_paths(self) -> None:
        app = FastAPI()

        register_media_routes(app)

        routes = {(method, route.path) for route in app.routes for method in (getattr(route, "methods", set()) or set())}
        self.assertIn(("GET", "/api/ui/audio/art/audiobook/{library_item_id}"), routes)
        self.assertIn(("GET", "/api/ui/audio/art/music"), routes)
        self.assertIn(("GET", "/audiobooks/stream/{playback_id}/{track_index}"), routes)

    @patch("oracle_app.media_routes.fetch_audiobook_cover")
    def test_audiobook_art_returns_upstream_content_type(self, mock_fetch_cover) -> None:
        mock_fetch_cover.return_value = _FakeResponse(
            body=b"cover",
            headers={"Content-Type": "image/png; charset=binary"},
        )

        response = ui_audio_audiobook_art("book-1", user_id="reader_one")

        self.assertEqual(response.body, b"cover")
        self.assertEqual(response.media_type, "image/png")
        mock_fetch_cover.assert_called_once_with("book-1", user_id="reader_one")

    @patch("oracle_app.media_routes.fetch_audiobook_cover", side_effect=RuntimeError("missing"))
    def test_audiobook_art_maps_fetch_failure_to_404(self, _mock_fetch_cover) -> None:
        with self.assertRaises(HTTPException) as context:
            ui_audio_audiobook_art("book-1")

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("Audiobook artwork unavailable", str(context.exception.detail))

    @patch(
        "oracle_app.media_routes.fetch_audiobook_cover",
        side_effect=AssertionError("canonical artwork used V1 provider"),
    )
    def test_canonical_audiobook_art_uses_typed_execution(self, _legacy_cover) -> None:
        execution = Mock()
        execution.request_raw.return_value = _FakeResponse(
            body=b"canonical-cover",
            headers={"Content-Type": "image/webp"},
        )

        response = ui_audio_audiobook_art(
            "book 1",
            user_id="reader_one",
            audiobook_execution=execution,
            canonical_authority=True,
        )

        self.assertEqual(response.body, b"canonical-cover")
        execution.request_raw.assert_called_once_with(
            "/api/items/book%201/cover",
            method="GET",
            user_id="reader_one",
        )

    def test_music_art_rejects_invalid_path(self) -> None:
        for path in ("", "relative/path", "https://plex.example/art.jpg"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPException) as context:
                    ui_audio_music_art(path)

                self.assertEqual(context.exception.status_code, 400)

    @patch(
        "oracle_app.media_routes.get_music_settings",
        return_value={
            "plex_configured": True,
            "plex_base_url": "http://plex.local",
            "plex_token": "secret token",
            "plex_timeout_seconds": 7,
        },
    )
    @patch("oracle_app.media_routes.urlrequest.urlopen")
    def test_music_art_proxies_plex_artwork_with_token(self, mock_urlopen, _mock_settings) -> None:
        mock_urlopen.return_value = _FakeResponse(
            body=b"art",
            headers={"Content-Type": "image/jpeg"},
        )

        response = ui_audio_music_art("/library/metadata/1/thumb")

        self.assertEqual(response.body, b"art")
        self.assertEqual(response.media_type, "image/jpeg")
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://plex.local/library/metadata/1/thumb?X-Plex-Token=secret%20token")
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 7)

    @patch(
        "oracle_app.media_routes.get_music_settings",
        side_effect=AssertionError("canonical artwork used V1 music settings"),
    )
    def test_canonical_music_art_uses_typed_execution(self, _legacy_settings) -> None:
        execution = Mock()
        execution.fetch_artwork.return_value = _FakeResponse(
            body=b"canonical-art",
            headers={"Content-Type": "image/webp"},
        )

        response = ui_audio_music_art(
            "/library/metadata/1/thumb",
            music_execution=execution,
            canonical_authority=True,
        )

        self.assertEqual(response.body, b"canonical-art")
        self.assertEqual(response.media_type, "image/webp")
        execution.fetch_artwork.assert_called_once_with("/library/metadata/1/thumb")

    @patch(
        "oracle_app.media_routes.get_music_settings",
        return_value={
            "plex_configured": True,
            "plex_base_url": "http://plex.local",
            "plex_token": "token",
            "plex_timeout_seconds": 10,
        },
    )
    @patch("oracle_app.media_routes.urlrequest.urlopen")
    def test_music_art_maps_upstream_failure_to_404(self, mock_urlopen, _mock_settings) -> None:
        mock_urlopen.side_effect = urlerror.URLError("offline")

        with self.assertRaises(HTTPException) as context:
            ui_audio_music_art("/library/metadata/1/thumb")

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("Music artwork unavailable", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()
