from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import ensure_schema
from .store import DB_PATH, transaction


ACTIVE_ALERT_STATUSES = frozenset({"pending", "leased"})
TERMINAL_ALERT_STATUSES = frozenset(
    {"acknowledged", "completed", "canceled", "expired"}
)
ALERT_STATUSES = ACTIVE_ALERT_STATUSES | TERMINAL_ALERT_STATUSES
ALERT_KINDS = frozenset({"alarm", "notification", "reminder", "sleep_timer", "timer"})


@dataclass(frozen=True)
class AlertRecord:
    alert_id: str
    kind: str
    source_id: str
    session_id: str | None
    due_at: datetime
    created_at: datetime
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    status: str = "pending"
    lease_id: str | None = None
    lease_expires_at: datetime | None = None

    @property
    def source(self) -> str:
        return self.source_id

    @property
    def delivered(self) -> bool:
        return self.status in TERMINAL_ALERT_STATUSES


def create_alert_record(
    *,
    kind: str,
    due_at: datetime,
    message: str,
    source_id: str,
    session_id: str | None,
    metadata: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
    alert_id: str | None = None,
    created_at: datetime | None = None,
    db_path: Path | None = None,
) -> tuple[AlertRecord, bool]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_kind = _kind(kind)
    clean_source = _required(source_id, "source_id")
    clean_message = str(message or "").strip()
    if not clean_message and clean_kind != "sleep_timer":
        raise ValueError("message is required")
    clean_due = _timestamp(due_at, "due_at")
    clean_created = _timestamp(created_at or _utc_now(), "created_at")
    clean_expires = _timestamp(expires_at, "expires_at") if expires_at else None
    if clean_expires is not None and clean_expires <= clean_created:
        raise ValueError("expires_at must be after created_at")
    clean_key = str(idempotency_key or "").strip() or None
    clean_alert_id = str(alert_id or uuid.uuid4().hex[:12]).strip()
    metadata_json = json.dumps(dict(metadata or {}), sort_keys=True, separators=(",", ":"))
    with transaction(path) as conn:
        _require_active_source(conn, clean_source)
        if clean_key:
            existing = conn.execute(
                "SELECT * FROM memory_alerts WHERE idempotency_key=? AND source_id=?",
                (clean_key, clean_source),
            ).fetchone()
            if existing is not None:
                return _row(existing), False
        try:
            conn.execute(
                """INSERT INTO memory_alerts (
                       alert_id, created_at, updated_at, kind, source_id, session_id,
                       due_at, expires_at, message, metadata_json, status, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    clean_alert_id,
                    clean_created.isoformat(),
                    clean_created.isoformat(),
                    clean_kind,
                    clean_source,
                    str(session_id or "").strip() or None,
                    clean_due.isoformat(),
                    None if clean_expires is None else clean_expires.isoformat(),
                    clean_message,
                    metadata_json,
                    clean_key,
                ),
            )
        except sqlite3.IntegrityError:
            if not clean_key:
                raise
            existing = conn.execute(
                "SELECT * FROM memory_alerts WHERE idempotency_key=? AND source_id=?",
                (clean_key, clean_source),
            ).fetchone()
            if existing is None:
                raise
            return _row(existing), False
        _transition_row(
            conn,
            alert_id=clean_alert_id,
            source_id=clean_source,
            from_status=None,
            to_status="pending",
            reason="created",
            at=clean_created,
        )
        created = conn.execute(
            "SELECT * FROM memory_alerts WHERE alert_id=?", (clean_alert_id,)
        ).fetchone()
    if created is None:
        raise RuntimeError("Created alert could not be reloaded.")
    return _row(created), True


def create_alert_records(
    *,
    kind: str,
    due_at: datetime,
    message: str,
    source_ids: Iterable[str],
    session_id: str | None,
    metadata: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    idempotency_key: str | None = None,
    db_path: Path | None = None,
) -> tuple[list[AlertRecord], bool]:
    sources = tuple(dict.fromkeys(_required(value, "source_id") for value in source_ids))
    if not sources:
        raise ValueError("At least one source_id is required.")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_kind = _kind(kind)
    clean_message = str(message or "").strip()
    if not clean_message and clean_kind != "sleep_timer":
        raise ValueError("message is required")
    clean_due = _timestamp(due_at, "due_at")
    clean_expires = _timestamp(expires_at, "expires_at") if expires_at else None
    clean_key = str(idempotency_key or "").strip() or None
    created_at = _utc_now()
    if clean_expires is not None and clean_expires <= created_at:
        raise ValueError("expires_at must be after created_at")
    metadata_json = json.dumps(dict(metadata or {}), sort_keys=True, separators=(",", ":"))
    with transaction(path) as conn:
        for source_id in sources:
            _require_active_source(conn, source_id)
        if clean_key:
            placeholders = ",".join("?" for _ in sources)
            existing = conn.execute(
                f"""SELECT 1 FROM memory_alerts
                    WHERE idempotency_key=? AND source_id IN ({placeholders}) LIMIT 1""",
                (clean_key, *sources),
            ).fetchone()
            if existing is not None:
                return [], True
        alert_ids: list[str] = []
        for source_id in sources:
            alert_id = uuid.uuid4().hex[:12]
            conn.execute(
                """INSERT INTO memory_alerts (
                       alert_id, created_at, updated_at, kind, source_id, session_id,
                       due_at, expires_at, message, metadata_json, status, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    alert_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                    clean_kind,
                    source_id,
                    str(session_id or "").strip() or None,
                    clean_due.isoformat(),
                    None if clean_expires is None else clean_expires.isoformat(),
                    clean_message,
                    metadata_json,
                    clean_key,
                ),
            )
            _transition_row(
                conn,
                alert_id=alert_id,
                source_id=source_id,
                from_status=None,
                to_status="pending",
                reason="created",
                at=created_at,
            )
            alert_ids.append(alert_id)
        placeholders = ",".join("?" for _ in alert_ids)
        rows = conn.execute(
            f"SELECT * FROM memory_alerts WHERE alert_id IN ({placeholders})",
            alert_ids,
        ).fetchall()
    by_id = {str(row["alert_id"]): _row(row) for row in rows}
    return [by_id[alert_id] for alert_id in alert_ids], False


