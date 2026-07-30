from __future__ import annotations

import sqlite3
from pathlib import Path

from .store import DB_PATH, PROVISIONAL_SUGGESTIONS_DB_PATH, transaction


SCHEMA_VERSION = "0007_notification_delivery_repeat_policy"
SCHEMA_VERSIONS = (
    "0001_core",
    "0002_sessions_transcripts",
    "0003_orchestration_runs",
    "0004_runbook_kernel_metadata",
    "0005_notification_delivery_receipts",
    "0006_notification_delivery_retry_policy",
    SCHEMA_VERSION,
)


CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory_users (
    user_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_sources (
    source_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    source_id TEXT,
    session_id TEXT,
    correlation_id TEXT,
    user_id TEXT,
    provider TEXT,
    domain TEXT,
    status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id),
    FOREIGN KEY(user_id) REFERENCES memory_users(user_id)
);

CREATE TABLE IF NOT EXISTS memory_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    source_id TEXT,
    provider TEXT,
    domain TEXT,
    status TEXT NOT NULL,
    correlation_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id)
);

CREATE TABLE IF NOT EXISTS memory_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    correlation_id TEXT,
    source_id TEXT,
    user_id TEXT,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    final_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (mode IN ('voice', 'ui', 'api', 'system', 'background')),
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id),
    FOREIGN KEY(user_id) REFERENCES memory_users(user_id)
);

CREATE TABLE IF NOT EXISTS memory_transcripts (
    transcript_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT,
    correlation_id TEXT,
    source_id TEXT,
    user_id TEXT,
    captured_at TEXT NOT NULL,
    raw_transcript TEXT,
    normalized_text TEXT,
    stt_provider TEXT,
    stt_model TEXT,
    confidence REAL,
    route_result_json TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    final_domain TEXT,
    final_intent TEXT,
    final_status TEXT NOT NULL,
    failure_stage TEXT,
    raw_transcript_retention_until TEXT,
    metadata_retention_until TEXT,
    raw_transcript_pruned_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (fallback_used IN (0, 1)),
    FOREIGN KEY(session_id) REFERENCES memory_sessions(session_id),
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id),
    FOREIGN KEY(user_id) REFERENCES memory_users(user_id)
);

CREATE TABLE IF NOT EXISTS memory_orchestration_runs (
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
    definition_domain TEXT NOT NULL DEFAULT '',
    definition_version TEXT NOT NULL DEFAULT '',
    correlation_key TEXT,
    activation_idempotency_key TEXT,
    controller_version TEXT NOT NULL DEFAULT '',
    controller_state_json TEXT NOT NULL DEFAULT '{}',
    cancellation_reason TEXT NOT NULL DEFAULT '',
    cancellation_requester TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    CHECK (kind IN ('recovery', 'routine')),
    CHECK (approval_consumed IN (0, 1))
);

