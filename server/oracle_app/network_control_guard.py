from __future__ import annotations

import time
from datetime import datetime, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4


_LOCK = Lock()
_ACTIVE: dict[str, Any] | None = None
_COOLDOWNS: dict[tuple[str, str], dict[str, Any]] = {}


def clear_network_control_guard() -> None:
    global _ACTIVE
    with _LOCK:
        _ACTIVE = None
        _COOLDOWNS.clear()


def network_control_cooldown_seconds(action_policy: dict[str, Any]) -> int:
    execution = action_policy.get("execution") if isinstance(action_policy.get("execution"), dict) else {}
    configured = execution.get("cooldown_seconds")
    if configured not in (None, ""):
        try:
            return max(0, min(int(configured), 3600))
        except (TypeError, ValueError):
            return 0
    action_id = str(action_policy.get("action_id") or "").strip()
    if action_id in {"restart_host", "restart_router", "power_cycle"}:
        return 300
    return 60


def acquire_network_control(
    *,
    target_type: str,
    target_id: str,
    action_id: str,
) -> dict[str, Any]:
    global _ACTIVE
    with _LOCK:
        now = time.monotonic()
        _prune_cooldowns_locked(now)
        state = _availability_locked(
            target_type=target_type,
            target_id=target_id,
            action_id=action_id,
            now=now,
        )
        if state["status"] != "ready":
            return {"acquired": False, "state": state}
        token = str(uuid4())
        _ACTIVE = {
            "token": token,
            "target_type": target_type,
            "target_id": target_id,
            "action_id": action_id,
            "started_at": datetime.now().astimezone().isoformat(),
        }
        return {
            "acquired": True,
            "token": token,
            "state": _availability_locked(
                target_type=target_type,
                target_id=target_id,
                action_id=action_id,
                now=now,
            ),
        }


def release_network_control(*, token: str, cooldown_seconds: int) -> dict[str, Any]:
    global _ACTIVE
    with _LOCK:
        if not isinstance(_ACTIVE, dict) or str(_ACTIVE.get("token") or "") != token:
            return {"status": "ready"}
        target_type = str(_ACTIVE.get("target_type") or "")
        target_id = str(_ACTIVE.get("target_id") or "")
        action_id = str(_ACTIVE.get("action_id") or "")
        _ACTIVE = None
        if cooldown_seconds > 0:
            now = time.monotonic()
            _COOLDOWNS[(target_type, target_id)] = {
                "expires_monotonic": now + cooldown_seconds,
                "cooldown_until": (datetime.now().astimezone() + timedelta(seconds=cooldown_seconds)).isoformat(),
            }
        now = time.monotonic()
        return _availability_locked(
            target_type=target_type,
            target_id=target_id,
            action_id=action_id,
            now=now,
        )


def get_network_control_availability(
    *,
    target_type: str,
    target_id: str,
    action_id: str,
) -> dict[str, Any]:
    with _LOCK:
        now = time.monotonic()
        _prune_cooldowns_locked(now)
        return _availability_locked(
            target_type=target_type,
            target_id=target_id,
            action_id=action_id,
            now=now,
        )


def get_network_control_availability_for_policy(
    control_policy: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    availability: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in control_policy.get("actions") or []:
        if not isinstance(raw, dict):
            continue
        target_type = str(raw.get("target_type") or "").strip().lower()
        target_id = str(raw.get("target_id") or "").strip()
        action_id = str(raw.get("action_id") or "").strip()
        if target_type and target_id and action_id:
            availability[(target_type, target_id, action_id)] = get_network_control_availability(
                target_type=target_type,
                target_id=target_id,
                action_id=action_id,
            )
    return availability


def _availability_locked(
    *,
    target_type: str,
    target_id: str,
    action_id: str,
    now: float,
) -> dict[str, Any]:
    if isinstance(_ACTIVE, dict):
        same_action = (
            str(_ACTIVE.get("target_type") or "") == target_type
            and str(_ACTIVE.get("target_id") or "") == target_id
            and str(_ACTIVE.get("action_id") or "") == action_id
        )
        return {
            "status": "in_progress" if same_action else "blocked_by_active",
            "active_target_type": str(_ACTIVE.get("target_type") or ""),
            "active_target_id": str(_ACTIVE.get("target_id") or ""),
            "active_action_id": str(_ACTIVE.get("action_id") or ""),
            "active_started_at": str(_ACTIVE.get("started_at") or ""),
        }
    cooldown = _COOLDOWNS.get((target_type, target_id))
    if isinstance(cooldown, dict):
        remaining = max(1, int(float(cooldown.get("expires_monotonic") or now) - now + 0.999))
        return {
            "status": "cooldown",
            "cooldown_remaining_seconds": remaining,
            "cooldown_until": str(cooldown.get("cooldown_until") or ""),
        }
    return {"status": "ready"}


def _prune_cooldowns_locked(now: float) -> None:
    expired = [
        key
        for key, value in _COOLDOWNS.items()
        if float(value.get("expires_monotonic") or 0) <= now
    ]
    for key in expired:
        _COOLDOWNS.pop(key, None)
