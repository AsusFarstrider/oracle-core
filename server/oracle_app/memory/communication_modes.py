from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .schema import ensure_schema
from .store import DB_PATH, transaction


@dataclass(frozen=True)
class CommunicationModeState:
    mode_id: str
    active: bool
    activated_at: datetime | None
    expires_at: datetime | None
    updated_at: datetime
    actor_source_id: str | None


def read_mode_state(
    mode_id: str, *, now: datetime, db_path: Path | None = None
) -> CommunicationModeState:
    path = db_path or DB_PATH
    ensure_schema(path)
    clock = _utc(now)
    with transaction(path) as conn:
        row = conn.execute(
            "SELECT * FROM memory_communication_modes WHERE mode_id = ?", (mode_id,)
        ).fetchone()
        if row is None:
            return CommunicationModeState(mode_id, False, None, None, clock, None)
        expires_at = _parse(row["expires_at"])
        if bool(row["active"]) and expires_at is not None and expires_at <= clock:
            conn.execute(
                "UPDATE memory_communication_modes SET active = 0, expires_at = NULL, updated_at = ? WHERE mode_id = ?",
                (clock.isoformat(), mode_id),
            )
            return CommunicationModeState(mode_id, False, _parse(row["activated_at"]), None, clock, row["actor_source_id"])
        return CommunicationModeState(
            mode_id, bool(row["active"]), _parse(row["activated_at"]), expires_at,
            _parse(row["updated_at"]) or clock, row["actor_source_id"],
        )


def set_mode_state(
    mode_id: str,
    *,
    active: bool,
    now: datetime,
    expires_at: datetime | None = None,
    actor_source_id: str | None = None,
    db_path: Path | None = None,
) -> CommunicationModeState:
    path = db_path or DB_PATH
    ensure_schema(path)
    clock = _utc(now)
    expiry = None if expires_at is None else _utc(expires_at)
    if not active:
        expiry = None
    if expiry is not None and expiry <= clock:
        raise ValueError("DND expiry must be in the future.")
    with transaction(path) as conn:
        conn.execute(
            """INSERT INTO memory_communication_modes
               (mode_id, active, activated_at, expires_at, updated_at, actor_source_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(mode_id) DO UPDATE SET
                 active=excluded.active, activated_at=excluded.activated_at,
                 expires_at=excluded.expires_at, updated_at=excluded.updated_at,
                 actor_source_id=excluded.actor_source_id""",
            (
                mode_id, int(active), clock.isoformat() if active else None,
                expiry.isoformat() if expiry else None, clock.isoformat(), actor_source_id,
            ),
        )
    return read_mode_state(mode_id, now=clock, db_path=path)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Communication-mode timestamps must be timezone-aware.")
    return value.astimezone(UTC)


def _parse(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value).astimezone(UTC)
