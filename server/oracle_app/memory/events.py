from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction
from .taxonomy import category_for_event_type


CORE_EVENT_COLUMNS = {
    "event_id",
    "created_at",
    "observed_at",
    "event_type",
    "category",
    "severity",
    "source_id",
    "session_id",
    "correlation_id",
    "user_id",
    "provider",
    "domain",
    "status",
    "payload_json",
}


@dataclass(frozen=True)
class EventQuery:
    event_type: str | None = None
    source_id: str | None = None
    correlation_id: str | None = None
    severity: str | None = None
    status: str | None = None
    domain: str | None = None
    observed_after: str | None = None
    observed_before: str | None = None
    limit: int = 100
    offset: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(
    event_type: str,
    *,
    severity: str = "info",
    observed_at: str | None = None,
    source_id: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
    user_id: str | None = None,
    provider: str | None = None,
    domain: str | None = None,
    status: str | None = None,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    category = category_for_event_type(event_type)
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    resolved_event_id = event_id or uuid.uuid4().hex
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_events (
                event_id, created_at, observed_at, event_type, category, severity,
                source_id, session_id, correlation_id, user_id, provider, domain,
                status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_event_id,
                now,
                observed_at or now,
                event_type,
                category,
                severity,
                source_id,
                session_id,
                correlation_id,
                user_id,
                provider,
                domain,
                status,
                json.dumps(payload or {}, sort_keys=True),
            ),
        )
    event = get_event(resolved_event_id, db_path=path)
    if event is None:
        raise RuntimeError(f"Failed to load Oracle Memory event {resolved_event_id}")
    return event


def get_event(event_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM memory_events WHERE event_id = ?", (event_id,)).fetchone()
    return _row_to_event(row) if row else None


def query_events(query: EventQuery | None = None, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    query = query or EventQuery()
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    sql = "SELECT * FROM memory_events"
    args: list[str] = []
    where: list[str] = []
    filters = {
        "event_type": _clean_filter(query.event_type),
        "source_id": _clean_filter(query.source_id),
        "correlation_id": _clean_filter(query.correlation_id),
        "severity": _clean_filter(query.severity).lower() if _clean_filter(query.severity) else None,
        "status": _clean_filter(query.status),
        "domain": _clean_filter(query.domain),
    }
    for column, value in filters.items():
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    observed_after = _clean_filter(query.observed_after)
    if observed_after:
        where.append("observed_at >= ?")
        args.append(observed_after)
    observed_before = _clean_filter(query.observed_before)
    if observed_before:
        where.append("observed_at <= ?")
        args.append(observed_before)
    if where:
        sql += " WHERE " + " AND ".join(where)
    limit = _clamp_limit(query.limit)
    offset = max(0, int(query.offset or 0))
    sql += " ORDER BY observed_at DESC, event_id DESC LIMIT ? OFFSET ?"
    args.extend([str(limit), str(offset)])
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_event(row) for row in rows]


def recent_events(*, limit: int = 100, db_path: Path | None = None) -> list[dict[str, Any]]:
    return query_events(EventQuery(limit=limit), db_path=db_path)


def list_events(*, db_path: Path | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
    return query_events(EventQuery(event_type=event_type), db_path=db_path)


def _clean_filter(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _row_to_event(row: Any) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "created_at": row["created_at"],
        "observed_at": row["observed_at"],
        "event_type": row["event_type"],
        "category": row["category"],
        "severity": row["severity"],
        "source_id": row["source_id"],
        "session_id": row["session_id"],
        "correlation_id": row["correlation_id"],
        "user_id": row["user_id"],
        "provider": row["provider"],
        "domain": row["domain"],
        "status": row["status"],
        "payload": _parse_payload_json(row["payload_json"]),
    }


def _parse_payload_json(value: str | None) -> dict[str, Any]:
    raw = value or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    if not isinstance(parsed, dict):
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    return parsed
