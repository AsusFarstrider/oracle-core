from __future__ import annotations

import sqlite3
from pathlib import Path

from .store import DB_PATH, transaction


SCHEMA_VERSION = "0009_durable_alerts"
SCHEMA_VERSIONS = (
    "0001_core",
    "0002_sessions_transcripts",
    "0003_orchestration_runs",
    "0004_runbook_kernel_metadata",
    "0005_notification_delivery_receipts",
    "0006_notification_delivery_retry_policy",
    "0007_notification_delivery_repeat_policy",
    "0008_current_state_and_retention",
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

CREATE TABLE IF NOT EXISTS memory_current_projections (
    projection_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    projection_type TEXT NOT NULL,
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
    CHECK (mode IN ('conversation', 'ui', 'api', 'system', 'background')),
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

CREATE TABLE IF NOT EXISTS memory_alerts (
    alert_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    session_id TEXT,
    due_at TEXT NOT NULL,
    expires_at TEXT,
    message TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    idempotency_key TEXT,
    lease_id TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    acknowledged_at TEXT,
    completed_at TEXT,
    canceled_at TEXT,
    CHECK (status IN ('pending', 'leased', 'acknowledged', 'completed', 'canceled', 'expired')),
    CHECK ((status = 'leased') = (lease_id IS NOT NULL AND lease_expires_at IS NOT NULL)),
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id)
);

CREATE TABLE IF NOT EXISTS memory_alert_transitions (
    transition_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    source_id TEXT NOT NULL,
    lease_id TEXT,
    reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(alert_id) REFERENCES memory_alerts(alert_id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES memory_sources(source_id)
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
CREATE INDEX IF NOT EXISTS idx_memory_current_projections_type ON memory_current_projections(projection_type);
CREATE INDEX IF NOT EXISTS idx_memory_current_projections_provider ON memory_current_projections(provider);
CREATE INDEX IF NOT EXISTS idx_memory_current_projections_source ON memory_current_projections(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_current_projections_status ON memory_current_projections(status);
CREATE INDEX IF NOT EXISTS idx_memory_current_projections_observed_at ON memory_current_projections(observed_at);
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_alerts_idempotency
ON memory_alerts(idempotency_key, source_id)
WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE INDEX IF NOT EXISTS idx_memory_alerts_claim
ON memory_alerts(source_id, status, due_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_memory_alerts_terminal
ON memory_alerts(status, completed_at, canceled_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_alert_transitions_alert
ON memory_alert_transitions(alert_id, created_at);
"""


SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestion_runs (
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
CREATE TABLE IF NOT EXISTS suggestions (
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
    mock INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(run_id) REFERENCES suggestion_runs(run_id)
);
CREATE TABLE IF NOT EXISTS suggestion_reviews (
    review_id TEXT PRIMARY KEY,
    suggestion_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    correction_text TEXT,
    rejection_reason TEXT,
    future_automation_candidate INTEGER NOT NULL DEFAULT 0,
    suppress_if_repeated INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(suggestion_id) REFERENCES suggestions(id)
);
CREATE TABLE IF NOT EXISTS suggestion_exchange_current (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    run_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    packet_json TEXT,
    response_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggestions_run ON suggestions(run_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);
CREATE INDEX IF NOT EXISTS idx_suggestions_similarity ON suggestions(similarity_key);
CREATE INDEX IF NOT EXISTS idx_reviews_suggestion ON suggestion_reviews(suggestion_id);
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


def ensure_schema(
    db_path: Path | None = None,
) -> None:
    path = db_path or DB_PATH
    _prepare_breaking_migration(path)
    with transaction(path) as conn:
        conn.executescript(CORE_SCHEMA)
        conn.executescript(SUGGESTIONS_SCHEMA)
        _ensure_runbook_kernel_schema(conn)
        _ensure_notification_delivery_schema(conn)
        conn.executemany(
            "INSERT OR IGNORE INTO memory_schema_migrations(version) VALUES (?)",
            [(version,) for version in SCHEMA_VERSIONS],
        )


def _prepare_breaking_migration(path: Path) -> None:
    """Apply the one-way V2 schema migration before normal schema creation.

    This deliberately has no compatibility view: after migration the explicit
    current-state projection and ``conversation`` session vocabulary are the
    only executable schema.
    """
    if not path.exists():
        return
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "memory_snapshots" in tables:
            if "memory_current_projections" not in tables:
                conn.execute("ALTER TABLE memory_snapshots RENAME TO memory_current_projections")
                conn.execute(
                    "ALTER TABLE memory_current_projections RENAME COLUMN snapshot_id TO projection_id"
                )
                conn.execute(
                    "ALTER TABLE memory_current_projections RENAME COLUMN snapshot_type TO projection_type"
                )
            else:
                # A pre-cutover writer may have recreated the old table after a
                # schema rehearsal. Merge its current rows deterministically,
                # then complete the one-way cutover without a compatibility view.
                conn.execute(
                    """INSERT INTO memory_current_projections (
                           projection_id, created_at, updated_at, observed_at,
                           projection_type, source_id, provider, domain, status,
                           correlation_id, payload_json
                       )
                       SELECT snapshot_id, created_at, updated_at, observed_at,
                              snapshot_type, source_id, provider, domain, status,
                              correlation_id, payload_json
                       FROM memory_snapshots
                       WHERE 1
                       ON CONFLICT(projection_id) DO UPDATE SET
                           updated_at=excluded.updated_at,
                           observed_at=excluded.observed_at,
                           projection_type=excluded.projection_type,
                           source_id=excluded.source_id,
                           provider=excluded.provider,
                           domain=excluded.domain,
                           status=excluded.status,
                           correlation_id=excluded.correlation_id,
                           payload_json=excluded.payload_json
                       WHERE excluded.observed_at > memory_current_projections.observed_at"""
                )
                conn.execute("DROP TABLE memory_snapshots")
        if "memory_sessions" in tables:
            create_sql_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_sessions'"
            ).fetchone()
            create_sql = str(create_sql_row[0] or "") if create_sql_row else ""
            if "'voice'" in create_sql:
                conn.execute(
                    """CREATE TABLE memory_sessions_v2 (
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
                        CHECK (mode IN ('conversation', 'ui', 'api', 'system', 'background')),
                        FOREIGN KEY(source_id) REFERENCES memory_sources(source_id),
                        FOREIGN KEY(user_id) REFERENCES memory_users(user_id)
                    )"""
                )
                conn.execute(
                    """INSERT INTO memory_sessions_v2 (
                        session_id, created_at, updated_at, correlation_id, source_id,
                        user_id, mode, started_at, ended_at, final_status, payload_json
                    )
                    SELECT session_id, created_at, updated_at, correlation_id, source_id,
                           user_id, CASE mode WHEN 'voice' THEN 'conversation' ELSE mode END,
                           started_at, ended_at, final_status, payload_json
                    FROM memory_sessions"""
                )
                conn.execute("DROP TABLE memory_sessions")
                conn.execute("ALTER TABLE memory_sessions_v2 RENAME TO memory_sessions")
        if "memory_users" in tables:
            user_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(memory_users)").fetchall()
            }
            if "role" in user_columns:
                conn.execute(
                    """CREATE TABLE memory_users_v2 (
                        user_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        payload_json TEXT NOT NULL DEFAULT '{}'
                    )"""
                )
                conn.execute(
                    """INSERT INTO memory_users_v2 (
                        user_id, created_at, updated_at, display_name, status, payload_json
                    ) SELECT user_id, created_at, updated_at, display_name, status, payload_json
                      FROM memory_users"""
                )
                conn.execute("DROP TABLE memory_users")
                conn.execute("ALTER TABLE memory_users_v2 RENAME TO memory_users")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
