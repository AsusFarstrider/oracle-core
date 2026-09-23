from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oracle_app.memory.communication_modes import read_mode_state, set_mode_state


def test_dnd_state_is_durable_and_expires_at_the_recorded_instant(tmp_path) -> None:
    path = tmp_path / "memory.sqlite3"
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    expiry = now + timedelta(minutes=10)

    active = set_mode_state("household_dnd", active=True, now=now, expires_at=expiry, db_path=path)
    assert active.active is True
    assert active.expires_at == expiry
    assert read_mode_state("household_dnd", now=now + timedelta(minutes=9), db_path=path).active is True

    expired = read_mode_state("household_dnd", now=expiry, db_path=path)
    assert expired.active is False
    assert expired.expires_at is None
    assert read_mode_state("household_dnd", now=expiry + timedelta(minutes=1), db_path=path).active is False
