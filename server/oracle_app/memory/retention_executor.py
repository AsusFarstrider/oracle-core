from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .retention import RetentionPolicy
from .schema import ensure_schema
from .store import DB_PATH, transaction
from .taxonomy import EVENT_TAXONOMY


@dataclass(frozen=True)
class RetentionClassReport:
    class_name: str
    action: str
    candidate_ids: tuple[str, ...] = ()
    protected_ids: tuple[str, ...] = ()
    transition_ids: tuple[str, ...] = ()
    blocked_ids: tuple[str, ...] = ()

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_ids)


@dataclass(frozen=True)
class RetentionReport:
    observed_at: str
    dry_run: bool
    classes: tuple[RetentionClassReport, ...]
    unknown_tables: tuple[str, ...]

    @property
    def changed_count(self) -> int:
        return sum(item.candidate_count + len(item.transition_ids) for item in self.classes)

    @property
    def blocked(self) -> bool:
        return bool(self.unknown_tables or any(item.blocked_ids for item in self.classes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "dry_run": self.dry_run,
            "changed_count": self.changed_count,
            "blocked": self.blocked,
            "unknown_tables": list(self.unknown_tables),
            "classes": [
                {**asdict(item), "candidate_count": item.candidate_count}
                for item in self.classes
            ],
        }


_KNOWN_TABLES = {
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
    "memory_alerts",
    "memory_alert_transitions",
    "suggestion_runs",
    "suggestions",
    "suggestion_reviews",
    "suggestion_exchange_current",
}
_SEVERITY_FIELDS = {
    "info": "routine_event_days",
    "warning": "warning_event_days",
    "error": "error_event_days",
    "critical": "critical_event_days",
}
_TERMINAL_ORCHESTRATION = {"completed", "failed", "canceled", "interrupted"}
_ACTIVE_ORCHESTRATION = {"running", "waiting"}
_TERMINAL_NOTIFICATIONS = {"accepted", "suppressed", "failed", "expired"}
_ACTIVE_NOTIFICATIONS = {"pending", "retry_wait"}
_TERMINAL_ALERTS = {"acknowledged", "completed", "canceled", "expired"}
_ACTIVE_ALERTS = {"pending", "leased"}
_ALERT_KINDS = {"alarm", "notification", "reminder", "sleep_timer", "timer"}
_REVIEWED_SUGGESTIONS = {"accepted", "rejected", "corrected", "ignored", "false_positive"}


def run_retention(
    policy: RetentionPolicy,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = True,
    active_session_ids: Iterable[str] = (),
) -> RetentionReport:
    path = db_path or DB_PATH
    ensure_schema(path)
    clock = _utc(now or datetime.now(timezone.utc))
    active_sessions = frozenset(str(item) for item in active_session_ids)
    with transaction(path) as conn:
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        unknown = tuple(sorted(
            table for table in tables
            if (table.startswith("memory_") or table.startswith("suggestion_"))
            and table not in _KNOWN_TABLES
        ))
        reports = (
            _event_report(conn, policy, clock),
            _transcript_raw_report(conn, clock),
            _transcript_metadata_report(conn, policy, clock, active_sessions),
            _session_report(conn, policy, clock, active_sessions),
            _orchestration_report(conn, policy, clock),
            _alert_report(conn, policy, clock),
            _notification_report(conn, policy, clock),
            _suggestion_raw_report(conn, policy, clock),
            _suggestion_run_diagnostics_report(conn, policy, clock),
            _suggestion_mock_report(conn, policy, clock),
            _suggestion_envelope_report(conn, policy, clock),
            _suggestion_exchange_report(conn, policy, clock),
            RetentionClassReport("current_projections", "preserve_current_state"),
        )
        report = RetentionReport(clock.isoformat(), dry_run, reports, unknown)
        if not dry_run:
            if report.blocked:
                raise RuntimeError("Retention apply blocked by unknown or incomplete data classes.")
            _apply(conn, reports, clock)
    if not dry_run and report.changed_count:
        from .runtime import safe_record_event

        safe_record_event(
            "retention_pruned",
            severity="info",
            observed_at=clock.isoformat(),
            source_id=None,
            domain="memory",
            status="completed",
            payload={
                "changed_count": report.changed_count,
                "classes": {
                    item.class_name: item.candidate_count
                    for item in report.classes if item.candidate_count
                },
            },
            db_path=path,
        )
    return report


def _event_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    blocked: list[str] = []
    categories = set(EVENT_TAXONOMY)
    for row in conn.execute(
        "SELECT event_id, observed_at, severity, category, provider FROM memory_events"
    ):
        event_id = str(row["event_id"])
        observed = _parse(row["observed_at"])
        severity = str(row["severity"] or "")
        category = str(row["category"] or "")
        if observed is None or observed > now or severity not in _SEVERITY_FIELDS or category not in categories:
            blocked.append(event_id)
            continue
        days = int(getattr(policy, _SEVERITY_FIELDS[severity]))
        if row["provider"] or category == "provider.status":
            days = max(days, policy.provider_status_event_days)
        if category in {"system.lifecycle", "notification.lifecycle", "orchestration"}:
            days = max(days, policy.lifecycle_event_days)
        if observed <= now - timedelta(days=days):
            candidates.append(event_id)
    return _report("events", "delete", candidates, blocked=blocked)


def _transcript_raw_report(conn: Any, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    blocked: list[str] = []
    for row in conn.execute(
        "SELECT transcript_id, raw_transcript, raw_transcript_retention_until FROM memory_transcripts"
    ):
        if row["raw_transcript"] is None:
            continue
        deadline = _parse(row["raw_transcript_retention_until"])
        if deadline is None or deadline > now + timedelta(days=3650):
            blocked.append(str(row["transcript_id"]))
        elif deadline <= now:
            candidates.append(str(row["transcript_id"]))
    return _report("transcript_raw", "scrub", candidates, blocked=blocked)


def _session_report(
    conn: Any,
    policy: RetentionPolicy,
    now: datetime,
    active_sessions: frozenset[str],
) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.session_metadata_days)
    for row in conn.execute(
        "SELECT session_id, updated_at, ended_at, final_status FROM memory_sessions"
    ):
        session_id = str(row["session_id"])
        if session_id in active_sessions:
            protected.append(session_id)
            continue
        age_value = row["ended_at"] or row["updated_at"]
        age = _parse(age_value)
        if age is None or age > now:
            blocked.append(session_id)
            continue
        if row["ended_at"] is None and age > cutoff:
            protected.append(session_id)
            continue
        if row["ended_at"] is not None and not row["final_status"]:
            blocked.append(session_id)
            continue
        if age <= cutoff:
            candidates.append(session_id)
    return _report("sessions_and_transcripts", "delete_dependents_then_session", candidates, protected, blocked)


def _transcript_metadata_report(
    conn: Any,
    policy: RetentionPolicy,
    now: datetime,
    active_sessions: frozenset[str],
) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.transcript_metadata_days)
    for row in conn.execute(
        "SELECT transcript_id, session_id, captured_at FROM memory_transcripts"
    ):
        transcript_id = str(row["transcript_id"])
        if str(row["session_id"] or "") in active_sessions:
            protected.append(transcript_id)
            continue
        age = _parse(row["captured_at"])
        if age is None or age > now:
            blocked.append(transcript_id)
        elif age <= cutoff:
            candidates.append(transcript_id)
    return _report("transcript_metadata", "delete_before_session", candidates, protected, blocked)