CREATE TABLE IF NOT EXISTS memory_orchestration_steps (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    target_type TEXT,
    target_id TEXT,
    target_label TEXT,
    action_id TEXT,
    policy_id TEXT,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    request_id TEXT,
    error_class TEXT,
    verification_status TEXT,
    started_at TEXT,
    completed_at TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(run_id, step_id),
    FOREIGN KEY(run_id) REFERENCES memory_orchestration_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_notification_deliveries (
    receipt_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    occurrence_id TEXT NOT NULL,
    correlation_id TEXT,
    channel TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    retry_seconds INTEGER NOT NULL,
    next_attempt_at TEXT,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    completed_at TEXT,
    failure_policy TEXT NOT NULL,
    repeat_policy TEXT NOT NULL,
    last_error_class TEXT NOT NULL DEFAULT '',
    last_error_code TEXT NOT NULL DEFAULT '',
    UNIQUE(notification_type, occurrence_id, channel, destination_id),
    CHECK (status IN ('pending', 'accepted', 'retry_wait', 'failed', 'expired', 'suppressed')),
    CHECK (attempt_count >= 0),
    CHECK (max_attempts >= 1),
    CHECK (retry_seconds >= 1),
    CHECK (failure_policy IN ('best_effort', 'required')),
    CHECK (repeat_policy IN ('every_occurrence', 'first_per_correlation'))
);

CREATE INDEX IF NOT EXISTS idx_memory_events_observed_at ON memory_events(observed_at);
CREATE INDEX IF NOT EXISTS idx_memory_events_event_type ON memory_events(event_type);
CREATE INDEX IF NOT EXISTS idx_memory_events_category ON memory_events(category);
CREATE INDEX IF NOT EXISTS idx_memory_events_source ON memory_events(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_session ON memory_events(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_correlation ON memory_events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_provider ON memory_events(provider);
CREATE INDEX IF NOT EXISTS idx_memory_events_domain ON memory_events(domain);
CREATE INDEX IF NOT EXISTS idx_memory_events_status ON memory_events(status);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_type ON memory_snapshots(snapshot_type);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_provider ON memory_snapshots(provider);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_source ON memory_snapshots(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_status ON memory_snapshots(status);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_observed_at ON memory_snapshots(observed_at);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_correlation ON memory_sessions(correlation_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_source ON memory_sessions(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_user ON memory_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_mode ON memory_sessions(mode);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_started_at ON memory_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_final_status ON memory_sessions(final_status);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_session ON memory_transcripts(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_correlation ON memory_transcripts(correlation_id);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_source ON memory_transcripts(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_user ON memory_transcripts(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_captured_at ON memory_transcripts(captured_at);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_fallback_used ON memory_transcripts(fallback_used);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_final_domain ON memory_transcripts(final_domain);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_failure_stage ON memory_transcripts(failure_stage);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_final_status ON memory_transcripts(final_status);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_retention_raw ON memory_transcripts(raw_transcript_retention_until);
CREATE INDEX IF NOT EXISTS idx_memory_transcripts_retention_metadata ON memory_transcripts(metadata_retention_until);
CREATE INDEX IF NOT EXISTS idx_memory_orchestration_runs_definition ON memory_orchestration_runs(orchestration_id);
CREATE INDEX IF NOT EXISTS idx_memory_orchestration_runs_status ON memory_orchestration_runs(status);
CREATE INDEX IF NOT EXISTS idx_memory_orchestration_runs_started_at ON memory_orchestration_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_memory_orchestration_steps_run ON memory_orchestration_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_memory_orchestration_steps_status ON memory_orchestration_steps(status);
CREATE INDEX IF NOT EXISTS idx_memory_notification_deliveries_occurrence
ON memory_notification_deliveries(notification_type, occurrence_id);
CREATE INDEX IF NOT EXISTS idx_memory_notification_deliveries_correlation
ON memory_notification_deliveries(correlation_id);
CREATE INDEX IF NOT EXISTS idx_memory_notification_deliveries_status_due
ON memory_notification_deliveries(status, next_attempt_at, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_notification_deliveries_first_per_correlation
ON memory_notification_deliveries(
    notification_type, correlation_id, channel, destination_id
)
WHERE repeat_policy = 'first_per_correlation'
  AND correlation_id IS NOT NULL
  AND correlation_id != '';
"""


_RUNBOOK_KERNEL_RUN_COLUMNS = {
    "definition_domain": "TEXT NOT NULL DEFAULT ''",
    "definition_version": "TEXT NOT NULL DEFAULT ''",
    "correlation_key": "TEXT",
    "activation_idempotency_key": "TEXT",
    "controller_version": "TEXT NOT NULL DEFAULT ''",
    "controller_state_json": "TEXT NOT NULL DEFAULT '{}'",
    "cancellation_reason": "TEXT NOT NULL DEFAULT ''",
    "cancellation_requester": "TEXT NOT NULL DEFAULT ''",
}

_NOTIFICATION_DELIVERY_COLUMNS = {
    "retry_seconds": "INTEGER NOT NULL DEFAULT 30",
}


SUGGESTION_TABLES = ("suggestion_runs", "suggestions", "suggestion_reviews")


def ensure_schema(
    db_path: Path | None = None,
    *,
    copy_provisional_suggestions: bool = True,
    provisional_db_path: Path | None = None,
) -> None:
    path = db_path or DB_PATH
    with transaction(path) as conn:
        conn.executescript(CORE_SCHEMA)
        _ensure_runbook_kernel_schema(conn)
        _ensure_notification_delivery_schema(conn)
        conn.executemany(
            "INSERT OR IGNORE INTO memory_schema_migrations(version) VALUES (?)",
            [(version,) for version in SCHEMA_VERSIONS],
        )
    if copy_provisional_suggestions:
        source_path = provisional_db_path or PROVISIONAL_SUGGESTIONS_DB_PATH
        _copy_provisional_suggestion_tables(path, source_path)


def table_names(db_path: Path | None = None) -> set[str]:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def _ensure_runbook_kernel_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(memory_orchestration_runs)").fetchall()
    }
    for name, declaration in _RUNBOOK_KERNEL_RUN_COLUMNS.items():
        if name in columns:
            continue
        conn.execute(
            f"ALTER TABLE memory_orchestration_runs ADD COLUMN {name} {declaration}"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memory_orchestration_runs_correlation
        ON memory_orchestration_runs(definition_domain, kind, correlation_key)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_orchestration_runs_activation_idempotency
        ON memory_orchestration_runs(activation_idempotency_key)
        WHERE activation_idempotency_key IS NOT NULL
          AND activation_idempotency_key != ''
        """
    )


def _ensure_notification_delivery_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info(memory_notification_deliveries)"
        ).fetchall()
    }
    for name, declaration in _NOTIFICATION_DELIVERY_COLUMNS.items():
        if name in columns:
            continue
        conn.execute(
            f"ALTER TABLE memory_notification_deliveries ADD COLUMN {name} {declaration}"
        )


def _copy_provisional_suggestion_tables(target_path: Path, source_path: Path) -> None:
    if not source_path.exists() or source_path.resolve() == target_path.resolve():
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(target_path)
    try:
        existing = {
            str(row[0])
            for row in target.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        missing = [table for table in SUGGESTION_TABLES if table not in existing]
        if not missing:
            return
        target.execute("ATTACH DATABASE ? AS provisional", (str(source_path),))
        try:
            source_tables = {
                str(row[0])
                for row in target.execute(
                    "SELECT name FROM provisional.sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in SUGGESTION_TABLES:
                if table in existing or table not in source_tables:
                    continue
                create_sql = target.execute(
                    "SELECT sql FROM provisional.sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if not create_sql or not create_sql[0]:
                    continue
                target.execute(create_sql[0])
                quoted = _quote_identifier(table)
                target.execute(f"INSERT INTO {quoted} SELECT * FROM provisional.{quoted}")
            target.commit()
        finally:
            target.execute("DETACH DATABASE provisional")
    finally:
        target.close()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
