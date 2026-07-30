from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from starlette.requests import Request


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app import state
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.media_routes import stream_audiobook_track


class _FakeUpstreamResponse:
    def __init__(self, *, body: bytes, headers: dict[str, str], status: int) -> None:
        self._body = body
        self._offset = 0
        self.headers = headers
        self.status = status
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class AudiobookStreamApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        state.clear_all_active_audiobook_playbacks()

    def _build_request(self, *, range_header: str | None = None, app=None) -> Request:
        headers: list[tuple[bytes, bytes]] = []
        if range_header is not None:
            headers.append((b"range", range_header.encode("ascii")))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/audiobooks/stream/test/0",
            "headers": headers,
            "app": app,
        }
        return Request(scope)

    @patch(
        "oracle_app.media_routes.fetch_audiobook_stream",
        side_effect=AssertionError("canonical stream used V1 provider"),
    )
    @patch("oracle_app.media_routes.get_active_audiobook_playback")
    def test_canonical_stream_uses_composition_execution(
        self,
        mock_get_active_audiobook_playback,
        _legacy_stream,
    ) -> None:
        playback = {"playback_id": "playback-canonical", "user_id": "reader_one"}
        mock_get_active_audiobook_playback.return_value = playback
        upstream = _FakeUpstreamResponse(
            body=b"canonical",
            headers={"Content-Type": "audio/mpeg"},
            status=200,
        )
        execution = Mock()
        execution.fetch_stream.return_value = upstream
        application = FastAPI()
        application.state.brain_application_composition = CanonicalBrainApplicationComposition(
            runtime=Mock(),
            core_consumers=Mock(),
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
            audiobook_execution=execution,
        )

        response = stream_audiobook_track(
            "playback-canonical",
            0,
            self._build_request(range_header="bytes=0-", app=application),
        )

        self.assertEqual(response.status_code, 200)
        execution.fetch_stream.assert_called_once_with(
            playback,
            0,
            range_header="bytes=0-",
        )

    @patch("oracle_app.media_routes.fetch_audiobook_stream")
    @patch("oracle_app.media_routes.get_active_audiobook_playback")
    def test_stream_endpoint_forwards_range_requests(
        self,
        mock_get_active_audiobook_playback,
        mock_fetch_audiobook_stream,
    ) -> None:
        mock_get_active_audiobook_playback.return_value = {"playback_id": "playback-1"}
        upstream = _FakeUpstreamResponse(
            body=b"abcdef",
            headers={
                "Content-Type": "audio/mpeg",
                "Accept-Ranges": "bytes",
                "Content-Length": "3",
                "Content-Range": "bytes 10-12/100",
            },
            status=206,
        )
        mock_fetch_audiobook_stream.return_value = upstream

        response = stream_audiobook_track(
            "playback-1",
            0,
            self._build_request(range_header="bytes=10-12"),
        )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.media_type, "audio/mpeg")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["content-length"], "3")
        self.assertEqual(response.headers["content-range"], "bytes 10-12/100")
        mock_fetch_audiobook_stream.assert_called_once_with(
            {"playback_id": "playback-1"},
            0,
            range_header="bytes=10-12",
        )

    @patch("oracle_app.media_routes.fetch_audiobook_stream")
    @patch("oracle_app.media_routes.get_active_audiobook_playback")
    def test_stream_endpoint_without_range_uses_full_response(
        self,
        mock_get_active_audiobook_playback,
        mock_fetch_audiobook_stream,
    ) -> None:
        mock_get_active_audiobook_playback.return_value = {"playback_id": "playback-2"}
        upstream = _FakeUpstreamResponse(
            body=b"hello world",
            headers={"Content-Type": "audio/mpeg", "Content-Length": "11"},
            status=200,
        )
        mock_fetch_audiobook_stream.return_value = upstream

        response = stream_audiobook_track("playback-2", 1, self._build_request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "audio/mpeg")
        self.assertEqual(response.headers["content-length"], "11")
        mock_fetch_audiobook_stream.assert_called_once_with(
            {"playback_id": "playback-2"},
            1,
            range_header=None,
        )

    def test_stream_endpoint_rejects_stale_playback_id_after_source_replacement(self) -> None:
        state.register_active_audiobook_playback(
            "playback-1",
            {
                "playback_id": "playback-1",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-1",
            },
        )
        state.register_active_audiobook_playback(
            "playback-2",
            {
                "playback_id": "playback-2",
                "source": "test_satellite_bravo",
                "abs_session_id": "session-2",
            },
        )

        with self.assertRaises(HTTPException) as context:
            stream_audiobook_track("playback-1", 0, self._build_request())

        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
