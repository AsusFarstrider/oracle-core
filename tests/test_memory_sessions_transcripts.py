from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.memory import schema, sessions, sources, transcripts
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration
from oracle_app.memory.store import transaction


POLICY = retention_policy_from_configuration(MemoryRetentionConfiguration())
record_transcript = lambda **kwargs: transcripts.record_transcript(retention_policy=POLICY, **kwargs)


class OracleMemorySessionTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"

    def test_schema_creates_session_and_transcript_tables_and_indexes(self) -> None:
        schema.ensure_schema(self.db_path)

        self.assertTrue({"memory_sessions", "memory_transcripts"}.issubset(schema.table_names(self.db_path)))
        conn = sqlite3.connect(self.db_path)
        try:
            indexes = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
            }
        finally:
            conn.close()

        self.assertTrue(
            {
                "idx_memory_sessions_correlation",
                "idx_memory_sessions_source",
                "idx_memory_sessions_started_at",
                "idx_memory_transcripts_session",
                "idx_memory_transcripts_correlation",
                "idx_memory_transcripts_source",
                "idx_memory_transcripts_captured_at",
                "idx_memory_transcripts_fallback_used",
                "idx_memory_transcripts_final_domain",
                "idx_memory_transcripts_failure_stage",
                "idx_memory_transcripts_final_status",
            }.issubset(indexes)
        )

    def test_schema_migration_is_idempotent(self) -> None:
        schema.ensure_schema(self.db_path)
        schema.ensure_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_schema_migrations WHERE version = ?",
                (schema.SCHEMA_VERSION,),
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(count, 1)

    def test_insert_and_query_sessions(self) -> None:
        sources.upsert_source(
            source_id="test_satellite_bravo",
            source_type="satellite",
            display_name="Reading Room Display",
            db_path=self.db_path,
        )
        first = sessions.record_session(
            session_id="session-1",
            mode="conversation",
            started_at="2026-04-25T10:00:00+00:00",
            correlation_id="corr-1",
            source_id="test_satellite_bravo",
            final_status="started",
            payload={"entry": "wake"},
            db_path=self.db_path,
        )
        second = sessions.record_session(
            session_id="session-2",
            mode="api",
            started_at="2026-04-25T10:01:00+00:00",
            correlation_id="corr-2",
            final_status="succeeded",
            db_path=self.db_path,
        )

        self.assertEqual(first["payload"], {"entry": "wake"})
        self.assertEqual(sessions.get_session("session-1", db_path=self.db_path)["mode"], "conversation")
        self.assertEqual(
            [item["session_id"] for item in sessions.query_sessions(db_path=self.db_path)],
            [second["session_id"], first["session_id"]],
        )
        self.assertEqual(
            [item["session_id"] for item in sessions.query_sessions(sessions.SessionQuery(correlation_id="corr-1"), db_path=self.db_path)],
            ["session-1"],
        )
        self.assertEqual(
            [item["session_id"] for item in sessions.query_sessions(sessions.SessionQuery(source_id="test_satellite_bravo"), db_path=self.db_path)],
            ["session-1"],
        )
        self.assertEqual(
            [item["session_id"] for item in sessions.query_sessions(sessions.SessionQuery(mode="api"), db_path=self.db_path)],
            ["session-2"],
        )

    def test_update_session_status_merges_payload(self) -> None:
        sessions.record_session(
            session_id="session-1",
            mode="conversation",
            started_at="2026-04-25T10:00:00+00:00",
            payload={"entry": "wake"},
            db_path=self.db_path,
        )

        updated = sessions.update_session_status(
            "session-1",
            ended_at="2026-04-25T10:02:00+00:00",
            final_status="succeeded",
            payload={"turns": 1},
            db_path=self.db_path,
        )

        self.assertEqual(updated["ended_at"], "2026-04-25T10:02:00+00:00")
        self.assertEqual(updated["final_status"], "succeeded")
        self.assertEqual(updated["payload"], {"entry": "wake", "turns": 1})

    def test_insert_and_query_transcripts_omit_raw_text_by_default(self) -> None:
        sources.upsert_source(source_id="satellite", source_type="satellite", display_name="Voice", db_path=self.db_path)
        sessions.record_session(
            session_id="session-1",
            mode="conversation",
            started_at="2026-04-25T10:00:00+00:00",
            source_id="satellite",
            db_path=self.db_path,
        )
        created = record_transcript(
            transcript_id="transcript-1",
            session_id="session-1",
            correlation_id="corr-1",
            source_id="satellite",
            captured_at="2026-04-25T10:01:00+00:00",
            raw_transcript="turn on the kitchen lights",
            normalized_text="turn on the kitchen lights",
            stt_provider="fast-whisper",
            stt_model="small.en",
            confidence=0.91,
            route_result={"target": "home_assistant"},
            fallback_used=False,
            final_domain="home_assistant",
            final_intent="turn_on",
            final_status="succeeded",
            payload={"duration_ms": 1200},
            db_path=self.db_path,
        )

        self.assertNotIn("raw_transcript", created)
        self.assertEqual(created["route_result"], {"target": "home_assistant"})
        self.assertEqual(created["payload"], {"duration_ms": 1200})
        self.assertNotIn("raw_transcript", transcripts.get_transcript("transcript-1", db_path=self.db_path))
        self.assertNotIn("raw_transcript", transcripts.query_transcripts(db_path=self.db_path)[0])
        self.assertNotIn("raw_transcript", transcripts.recent_transcripts(db_path=self.db_path)[0])

    def test_raw_transcript_can_be_explicitly_included(self) -> None:
        record_transcript(
            transcript_id="transcript-1",
            captured_at="2026-04-25T10:01:00+00:00",
            raw_transcript="raw household speech",
            final_status="succeeded",
            db_path=self.db_path,
        )

        self.assertEqual(
            transcripts.get_transcript("transcript-1", include_raw_transcript=True, db_path=self.db_path)["raw_transcript"],
            "raw household speech",
        )
        self.assertEqual(
            transcripts.query_transcripts(
                transcripts.TranscriptQuery(include_raw_transcript=True),
                db_path=self.db_path,
            )[0]["raw_transcript"],
            "raw household speech",
        )
        self.assertEqual(
            transcripts.recent_transcripts(include_raw_transcript=True, db_path=self.db_path)[0]["raw_transcript"],
            "raw household speech",
        )

    def test_query_transcripts_filters_and_orders_newest_first(self) -> None:
        sources.upsert_source(source_id="satellite", source_type="satellite", display_name="Voice", db_path=self.db_path)
        sessions.record_session(session_id="session-1", mode="conversation", started_at="2026-04-25T10:00:00+00:00", db_path=self.db_path)
        sessions.record_session(session_id="session-2", mode="conversation", started_at="2026-04-25T10:00:00+00:00", db_path=self.db_path)
        record_transcript(
            transcript_id="transcript-1",
            session_id="session-1",
            correlation_id="corr-1",
            source_id="satellite",
            captured_at="2026-04-25T10:00:00+00:00",
            fallback_used=False,
            final_domain="home_assistant",
            final_status="succeeded",
            db_path=self.db_path,
        )
        record_transcript(
            transcript_id="transcript-2",
            session_id="session-2",
            correlation_id="corr-2",
            captured_at="2026-04-25T10:02:00+00:00",
            fallback_used=True,
            fallback_reason="deterministic_parser_miss",
            final_domain="music",
            final_status="failed",
            failure_stage="routing",
            db_path=self.db_path,
        )
        record_transcript(
            transcript_id="transcript-3",
            session_id="session-1",
            correlation_id="corr-1",
            captured_at="2026-04-25T10:01:00+00:00",
            fallback_used=True,
            final_domain="calendar",
            final_intent="read",
            final_status="succeeded",
            db_path=self.db_path,
        )

        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(db_path=self.db_path)],
            ["transcript-2", "transcript-3", "transcript-1"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(session_id="session-1"), db_path=self.db_path)],
            ["transcript-3", "transcript-1"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(correlation_id="corr-2"), db_path=self.db_path)],
            ["transcript-2"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(source_id="satellite"), db_path=self.db_path)],
            ["transcript-1"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(fallback_used=True), db_path=self.db_path)],
            ["transcript-2", "transcript-3"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(final_domain="music"), db_path=self.db_path)],
            ["transcript-2"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(failure_stage="routing"), db_path=self.db_path)],
            ["transcript-2"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(final_status="succeeded"), db_path=self.db_path)],
            ["transcript-3", "transcript-1"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(captured_after="2026-04-25T10:01:00+00:00"), db_path=self.db_path)],
            ["transcript-2", "transcript-3"],
        )
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(captured_before="2026-04-25T10:01:00+00:00"), db_path=self.db_path)],
            ["transcript-3", "transcript-1"],
        )

    def test_query_transcripts_limits_and_offsets(self) -> None:
        for index in range(3):
            record_transcript(
                transcript_id=f"transcript-{index}",
                captured_at=f"2026-04-25T10:0{index}:00+00:00",
                final_status="succeeded",
                db_path=self.db_path,
            )

        self.assertEqual(len(transcripts.query_transcripts(db_path=self.db_path)), 3)
        self.assertEqual(len(transcripts.query_transcripts(transcripts.TranscriptQuery(limit=0), db_path=self.db_path)), 1)
        self.assertEqual(len(transcripts.query_transcripts(transcripts.TranscriptQuery(limit=999), db_path=self.db_path)), 3)
        self.assertEqual(
            [item["transcript_id"] for item in transcripts.query_transcripts(transcripts.TranscriptQuery(limit=1, offset=1), db_path=self.db_path)],
            ["transcript-1"],
        )

    def test_transcript_retention_fields_are_schema_ready(self) -> None:
        successful = record_transcript(
            transcript_id="success",
            captured_at="2026-04-25T10:00:00+00:00",
            confidence=0.9,
            final_status="succeeded",
            db_path=self.db_path,
        )
        failed = record_transcript(
            transcript_id="failed",
            captured_at="2026-04-25T10:00:00+00:00",
            confidence=0.1,
            final_status="failed",
            failure_stage="stt",
            db_path=self.db_path,
        )

        self.assertEqual(successful["raw_transcript_retention_until"], "2026-05-09T10:00:00+00:00")
        self.assertEqual(failed["raw_transcript_retention_until"], "2026-05-25T10:00:00+00:00")
        self.assertEqual(successful["metadata_retention_until"], "2026-07-24T10:00:00+00:00")
        self.assertIsNone(successful["raw_transcript_pruned_at"])

    def test_nullable_raw_transcript_preserves_metadata(self) -> None:
        created = record_transcript(
            transcript_id="metadata-only",
            captured_at="2026-04-25T10:00:00+00:00",
            raw_transcript=None,
            normalized_text="turn on the light",
            final_domain="home_assistant",
            final_status="succeeded",
            db_path=self.db_path,
        )

        self.assertEqual(created["normalized_text"], "turn on the light")
        self.assertEqual(created["final_domain"], "home_assistant")
        self.assertIsNone(
            transcripts.get_transcript("metadata-only", include_raw_transcript=True, db_path=self.db_path)["raw_transcript"]
        )

    def test_malformed_json_is_returned_as_diagnostic_marker(self) -> None:
        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_transcripts (
                    transcript_id, created_at, updated_at, captured_at, route_result_json,
                    fallback_used, final_status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bad-json",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "2026-04-25T10:00:00+00:00",
                    "{bad-route",
                    0,
                    "failed",
                    '["bad-payload"]',
                ),
            )

        [row] = transcripts.query_transcripts(db_path=self.db_path)
        self.assertEqual(row["route_result"], {"_route_result_parse_error": True, "raw_route_result_json": "{bad-route"})
        self.assertEqual(row["payload"], {"_payload_parse_error": True, "raw_payload_json": '["bad-payload"]'})

    def test_transaction_rolls_back_session_and_transcript_rows(self) -> None:
        schema.ensure_schema(self.db_path)

        with self.assertRaises(RuntimeError):
            with transaction(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO memory_sessions (
                        session_id, created_at, updated_at, mode, started_at, payload_json
                    ) VALUES ('rolled-back-session', 'now', 'now', 'conversation', 'now', '{}')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO memory_transcripts (
                        transcript_id, created_at, updated_at, captured_at, fallback_used, final_status, payload_json
                    ) VALUES ('rolled-back-transcript', 'now', 'now', 'now', 0, 'failed', '{}')
                    """
                )
                raise RuntimeError("force rollback")

        self.assertIsNone(sessions.get_session("rolled-back-session", db_path=self.db_path))
        self.assertIsNone(transcripts.get_transcript("rolled-back-transcript", db_path=self.db_path))

    def test_no_raw_audio_storage_field_exists(self) -> None:
        schema.ensure_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(memory_transcripts)").fetchall()
            }
        finally:
            conn.close()

        self.assertFalse({"raw_audio", "raw_audio_path", "audio_blob", "audio_bytes"}.intersection(columns))

    def test_query_helpers_do_not_write_or_modify_rows(self) -> None:
        sessions.record_session(session_id="session-1", mode="conversation", started_at="2026-04-25T10:00:00+00:00", db_path=self.db_path)
        record_transcript(transcript_id="transcript-1", session_id="session-1", final_status="succeeded", db_path=self.db_path)
        with transaction(self.db_path) as conn:
            before = {
                "sessions": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_sessions").fetchone(),
                "transcripts": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_transcripts").fetchone(),
            }

        sessions.query_sessions(db_path=self.db_path)
        sessions.recent_sessions(db_path=self.db_path)
        sessions.get_session("session-1", db_path=self.db_path)
        transcripts.query_transcripts(db_path=self.db_path)
        transcripts.recent_transcripts(db_path=self.db_path)
        transcripts.get_transcript("transcript-1", db_path=self.db_path)

        with transaction(self.db_path) as conn:
            after = {
                "sessions": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_sessions").fetchone(),
                "transcripts": conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_transcripts").fetchone(),
            }

        self.assertEqual({key: tuple(value) for key, value in after.items()}, {key: tuple(value) for key, value in before.items()})


if __name__ == "__main__":
    unittest.main()