def _orchestration_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.orchestration_history_days)
    for row in conn.execute(
        "SELECT run_id, status, completed_at, updated_at FROM memory_orchestration_runs"
    ):
        run_id = str(row["run_id"])
        status = str(row["status"] or "")
        if status in _ACTIVE_ORCHESTRATION:
            protected.append(run_id)
            continue
        if status not in _TERMINAL_ORCHESTRATION:
            blocked.append(run_id)
            continue
        age = _parse(row["completed_at"] or row["updated_at"])
        if age is None or age > now:
            blocked.append(run_id)
        elif age <= cutoff:
            candidates.append(run_id)
    return _report("orchestration_history", "delete_atomic_run_and_steps", candidates, protected, blocked)


def _notification_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    transitions: list[str] = []
    blocked: list[str] = []
    for row in conn.execute(
        "SELECT receipt_id, status, completed_at, updated_at, expires_at FROM memory_notification_deliveries"
    ):
        receipt_id = str(row["receipt_id"])
        status = str(row["status"] or "")
        expires = _parse(row["expires_at"])
        if status in _ACTIVE_NOTIFICATIONS:
            if expires is None or expires > now:
                if expires is None:
                    blocked.append(receipt_id)
                continue
            transitions.append(receipt_id)
            continue
        if status not in _TERMINAL_NOTIFICATIONS:
            blocked.append(receipt_id)
            continue
        age = _parse(row["completed_at"] or row["updated_at"])
        if age is None or age > now:
            blocked.append(receipt_id)
            continue
        days = {
            "accepted": policy.notification_accepted_days,
            "suppressed": policy.notification_suppressed_days,
            "failed": policy.notification_failed_days,
            "expired": policy.notification_failed_days,
        }[status]
        if age <= now - timedelta(days=days):
            candidates.append(receipt_id)
    return _report(
        "notification_receipts",
        "transition_overdue_then_delete_terminal",
        candidates,
        transition_ids=transitions,
        blocked=blocked,
    )


