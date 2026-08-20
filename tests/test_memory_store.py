from __future__ import annotations

import importlib
import inspect
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.memory import events, identities, schema, sources
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration
from oracle_app.memory.store import transaction
from oracle_app.memory.taxonomy import category_for_event_type, validate_event_type


class OracleMemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"

    def test_schema_creation_creates_core_tables(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)

        self.assertTrue(self.db_path.exists())
        self.assertTrue(
            {
                "memory_schema_migrations",
                "memory_users",
                "memory_sources",
                "memory_events",
                "memory_current_projections",
                "memory_sessions",
                "memory_transcripts",
                "memory_orchestration_runs",
                "memory_orchestration_steps",
                "memory_notification_deliveries",
            }.issubset(schema.table_names(self.db_path))
        )

    def test_schema_migration_is_idempotent(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)

        conn = sqlite3.connect(self.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_schema_migrations WHERE version = ?",
                (schema.SCHEMA_VERSION,),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1)

    def test_runbook_kernel_metadata_migrates_existing_orchestration_table(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE memory_orchestration_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    orchestration_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    preview_id TEXT,
                    digest TEXT,
                    client_id TEXT,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    approval_consumed INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    CHECK (kind IN ('recovery', 'routine')),
                    CHECK (approval_consumed IN (0, 1))
                )
                """
            )
            conn.execute(
                """
                INSERT INTO memory_orchestration_runs (
                    run_id, created_at, updated_at, orchestration_id, kind,
                    status, started_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-run",
                    "2026-06-20T12:00:00+00:00",
                    "2026-06-20T12:00:00+00:00",
                    "evening_wind_down",
                    "routine",
                    "completed",
                    "2026-06-20T12:00:00+00:00",
                    '{"legacy":true}',
                ),
            )
            conn.commit()
        finally:
            conn.close()

        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)

        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(memory_orchestration_runs)"
                ).fetchall()
            }
            row = conn.execute(
                """
                SELECT definition_domain, definition_version, correlation_key,
                       activation_idempotency_key, controller_state_json,
                       payload_json
                FROM memory_orchestration_runs WHERE run_id = 'legacy-run'
                """
            ).fetchone()
        finally:
            conn.close()

        self.assertTrue(
            {
                "definition_domain",
                "definition_version",
                "correlation_key",
                "activation_idempotency_key",
                "controller_version",
                "controller_state_json",
                "cancellation_reason",
                "cancellation_requester",
            }.issubset(columns)
        )
        self.assertEqual(row, ("", "", None, None, "{}", '{"legacy":true}'))

    def test_insert_and_query_users(self) -> None:
        created = identities.upsert_user(
            user_id="system",
            display_name="System",
            payload={"owner": "memory"},
            db_path=self.db_path,
        )

        self.assertEqual(created["user_id"], "system")
        self.assertEqual(created["payload"], {"owner": "memory"})
        self.assertEqual(identities.get_user("system", db_path=self.db_path)["display_name"], "System")
        self.assertEqual(len(identities.list_users(db_path=self.db_path)), 1)

    def test_insert_and_query_sources(self) -> None:
        created = sources.upsert_source(
            source_id="test_satellite_bravo",
            source_type="satellite",
            display_name="Reading Room Display",
            payload={"room": "bedroom"},
            db_path=self.db_path,
        )

        self.assertEqual(created["source_id"], "test_satellite_bravo")
        self.assertEqual(created["source_type"], "satellite")
        self.assertEqual(created["payload"], {"room": "bedroom"})
        self.assertEqual(sources.get_source("test_satellite_bravo", db_path=self.db_path)["display_name"], "Reading Room Display")
        self.assertEqual(len(sources.list_sources(db_path=self.db_path)), 1)

    def test_default_internal_sources_returns_expected_sources(self) -> None:
        definitions = sources.default_internal_sources()

        self.assertEqual(
            {definition["source_id"] for definition in definitions},
            {"brain", "system", "api", "ui", "background"},
        )
        self.assertTrue(all(definition["payload"] == {"internal": True} for definition in definitions))

    def test_seed_default_sources_creates_internal_sources(self) -> None:
        seeded = sources.seed_default_sources(db_path=self.db_path)

        self.assertEqual(len(seeded), 5)
        self.assertEqual(sources.get_source("brain", db_path=self.db_path)["display_name"], "Oracle Brain")
        self.assertEqual(sources.get_source("background", db_path=self.db_path)["source_type"], "background")
        self.assertEqual(len(sources.list_sources(db_path=self.db_path)), 5)

    def test_seed_default_sources_is_idempotent(self) -> None:
        sources.seed_default_sources(db_path=self.db_path)
        first_brain = sources.get_source("brain", db_path=self.db_path)

        sources.seed_default_sources(db_path=self.db_path)
        second_brain = sources.get_source("brain", db_path=self.db_path)

        self.assertEqual(len(sources.list_sources(db_path=self.db_path)), 5)
        self.assertEqual(second_brain["created_at"], first_brain["created_at"])
        self.assertEqual(second_brain["display_name"], "Oracle Brain")

    def test_source_definitions_from_registry_converts_satellites(self) -> None:
        definitions = sources.source_definitions_from_registry(
            {
                "test_satellite_bravo": {
                    "source_type": "satellite",
                    "fixed": True,
                    "default_room": "Bedroom",
                    "display_name": "Reading Room Satellite",
                },
                "mobile-ui": {"source_type": "mobile", "fixed": False},
            }
        )

        self.assertEqual(
            definitions,
            [
                {
                    "source_id": "test_satellite_bravo",
                    "source_type": "satellite",
                    "display_name": "Reading Room Satellite",
                    "payload": {
                        "fixed": True,
                        "default_room": "bedroom",
                        "source_registry": True,
                    },
                }
            ],
        )

    def test_seed_default_sources_accepts_supplied_source_registry(self) -> None:
        sources.seed_default_sources(
            source_registry={
                "test_satellite_bravo": {
                    "source_type": "satellite",
                    "fixed": True,
                    "default_room": "bedroom",
                }
            },
            db_path=self.db_path,
        )

        satellite = sources.get_source("test_satellite_bravo", db_path=self.db_path)
        self.assertEqual(satellite["source_type"], "satellite")
        self.assertEqual(
            satellite["payload"],
            {"fixed": True, "default_room": "bedroom", "source_registry": True},
        )
        self.assertEqual(len(sources.list_sources(db_path=self.db_path)), 6)

    def test_seed_sources_rejects_invalid_source_type(self) -> None:
        with self.assertRaises(ValueError):
            sources.seed_sources(
                [
                    {
                        "source_id": "mobile-ui",
                        "source_type": "mobile",
                        "display_name": "Mobile UI",
                    }
                ],
                db_path=self.db_path,
            )

    def test_source_seeding_creates_no_memory_events(self) -> None:
        sources.seed_default_sources(db_path=self.db_path)

        self.assertEqual(events.query_events(db_path=self.db_path), [])

    def test_insert_and_query_events(self) -> None:
        identities.upsert_user(user_id="system", display_name="System", db_path=self.db_path)
        sources.upsert_source(source_id="brain", source_type="brain", display_name="Brain", db_path=self.db_path)

        created = events.record_event(
            "server_started",
            severity="info",
            source_id="brain",
            user_id="system",
            correlation_id="corr-1",
            provider="oracle",
            domain="system",
            status="ok",
            payload={"pid": 123},
            db_path=self.db_path,
        )

        self.assertEqual(created["event_type"], "server_started")
        self.assertEqual(created["category"], "system.lifecycle")
        self.assertEqual(created["correlation_id"], "corr-1")
        self.assertEqual(created["payload"], {"pid": 123})
        self.assertEqual(events.get_event(created["event_id"], db_path=self.db_path)["status"], "ok")
        self.assertEqual(len(events.list_events(db_path=self.db_path, event_type="server_started")), 1)

    def test_query_events_filters_and_orders_newest_first(self) -> None:
        sources.upsert_source(source_id="brain", source_type="brain", display_name="Brain", db_path=self.db_path)
        sources.upsert_source(source_id="ui", source_type="ui", display_name="UI", db_path=self.db_path)
        first = events.record_event(
            "server_started",
            severity="info",
            observed_at="2026-04-25T10:00:00+00:00",
            source_id="brain",
            correlation_id="corr-a",
            domain="system",
            status="starting",
            payload={"order": 1},
            event_id="event-1",
            db_path=self.db_path,
        )
        second = events.record_event(
            "config_warning",
            severity="warning",
            observed_at="2026-04-25T10:01:00+00:00",
            source_id="brain",
            correlation_id="corr-a",
            domain="config",
            status="unknown_env",
            payload={"order": 2},
            event_id="event-2",
            db_path=self.db_path,
        )
        third = events.record_event(
            "server_stopped",
            severity="info",
            observed_at="2026-04-25T10:02:00+00:00",
            source_id="ui",
            correlation_id="corr-b",
            domain="system",
            status="stopping",
            payload={"order": 3},
            event_id="event-3",
            db_path=self.db_path,
        )

        self.assertEqual([item["event_id"] for item in events.recent_events(db_path=self.db_path)], [third["event_id"], second["event_id"], first["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(event_type="config_warning"), db_path=self.db_path)], [second["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(source_id="ui"), db_path=self.db_path)], [third["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(correlation_id="corr-a"), db_path=self.db_path)], [second["event_id"], first["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(severity="WARNING"), db_path=self.db_path)], [second["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(status="unknown_env"), db_path=self.db_path)], [second["event_id"]])
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(domain="system"), db_path=self.db_path)], [third["event_id"], first["event_id"]])
        self.assertEqual(
            [item["event_id"] for item in events.query_events(events.EventQuery(observed_after="2026-04-25T10:01:00+00:00"), db_path=self.db_path)],
            [third["event_id"], second["event_id"]],
        )
        self.assertEqual(
            [item["event_id"] for item in events.query_events(events.EventQuery(observed_before="2026-04-25T10:01:00+00:00"), db_path=self.db_path)],
            [second["event_id"], first["event_id"]],
        )
        self.assertEqual(
            [item["event_id"] for item in events.query_events(events.EventQuery(source_id="brain", domain="config"), db_path=self.db_path)],
            [second["event_id"]],
        )

    def test_query_events_limits_offsets_and_list_events_compatibility(self) -> None:
        for index in range(3):
            events.record_event(
                "server_started",
                observed_at=f"2026-04-25T10:0{index}:00+00:00",
                event_id=f"event-{index}",
                db_path=self.db_path,
            )

        self.assertEqual(len(events.query_events(db_path=self.db_path)), 3)
        self.assertEqual(len(events.query_events(events.EventQuery(limit=0), db_path=self.db_path)), 1)
        self.assertEqual(len(events.query_events(events.EventQuery(limit=999), db_path=self.db_path)), 3)
        self.assertEqual([item["event_id"] for item in events.query_events(events.EventQuery(limit=1, offset=1), db_path=self.db_path)], ["event-1"])
        self.assertEqual(len(events.recent_events(limit=2, db_path=self.db_path)), 2)
        self.assertEqual(len(events.list_events(event_type="server_started", db_path=self.db_path)), 3)

    def test_query_events_handles_malformed_payload_json(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_events (
                    event_id, created_at, observed_at, event_type, category, severity, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-payload",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "server_started",
                    "system.lifecycle",
                    "info",
                    "{not-json",
                ),
            )

        [event] = events.query_events(events.EventQuery(event_type="server_started"), db_path=self.db_path)
        self.assertEqual(event["payload"], {"_payload_parse_error": True, "raw_payload_json": "{not-json"})

    def test_query_events_do_not_write_rows(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        before = len(events.query_events(db_path=self.db_path))

        events.query_events(events.EventQuery(event_type="server_started"), db_path=self.db_path)
        events.recent_events(db_path=self.db_path)
        events.list_events(db_path=self.db_path)

        after = len(events.query_events(db_path=self.db_path))
        self.assertEqual(after, before)

    def test_transaction_rolls_back_on_error(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)

        with self.assertRaises(RuntimeError):
            with transaction(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO memory_users (
                        user_id, created_at, updated_at, display_name, status, payload_json
                    ) VALUES ('rolled-back', 'now', 'now', 'Rolled Back', 'active', '{}')
                    """
                )
                raise RuntimeError("force rollback")

        self.assertIsNone(identities.get_user("rolled-back", db_path=self.db_path))

    def test_event_taxonomy_validation(self) -> None:
        self.assertEqual(validate_event_type("command_failed"), "command_failed")
        self.assertEqual(category_for_event_type("command_failed"), "command")
        self.assertEqual(category_for_event_type("orchestration_recovery_started"), "orchestration")
        self.assertEqual(category_for_event_type("orchestration_routine_started"), "orchestration")
        with self.assertRaises(ValueError):
            validate_event_type("invented_event")

    def test_payload_json_discipline_keeps_core_event_fields_as_columns(self) -> None:
        schema.ensure_schema(self.db_path, copy_provisional_suggestions=False)
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(memory_events)").fetchall()
            }
        finally:
            conn.close()

        self.assertTrue(events.CORE_EVENT_COLUMNS.issubset(columns))
        self.assertIn("payload_json", columns)

    def test_retention_scaffolding_exposes_defaults_without_deleting(self) -> None:
        policy = retention_policy_from_configuration(MemoryRetentionConfiguration()).__dict__

        self.assertEqual(policy["successful_raw_transcript_days"], 14)
        self.assertEqual(policy["failed_raw_transcript_days"], 30)
        self.assertEqual(policy["transcript_metadata_days"], 90)
        self.assertEqual(policy["orchestration_history_days"], 365)

    def test_no_execution_boundary_imports(self) -> None:
        forbidden_fragments = (
            "oracle_app.handlers",
            "import subprocess",
            "subprocess.",
            "systemctl",
            "/command",
            "oracle_app.api",
            "oracle_app.dispatch",
            "oracle_app.config",
            "get_source_registry",
        )
        module_names = (
            "oracle_app.memory.store",
            "oracle_app.memory.schema",
            "oracle_app.memory.events",
            "oracle_app.memory.identities",
            "oracle_app.memory.sources",
            "oracle_app.memory.provider_status",
            "oracle_app.memory.retention",
            "oracle_app.memory.taxonomy",
            "oracle_app.memory.runtime",
        )

        for module_name in module_names:
            module = importlib.import_module(module_name)
            source = inspect.getsource(module)
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, source, f"{fragment} leaked into {module_name}")

    def test_can_copy_existing_provisional_suggestion_rows(self) -> None:
        provisional = Path(self.tmpdir.name) / "openclaw_suggestions.sqlite3"
        conn = sqlite3.connect(provisional)
        try:
            conn.executescript(
                """
                CREATE TABLE suggestion_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    run_type TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    reason TEXT,
                    custom_prompt TEXT,
                    openclaw_status TEXT,
                    collector_status_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    packet_path TEXT,
                    response_path TEXT,
                    suggestion_count INTEGER NOT NULL DEFAULT 0,
                    mock INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE suggestions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    suggested_action TEXT NOT NULL,
                    recommended_oracle_action TEXT,
                    confidence REAL NOT NULL,
                    requires_review INTEGER NOT NULL,
                    similarity_key TEXT NOT NULL,
                    similar_to_id TEXT,
                    raw_openclaw_item_json TEXT NOT NULL,
                    reviewed_at TEXT,
                    review_decision TEXT,
                    review_notes TEXT,
                    correction_text TEXT,
                    rejection_reason TEXT,
                    future_automation_candidate INTEGER NOT NULL DEFAULT 0,
                    suppress_if_repeated INTEGER NOT NULL DEFAULT 0,
                    mock INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE suggestion_reviews (
                    review_id TEXT PRIMARY KEY,
                    suggestion_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    notes TEXT,
                    correction_text TEXT,
                    rejection_reason TEXT,
                    future_automation_candidate INTEGER NOT NULL DEFAULT 0,
                    suppress_if_repeated INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO suggestion_runs (
                    run_id, created_at, status, run_type, window_start, window_end
                ) VALUES ('run-1', 'now', 'completed', 'oracle', 'start', 'end');
                INSERT INTO suggestions (
                    id, run_id, created_at, status, title, severity, category, source,
                    summary, evidence_json, suggested_action, confidence, requires_review,
                    similarity_key, raw_openclaw_item_json
                ) VALUES (
                    'sug-1', 'run-1', 'now', 'new', 'Test', 'low', 'oracle', 'oracle',
                    'summary', '[]', 'review', 0.5, 1, 'test', '{}'
                );
                INSERT INTO suggestion_reviews (
                    review_id, suggestion_id, run_id, reviewed_at, status
                ) VALUES ('rev-1', 'sug-1', 'run-1', 'now', 'ignored');
                """
            )
        finally:
            conn.close()

        schema.ensure_schema(self.db_path, provisional_db_path=provisional)

        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM suggestion_runs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM suggestions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM suggestion_reviews").fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
