from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
import wave

from fastapi import FastAPI, HTTPException, UploadFile
from starlette.requests import Request

from oracle_app.configuration import SatelliteProjectionAuthenticationError
from oracle_app.wake_capture_upload_routes import (
    WakeCaptureUploadService,
    configure_wake_capture_upload_routes,
    register_wake_capture_upload_routes,
    satellite_wake_capture_upload,
    wake_capture_archive_root_from_environment,
)


class _Resolver:
    def __init__(self, *, source_id: str = "living_room_source", fail: bool = False) -> None:
        self.source_id = source_id
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def resolve(self, satellite_id: str, credential: str):
        self.calls.append((satellite_id, credential))
        if self.fail:
            raise SatelliteProjectionAuthenticationError("private reason")
        projection = types.SimpleNamespace(source_id=self.source_id)
        generation = types.SimpleNamespace(projection=projection)
        installed = types.SimpleNamespace(projection=generation)
        return types.SimpleNamespace(installed=installed)


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x01\x00" * 160)
    return output.getvalue()


def _metadata(*, source_id: str = "living_room_source") -> dict[str, object]:
    timestamp = 1_720_000_000.0
    return {
        "event_type": "activation",
        "timestamp": timestamp,
        "source_id": source_id,
        "score": 0.91,
        "playback_active": False,
        "ducking_triggered": True,
        "timestamp_iso": datetime.fromtimestamp(timestamp, tz=UTC).isoformat(),
        "sample_rate": 16_000,
        "channels": 1,
        "sample_width_bytes": 2,
        "format": "wav_pcm_s16le",
    }


def _request(app: FastAPI, authorization: str | None = "Bearer operational-token") -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/satellite/wake-captures/living_room_satellite",
            "raw_path": b"/api/satellite/wake-captures/living_room_satellite",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("oracle", 80),
            "app": app,
        }
    )


class WakeCaptureUploadRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_authenticated_upload_persists_one_idempotent_pair(self) -> None:
        app = FastAPI()
        resolver = _Resolver()
        configure_wake_capture_upload_routes(app, resolver, self.root)  # type: ignore[arg-type]
        encoded_metadata = json.dumps(_metadata())

        first = asyncio.run(
            satellite_wake_capture_upload(
                "living_room_satellite",
                _request(app),
                encoded_metadata,
                UploadFile(file=io.BytesIO(_wav()), filename="untrusted.wav"),
            )
        )
        second = asyncio.run(
            satellite_wake_capture_upload(
                "living_room_satellite",
                _request(app),
                encoded_metadata,
                UploadFile(file=io.BytesIO(_wav()), filename="different.wav"),
            )
        )

        first_body = json.loads(first.body)
        second_body = json.loads(second.body)
        self.assertEqual(first_body, second_body)
        self.assertTrue(first_body["ok"])
        self.assertEqual(first.headers["cache-control"], "no-store")
        archive = self.root / "living_room_satellite" / "2024-07-03" / "activation"
        self.assertEqual(sorted(path.suffix for path in archive.iterdir()), [".json", ".wav"])
        self.assertEqual(
            resolver.calls,
            [
                ("living_room_satellite", "operational-token"),
                ("living_room_satellite", "operational-token"),
            ],
        )

    def test_source_mismatch_and_invalid_wav_are_rejected_without_archive(self) -> None:
        service = WakeCaptureUploadService(_Resolver(), self.root)  # type: ignore[arg-type]
        cases = (
            (json.dumps(_metadata(source_id="claimed_source")).encode(), _wav()),
            (json.dumps(_metadata()).encode(), b"not-wave"),
        )
        for metadata, audio in cases:
            with self.subTest(audio=audio[:8]):
                with self.assertRaises(ValueError):
                    service.persist(
                        "living_room_satellite",
                        "living_room_source",
                        metadata,
                        audio,
                    )
        self.assertEqual(list(self.root.rglob("*.json")), [])

    def test_authentication_and_unconfigured_service_fail_generically(self) -> None:
        for resolver, authorization, status_code in (
            (_Resolver(), None, 401),
            (_Resolver(fail=True), "Bearer wrong", 401),
            (None, "Bearer token", 503),
        ):
            app = FastAPI()
            if resolver is not None:
                configure_wake_capture_upload_routes(app, resolver, self.root)  # type: ignore[arg-type]
            with self.subTest(status_code=status_code, authorization=authorization):
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        satellite_wake_capture_upload(
                            "living_room_satellite",
                            _request(app, authorization),
                            json.dumps(_metadata()),
                            UploadFile(file=io.BytesIO(_wav()), filename="clip.wav"),
                        )
                    )
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertNotIn("private reason", str(raised.exception.detail))
                self.assertEqual(raised.exception.headers["Cache-Control"], "no-store")

    def test_archive_root_is_typed_host_bootstrap_and_route_is_fixed(self) -> None:
        configured = self.root / "archive"
        self.assertEqual(
            wake_capture_archive_root_from_environment(
                {"ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT": str(configured)}
            ),
            configured,
        )
        self.assertTrue(configured.is_dir())
        with self.assertRaisesRegex(ValueError, "absolute"):
            wake_capture_archive_root_from_environment(
                {"ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT": "relative/archive"}
            )

        app = FastAPI()
        register_wake_capture_upload_routes(app)
        routes = {
            (method, route.path)
            for route in app.routes
            for method in (getattr(route, "methods", set()) or set())
        }
        self.assertIn(
            ("POST", "/api/satellite/wake-captures/{satellite_id}"),
            routes,
        )


if __name__ == "__main__":
    unittest.main()
