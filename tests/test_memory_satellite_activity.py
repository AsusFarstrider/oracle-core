from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from fastapi import HTTPException

from oracle_app import api
from oracle_app.memory import satellite_activity, schema, sources
from oracle_app.memory.events import list_events
from oracle_app.memory.store import transaction
from oracle_app.memory.taxonomy import validate_event_type
from oracle_app.schemas import SatelliteActivityRequest


class OracleMemorySatelliteActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"
        sources.seed_sources(
            [
                {"source_id": "satellite-alpha", "source_type": "satellite", "display_name": "Wall Display"},
                {"source_id": "satellite-beta", "source_type": "satellite", "display_name": "Guest Room"},
                {"source_id": "satellite-gamma", "source_type": "satellite", "display_name": "Common Room"},
            ],
            db_path=self.db_path,
        )

    def test_satellite_taxonomy_accepts_only_approved_initial_events(self) -> None:
        approved = {
            "satellite_started",
            "satellite_stopped",
            "satellite_error",
            "wake_detected",
            "audio_capture_failed",
            "stt_upload_failed",
            "tts_playback_failed",
        }

        for event_type in approved:
            self.assertEqual(validate_event_type(event_type), event_type)

        with self.assertRaises(ValueError):
            validate_event_type("satellite_polling")
        with self.assertRaises(ValueError):
            validate_event_type("satellite_heartbeat")

    def test_observe_satellite_activity_writes_event_and_snapshot(self) -> None:
        result = satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            event_type="wake_detected",
            status="available",
            correlation_id="corr-wake-1",
            payload={"wake_score": 0.88},
            db_path=self.db_path,
        )

        self.assertTrue(result["event_recorded"])
        [event] = list_events(db_path=self.db_path)
        self.assertEqual(event["event_type"], "wake_detected")
        self.assertEqual(event["source_id"], "satellite-beta")
        self.assertEqual(event["correlation_id"], "corr-wake-1")
        self.assertEqual(event["domain"], "satellite")
        self.assertEqual(event["provider"], "satellite-beta")
        self.assertEqual(event["status"], "available")
        self.assertEqual(event["payload"]["wake_score"], 0.88)

        snapshot = satellite_activity.get_satellite_status_snapshot("satellite-beta", db_path=self.db_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["projection_type"], "satellite_status")
        self.assertEqual(snapshot["source_id"], "satellite-beta")
        self.assertEqual(snapshot["provider"], "satellite-beta")
        self.assertEqual(snapshot["domain"], "satellite")
        self.assertEqual(snapshot["status"], "available")
        self.assertEqual(snapshot["payload"]["last_event_type"], "wake_detected")
        self.assertIn("last_wake_at", snapshot["payload"])

    def test_source_identity_is_not_rewritten(self) -> None:
        with self.assertRaises(ValueError):
            satellite_activity.observe_satellite_activity(
                source_id="satellite-beta-alias",
                event_type="wake_detected",
                db_path=self.db_path,
            )

    def test_unknown_satellite_source_is_rejected_before_writing(self) -> None:
        with self.assertRaises(ValueError):
            satellite_activity.observe_satellite_activity(
                source_id="unknown-satellite",
                event_type="wake_detected",
                db_path=self.db_path,
            )

        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_current_projections").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_sources").fetchone()[0], 3)

    def test_non_satellite_source_is_rejected(self) -> None:
        sources.upsert_source(
            source_id="browser-client",
            source_type="ui",
            display_name="Browser",
            db_path=self.db_path,
        )
        with self.assertRaises(ValueError):
            satellite_activity.observe_satellite_activity(
                source_id="browser-client",
                event_type="satellite_started",
                db_path=self.db_path,
            )

    def test_api_endpoint_rejects_unknown_satellite_source_before_writing(self) -> None:
        request = SatelliteActivityRequest(source_id="unknown-satellite", event_type="wake_detected")
        fake_request = SimpleNamespace(headers={})

        with patch("oracle_app.memory.satellite_activity.DB_PATH", self.db_path):
            with self.assertRaises(HTTPException) as raised:
                api.satellite_activity(request, fake_request)

        self.assertEqual(raised.exception.status_code, 422)
        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_current_projections").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_sources").fetchone()[0], 3)

    def test_snapshot_only_update_creates_no_event(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="available",
            snapshot={"last_seen_at": "2026-04-28T12:00:00+00:00"},
            db_path=self.db_path,
        )

        self.assertEqual(list_events(db_path=self.db_path), [])
        snapshot = satellite_activity.get_satellite_status_snapshot("satellite-beta", db_path=self.db_path)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["status"], "available")

    def test_same_status_snapshot_updates_do_not_create_events(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="available",
            snapshot={"last_seen_at": "2026-04-28T12:00:00+00:00"},
            db_path=self.db_path,
        )
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="available",
            snapshot={"last_seen_at": "2026-04-28T12:01:00+00:00"},
            db_path=self.db_path,
        )

        self.assertEqual(list_events(db_path=self.db_path), [])

    def test_unknown_event_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            satellite_activity.observe_satellite_activity(
                source_id="satellite-beta",
                event_type="satellite_heartbeat",
                db_path=self.db_path,
            )

    def test_safe_observe_satellite_activity_fails_open(self) -> None:
        with patch(
            "oracle_app.memory.satellite_activity.observe_satellite_activity",
            side_effect=RuntimeError("db unavailable"),
        ):
            result = satellite_activity.safe_observe_satellite_activity(
                source_id="satellite-beta",
                event_type="wake_detected",
                db_path=self.db_path,
            )

        self.assertFalse(result)

    def test_api_endpoint_writes_activity_and_returns_accepted(self) -> None:
        request = SatelliteActivityRequest(
            source_id="satellite-beta",
            event_type="satellite_started",
            status="available",
            payload={"model_path": "model.onnx"},
        )
        fake_request = SimpleNamespace(headers={})

        with patch("oracle_app.memory.satellite_activity.DB_PATH", self.db_path):
            response = api.satellite_activity(request, fake_request)

        self.assertEqual(response.model_dump(), {"accepted": True})
        [event] = list_events(db_path=self.db_path)
        self.assertEqual(event["event_type"], "satellite_started")

    def test_api_endpoint_rejects_unknown_event_type_before_writing(self) -> None:
        request = SatelliteActivityRequest(source_id="satellite-beta", event_type="satellite_heartbeat")
        fake_request = SimpleNamespace(headers={})

        with patch("oracle_app.memory.satellite_activity.DB_PATH", self.db_path):
            with self.assertRaises(HTTPException) as raised:
                api.satellite_activity(request, fake_request)

        self.assertEqual(raised.exception.status_code, 422)
        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_current_projections").fetchone()[0], 0)

    def test_api_endpoint_memory_write_failure_still_accepts(self) -> None:
        request = SatelliteActivityRequest(source_id="satellite-beta", event_type="wake_detected")
        fake_request = SimpleNamespace(headers={})

        with patch("oracle_app.satellite_activity_routes.observe_satellite_activity", side_effect=RuntimeError("db unavailable")):
            response = api.satellite_activity(request, fake_request)

        self.assertEqual(response.model_dump(), {"accepted": True})

    def test_query_satellite_status_snapshots_filters_and_orders_newest_first(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="available",
            observed_at="2026-04-28T12:00:00+00:00",
            snapshot={"last_seen_at": "2026-04-28T12:00:00+00:00"},
            db_path=self.db_path,
        )
        satellite_activity.observe_satellite_activity(
            source_id="satellite-gamma",
            status="degraded",
            observed_at="2026-04-28T12:05:00+00:00",
            snapshot={"last_error": "speaker offline"},
            db_path=self.db_path,
        )

        rows = satellite_activity.query_satellite_status_snapshots(db_path=self.db_path)
        self.assertEqual([row["source_id"] for row in rows], ["satellite-gamma", "satellite-beta"])
        self.assertEqual(
            [row["source_id"] for row in satellite_activity.query_satellite_status_snapshots(
                satellite_activity.SatelliteStatusQuery(source_id="satellite-beta"),
                db_path=self.db_path,
            )],
            ["satellite-beta"],
        )
        self.assertEqual(
            [row["source_id"] for row in satellite_activity.query_satellite_status_snapshots(
                satellite_activity.SatelliteStatusQuery(status="degraded"),
                db_path=self.db_path,
            )],
            ["satellite-gamma"],
        )

    def test_query_satellite_status_snapshots_limits_offsets_and_clamps(self) -> None:
        for index, source_id in enumerate(("satellite-beta", "satellite-alpha", "satellite-gamma")):
            satellite_activity.observe_satellite_activity(
                source_id=source_id,
                status="available",
                observed_at=f"2026-04-28T12:0{index}:00+00:00",
                snapshot={"last_seen_at": f"2026-04-28T12:0{index}:00+00:00"},
                db_path=self.db_path,
            )

        self.assertEqual(len(satellite_activity.query_satellite_status_snapshots(db_path=self.db_path)), 3)
        rows = satellite_activity.query_satellite_status_snapshots(
            satellite_activity.SatelliteStatusQuery(limit=1, offset=1),
            db_path=self.db_path,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "satellite-alpha")
        self.assertEqual(
            len(satellite_activity.query_satellite_status_snapshots(
                satellite_activity.SatelliteStatusQuery(limit=0),
                db_path=self.db_path,
            )),
            1,
        )
        self.assertEqual(
            len(satellite_activity.query_satellite_status_snapshots(
                satellite_activity.SatelliteStatusQuery(limit=999),
                db_path=self.db_path,
            )),
            3,
        )

    def test_query_satellite_status_snapshots_handles_malformed_payload_json(self) -> None:
        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_current_projections (
                    projection_id, created_at, updated_at, observed_at, projection_type,
                    source_id, provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "satellite_status:satellite-beta",
                    "2026-04-28T12:00:00+00:00",
                    "2026-04-28T12:00:00+00:00",
                    "2026-04-28T12:00:00+00:00",
                    "satellite_status",
                    "satellite-beta",
                    "satellite-beta",
                    "satellite",
                    "available",
                    '["bad"]',
                ),
            )

        [row] = satellite_activity.query_satellite_status_snapshots(db_path=self.db_path)
        self.assertEqual(row["payload"], {"_payload_parse_error": True, "raw_payload_json": '["bad"]'})

    def test_query_satellite_status_snapshots_do_not_write_or_modify_rows(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="available",
            snapshot={"last_seen_at": "2026-04-28T12:00:00+00:00"},
            db_path=self.db_path,
        )
        with transaction(self.db_path) as conn:
            before = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_current_projections").fetchone()

        satellite_activity.query_satellite_status_snapshots(db_path=self.db_path)
        satellite_activity.list_satellite_status_snapshots(db_path=self.db_path)
        satellite_activity.get_latest_satellite_status("satellite-beta", db_path=self.db_path)
        satellite_activity.get_satellite_status_snapshot("satellite-beta", db_path=self.db_path)

        with transaction(self.db_path) as conn:
            after = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_current_projections").fetchone()

        self.assertEqual(tuple(after), tuple(before))


if __name__ == "__main__":
    unittest.main()
