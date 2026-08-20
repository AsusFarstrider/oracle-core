from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from oracle_app.memory.identity_reconciliation import reconcile_identities
from oracle_app.memory.retention import RetentionPolicy
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration
from oracle_app.memory.retention_executor import run_retention
from oracle_app.memory.schema import SCHEMA_VERSION, ensure_schema, table_names
from oracle_app.memory.store import transaction
from oracle_app.suggestions.storage import ensure_storage


UTC = timezone.utc
NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
POLICY = retention_policy_from_configuration(MemoryRetentionConfiguration())


class Stage5Slice6MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db_path = Path(self.temporary.name) / "memory.sqlite3"

    def test_current_shape_migration_is_one_way_and_preserves_25_projections(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE memory_users (
                    user_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    role TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE memory_sources (
                    source_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    source_type TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE memory_snapshots (
                    snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL, snapshot_type TEXT NOT NULL, source_id TEXT,
                    provider TEXT, domain TEXT, status TEXT NOT NULL, correlation_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE memory_sessions (
                    session_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    correlation_id TEXT, source_id TEXT, user_id TEXT, mode TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, final_status TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    CHECK (mode IN ('voice','ui','api','system','background'))
                );
                CREATE TABLE memory_transcripts (
                    transcript_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    session_id TEXT, correlation_id TEXT, source_id TEXT, user_id TEXT,
                    captured_at TEXT NOT NULL, raw_transcript TEXT, normalized_text TEXT,
                    stt_provider TEXT, stt_model TEXT, confidence REAL, route_result_json TEXT,
                    fallback_used INTEGER NOT NULL DEFAULT 0, fallback_reason TEXT,
                    final_domain TEXT, final_intent TEXT, final_status TEXT NOT NULL,
                    failure_stage TEXT, raw_transcript_retention_until TEXT,
                    metadata_retention_until TEXT, raw_transcript_pruned_at TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                INSERT INTO memory_users VALUES ('resident','now','now','adult','Resident','active','{}');
                INSERT INTO memory_sources VALUES ('sat','now','now','satellite','Sat','active','{}');
                INSERT INTO memory_sessions VALUES (
                    'session','now','now',NULL,'sat','resident','voice','now',NULL,NULL,'{}'
                );
                INSERT INTO memory_transcripts (
                    transcript_id,created_at,updated_at,session_id,source_id,user_id,captured_at,
                    final_status,payload_json
                ) VALUES ('transcript','now','now','session','sat','resident','now','ok','{}');
                """
            )
            for index in range(25):
                conn.execute(
                    "INSERT INTO memory_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"projection-{index}", "now", "now", "now", "provider_status", None,
                     "provider", "domain", "available", None, "{}"),
                )
            conn.commit()
        finally:
            conn.close()

        ensure_schema(self.db_path, copy_provisional_suggestions=False)

        self.assertNotIn("memory_snapshots", table_names(self.db_path))
        self.assertIn("memory_current_projections", table_names(self.db_path))
        with transaction(self.db_path) as migrated:
            self.assertEqual(migrated.execute(
                "SELECT COUNT(*) FROM memory_current_projections"
            ).fetchone()[0], 25)
            self.assertEqual(migrated.execute(
                "SELECT mode FROM memory_sessions WHERE session_id='session'"
            ).fetchone()[0], "conversation")
            self.assertNotIn("role", {
                row[1] for row in migrated.execute("PRAGMA table_info(memory_users)")
            })
            self.assertEqual(migrated.execute(
                "SELECT COUNT(*) FROM memory_schema_migrations WHERE version=?", (SCHEMA_VERSION,)
            ).fetchone()[0], 1)

    def test_identity_reconciliation_seeds_users_rewrites_known_alias_and_retires_unknown(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memory_sources VALUES (?,?,?,?,?,?,?)",
                ("surface-999-satellite", NOW.isoformat(), NOW.isoformat(), "satellite", "Old", "active", "{}"),
            )
            conn.execute(
                "INSERT INTO memory_sources VALUES (?,?,?,?,?,?,?)",
                ("unknown-source", NOW.isoformat(), NOW.isoformat(), "satellite", "Unknown", "active", "{}"),
            )
            conn.execute(
                """INSERT INTO memory_current_projections VALUES
                   (?,?,?,?,?,?,?,?,?,?,?)""",
                ("satellite_status:surface-999-satellite", NOW.isoformat(), NOW.isoformat(),
                 NOW.isoformat(), "satellite_status", "surface-999-satellite",
                 "surface-999-satellite", "satellite", "available", None, "{}"),
            )
        household = SimpleNamespace(
            users={
                "resident": SimpleNamespace(
                    enabled=True, display_name="Resident", aliases=["R"],
                )
            },
            sources={
                "child_room_satellite": SimpleNamespace(
                    enabled=True, type="satellite", fixed=True,
                    associated_room_id="child_room", associated_user_id="resident",
                )
            },
        )
        satellites = SimpleNamespace(
            satellites={
                "surface_satellite_999": SimpleNamespace(source_id="child_room_satellite")
            }
        )

        report = reconcile_identities(household, satellites, db_path=self.db_path)

        self.assertIn(("surface-999-satellite", "child_room_satellite"), report.aliases_rewritten)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT status FROM memory_users WHERE user_id='resident'"
            ).fetchone()[0], "active")
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM memory_sources WHERE source_id='surface-999-satellite'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT status FROM memory_sources WHERE source_id='unknown-source'"
            ).fetchone()[0], "retired")
            projection = conn.execute(
                "SELECT projection_id,source_id FROM memory_current_projections"
            ).fetchone()
            self.assertEqual(tuple(projection), (
                "satellite_status:child_room_satellite", "child_room_satellite"
            ))

    def test_schema_finishes_partial_projection_rehearsal_by_merging_latest_row(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_current_projections VALUES
                   ('shared','old','old','2026-08-01T00:00:00+00:00','provider_status',
                    NULL,'old-provider','domain','degraded',NULL,'{}')"""
            )
            conn.execute(
                """CREATE TABLE memory_snapshots (
                   snapshot_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL, observed_at TEXT NOT NULL,
                   snapshot_type TEXT NOT NULL, source_id TEXT, provider TEXT,
                   domain TEXT, status TEXT NOT NULL, correlation_id TEXT,
                   payload_json TEXT NOT NULL)"""
            )
            conn.execute(
                """INSERT INTO memory_snapshots VALUES
                   ('shared','new','new','2026-08-02T00:00:00+00:00','provider_status',
                    NULL,'new-provider','domain','available',NULL,'{}')"""
            )

        ensure_schema(self.db_path, copy_provisional_suggestions=False)

        self.assertNotIn("memory_snapshots", table_names(self.db_path))
        with transaction(self.db_path) as conn:
            row = conn.execute(
                "SELECT provider,status FROM memory_current_projections WHERE projection_id='shared'"
            ).fetchone()
        self.assertEqual(tuple(row), ("new-provider", "available"))

    def test_retention_dry_run_reports_exact_boundaries_dependencies_and_unknowns(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        ensure_storage(self.db_path)
        old = (NOW - timedelta(days=91)).isoformat()
        edge = (NOW - timedelta(days=90)).isoformat()
        future = (NOW + timedelta(seconds=1)).isoformat()
        with transaction(self.db_path) as conn:
            for event_id, observed_at, severity, category in (
                ("old-info", old, "info", "routing"),
                ("edge-info", edge, "info", "routing"),
                ("future-info", future, "info", "routing"),
                ("unknown-severity", old, "notice", "routing"),
            ):
                conn.execute(
                    """INSERT INTO memory_events (
                       event_id,created_at,observed_at,event_type,category,severity,payload_json
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (event_id, observed_at, observed_at, "routing_failed", category, severity, "{}"),
                )
            conn.execute(
                """INSERT INTO memory_sessions (
                   session_id,created_at,updated_at,mode,started_at,ended_at,final_status,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                ("old-session", old, old, "conversation", old, old, "executed", "{}"),
            )
            conn.execute(
                """INSERT INTO memory_transcripts (
                   transcript_id,created_at,updated_at,session_id,captured_at,raw_transcript,
                   final_status,raw_transcript_retention_until,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                ("old-transcript", old, old, "old-session", old, "words", "executed", old, "{}"),
            )
            conn.execute(
                """INSERT INTO memory_orchestration_runs (
                   run_id,created_at,updated_at,orchestration_id,kind,status,started_at,completed_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                ("waiting-run", old, old, "routine", "routine", "waiting", old, None),
            )
            conn.execute(
                """INSERT INTO memory_notification_deliveries (
                   receipt_id,created_at,updated_at,notification_type,occurrence_id,channel,
                   destination_id,status,max_attempts,retry_seconds,expires_at,failure_policy,repeat_policy
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("overdue", old, old, "notice", "occ", "external", "resident", "pending",
                 1, 30, old, "best_effort", "every_occurrence"),
            )
        report = run_retention(
            POLICY, db_path=self.db_path, now=NOW, dry_run=True,
            active_session_ids=(),
        )
        classes = {item.class_name: item for item in report.classes}

        self.assertEqual(classes["events"].candidate_ids, ("edge-info", "old-info"))
        self.assertEqual(classes["events"].blocked_ids, ("future-info", "unknown-severity"))
        self.assertEqual(classes["transcript_metadata"].candidate_ids, ("old-transcript",))
        self.assertEqual(classes["sessions_and_transcripts"].candidate_ids, ("old-session",))
        self.assertEqual(classes["orchestration_history"].protected_ids, ("waiting-run",))
        self.assertEqual(classes["notification_receipts"].transition_ids, ("overdue",))
        self.assertTrue(report.blocked)

    def test_retention_apply_is_atomic_and_emits_no_event_when_nothing_changes(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        ensure_storage(self.db_path)
        report = run_retention(
            POLICY, db_path=self.db_path, now=NOW, dry_run=False,
        )
        self.assertEqual(report.changed_count, 0)
        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE event_type='retention_pruned'"
            ).fetchone()[0], 0)

    def test_retention_apply_deletes_candidate_and_emits_one_aggregate_event(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        old = (NOW - timedelta(days=91)).isoformat()
        with transaction(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_events (
                   event_id,created_at,observed_at,event_type,category,severity,payload_json
                   ) VALUES ('old',?,?,?,?,?,?)""",
                (old, old, "routing_failed", "routing", "info", "{}"),
            )

        report = run_retention(POLICY, db_path=self.db_path, now=NOW, dry_run=False)

        self.assertEqual(report.changed_count, 1)
        with transaction(self.db_path) as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM memory_events WHERE event_id='old'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE event_type='retention_pruned'"
            ).fetchone()[0], 1)

    def test_retention_apply_fails_closed_without_partial_deletion(self) -> None:
        ensure_schema(self.db_path, copy_provisional_suggestions=False)
        old = (NOW - timedelta(days=91)).isoformat()
        future = (NOW + timedelta(seconds=1)).isoformat()
        with transaction(self.db_path) as conn:
            for event_id, observed_at in (("old", old), ("future", future)):
                conn.execute(
                    """INSERT INTO memory_events (
                       event_id,created_at,observed_at,event_type,category,severity,payload_json
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (event_id, observed_at, observed_at, "routing_failed", "routing", "info", "{}"),
                )

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            run_retention(POLICY, db_path=self.db_path, now=NOW, dry_run=False)

        with transaction(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM memory_events"
            ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
