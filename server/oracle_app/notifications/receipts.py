from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from oracle_app.memory.schema import ensure_schema
from oracle_app.memory.store import DB_PATH, transaction


DELIVERY_STATUSES = {
    "pending",
    "accepted",
    "retry_wait",
    "failed",
    "expired",
    "suppressed",
}
TERMINAL_DELIVERY_STATUSES = {"accepted", "failed", "expired", "suppressed"}
_ALLOWED_TRANSITIONS = {
    "pending": TERMINAL_DELIVERY_STATUSES | {"retry_wait"},
    "retry_wait": TERMINAL_DELIVERY_STATUSES | {"pending"},
    "accepted": set(),
    "failed": set(),
    "expired": set(),
    "suppressed": set(),
}


@dataclass(frozen=True)
class NotificationDeliveryQuery:
    notification_type: str | None = None
    occurrence_id: str | None = None
    correlation_id: str | None = None
    channel: str | None = None
    destination_id: str | None = None
    provider: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


def reserve_notification_delivery(
    *,
    notification_type: str,
    occurrence_id: str,
    channel: str,
    destination_id: str,
    provider: str = "",
    correlation_id: str = "",
    max_attempts: int,
    retry_seconds: int,
    expires_at: str,
    failure_policy: str,
    repeat_policy: str,
    db_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    values = {
        "notification_type": _clean_required(notification_type, "notification_type"),
        "occurrence_id": _clean_required(occurrence_id, "occurrence_id"),
        "channel": _clean_required(channel, "channel"),
        "destination_id": _clean_required(destination_id, "destination_id"),
        "provider": str(provider or "").strip().lower(),
        "correlation_id": str(correlation_id or "").strip(),
        "expires_at": _clean_timestamp(expires_at, "expires_at"),
        "failure_policy": _choice(failure_policy, {"best_effort", "required"}, "failure_policy"),
        "repeat_policy": _choice(
            repeat_policy,
            {"every_occurrence", "first_per_correlation"},
            "repeat_policy",
        ),
    }
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if isinstance(retry_seconds, bool) or not isinstance(retry_seconds, int) or retry_seconds < 1:
        raise ValueError("retry_seconds must be a positive integer")
    path = db_path or DB_PATH
    ensure_schema(path)
    receipt_id = _receipt_id(
        values["notification_type"],
        values["occurrence_id"],
        values["channel"],
        values["destination_id"],
    )
    now = _utc_now_iso()
    with transaction(path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO memory_notification_deliveries (
                receipt_id, created_at, updated_at, notification_type,
                occurrence_id, correlation_id, channel, destination_id,
                provider, status, attempt_count, max_attempts, retry_seconds, expires_at,
                failure_policy, repeat_policy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                now,
                now,
                values["notification_type"],
                values["occurrence_id"],
                values["correlation_id"] or None,
                values["channel"],
                values["destination_id"],
                values["provider"],
                max_attempts,
                retry_seconds,
                values["expires_at"],
                values["failure_policy"],
                values["repeat_policy"],
            ),
        )
        created = cursor.rowcount == 1
        row = conn.execute(
            "SELECT * FROM memory_notification_deliveries WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None and values["repeat_policy"] == "first_per_correlation":
            row = conn.execute(
                """
                SELECT * FROM memory_notification_deliveries
                WHERE notification_type = ?
                  AND correlation_id = ?
                  AND channel = ?
                  AND destination_id = ?
                  AND repeat_policy = 'first_per_correlation'
                """,
                (
                    values["notification_type"],
                    values["correlation_id"],
                    values["channel"],
                    values["destination_id"],
                ),
            ).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to load notification delivery receipt {receipt_id}")
    return _row_to_delivery(row), created


def transition_notification_delivery(
    receipt_id: str,
    *,
    status: str,
    attempted: bool = False,
    next_attempt_at: str | None = None,
    last_error_class: str = "",
    last_error_code: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    clean_receipt_id = _clean_required(receipt_id, "receipt_id")
    clean_status = _choice(status, DELIVERY_STATUSES, "status")
    path = db_path or DB_PATH
    ensure_schema(path)
    now = _utc_now_iso()
    with transaction(path) as conn:
        current = conn.execute(
            "SELECT * FROM memory_notification_deliveries WHERE receipt_id = ?",
            (clean_receipt_id,),
        ).fetchone()
        if current is None:
            raise KeyError(f"Unknown notification delivery receipt {clean_receipt_id}")
        current_status = str(current["status"])
        if clean_status == current_status:
            return _row_to_delivery(current)
        if clean_status not in _ALLOWED_TRANSITIONS[current_status]:
            raise ValueError(
                f"Invalid notification delivery transition {current_status} -> {clean_status}"
            )
        attempt_count = int(current["attempt_count"]) + (1 if attempted else 0)
        if attempt_count > int(current["max_attempts"]):
            raise ValueError("notification delivery attempt count exceeds max_attempts")
        clean_next_attempt = (
            _clean_timestamp(next_attempt_at, "next_attempt_at")
            if next_attempt_at
            else None
        )
        if clean_status == "retry_wait" and clean_next_attempt is None:
            raise ValueError("retry_wait requires next_attempt_at")
        if clean_status != "retry_wait":
            clean_next_attempt = None
        accepted_at = now if clean_status == "accepted" else current["accepted_at"]
        completed_at = now if clean_status in TERMINAL_DELIVERY_STATUSES else None
        conn.execute(
            """
            UPDATE memory_notification_deliveries
            SET updated_at = ?, status = ?, attempt_count = ?,
                next_attempt_at = ?, accepted_at = ?, completed_at = ?,
                last_error_class = ?, last_error_code = ?
            WHERE receipt_id = ?
            """,
            (
                now,
                clean_status,
                attempt_count,
                clean_next_attempt,
                accepted_at,
                completed_at,
                _clean_error(last_error_class),
                _clean_error(last_error_code),
                clean_receipt_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM memory_notification_deliveries WHERE receipt_id = ?",
            (clean_receipt_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to reload notification delivery receipt {clean_receipt_id}")
    return _row_to_delivery(row)


def get_notification_delivery(
    receipt_id: str,
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_notification_deliveries WHERE receipt_id = ?",
            (str(receipt_id or "").strip(),),
        ).fetchone()
    return _row_to_delivery(row) if row else None


def list_notification_deliveries(
    query: NotificationDeliveryQuery | None = None,
    *,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    resolved = query or NotificationDeliveryQuery()
    clauses: list[str] = []
    args: list[Any] = []
    for column in (
        "notification_type",
        "occurrence_id",
        "correlation_id",
        "channel",
        "destination_id",
        "provider",
        "status",
    ):
        value = getattr(resolved, column)
        if value is None:
            continue
        clauses.append(f"{column} = ?")
        args.append(str(value).strip())
    limit = max(1, min(int(resolved.limit), 500))
    offset = max(0, int(resolved.offset))
    sql = "SELECT * FROM memory_notification_deliveries"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, receipt_id DESC LIMIT ? OFFSET ?"
    args.extend((limit, offset))
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_delivery(row) for row in rows]


def summarize_notification_deliveries(
    *,
    channel: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clauses: list[str] = []
    args: list[Any] = []
    if channel is not None:
        clauses.append("channel = ?")
        args.append(str(channel).strip())
    sql = "SELECT status, COUNT(*) AS count FROM memory_notification_deliveries"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " GROUP BY status ORDER BY status"
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    by_status = {str(row["status"]): int(row["count"]) for row in rows}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
    }


def list_due_notification_deliveries(
    *,
    now: str,
    channel: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    clean_now = _clean_timestamp(now, "now")
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        channel_clause = "" if channel is None else " AND channel = ?"
        args: list[Any] = [clean_now, clean_now]
        if channel is not None:
            args.append(str(channel).strip())
        args.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"""
            SELECT * FROM memory_notification_deliveries
            WHERE status IN ('pending', 'retry_wait')
              AND expires_at > ?
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              {channel_clause}
            ORDER BY COALESCE(next_attempt_at, created_at), receipt_id
            LIMIT ?
            """,
            args,
        ).fetchall()
    return [_row_to_delivery(row) for row in rows]


def list_expired_notification_deliveries(
    *,
    now: str,
    channel: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    clean_now = _clean_timestamp(now, "now")
    channel_clause = "" if channel is None else " AND channel = ?"
    args: list[Any] = [clean_now]
    if channel is not None:
        args.append(str(channel).strip())
    args.append(max(1, min(int(limit), 500)))
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM memory_notification_deliveries
            WHERE status IN ('pending', 'retry_wait')
              AND expires_at <= ?
              {channel_clause}
            ORDER BY expires_at, receipt_id
            LIMIT ?
            """,
            args,
        ).fetchall()
    return [_row_to_delivery(row) for row in rows]


def _receipt_id(
    notification_type: str,
    occurrence_id: str,
    channel: str,
    destination_id: str,
) -> str:
    identity = "\x1f".join((notification_type, occurrence_id, channel, destination_id))
    return f"delivery-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _row_to_delivery(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _clean_required(value: str, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field} is required")
    return clean


def _choice(value: str, allowed: set[str], field: str) -> str:
    clean = _clean_required(value, field).lower()
    if clean not in allowed:
        raise ValueError(f"Unsupported {field}: {value!r}")
    return clean


def _clean_timestamp(value: str, field: str) -> str:
    clean = _clean_required(value, field)
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _clean_error(value: str) -> str:
    return str(value or "").strip()[:160]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