def list_alert_records(
    *,
    source_id: str | None = None,
    kind: str | None = None,
    statuses: Iterable[str] | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    db_path: Path | None = None,
) -> list[AlertRecord]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clauses: list[str] = []
    args: list[Any] = []
    if source_id is not None:
        clauses.append("source_id=?")
        args.append(_required(source_id, "source_id"))
    if kind is not None:
        clauses.append("kind=?")
        args.append(_kind(kind))
    if statuses is not None:
        clean_statuses = tuple(dict.fromkeys(_status(value) for value in statuses))
        if not clean_statuses:
            return []
        clauses.append("status IN (" + ",".join("?" for _ in clean_statuses) + ")")
        args.extend(clean_statuses)
    if due_before is not None:
        clauses.append("due_at<=?")
        args.append(_timestamp(due_before, "due_before").isoformat())
    if due_after is not None:
        clauses.append("due_at>=?")
        args.append(_timestamp(due_after, "due_after").isoformat())
    sql = "SELECT * FROM memory_alerts"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY due_at, created_at, alert_id"
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row(row) for row in rows]


def claim_due_alerts(
    *,
    source_id: str,
    now: datetime,
    lease_seconds: int = 30,
    limit: int = 20,
    notification_decisions: dict[str, str] | None = None,
    kind: str | None = None,
    exclude_kinds: Iterable[str] = (),
    db_path: Path | None = None,
) -> list[AlertRecord]:
    if isinstance(lease_seconds, bool) or not 1 <= int(lease_seconds) <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    if isinstance(limit, bool) or not 1 <= int(limit) <= 100:
        raise ValueError("limit must be between 1 and 100")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_source = _required(source_id, "source_id")
    clock = _timestamp(now, "now")
    lease_expires = clock + timedelta(seconds=int(lease_seconds))
    claimed: list[AlertRecord] = []
    with transaction(path) as conn:
        _require_active_source(conn, clean_source)
        expired_leases = conn.execute(
            """SELECT alert_id FROM memory_alerts
               WHERE source_id=? AND status='leased' AND lease_expires_at<=?""",
            (clean_source, clock.isoformat()),
        ).fetchall()
        for item in expired_leases:
            _set_status(
                conn,
                str(item["alert_id"]),
                expected="leased",
                status="pending",
                source_id=clean_source,
                at=clock,
                reason="lease_expired",
            )
        excluded = tuple(dict.fromkeys(_kind(value) for value in exclude_kinds))
        if kind is not None and _kind(kind) in excluded:
            return []
        kind_clause = "" if kind is None else " AND kind=?"
        exclude_clause = (
            "" if not excluded
            else " AND kind NOT IN (" + ",".join("?" for _ in excluded) + ")"
        )
        args: list[Any] = [clean_source, clock.isoformat()]
        if kind is not None:
            args.append(_kind(kind))
        args.extend(excluded)
        args.append(int(limit))
        candidates = conn.execute(
            f"""SELECT * FROM memory_alerts
                WHERE source_id=? AND status='pending' AND due_at<=?{kind_clause}{exclude_clause}
                ORDER BY due_at, created_at, alert_id LIMIT ?""",
            args,
        ).fetchall()
        for candidate in candidates:
            alert_id = str(candidate["alert_id"])
            expires_at = _optional_datetime(candidate["expires_at"])
            if expires_at is not None and expires_at <= clock:
                _set_status(
                    conn,
                    alert_id,
                    expected="pending",
                    status="expired",
                    source_id=clean_source,
                    at=clock,
                    reason="delivery_expired",
                )
                continue
            if str(candidate["kind"]) == "notification":
                decision = str((notification_decisions or {}).get(alert_id) or "defer")
                if decision == "defer":
                    continue
                if decision == "suppress":
                    _set_status(
                        conn,
                        alert_id,
                        expected="pending",
                        status="canceled",
                        source_id=clean_source,
                        at=clock,
                        reason="notification_suppressed",
                    )
                    continue
                if decision != "deliver":
                    raise ValueError(f"Unsupported notification decision {decision!r}")
            lease_id = f"lease-{uuid.uuid4().hex[:24]}"
            updated = conn.execute(
                """UPDATE memory_alerts
                   SET status='leased', updated_at=?, lease_id=?, leased_at=?, lease_expires_at=?
                   WHERE alert_id=? AND status='pending'""",
                (
                    clock.isoformat(),
                    lease_id,
                    clock.isoformat(),
                    lease_expires.isoformat(),
                    alert_id,
                ),
            )
            if updated.rowcount != 1:
                continue
            _transition_row(
                conn,
                alert_id=alert_id,
                source_id=clean_source,
                from_status="pending",
                to_status="leased",
                reason="claimed",
                at=clock,
                lease_id=lease_id,
            )
            row = conn.execute(
                "SELECT * FROM memory_alerts WHERE alert_id=?", (alert_id,)
            ).fetchone()
            if row is not None:
                claimed.append(_row(row))
    return claimed


