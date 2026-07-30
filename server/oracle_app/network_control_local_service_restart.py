from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH
from .memory.events import record_event
from .network_control_results import build_network_control_audit_payload, record_network_control_result


_PROCESS_STAT_PATH = Path("/proc/self/stat")
logger = logging.getLogger("oracle-brain.network_control_local_service_restart")


def stage_pending_local_service_restart(
    *,
    control_context: dict[str, Any],
    target_id: str,
    host_id: str,
    service_name: str,
    state_path: Path = NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH,
    process_stat_path: Path = _PROCESS_STAT_PATH,
) -> dict[str, Any]:
    request_id = str(control_context.get("request_id") or "").strip()
    process_identity = _read_process_identity(process_stat_path)
    if not request_id or not process_identity:
        return {
            "ok": False,
            "error": "network_control_local_service_restart_state_unavailable",
            "detail": "Oracle could not persist local service restart recovery state.",
        }
    payload = {
        "version": 1,
        "request_id": request_id,
        "requested_at": str(control_context.get("requested_at") or "").strip(),
        "actor": str(control_context.get("actor") or "").strip(),
        "source": str(control_context.get("source") or "").strip(),
        "reason": str(control_context.get("reason") or "").strip(),
        "target_type": "service",
        "target_id": target_id,
        "action_id": "restart_service",
        "provider": "service_control",
        "adapter": "service_control",
        "host_id": host_id,
        "service_name": service_name,
        "process_identity_before": process_identity,
        "staged_at": datetime.now().astimezone().isoformat(),
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(state_path)
    except OSError:
        return {
            "ok": False,
            "error": "network_control_local_service_restart_state_unavailable",
            "detail": "Oracle could not persist local service restart recovery state.",
        }
    return {"ok": True, "status": "staged"}


def clear_pending_local_service_restart(
    *,
    state_path: Path = NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH,
) -> None:
    try:
        state_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("network_control_local_service_restart_state_clear_failed")


def complete_pending_local_service_restart(
    *,
    state_path: Path = NETWORK_LOCAL_SERVICE_RESTART_STATE_PATH,
    process_stat_path: Path = _PROCESS_STAT_PATH,
    db_path: Path | None = None,
) -> dict[str, Any]:
    pending = _load_pending(state_path)
    if not pending:
        return {"status": "none"}
    process_identity_after = _read_process_identity(process_stat_path)
    if not process_identity_after:
        return {"status": "pending", "reason": "process_identity_unavailable"}
    if process_identity_after == str(pending.get("process_identity_before") or ""):
        return {"status": "pending", "reason": "process_not_changed"}

    control = {
        "request_id": str(pending.get("request_id") or "").strip(),
        "requested_at": str(pending.get("requested_at") or "").strip(),
        "actor": str(pending.get("actor") or "").strip(),
        "source": str(pending.get("source") or "").strip(),
        "target_type": "service",
        "target_id": str(pending.get("target_id") or "").strip(),
        "action_id": "restart_service",
        "mode": "execute",
        "provider": "service_control",
        "adapter": "service_control",
        "policy_status": "allowed",
        "confirmation_status": "confirmed",
        "result_status": "executed",
        "error_class": "",
        "summary": "Oracle Brain restarted and completed application startup.",
        "execution": {
            "adapter": "service_control",
            "deferred": False,
            "local_service_restart_completed": True,
            "process_changed": True,
            "verification_status": "passed",
            "readiness_status": "passed",
            "availability_status": "ready",
        },
        "steps": [
            {
                "id": "local_service_process_restart_observed",
                "kind": "verification",
                "summary": "Oracle observed a new Brain process after the restart request.",
            },
            {
                "id": "local_service_startup_completed",
                "kind": "verification",
                "summary": "The new Brain process completed application startup.",
            },
        ],
    }
    record_event(
        "network_control_confirm",
        severity="info",
        source_id="brain",
        correlation_id=control["request_id"],
        provider="service_control",
        domain="network_control",
        status="executed",
        payload=build_network_control_audit_payload(control),
        db_path=db_path,
    )
    record_network_control_result(control)
    clear_pending_local_service_restart(state_path=state_path)
    return {"status": "completed", "request_id": control["request_id"]}


def safe_complete_pending_local_service_restart() -> dict[str, Any]:
    try:
        return complete_pending_local_service_restart()
    except Exception as exc:
        logger.warning("network_control_local_service_restart_completion_failed detail=%s", exc)
        return {"status": "error"}


def _load_pending(state_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_process_identity(process_stat_path: Path) -> str:
    try:
        stat = process_stat_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    closing = stat.rfind(")")
    fields = stat[closing + 1 :].split() if closing >= 0 else []
    if len(fields) < 20:
        return ""
    return f"{stat.split(' ', 1)[0]}:{fields[19]}"