def _alert_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.alert_terminal_days)
    for row in conn.execute(
        """SELECT alert_id, status, acknowledged_at, completed_at, canceled_at,
                  updated_at, source_id, kind
           FROM memory_alerts"""
    ):
        alert_id = str(row["alert_id"])
        status = str(row["status"] or "")
        source_exists = conn.execute(
            "SELECT 1 FROM memory_sources WHERE source_id=?", (row["source_id"],)
        ).fetchone()
        if source_exists is None or str(row["kind"] or "") not in _ALERT_KINDS:
            blocked.append(alert_id)
            continue
        if status in _ACTIVE_ALERTS:
            protected.append(alert_id)
            continue
        if status not in _TERMINAL_ALERTS:
            blocked.append(alert_id)
            continue
        age = _parse(
            row["completed_at"]
            or row["acknowledged_at"]
            or row["canceled_at"]
            or row["updated_at"]
        )
        if age is None or age > now:
            blocked.append(alert_id)
        elif age <= cutoff:
            candidates.append(alert_id)
    return _report(
        "terminal_alerts",
        "delete_transitions_then_alert",
        candidates,
        protected,
        blocked,
    )


def _suggestion_raw_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.suggestion_raw_evidence_days)
    for row in conn.execute("SELECT id, created_at, status, mock FROM suggestions"):
        item_id = str(row["id"])
        age = _parse(row["created_at"])
        if not bool(row["mock"]) and str(row["status"]) not in _REVIEWED_SUGGESTIONS:
            protected.append(item_id)
        elif age is None or age > now:
            blocked.append(item_id)
        elif age <= cutoff:
            candidates.append(item_id)
    return _report("suggestion_raw_evidence", "scrub", candidates, protected, blocked=blocked)


def _suggestion_run_diagnostics_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    cutoff = now - timedelta(days=policy.suggestion_raw_evidence_days)
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    for row in conn.execute(
        "SELECT run_id,status,completed_at,created_at FROM suggestion_runs"
    ):
        run_id = str(row["run_id"])
        status = str(row["status"] or "")
        if status == "running":
            protected.append(run_id)
            continue
        if status not in {"completed", "failed"}:
            blocked.append(run_id)
            continue
        age = _parse(row["completed_at"] or row["created_at"])
        if age is None or age > now:
            blocked.append(run_id)
        elif age <= cutoff:
            candidates.append(run_id)
    return _report("suggestion_run_diagnostics", "scrub", candidates, protected, blocked)


def _suggestion_mock_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    cutoff = now - timedelta(days=policy.suggestion_mock_days)
    ids = [str(row["id"]) for row in conn.execute(
        "SELECT id FROM suggestions WHERE mock=1 AND created_at<=?", (cutoff.isoformat(),)
    )]
    return _report("mock_suggestions", "delete", ids)


