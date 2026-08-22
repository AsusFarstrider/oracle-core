from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from .memory.events import EventQuery, query_events, record_event


_MAX_RESULTS = 100
_RESULTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_LOCK = Lock()
logger = logging.getLogger("oracle-brain.network_control_results")


def clear_network_control_results() -> None:
    with _LOCK:
        _RESULTS.clear()


def record_network_control_result(
    control: dict[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    target_type = str(control.get("target_type") or "").strip().lower()
    target_id = str(control.get("target_id") or "").strip()
    action_id = str(control.get("action_id") or "").strip()
    if not target_type or not target_id or not action_id:
        return {}
    item = _sanitize_control_result(control, recorded_at=recorded_at)
    with _LOCK:
        _RESULTS[(target_type, target_id, action_id)] = item
        _trim_results_locked()
    return item


def get_network_control_results_snapshot() -> dict[tuple[str, str, str], dict[str, Any]]:
    with _LOCK:
        return {key: dict(value) for key, value in _RESULTS.items()}


def restore_network_control_results_from_memory(*, db_path: Path | None = None) -> int:
    events = query_events(
        EventQuery(
            event_type="network_control_confirm",
            domain="network_control",
            limit=500,
        ),
        db_path=db_path,
    )
    restored: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        target_type = str(payload.get("target_type") or "").strip().lower()
        target_id = str(payload.get("target_id") or "").strip()
        action_id = str(payload.get("action_id") or "").strip()
        key = (target_type, target_id, action_id)
        if not all(key) or key in restored:
            continue
        restored[key] = _sanitize_control_result(
            payload,
            recorded_at=str(event.get("observed_at") or event.get("created_at") or ""),
        )
        if len(restored) >= _MAX_RESULTS:
            break
    with _LOCK:
        _RESULTS.clear()
        _RESULTS.update(restored)
    return len(restored)


def safe_restore_network_control_results_from_memory() -> int:
    try:
        return restore_network_control_results_from_memory()
    except Exception as exc:
        logger.warning("network_control_result_restore_failed detail=%s", exc)
        return 0


def get_network_control_verification_snapshot(*, db_path: Path | None = None) -> dict[tuple[str, str, str], dict[str, Any]]:
    events = query_events(
        EventQuery(
            event_type="network_control_confirm",
            domain="network_control",
            limit=500,
        ),
        db_path=db_path,
    )
    verified: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        target_type = str(payload.get("target_type") or "").strip().lower()
        target_id = str(payload.get("target_id") or "").strip()
        action_id = str(payload.get("action_id") or "").strip()
        key = (target_type, target_id, action_id)
        if not all(key) or key in verified or not _is_verified_control_result(payload):
            continue
        verified[key] = {
            "request_id": str(payload.get("request_id") or event.get("correlation_id") or "").strip(),
            "verified_at": str(event.get("observed_at") or event.get("created_at") or "").strip(),
            "verification_status": "passed",
        }
    return verified


def safe_get_network_control_verification_snapshot() -> dict[tuple[str, str, str], dict[str, Any]]:
    try:
        return get_network_control_verification_snapshot()
    except Exception as exc:
        logger.warning("network_control_verification_load_failed detail=%s", exc)
        return {}


def reconcile_interrupted_network_controls(*, db_path: Path | None = None) -> int:
    started_events = query_events(
        EventQuery(
            event_type="network_control_started",
            domain="network_control",
            limit=500,
        ),
        db_path=db_path,
    )
    final_events = query_events(
        EventQuery(
            event_type="network_control_confirm",
            domain="network_control",
            limit=500,
        ),
        db_path=db_path,
    )
    final_request_ids = {
        _event_request_id(event)
        for event in final_events
        if _event_request_id(event)
    }
    reconciled_count = 0
    for event in reversed(started_events):
        request_id = _event_request_id(event)
        if not request_id or request_id in final_request_ids:
            continue
        started_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        started_execution = started_payload.get("execution") if isinstance(started_payload.get("execution"), dict) else {}
        interrupted = {
            **started_payload,
            "request_id": request_id,
            "requested_at": str(
                started_payload.get("requested_at")
                or event.get("observed_at")
                or event.get("created_at")
                or ""
            ),
            "mode": str(started_payload.get("mode") or "execute"),
            "policy_status": "interrupted",
            "confirmation_status": str(started_payload.get("confirmation_status") or "confirmed"),
            "result_status": "interrupted",
            "error_class": "network_control_interrupted_by_restart",
            "summary": (
                "Oracle restarted before the network command recorded a final outcome. "
                "The action was not retried; fresh checks and confirmation are required."
            ),
            "execution": {
                **{
                    key: value
                    for key, value in started_execution.items()
                    if key
                    not in {
                        "active_action_id",
                        "active_started_at",
                        "active_target_id",
                        "active_target_type",
                        "cooldown_remaining_seconds",
                        "cooldown_seconds",
                        "cooldown_until",
                    }
                },
                "verification_status": "unknown",
                "availability_status": "ready",
                **(
                    {"lifecycle_status": "interrupted"}
                    if dict(started_payload.get("lifecycle") or {}).get("configured") is True
                    else {}
                ),
            },
            "steps": [
                *_safe_summary_items(started_payload.get("steps")),
                {
                    "id": "execution_interrupted",
                    "kind": "interruption",
                    "summary": (
                        "Oracle restarted before a final provider outcome was recorded. "
                        "No automatic retry was attempted."
                    ),
                },
            ],
        }
        record_event(
            "network_control_confirm",
            severity="warning",
            source_id="brain",
            correlation_id=request_id,
            provider=str(interrupted.get("provider") or ""),
            domain="network_control",
            status="interrupted",
            payload=build_network_control_audit_payload(interrupted),
            db_path=db_path,
        )
        final_request_ids.add(request_id)
        reconciled_count += 1
    return reconciled_count


def safe_reconcile_interrupted_network_controls() -> int:
    try:
        return reconcile_interrupted_network_controls()
    except Exception as exc:
        logger.warning("network_control_interruption_reconciliation_failed detail=%s", exc)
        return 0


def build_network_control_audit_payload(control: dict[str, Any]) -> dict[str, Any]:
    result = _sanitize_control_result(control)
    lifecycle = control.get("lifecycle") if isinstance(control.get("lifecycle"), dict) else {}
    return {
        **result,
        "lifecycle": {
            "configured": lifecycle.get("configured") is True,
            "mode": str(lifecycle.get("mode") or "").strip(),
            "phases": _safe_summary_items(lifecycle.get("phases")),
        },
        "steps": _safe_summary_items(control.get("steps")),
        "preconditions": [
            {
                "id": str(item.get("id") or "").strip(),
                "provider": str(item.get("provider") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "observed_value": item.get("observed_value"),
                "summary": str(item.get("summary") or "").strip(),
            }
            for item in control.get("preconditions") or []
            if isinstance(item, dict)
        ],
    }


def _sanitize_control_result(
    control: dict[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    execution = control.get("execution") if isinstance(control.get("execution"), dict) else {}
    return {
        "request_id": str(control.get("request_id") or "").strip(),
        "recorded_at": recorded_at or datetime.now().astimezone().isoformat(),
        "requested_at": str(control.get("requested_at") or "").strip(),
        "actor": str(control.get("actor") or "").strip(),
        "source": str(control.get("source") or "").strip(),
        "target_type": str(control.get("target_type") or "").strip().lower(),
        "target_id": str(control.get("target_id") or "").strip(),
        "action_id": str(control.get("action_id") or "").strip(),
        "mode": str(control.get("mode") or "").strip(),
        "provider": str(control.get("provider") or "").strip(),
        "adapter": str(control.get("adapter") or "").strip(),
        "policy_status": str(control.get("policy_status") or "").strip(),
        "confirmation_status": str(control.get("confirmation_status") or "").strip(),
        "result_status": str(control.get("result_status") or "").strip(),
        "error_class": str(control.get("error_class") or "").strip(),
        "summary": str(control.get("summary") or "").strip(),
        "execution": {
            key: value
            for key, value in dict(execution).items()
            if key
            in {
                "adapter",
                "method",
                "service_manager",
                "wait_seconds",
                "restart_timeout_seconds",
                "off_seconds",
                "shutdown_timeout_seconds",
                "recovery_timeout_seconds",
                "recovery_poll_seconds",
                "readiness_timeout_seconds",
                "verification_status",
                "readiness_status",
                "readiness_check_count",
                "readiness_passed_count",
                "readiness_failed_check_ids",
                "power_restored",
                "shutdown_observed",
                "local_restart_completed",
                "boot_changed",
                "local_service_restart_completed",
                "process_changed",
                "deferred",
                "availability_status",
                "active_target_type",
                "active_target_id",
                "active_action_id",
                "active_started_at",
                "cooldown_seconds",
                "cooldown_remaining_seconds",
                "cooldown_until",
                "lifecycle_status",
                "lifecycle_completed_phase_ids",
            }
        },
    }


def _safe_summary_items(value: Any) -> list[dict[str, str]]:
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "kind": str(item.get("kind") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
        }
        for item in value or []
        if isinstance(item, dict)
    ]


def _event_request_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(payload.get("request_id") or event.get("correlation_id") or "").strip()


def _is_verified_control_result(payload: dict[str, Any]) -> bool:
    if str(payload.get("result_status") or "").strip() != "executed":
        return False
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    if str(execution.get("verification_status") or "").strip() != "passed":
        return False
    if str(payload.get("action_id") or "").strip() == "restart_router" and execution.get("shutdown_observed") is not True:
        return False
    readiness_status = str(execution.get("readiness_status") or "").strip()
    return readiness_status in {"", "passed"}


def _trim_results_locked() -> None:
    if len(_RESULTS) <= _MAX_RESULTS:
        return
    sorted_items = sorted(
        _RESULTS.items(),
        key=lambda item: str(item[1].get("recorded_at") or ""),
    )
    for key, _value in sorted_items[: max(0, len(sorted_items) - _MAX_RESULTS)]:
        _RESULTS.pop(key, None)
