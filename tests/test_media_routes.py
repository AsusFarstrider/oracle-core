from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

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
        self.assertIn(("GET", "/api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}"), routes)
        self.assertNotIn(("GET", "/audiobooks/stream/{playback_id}/{track_index}"), routes)

    def test_audiobook_art_returns_upstream_content_type(self) -> None:
        execution = Mock()
        execution.request_raw.return_value = _FakeResponse(
            body=b"cover",
            headers={"Content-Type": "image/png; charset=binary"},
        )

        response = ui_audio_audiobook_art(
            "book-1", user_id="reader_one", audiobook_execution=execution
        )

        self.assertEqual(response.body, b"cover")
        self.assertEqual(response.media_type, "image/png")
        execution.request_raw.assert_called_once_with(
            "/api/items/book-1/cover", method="GET", user_id="reader_one"
        )

    def test_audiobook_art_maps_fetch_failure_to_404(self) -> None:
        execution = Mock()
        execution.request_raw.side_effect = RuntimeError("missing")
        with self.assertRaises(HTTPException) as context:
            ui_audio_audiobook_art("book-1", audiobook_execution=execution)

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("Audiobook artwork unavailable", str(context.exception.detail))

    def test_canonical_audiobook_art_uses_typed_execution(self) -> None:
        execution = Mock()
        execution.request_raw.return_value = _FakeResponse(
            body=b"canonical-cover",
            headers={"Content-Type": "image/webp"},
        )

        response = ui_audio_audiobook_art(
            "book 1",
            user_id="reader_one",
            audiobook_execution=execution,
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
                    ui_audio_music_art(path, music_execution=Mock())

                self.assertEqual(context.exception.status_code, 400)

    def test_canonical_music_art_uses_typed_execution(self) -> None:
        execution = Mock()
        execution.fetch_artwork.return_value = _FakeResponse(
            body=b"canonical-art",
            headers={"Content-Type": "image/webp"},
        )

        response = ui_audio_music_art(
            "/library/metadata/1/thumb",
            music_execution=execution,
        )

        self.assertEqual(response.body, b"canonical-art")
        self.assertEqual(response.media_type, "image/webp")
        execution.fetch_artwork.assert_called_once_with("/library/metadata/1/thumb")

    def test_music_art_maps_upstream_failure_to_404(self) -> None:
        execution = Mock()
        execution.fetch_artwork.side_effect = RuntimeError("offline")

        with self.assertRaises(HTTPException) as context:
            ui_audio_music_art("/library/metadata/1/thumb", music_execution=execution)

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("Music artwork unavailable", str(context.exception.detail))


if __name__ == "__main__":
    unittest.main()
