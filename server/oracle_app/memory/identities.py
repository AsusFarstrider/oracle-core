from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import ensure_schema
from .store import DB_PATH, transaction


VALID_ROLES = {"admin", "adult", "child", "system", "guest"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_user(
    *,
    user_id: str | None = None,
    display_name: str,
    role: str,
    status: str = "active",
    payload: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown Oracle Memory user role: {role!r}")
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    now = utc_now_iso()
    resolved_user_id = user_id or uuid.uuid4().hex
    payload_json = json.dumps(payload or {}, sort_keys=True)
    with transaction(path) as conn:
        conn.execute(
            """
            INSERT INTO memory_users (
                user_id, created_at, updated_at, role, display_name, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                role = excluded.role,
                display_name = excluded.display_name,
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (resolved_user_id, now, now, role, display_name, status, payload_json),
        )
    user = get_user(resolved_user_id, db_path=path)
    if user is None:
        raise RuntimeError(f"Failed to load Oracle Memory user {resolved_user_id}")
    return user


def get_user(user_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        row = conn.execute("SELECT * FROM memory_users WHERE user_id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def list_users(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    path = db_path or DB_PATH
    ensure_schema(path, copy_provisional_suggestions=False)
    with transaction(path) as conn:
        rows = conn.execute("SELECT * FROM memory_users ORDER BY created_at, user_id").fetchall()
    return [_row_to_user(row) for row in rows]


def _row_to_user(row: Any) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "role": row["role"],
        "display_name": row["display_name"],
        "status": row["status"],
        "payload": json.loads(row["payload_json"] or "{}"),
    }
