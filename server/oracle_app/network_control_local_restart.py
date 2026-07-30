from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .constants import NETWORK_LOCAL_RESTART_STATE_PATH
from .memory.events import record_event
from .network_control_results import build_network_control_audit_payload, record_network_control_result
from .provider_bridges.service_control import check_host_readiness


_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
logger = logging.getLogger("oracle-brain.network_control_local_restart")


def stage_pending_local_host_restart(
    *,
    control_context: dict[str, Any],
    host_id: str,
    readiness_timeout_seconds: int,
    recovery_poll_seconds: int,
    lifecycle_status: str,
    state_path: Path = NETWORK_LOCAL_RESTART_STATE_PATH,
    boot_id_path: Path = _BOOT_ID_PATH,
) -> dict[str, Any]:
    request_id = str(control_context.get("request_id") or "").strip()
    boot_id = _read_boot_id(boot_id_path)
    if not request_id or not boot_id:
        return {
            "ok": False,
            "error": "network_control_local_restart_state_unavailable",
            "detail": "Oracle could not persist local restart recovery state.",
        }
    payload = {
        "version": 1,
        "request_id": request_id,
        "requested_at": str(control_context.get("requested_at") or "").strip(),
        "actor": str(control_context.get("actor") or "").strip(),
        "source": str(control_context.get("source") or "").strip(),
        "reason": str(control_context.get("reason") or "").strip(),
        "target_type": "host",
        "target_id": host_id,
        "action_id": "restart_host",
        "provider": "service_control",
        "adapter": "service_control",
        "boot_id_before": boot_id,
        "staged_at": datetime.now().astimezone().isoformat(),
        "readiness_timeout_seconds": max(15, min(300, int(readiness_timeout_seconds))),
        "recovery_poll_seconds": max(2, min(30, int(recovery_poll_seconds))),
        "lifecycle_status": lifecycle_status,
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(state_path)
    except OSError:
        return {
            "ok": False,
            "error": "network_control_local_restart_state_unavailable",
            "detail": "Oracle could not persist local restart recovery state.",
        }
    return {"ok": True, "status": "staged"}


def clear_pending_local_host_restart(
    *,
    state_path: Path = NETWORK_LOCAL_RESTART_STATE_PATH,
) -> None:
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("network_control_local_restart_state_clear_failed")


def complete_pending_local_host_restart(
    *,
    service_control_settings: dict[str, Any] | None = None,
    canonical_execution=None,
    canonical_authority: bool = False,
    state_path: Path = NETWORK_LOCAL_RESTART_STATE_PATH,
    boot_id_path: Path = _BOOT_ID_PATH,
    db_path: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    pending = _load_pending(state_path)
    if not pending:
        return {"status": "none"}
    boot_id_after = _read_boot_id(boot_id_path)
    if not boot_id_after:
        return {"status": "pending", "reason": "boot_id_unavailable"}
    if boot_id_after == str(pending.get("boot_id_before") or ""):
        return {"status": "pending", "reason": "boot_not_changed"}
    if canonical_authority and canonical_execution is None:
        return {"status": "pending", "reason": "canonical_network_unavailable"}

    host_id = str(pending.get("target_id") or "").strip()
    timeout_seconds = max(15, min(300, int(pending.get("readiness_timeout_seconds") or 120)))
    poll_seconds = max(2, min(30, int(pending.get("recovery_poll_seconds") or 5)))
    deadline = time.monotonic() + timeout_seconds
    readiness: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if canonical_execution is not None:
            from .network_runtime.service_control import TypedServiceControl

            action = canonical_execution.policy.action_for(
                target_type="host",
                target_id=host_id,
                operation="restart_host",
            )
            readiness = (
                TypedServiceControl(canonical_execution.adapters).check_readiness(
                    action.adapter,
                    timeout_seconds=min(15, poll_seconds + 5),
                )
                if action is not None
                else {"ok": False, "check_count": 0, "passed_count": 0, "failed_check_ids": []}
            )
        else:
            readiness = check_host_readiness(
                settings=service_control_settings or {},
                host_id=host_id,
                timeout_seconds=min(15, poll_seconds + 5),
            )
        if readiness.get("ok") is True:
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            sleep(min(poll_seconds, remaining))

    passed = readiness.get("ok") is True
    failed_check_ids = [
        str(item).strip()
        for item in readiness.get("failed_check_ids") or []
        if str(item).strip()
    ]
    control = {
        "request_id": str(pending.get("request_id") or "").strip(),
        "requested_at": str(pending.get("requested_at") or "").strip(),
        "actor": str(pending.get("actor") or "").strip(),
        "source": str(pending.get("source") or "").strip(),
        "target_type": "host",
        "target_id": host_id,
        "action_id": "restart_host",
        "mode": "execute",
        "provider": "service_control",
        "adapter": "service_control",
        "policy_status": "allowed",
        "confirmation_status": "confirmed",
        "result_status": "executed" if passed else "failed",
        "error_class": "" if passed else "network_control_local_host_readiness_failed",
        "summary": (
            "Oracle Server restarted and passed startup readiness checks."
            if passed
            else "Oracle Server restarted, but startup readiness checks did not pass."
        ),
        "execution": {
            "adapter": "service_control",
            "deferred": False,
            "local_restart_completed": True,
            "boot_changed": True,
            "verification_status": "passed" if passed else "failed",
            "readiness_status": "passed" if passed else "failed",
            "readiness_check_count": int(readiness.get("check_count") or 0),
            "readiness_passed_count": int(readiness.get("passed_count") or 0),
            "readiness_failed_check_ids": failed_check_ids,
            "readiness_timeout_seconds": timeout_seconds,
            "recovery_poll_seconds": poll_seconds,
            "lifecycle_status": (
                "passed"
                if passed and str(pending.get("lifecycle_status") or "") == "prepared"
                else str(pending.get("lifecycle_status") or "not_required")
            ),
            "availability_status": "ready",
        },
        "steps": [
            {
                "id": "local_host_reboot_observed",
                "kind": "verification",
                "summary": "Oracle observed a new Linux boot identity after the local restart request.",
            },
            {
                "id": "local_host_readiness_verified" if passed else "local_host_readiness_failed",
                "kind": "verification",
                "summary": (
                    f"All {readiness.get('check_count', 0)} configured readiness checks passed."
                    if passed
                    else (
                        f"Startup readiness checks did not pass: {', '.join(failed_check_ids)}."
                        if failed_check_ids
                        else "Startup readiness checks did not pass."
                    )
                ),
            },
        ],
    }
    record_event(
        "network_control_confirm",
        severity="info" if passed else "error",
        source_id="brain",
        correlation_id=control["request_id"],
        provider="service_control",
        domain="network_control",
        status=control["result_status"],
        payload=build_network_control_audit_payload(control),
        db_path=db_path,
    )
    record_network_control_result(control)
    clear_pending_local_host_restart(state_path=state_path)
    return {
        "status": "completed" if passed else "failed",
        "request_id": control["request_id"],
        "readiness_check_count": control["execution"]["readiness_check_count"],
        "readiness_passed_count": control["execution"]["readiness_passed_count"],
    }


def safe_complete_pending_local_host_restart(
    *,
    service_control_settings: dict[str, Any] | None = None,
    canonical_execution=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    try:
        return complete_pending_local_host_restart(
            service_control_settings=service_control_settings,
            canonical_execution=canonical_execution,
            canonical_authority=canonical_authority,
        )
    except Exception as exc:
        logger.warning("network_control_local_restart_completion_failed detail=%s", exc)
        return {"status": "error"}


def _load_pending(state_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_boot_id(boot_id_path: Path) -> str:
    try:
        return boot_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
