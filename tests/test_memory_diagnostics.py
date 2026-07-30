from __future__ import annotations

import importlib
import inspect
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.memory import diagnostics, events, provider_status, satellite_activity, schema, sources
from oracle_app.memory.store import transaction


class OracleMemoryDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"
        sources.seed_sources(
            [
                {"source_id": "satellite-alpha", "source_type": "satellite", "display_name": "Satellite Alpha"},
                {"source_id": "satellite-beta", "source_type": "satellite", "display_name": "Satellite Beta"},
            ],
            db_path=self.db_path,
        )

    def test_default_summary_uses_24_hour_event_window(self) -> None:
        events.record_event(
            "server_started",
            observed_at="2026-04-24T11:59:59+00:00",
            event_id="too-old",
            db_path=self.db_path,
        )
        events.record_event(
            "config_warning",
            severity="warning",
            observed_at="2026-04-24T12:00:00+00:00",
            event_id="in-window",
            domain="config",
            status="warning",
            db_path=self.db_path,
        )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["window"]["observed_after"], "2026-04-24T12:00:00+00:00")
        self.assertEqual(summary["events"]["total"], 1)
        self.assertEqual(summary["events"]["recent"][0]["event_id"], "in-window")

    def test_summary_groups_events_providers_and_sources(self) -> None:
        sources.upsert_source(
            source_id="brain",
            source_type="brain",
            display_name="Oracle Brain",
            status="active",
            db_path=self.db_path,
        )
        sources.upsert_source(
            source_id="satellite-alpha",
            source_type="satellite",
            display_name="Satellite Alpha",
            status="active",
            db_path=self.db_path,
        )
        events.record_event(
            "provider_available",
            severity="info",
            observed_at="2026-04-25T10:00:00+00:00",
            event_id="event-1",
            provider="ollama",
            domain="ollama",
            status="available",
            db_path=self.db_path,
        )
        events.record_event(
            "provider_unavailable",
            severity="error",
            observed_at="2026-04-25T10:01:00+00:00",
            event_id="event-2",
            provider="plex",
            domain="music",
            status="unavailable",
            db_path=self.db_path,
        )
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider_status:ollama:ollama",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "provider_status",
                    "ollama",
                    "ollama",
                    "available",
                    "{}",
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider_status:music:plex",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "provider_status",
                    "plex",
                    "music",
                    "unavailable",
                    "{}",
                ),
            )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["events"]["by_type"]["provider_available"], 1)
        self.assertEqual(summary["events"]["by_type"]["provider_unavailable"], 1)
        self.assertEqual(summary["events"]["by_category"], {"provider.status": 2})
        self.assertEqual(summary["events"]["by_severity"]["error"], 1)
        self.assertEqual(summary["events"]["by_domain"]["music"], 1)
        self.assertEqual(summary["events"]["by_status"]["unavailable"], 1)
        self.assertEqual(summary["providers"]["by_status"], {"available": 1, "unavailable": 1})
        self.assertEqual(summary["providers"]["by_domain"], {"music": 1, "ollama": 1})
        self.assertEqual(summary["providers"]["by_provider"], {"ollama": 1, "plex": 1})
        self.assertEqual(summary["sources"]["by_type"], {"brain": 1, "satellite": 2})
        self.assertEqual(summary["sources"]["by_status"], {"active": 3})

    def test_summary_applies_filters_and_limits(self) -> None:
        for index in range(3):
            events.record_event(
                "provider_available",
                severity="info",
                observed_at=f"2026-04-25T10:0{index}:00+00:00",
                event_id=f"event-{index}",
                domain="ollama",
                status="available",
                db_path=self.db_path,
            )
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider_status:ollama:ollama",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "provider_status",
                    "ollama",
                    "ollama",
                    "available",
                    "{}",
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider_status:music:plex",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "provider_status",
                    "plex",
                    "music",
                    "unavailable",
                    "{}",
                ),
            )
        sources.upsert_source(source_id="brain", source_type="brain", display_name="Brain", db_path=self.db_path)
        sources.upsert_source(
            source_id="satellite",
            source_type="satellite",
            display_name="Satellite",
            db_path=self.db_path,
        )

        summary = diagnostics.build_memory_diagnostics_summary(
            diagnostics.DiagnosticsSummaryQuery(
                event_type="provider_available",
                domain="ollama",
                status="available",
                provider="ollama",
                source_type="brain",
                event_limit=2,
                provider_limit=1,
                source_limit=1,
            ),
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["events"]["total"], 2)
        self.assertEqual(summary["events"]["limit"], 2)
        self.assertEqual([item["event_id"] for item in summary["events"]["recent"]], ["event-2", "event-1"])
        self.assertEqual(summary["providers"]["total"], 1)
        self.assertEqual(summary["providers"]["latest"][0]["provider"], "ollama")
        self.assertEqual(summary["sources"]["total"], 1)
        self.assertEqual(summary["sources"]["items"][0]["source_type"], "brain")

    def test_summary_computes_stale_provider_count_without_live_checks(self) -> None:
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            db_path=self.db_path,
        )
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memory_snapshots
                SET observed_at = ?, updated_at = ?
                WHERE snapshot_id = ?
                """,
                (
                    "2026-04-24T11:59:59+00:00",
                    "2026-04-24T11:59:59+00:00",
                    provider_status.provider_status_snapshot_id("ollama", "ollama"),
                ),
            )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["providers"]["stale_count"], 1)
        self.assertEqual(summary["providers"]["stale_provider_count_threshold_hours"], 24)

    def test_summary_passes_through_malformed_event_provider_and_source_payloads(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_events (
                    event_id, created_at, observed_at, event_type, category, severity, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-event",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "server_started",
                    "system.lifecycle",
                    "info",
                    "{bad-event",
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider_status:ollama:ollama",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "provider_status",
                    "ollama",
                    "ollama",
                    "available",
                    '["bad-provider"]',
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_sources (
                    source_id, created_at, updated_at, source_type, display_name, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-source",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "brain",
                    "Bad Source",
                    "active",
                    "{bad-source",
                ),
            )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            summary["events"]["recent"][0]["payload"],
            {"_payload_parse_error": True, "raw_payload_json": "{bad-event"},
        )
        self.assertEqual(
            summary["providers"]["latest"][0]["payload"],
            {"_payload_parse_error": True, "raw_payload_json": '["bad-provider"]'},
        )
        self.assertEqual(
            summary["sources"]["items"][0]["payload"],
            {"_payload_parse_error": True, "raw_payload_json": "{bad-source"},
        )

    def test_summary_includes_satellite_status_snapshots(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-alpha",
            event_type="wake_detected",
            status="available",
            correlation_id="corr-wake-1",
            observed_at="2026-04-25T11:55:00+00:00",
            payload={"wake_score": 0.92},
            db_path=self.db_path,
        )
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            event_type="tts_playback_failed",
            status="degraded",
            correlation_id="corr-tts-1",
            observed_at="2026-04-25T10:00:00+00:00",
            payload={"detail": "speaker offline"},
            db_path=self.db_path,
        )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["satellites"]["total"], 2)
        self.assertEqual(summary["satellites"]["limit"], 100)
        self.assertEqual(summary["satellites"]["by_status"], {"available": 1, "degraded": 1})
        self.assertEqual(summary["satellites"]["stale_count"], 1)
        self.assertEqual(summary["satellites"]["stale_threshold_minutes"], 15)
        self.assertEqual(
            [item["source_id"] for item in summary["satellites"]["latest"]],
            ["satellite-alpha", "satellite-beta"],
        )
        fresh = summary["satellites"]["latest"][0]
        stale = summary["satellites"]["latest"][1]
        self.assertFalse(fresh["is_stale"])
        self.assertEqual(fresh["payload"]["last_event_type"], "wake_detected")
        self.assertEqual(fresh["payload"]["last_seen_at"], "2026-04-25T11:55:00+00:00")
        self.assertEqual(fresh["payload"]["last_wake_at"], "2026-04-25T11:55:00+00:00")
        self.assertTrue(stale["is_stale"])
        self.assertEqual(stale["payload"]["last_error"], "speaker offline")

    def test_summary_filters_satellite_status_snapshots(self) -> None:
        satellite_activity.observe_satellite_activity(
            source_id="satellite-alpha",
            status="available",
            snapshot={"last_seen_at": "2026-04-25T11:55:00+00:00"},
            db_path=self.db_path,
        )
        satellite_activity.observe_satellite_activity(
            source_id="satellite-beta",
            status="degraded",
            snapshot={"last_error": "speaker offline"},
            db_path=self.db_path,
        )

        summary = diagnostics.build_memory_diagnostics_summary(
            diagnostics.DiagnosticsSummaryQuery(
                satellite_source_id="satellite-beta",
                satellite_status="degraded",
                satellite_limit=1,
            ),
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["satellites"]["total"], 1)
        self.assertEqual(summary["satellites"]["limit"], 1)
        self.assertEqual(summary["satellites"]["latest"][0]["source_id"], "satellite-beta")

    def test_summary_marks_malformed_satellite_observed_at_as_stale_unknown(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_sources (
                    source_id, created_at, updated_at, source_type, display_name, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "test_satellite_alpha",
                    "2026-04-25T12:00:00+00:00",
                    "2026-04-25T12:00:00+00:00",
                    "satellite",
                    "test_satellite_alpha",
                    "active",
                    "{}",
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_snapshots (
                    snapshot_id, created_at, updated_at, observed_at, snapshot_type,
                    source_id, provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "satellite_status:test_satellite_alpha",
                    "bad-date",
                    "bad-date",
                    "bad-date",
                    "satellite_status",
                    "test_satellite_alpha",
                    "test_satellite_alpha",
                    "satellite",
                    "available",
                    "{bad-satellite",
                ),
            )

        summary = diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        [satellite] = summary["satellites"]["latest"]
        self.assertFalse(satellite["is_stale"])
        self.assertTrue(satellite["stale_unknown"])
        self.assertEqual(
            satellite["payload"],
            {"_payload_parse_error": True, "raw_payload_json": "{bad-satellite"},
        )

    def test_summary_does_not_write_or_modify_rows(self) -> None:
        events.record_event(
            "server_started",
            observed_at="2026-04-25T10:00:00+00:00",
            event_id="event-1",
            db_path=self.db_path,
        )
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            db_path=self.db_path,
        )
        sources.upsert_source(source_id="brain", source_type="brain", display_name="Brain", db_path=self.db_path)
        satellite_activity.observe_satellite_activity(
            source_id="satellite-alpha",
            status="available",
            snapshot={"last_seen_at": "2026-04-25T10:00:00+00:00"},
            db_path=self.db_path,
        )
        with transaction(self.db_path) as conn:
            before = {
                "events": conn.execute("SELECT COUNT(*), MAX(observed_at) FROM memory_events").fetchone(),
                "snapshots": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_snapshots").fetchone(),
                "sources": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_sources").fetchone(),
            }

        diagnostics.build_memory_diagnostics_summary(
            db_path=self.db_path,
            now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
        )

        with transaction(self.db_path) as conn:
            after = {
                "events": conn.execute("SELECT COUNT(*), MAX(observed_at) FROM memory_events").fetchone(),
                "snapshots": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_snapshots").fetchone(),
                "sources": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_sources").fetchone(),
            }

        self.assertEqual({key: tuple(value) for key, value in after.items()}, {key: tuple(value) for key, value in before.items()})

    def test_diagnostics_module_does_not_import_live_or_execution_paths(self) -> None:
        forbidden_fragments = (
            "check_",
            "safe_observe_provider_health",
            "observe_provider_status",
            "record_event",
            "safe_record_event",
            "oracle_app.api",
            "oracle_app.health",
            "oracle_app.dispatch",
            "oracle_app.handlers",
            "oracle_app.provider_bridges",
            "oracle_app.command",
            "stt",
            "tts",
        )
        module = importlib.import_module("oracle_app.memory.diagnostics")
        source = inspect.getsource(module)

        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
