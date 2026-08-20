from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


logger = logging.getLogger("oracle-brain.memory.sessions")

VALID_SESSION_MODES = {"conversation", "ui", "api", "system", "background"}


@dataclass(frozen=True)
class SessionQuery:
    session_id: str | None = None
    correlation_id: str | None = None
    source_id: str | None = None
    user_id: str | None = None
    mode: str | None = None
    final_status: str | None = None
    started_after: str | None = None
    started_before: str | None = None
    limit: int = 100
    offset: int = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_record_session(**kwargs: Any) -> bool:
    try:
        record_session(**kwargs)
    except Exception as exc:
        logger.warning("memory_session_write_failed session_id=%s detail=%s", kwargs.get("session_id") or "-", exc)
        return False
    return True


def safe_update_session_status(session_id: str, **kwargs: Any) -> bool:
    try:
        update_session_status(session_id, **kwargs)
    except Exception as exc:
        logger.warning("memory_session_update_failed session_id=%s detail=%s", session_id or "-", exc)
        return False
    return True


def record_session(
    *,
    session_id: str,
    mode: str,
    started_at: str | None = None,
    ended_at: str | None = None,
    final_status: str | None = None,
    correlation_id: str | None = None,
    source_id: str | None = None,
    user_id: str | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clean_session_id = _clean_required(session_id, "session_id")
    clean_mode = _clean_required(mode, "mode")
    if clean_mode not in VALID_SESSION_MODES:
        raise ValueError(f"Unknown Oracle Memory session mode: {mode!r}")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    resolved_started_at = started_at or now
    resolved_source_id = _existing_reference("memory_sources", "source_id", source_id, db_path=path)
    resolved_user_id = _existing_reference("memory_users", "user_id", user_id, db_path=path)
    payload_json = json.dumps(payload or {}, sort_keys=True)
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_sessions (
                session_id, created_at, updated_at, correlation_id, source_id, user_id,
                mode, started_at, ended_at, final_status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                correlation_id = excluded.correlation_id,
                source_id = excluded.source_id,
                user_id = excluded.user_id,
                mode = excluded.mode,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                final_status = excluded.final_status,
                payload_json = excluded.payload_json
            """,
            (
                clean_session_id,
                now,
                now,
                _clean_filter(correlation_id),
                resolved_source_id,
                resolved_user_id,
                clean_mode,
                resolved_started_at,
                ended_at,
                _clean_filter(final_status),
                payload_json,
            ),
        )
    session = get_session(clean_session_id, db_path=path)
    if session is None:
        raise RuntimeError(f"Failed to load Oracle Memory session {clean_session_id}")
    return session


def update_session_status(
    session_id: str,
    *,
    ended_at: str | None = None,
    final_status: str | None = None,
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    clean_session_id = _clean_required(session_id, "session_id")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    existing = get_session(clean_session_id, db_path=path)
    if existing is None:
        return None
    now = utc_now_iso()
    merged_payload = dict(existing.get("payload") or {})
    if payload:
        merged_payload.update(payload)
    with transaction(path) as conn:
        conn.execute(
            """
            UPDATE memory_sessions
            SET updated_at = ?, ended_at = COALESCE(?, ended_at),
                final_status = COALESCE(?, final_status), payload_json = ?
            WHERE session_id = ?
            """,
            (
                now,
                ended_at,
                _clean_filter(final_status),
                json.dumps(merged_payload, sort_keys=True),
                clean_session_id,
            ),
        )
    return get_session(clean_session_id, db_path=path)


def get_session(session_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_sessions WHERE session_id = ?",
            (_clean_required(session_id, "session_id"),),
        ).fetchone()
    return _row_to_session(row) if row else None


def query_sessions(query: SessionQuery | None = None, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    query = query or SessionQuery()
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    sql = "SELECT * FROM memory_sessions"
    args: list[Any] = []
    where: list[str] = []
    filters = {
        "session_id": _clean_filter(query.session_id),
        "correlation_id": _clean_filter(query.correlation_id),
        "source_id": _clean_filter(query.source_id),
        "user_id": _clean_filter(query.user_id),
        "mode": _clean_filter(query.mode),
        "final_status": _clean_filter(query.final_status),
    }
    if filters["mode"] and filters["mode"] not in VALID_SESSION_MODES:
        raise ValueError(f"Unknown Oracle Memory session mode: {query.mode!r}")
    for column, value in filters.items():
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    started_after = _clean_filter(query.started_after)
    if started_after:
        where.append("started_at >= ?")
        args.append(started_after)
    started_before = _clean_filter(query.started_before)
    if started_before:
        where.append("started_at <= ?")
        args.append(started_before)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY started_at DESC, session_id DESC LIMIT ? OFFSET ?"
    args.extend([_clamp_limit(query.limit), _clean_offset(query.offset)])
    with transaction(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_session(row) for row in rows]


def recent_sessions(*, limit: int = 100, db_path: Path | None = None) -> list[dict[str, Any]]:
    return query_sessions(SessionQuery(limit=limit), db_path=db_path)


def _row_to_session(row: Any) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "correlation_id": row["correlation_id"],
        "source_id": row["source_id"],
        "user_id": row["user_id"],
        "mode": row["mode"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "final_status": row["final_status"],
        "payload": _parse_payload_json(row["payload_json"]),
    }


def _clean_required(value: str | None, field_name: str) -> str:
    cleaned = _clean_filter(value)
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _clean_filter(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clamp_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = 100
    return min(500, max(1, parsed))


def _clean_offset(offset: int) -> int:
    try:
        return max(0, int(offset or 0))
    except (TypeError, ValueError):
        return 0


def _existing_reference(table: str, column: str, value: str | None, *, db_path: Path) -> str | None:
    cleaned = _clean_filter(value)
    if not cleaned:
        return None
    with transaction(db_path) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?",
            (cleaned,),
        ).fetchone()
    return cleaned if row else None


def _parse_payload_json(value: str | None) -> dict[str, Any]:
    raw = value or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    if not isinstance(parsed, dict):
        return {"_payload_parse_error": True, "raw_payload_json": raw}
    return parsed
