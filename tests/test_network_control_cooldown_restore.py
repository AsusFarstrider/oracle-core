from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from oracle_app.memory.events import record_event
from oracle_app.admin_network_routes import admin_network_control_confirm_canonical
from oracle_app.memory.store import transaction
from oracle_app.network_control_guard import (
    acquire_network_control,
    clear_network_control_guard,
    get_network_control_availability,
    network_control_cooldown_seconds,
)
from oracle_app.network_control_results import (
    restore_network_control_cooldowns_from_memory,
    safe_restore_network_control_cooldowns_from_memory,
)


def _attempt(path: Path, *, seconds: int, target: str = "edge", event_type: str = "network_control_started") -> str:
    event = record_event(
        event_type, domain="network_control", status="in_progress" if event_type == "network_control_started" else "executed",
        payload={
            "request_id": "attempt-" + target, "target_type": "host", "target_id": target,
            "action_id": "restart_host", "confirmation_status": "confirmed",
            "result_status": "in_progress" if event_type == "network_control_started" else "executed",
            "execution": {"cooldown_seconds": seconds},
        }, db_path=path,
    )
    return str(event["event_id"])


def test_default_network_cooldowns_match_ratified_service_and_disruptive_bounds() -> None:
    assert network_control_cooldown_seconds({"action_id": "restart_service", "execution": {"cooldown_seconds": None}}) == 60
    assert network_control_cooldown_seconds({"action_id": "restart_host", "execution": {"cooldown_seconds": None}}) == 300
    assert network_control_cooldown_seconds({"action_id": "power_cycle", "execution": {"cooldown_seconds": 0}}) == 0


def test_restart_restores_only_unexpired_target_cooldown_not_lease_or_approval(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    _attempt(path, seconds=300)
    clear_network_control_guard()
    assert restore_network_control_cooldowns_from_memory(db_path=path) == 1
    state = get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host")
    assert state["status"] == "cooldown" and 0 < state["cooldown_remaining_seconds"] <= 300
    assert acquire_network_control(target_type="host", target_id="edge", action_id="restart_host")["acquired"] is False
    assert get_network_control_availability(target_type="host", target_id="other", action_id="restart_host") == {"status": "ready"}
    clear_network_control_guard()


def test_expired_and_zero_cooldowns_are_not_reconstructed(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    _attempt(path, seconds=60, event_type="network_control_confirm")
    _attempt(path, seconds=0, target="zero", event_type="network_control_confirm")
    clear_network_control_guard()
    assert restore_network_control_cooldowns_from_memory(db_path=path, now=datetime.now(timezone.utc) + timedelta(seconds=61)) == 0
    assert get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host") == {"status": "ready"}
    clear_network_control_guard()


def test_interrupted_start_uses_durable_attempt_time_not_startup_time(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    _attempt(path, seconds=300)
    record_event(
        "network_control_confirm", domain="network_control", status="interrupted",
        payload={
            "request_id": "attempt-edge", "target_type": "host", "target_id": "edge",
            "action_id": "restart_host", "result_status": "interrupted",
            "error_class": "network_control_interrupted_by_restart", "execution": {},
        }, db_path=path,
    )
    clear_network_control_guard()
    assert restore_network_control_cooldowns_from_memory(db_path=path, now=datetime.now(timezone.utc) + timedelta(seconds=301)) == 0
    state = get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host")
    assert state == {"status": "ready"}
    clear_network_control_guard()


def test_missing_audit_database_fails_closed(tmp_path: Path) -> None:
    clear_network_control_guard()
    assert safe_restore_network_control_cooldowns_from_memory(db_path=tmp_path / "missing.sqlite3") == 0
    assert get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host") == {"status": "audit_unavailable"}
    clear_network_control_guard()


def test_future_clock_timestamp_cannot_extend_lockout_beyond_configured_window(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    event_id = _attempt(path, seconds=60)
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    with transaction(path) as conn:
        conn.execute("UPDATE memory_events SET created_at = ? WHERE event_id = ?", (future, event_id))
    clear_network_control_guard()
    assert restore_network_control_cooldowns_from_memory(db_path=path) == 1
    state = get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host")
    assert state["status"] == "cooldown" and state["cooldown_remaining_seconds"] <= 60
    clear_network_control_guard()


def test_corrupt_recent_audit_fails_closed_without_restoring_execution_claim(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    event_id = _attempt(path, seconds=60)
    with transaction(path) as conn:
        conn.execute("UPDATE memory_events SET payload_json = ? WHERE event_id = ?", ("not-json", event_id))
    clear_network_control_guard()
    assert safe_restore_network_control_cooldowns_from_memory(db_path=path) == 0
    state = get_network_control_availability(target_type="host", target_id="edge", action_id="restart_host")
    assert state == {"status": "audit_unavailable"}
    assert acquire_network_control(target_type="host", target_id="edge", action_id="restart_host")["acquired"] is False
    clear_network_control_guard()


def test_incomplete_recent_attempt_audit_is_not_mistaken_for_no_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    record_event("network_control_started", domain="network_control", payload={"request_id": "orphan", "target_type": "host"}, db_path=path)
    clear_network_control_guard()
    with pytest.raises(ValueError, match="identity"):
        restore_network_control_cooldowns_from_memory(db_path=path)
    clear_network_control_guard()


def test_network_action_is_never_sent_when_durable_start_audit_fails() -> None:
    class FakeExecution:
        policy = SimpleNamespace(action_for=lambda **kwargs: SimpleNamespace(definition=SimpleNamespace(operation="restart_service", execution=SimpleNamespace(cooldown_seconds=None))))

        def control_confirm(self, payload, result=None):
            base = {"allowed": True, "confirmation_status": "confirmed", "result_status": "not_implemented", "request_id": "request-1", "requested_at": datetime.now(timezone.utc).isoformat(), "target_type": "service", "target_id": "dns", "action_id": "restart_service", "provider": "service_control"}
            return base if result is None else {**base, **result}

        def execute_control(self, payload, context):
            pytest.fail("provider action was sent without durable start audit")

    clear_network_control_guard()
    with patch("oracle_app.admin_network_routes.safe_record_event", return_value=False):
        control = admin_network_control_confirm_canonical(FakeExecution(), {"confirmed": True})["control"]
    assert control["result_status"] == "blocked"
    assert control["error_class"] == "network_control_audit_unavailable"
    assert get_network_control_availability(target_type="service", target_id="dns", action_id="restart_service") == {"status": "ready"}
    clear_network_control_guard()