def acknowledge_alert(
    *,
    alert_id: str,
    source_id: str,
    lease_id: str,
    now: datetime,
    completed: bool = False,
    db_path: Path | None = None,
) -> AlertRecord:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_id = _required(alert_id, "alert_id")
    clean_source = _required(source_id, "source_id")
    clean_lease = _required(lease_id, "lease_id")
    clock = _timestamp(now, "now")
    status = "completed" if completed else "acknowledged"
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_alerts WHERE alert_id=?", (clean_id,)
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown alert {clean_id}")
        if str(current["source_id"]) != clean_source:
            raise PermissionError("Alert acknowledgement source mismatch.")
        if str(current["status"]) in {"acknowledged", "completed"}:
            accepted_lease = conn.execute(
                """SELECT lease_id FROM memory_alert_transitions
                   WHERE alert_id=? AND to_status IN ('acknowledged','completed')
                   ORDER BY created_at DESC, transition_id DESC LIMIT 1""",
                (clean_id,),
            ).fetchone()
            if accepted_lease is None or str(accepted_lease["lease_id"] or "") != clean_lease:
                raise ValueError("Alert acknowledgement lease mismatch.")
            return _row(current)
        if str(current["status"]) != "leased" or str(current["lease_id"] or "") != clean_lease:
            raise ValueError("Alert acknowledgement requires the active lease.")
        lease_expires = _optional_datetime(current["lease_expires_at"])
        if lease_expires is None or lease_expires <= clock:
            raise ValueError("Alert lease has expired.")
        _set_status(
            conn,
            clean_id,
            expected="leased",
            status=status,
            source_id=clean_source,
            at=clock,
            reason="satellite_accepted",
            lease_id=clean_lease,
        )
        row = conn.execute(
            "SELECT * FROM memory_alerts WHERE alert_id=?", (clean_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError("Acknowledged alert could not be reloaded.")
    return _row(row)


def cancel_alert_records(
    *,
    source_id: str,
    kind: str,
    all_matches: bool,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> int:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    clean_source = _required(source_id, "source_id")
    clock = _timestamp(now or _utc_now(), "now")
    with transaction(path) as conn:
        rows = conn.execute(
            """SELECT alert_id, status FROM memory_alerts
               WHERE source_id=? AND kind=? AND status IN ('pending', 'leased')
               ORDER BY due_at, created_at, alert_id""",
            (clean_source, _kind(kind)),
        ).fetchall()
        selected = rows if all_matches else rows[:1]
        for row in selected:
            _set_status(
                conn,
                str(row["alert_id"]),
                expected=str(row["status"]),
                status="canceled",
                source_id=clean_source,
                at=clock,
                reason="canceled",
            )
    return len(selected)


def clear_alert_records(*, db_path: Path | None = None) -> None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        conn.execute("DELETE FROM memory_alert_transitions")
        conn.execute("DELETE FROM memory_alerts")


def import_legacy_alerts(
    payload: object,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    if not isinstance(payload, list):
        raise ValueError("Legacy alert payload must be a list.")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    imported = 0
    duplicates = 0
    rejected = 0
    clock = now or _utc_now()
    with transaction(path) as conn:
        for item in payload:
            if not isinstance(item, dict):
                rejected += 1
                continue
            try:
                source_id = _required(item.get("source"), "source")
                _require_active_source(conn, source_id)
                alert_id = _required(item.get("alert_id"), "alert_id")
                kind = _kind(item.get("kind"))
                due_at = _timestamp(item.get("due_at"), "due_at")
                created_at = _timestamp(item.get("created_at"), "created_at")
                message = str(item.get("message") or "").strip()
                if not message and kind != "sleep_timer":
                    raise ValueError("message is required")
                expires_at = (
                    _timestamp(item.get("expires_at"), "expires_at")
                    if item.get("expires_at") not in (None, "")
                    else None
                )
                if expires_at is not None and expires_at <= created_at:
                    raise ValueError("expires_at must be after created_at")
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                idempotency_key = str(metadata.get("idempotency_key") or "").strip() or None
                if kind == "notification":
                    _required(metadata.get("notification_id"), "notification_id")
                    _required(metadata.get("event_id"), "event_id")
                    if expires_at is None:
                        raise ValueError("Notification alert requires expires_at")
            except (KeyError, TypeError, ValueError, sqlite3.Error):
                rejected += 1
                continue
            existing = conn.execute(
                "SELECT 1 FROM memory_alerts WHERE alert_id=?",
                (alert_id,),
            ).fetchone()
            if existing is None and idempotency_key:
                existing = conn.execute(
                    "SELECT 1 FROM memory_alerts WHERE idempotency_key=? AND source_id=?",
                    (idempotency_key, source_id),
                ).fetchone()
            if existing is not None:
                duplicates += 1
                continue
            conn.execute(
                """INSERT INTO memory_alerts (
                       alert_id, created_at, updated_at, kind, source_id, session_id,
                       due_at, expires_at, message, metadata_json, status, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    alert_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                    kind,
                    source_id,
                    str(item.get("session_id") or "").strip() or None,
                    due_at.isoformat(),
                    None if expires_at is None else expires_at.isoformat(),
                    message,
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                    idempotency_key,
                ),
            )
            _transition_row(
                conn,
                alert_id=alert_id,
                source_id=source_id,
                from_status=None,
                to_status="pending",
                reason="legacy_import",
                at=clock,
            )
            if kind == "notification":
                _import_notification_receipt(
                    conn,
                    source_id=source_id,
                    metadata=metadata,
                    expires_at=expires_at,
                    delivered=bool(item.get("delivered")),
                    at=clock,
                )
            if bool(item.get("delivered")):
                _set_status(
                    conn,
                    alert_id,
                    expected="pending",
                    status="completed",
                    source_id=source_id,
                    at=clock,
                    reason="legacy_delivered",
                )
            imported += 1
    return {"imported": imported, "duplicates": duplicates, "rejected": rejected}


def _import_notification_receipt(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    metadata: dict[str, Any],
    expires_at: datetime | None,
    delivered: bool,
    at: datetime,
) -> None:
    if expires_at is None:
        raise ValueError("Notification alert requires expires_at")
    notification_type = _required(metadata.get("notification_id"), "notification_id")
    occurrence_id = _required(metadata.get("event_id"), "event_id")
    identity = "\x1f".join(
        (notification_type, occurrence_id, "satellite_announcement", source_id)
    )
    receipt_id = f"delivery-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    status = "accepted" if delivered else "pending"
    terminal_at = at.isoformat() if delivered else None
    conn.execute(
        """INSERT OR IGNORE INTO memory_notification_deliveries (
               receipt_id, created_at, updated_at, notification_type,
               occurrence_id, channel, destination_id, provider, status,
               attempt_count, max_attempts, retry_seconds, expires_at,
               accepted_at, completed_at, failure_policy, repeat_policy
           ) VALUES (?, ?, ?, ?, ?, 'satellite_announcement', ?, 'oracle_brain',
                     ?, 0, 1, 30, ?, ?, ?, 'best_effort', 'every_occurrence')""",
        (
            receipt_id,
            at.isoformat(),
            at.isoformat(),
            notification_type,
            occurrence_id,
            source_id,
            status,
            expires_at.isoformat(),
            terminal_at,
            terminal_at,
        ),
    )


def active_alert_source_ids(*, db_path: Path | None = None) -> set[str]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT source_id FROM memory_alerts WHERE status IN ('pending', 'leased')"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _set_status(
    conn: sqlite3.Connection,
    alert_id: str,
    *,
    expected: str,
    status: str,
    source_id: str,
    at: datetime,
    reason: str,
    lease_id: str | None = None,
) -> None:
    _status(status)
    terminal_at = at.isoformat() if status in TERMINAL_ALERT_STATUSES else None
    updated = conn.execute(
        """UPDATE memory_alerts
           SET status=?, updated_at=?, lease_id=NULL, leased_at=NULL, lease_expires_at=NULL,
               acknowledged_at=CASE WHEN ?='acknowledged' THEN ? ELSE acknowledged_at END,
               completed_at=CASE WHEN ? IN ('acknowledged','completed','expired') THEN ? ELSE completed_at END,
               canceled_at=CASE WHEN ?='canceled' THEN ? ELSE canceled_at END
           WHERE alert_id=? AND status=?""",
        (
            status,
            at.isoformat(),
            status,
            terminal_at,
            status,
            terminal_at,
            status,
            terminal_at,
            alert_id,
            expected,
        ),
    )
    if updated.rowcount != 1:
        raise ValueError(f"Invalid alert transition {expected} -> {status}")
    _transition_row(
        conn,
        alert_id=alert_id,
        source_id=source_id,
        from_status=expected,
        to_status=status,
        reason=reason,
        at=at,
        lease_id=lease_id,
    )


def _transition_row(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    source_id: str,
    from_status: str | None,
    to_status: str,
    reason: str,
    at: datetime,
    lease_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO memory_alert_transitions (
               transition_id, alert_id, created_at, from_status, to_status,
               source_id, lease_id, reason
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f"alert-transition-{uuid.uuid4().hex}",
            alert_id,
            at.isoformat(),
            from_status,
            to_status,
            source_id,
            lease_id,
            str(reason or "").strip()[:160],
        ),
    )


def _require_active_source(conn: sqlite3.Connection, source_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM memory_sources WHERE source_id=?", (source_id,)
    ).fetchone()
    if row is None or str(row["status"]) != "active":
        raise ValueError(f"Alert source {source_id!r} is not an active canonical identity.")


def _kind(value: object) -> str:
    clean = _required(value, "kind")
    if clean not in ALERT_KINDS:
        raise ValueError(f"Unsupported alert kind {clean!r}")
    return clean


def _row(row: Any) -> AlertRecord:
    metadata = json.loads(str(row["metadata_json"] or "{}"))
    return AlertRecord(
        alert_id=str(row["alert_id"]),
        kind=str(row["kind"]),
        source_id=str(row["source_id"]),
        session_id=str(row["session_id"]) if row["session_id"] is not None else None,
        due_at=_timestamp(row["due_at"], "due_at"),
        created_at=_timestamp(row["created_at"], "created_at"),
        message=str(row["message"]),
        metadata=metadata if isinstance(metadata, dict) else {},
        expires_at=_optional_datetime(row["expires_at"]),
        status=str(row["status"]),
        lease_id=str(row["lease_id"]) if row["lease_id"] is not None else None,
        lease_expires_at=_optional_datetime(row["lease_expires_at"]),
    )


def _required(value: object, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} is required")
    return clean


def _status(value: object) -> str:
    clean = _required(value, "status")
    if clean not in ALERT_STATUSES:
        raise ValueError(f"Unsupported alert status {clean!r}")
    return clean


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _optional_datetime(value: object) -> datetime | None:
    return None if value in (None, "") else _timestamp(value, "timestamp")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