def _suggestion_envelope_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    candidates: list[str] = []
    protected: list[str] = []
    blocked: list[str] = []
    cutoff = now - timedelta(days=policy.suggestion_envelope_days)
    for row in conn.execute("SELECT run_id, status, completed_at, created_at FROM suggestion_runs"):
        run_id = str(row["run_id"])
        if str(row["status"]) == "running":
            protected.append(run_id)
            continue
        if str(row["status"]) not in {"completed", "failed"}:
            blocked.append(run_id)
            continue
        age = _parse(row["completed_at"] or row["created_at"])
        if age is None or age > now:
            blocked.append(run_id)
        elif age <= cutoff:
            referenced = conn.execute(
                "SELECT 1 FROM suggestions WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone()
            (protected if referenced else candidates).append(run_id)
    return _report("suggestion_envelopes", "delete_unreferenced", candidates, protected, blocked)


def _suggestion_exchange_report(conn: Any, policy: RetentionPolicy, now: datetime) -> RetentionClassReport:
    row = conn.execute(
        "SELECT run_id, updated_at FROM suggestion_exchange_current WHERE singleton_id=1"
    ).fetchone()
    if row is None:
        return _report("suggestion_exchange", "scrub")
    age = _parse(row["updated_at"])
    if age is None or age > now:
        return _report("suggestion_exchange", "scrub", blocked=[str(row["run_id"])])
    ids = [str(row["run_id"])] if age <= now - timedelta(days=policy.suggestion_exchange_days) else []
    return _report("suggestion_exchange", "scrub", ids)


def _apply(conn: Any, reports: tuple[RetentionClassReport, ...], now: datetime) -> None:
    by_name = {item.class_name: item for item in reports}
    _delete_ids(conn, "memory_events", "event_id", by_name["events"].candidate_ids)
    for transcript_id in by_name["transcript_raw"].candidate_ids:
        conn.execute(
            """UPDATE memory_transcripts SET raw_transcript=NULL, raw_transcript_pruned_at=?
               WHERE transcript_id=?""",
            (now.isoformat(), transcript_id),
        )
    _delete_ids(
        conn,
        "memory_transcripts",
        "transcript_id",
        by_name["transcript_metadata"].candidate_ids,
    )
    for session_id in by_name["sessions_and_transcripts"].candidate_ids:
        conn.execute("DELETE FROM memory_transcripts WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM memory_sessions WHERE session_id=?", (session_id,))
    _delete_ids(conn, "memory_orchestration_runs", "run_id", by_name["orchestration_history"].candidate_ids)
    for alert_id in by_name["terminal_alerts"].candidate_ids:
        conn.execute("DELETE FROM memory_alert_transitions WHERE alert_id=?", (alert_id,))
        conn.execute("DELETE FROM memory_alerts WHERE alert_id=?", (alert_id,))
    for receipt_id in by_name["notification_receipts"].transition_ids:
        conn.execute(
            """UPDATE memory_notification_deliveries
               SET status='expired', completed_at=?, updated_at=?, next_attempt_at=NULL
               WHERE receipt_id=? AND status IN ('pending','retry_wait')""",
            (now.isoformat(), now.isoformat(), receipt_id),
        )
    _delete_ids(conn, "memory_notification_deliveries", "receipt_id", by_name["notification_receipts"].candidate_ids)
    for item_id in by_name["suggestion_raw_evidence"].candidate_ids:
        conn.execute(
            """UPDATE suggestions SET evidence_json='[]', raw_openclaw_item_json='{}'
               WHERE id=?""",
            (item_id,),
        )
    for run_id in by_name["suggestion_run_diagnostics"].candidate_ids:
        conn.execute(
            """UPDATE suggestion_runs
               SET collector_status_json='{}', error=NULL, custom_prompt=NULL,
                   packet_path=NULL, response_path=NULL
               WHERE run_id=?""",
            (run_id,),
        )
    for item_id in by_name["mock_suggestions"].candidate_ids:
        conn.execute("DELETE FROM suggestion_reviews WHERE suggestion_id=?", (item_id,))
        conn.execute("DELETE FROM suggestions WHERE id=?", (item_id,))
    _delete_ids(conn, "suggestion_runs", "run_id", by_name["suggestion_envelopes"].candidate_ids)
    if by_name["suggestion_exchange"].candidate_ids:
        conn.execute(
            "UPDATE suggestion_exchange_current SET packet_json=NULL, response_json=NULL WHERE singleton_id=1"
        )


def _delete_ids(conn: Any, table: str, column: str, ids: tuple[str, ...]) -> None:
    conn.executemany(f"DELETE FROM {table} WHERE {column}=?", ((item,) for item in ids))


def _report(
    name: str,
    action: str,
    candidates: Iterable[str] = (),
    protected: Iterable[str] = (),
    blocked: Iterable[str] = (),
    transition_ids: Iterable[str] = (),
) -> RetentionClassReport:
    return RetentionClassReport(
        name,
        action,
        tuple(sorted(set(candidates))),
        tuple(sorted(set(protected))),
        tuple(sorted(set(transition_ids))),
        tuple(sorted(set(blocked))),
    )


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
