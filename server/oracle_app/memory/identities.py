from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_user(
    *,
    user_id: str,
    display_name: str,
    status: str = "active",
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if status not in {"active", "disabled", "retired"}:
        raise ValueError(f"Unknown Oracle Memory user status: {status!r}")
    path = db_path or DB_PATH
    ensure_schema(path)
    now = utc_now_iso()
    resolved_user_id = str(user_id).strip()
    if not resolved_user_id:
        raise ValueError("Oracle Memory user_id is required")
    payload_json = json.dumps(payload or {}, sort_keys=True)
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_users (
                user_id, created_at, updated_at, display_name, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                display_name = excluded.display_name,
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (resolved_user_id, now, now, display_name, status, payload_json),
        )
    user = get_user(resolved_user_id, db_path=path)
    if user is None:
        raise RuntimeError(f"Failed to load Oracle Memory user {resolved_user_id}")
    return user


def get_user(user_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM memory_users WHERE user_id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def list_users(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    path = db_path or DB_PATH
    ensure_schema(path)
    with transaction(path) as conn:
        rows = conn.execute("SELECT * FROM memory_users ORDER BY created_at, user_id").fetchall()
    return [_row_to_user(row) for row in rows]


def _row_to_user(row: Any) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "display_name": row["display_name"],
        "status": row["status"],
        "payload": json.loads(row["payload_json"] or "{}"),
    }
