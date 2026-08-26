from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .memory.runtime import safe_record_event
from .network_control_guard import (
    acquire_network_control,
    get_network_control_availability,
    get_network_control_availability_for_policy,
    release_network_control,
)
from .network_control_results import (
    build_network_control_audit_payload,
    record_network_control_result,
    safe_get_network_control_verification_snapshot,
)
from .network_status import build_network_admin_payload


def register_admin_network_routes(app: FastAPI) -> None:
    app.get("/api/admin/network/status")(admin_network_status_http)
    app.get("/api/admin/network/control/actions")(admin_network_control_actions_http)
    app.post("/api/admin/network/control/dry-run")(admin_network_control_dry_run_http)
    app.post("/api/admin/network/control/confirm")(admin_network_control_confirm_http)


def admin_network_status_http(request: Request) -> dict[str, object]:
    execution = _canonical_network_execution(request)
    if execution is None:
        return {"ok": False, "network": {"status": "unconfigured"}}
    return admin_network_status_canonical(execution)


def admin_network_status_canonical(execution) -> dict[str, object]:
    snapshot = execution.status_snapshot()
    verification = safe_get_network_control_verification_snapshot()
    diagnostics = execution.control_diagnostics(verification)
    control_policy = {"actions": list(diagnostics.get("actions") or [])}
    return {
        "ok": True,
        "network": build_network_admin_payload(
            snapshot,
            control_policy=control_policy,
            control_results=verification,
            control_availability=get_network_control_availability_for_policy(control_policy),
        ),
    }


def admin_network_control_actions_http(request: Request) -> dict[str, object]:
    execution = _canonical_network_execution(request)
    if execution is None:
        return {"ok": True, "diagnostics": {"actions": [], "counts": {"total": 0}}}
    return {
        "ok": True,
        "diagnostics": execution.control_diagnostics(
            safe_get_network_control_verification_snapshot()
        ),
    }


def admin_network_control_dry_run_http(
    request: Request,
    payload: dict[str, object],
) -> dict[str, object]:
    execution = _canonical_network_execution(request)
    if execution is None:
        return {"ok": False, "control": {"allowed": False, "error_class": "network_control_not_configured"}}
    control = _with_network_control_availability(execution.control_dry_run(dict(payload or {})))
    _record_network_control_audit("network_control_dry_run", control)
    return {"ok": True, "control": control}


def admin_network_control_confirm_http(
    request: Request,
    payload: dict[str, object],
) -> dict[str, object]:
    execution = _canonical_network_execution(request)
    if execution is None:
        return {"ok": False, "control": {"allowed": False, "error_class": "network_control_not_configured"}}
    return admin_network_control_confirm_canonical(execution, payload)


def admin_network_control_confirm_canonical(
    execution,
    payload: dict[str, object],
) -> dict[str, object]:
    request_payload = dict(payload or {})
    control = execution.control_confirm(request_payload)
    if (
        control.get("allowed") is True
        and control.get("confirmation_status") == "confirmed"
        and control.get("result_status") == "not_implemented"
    ):
        request_id = str(control.get("request_id") or "")
        requested_at = str(control.get("requested_at") or "")
        action = execution.policy.action_for(
            target_type=str(control.get("target_type") or ""),
            target_id=str(control.get("target_id") or ""),
            operation=str(control.get("action_id") or ""),
        )
        lease = acquire_network_control(
            target_type=str(control.get("target_type") or ""),
            target_id=str(control.get("target_id") or ""),
            action_id=str(control.get("action_id") or ""),
        )
        if lease.get("acquired") is not True:
            result = _network_control_guard_blocked_result(lease.get("state"))
        else:
            cooldown_seconds = int(
                0 if action is None else (action.definition.execution.cooldown_seconds or 0)
            )
            _record_network_control_started(control, cooldown_seconds=cooldown_seconds)
            try:
                result = execution.execute_control(
                    request_payload,
                    {
                        "request_id": request_id,
                        "requested_at": requested_at,
                        "actor": str(control.get("actor") or ""),
                        "source": str(control.get("source") or ""),
                        "reason": str(control.get("reason") or ""),
                    },
                )
            except Exception:
                result = {
                    "ok": False,
                    "result_status": "failed",
                    "error_class": "network_control_execution_failed",
                    "summary": "Network control execution failed unexpectedly.",
                    "execution": {"verification_status": "failed"},
                    "steps": [],
                }
            finally:
                cooldown = release_network_control(
                    token=str(lease.get("token") or ""),
                    cooldown_seconds=cooldown_seconds,
                )
            result = {
                **result,
                "execution": {
                    **dict(result.get("execution") or {}),
                    "availability_status": str(cooldown.get("status") or "ready"),
                    "cooldown_seconds": cooldown_seconds,
                    "cooldown_until": str(cooldown.get("cooldown_until") or ""),
                },
            }
        completed = execution.control_confirm(request_payload, result=result)
        control = {**completed, "request_id": request_id, "requested_at": requested_at}
    record_network_control_result(control)
    _record_network_control_audit("network_control_confirm", control)
    return {"ok": True, "control": control}


def _canonical_network_execution(request: Request):
    from .brain_application_composition import (
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        CanonicalBrainApplicationComposition,
    )

    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return composition.network_execution


def _with_network_control_availability(control: dict[str, object]) -> dict[str, object]:
    if control.get("allowed") is not True:
        return control
    availability = get_network_control_availability(
        target_type=str(control.get("target_type") or ""),
        target_id=str(control.get("target_id") or ""),
        action_id=str(control.get("action_id") or ""),
    )
    if availability.get("status") == "ready":
        return {**control, "availability": availability}
    blocked = _network_control_guard_blocked_result(availability)
    return {
        **control,
        "allowed": False,
        "policy_status": "blocked",
        "result_status": "not_executed",
        "error_class": blocked["error_class"],
        "summary": blocked["summary"],
        "availability": availability,
    }


def _network_control_guard_blocked_result(raw_state: object) -> dict[str, object]:
    state = raw_state if isinstance(raw_state, dict) else {"status": "blocked_by_active"}
    status = str(state.get("status") or "blocked_by_active")
    if status == "cooldown":
        remaining = int(state.get("cooldown_remaining_seconds") or 0)
        return {
            "ok": False,
            "result_status": "blocked",
            "error_class": "network_control_action_cooldown",
            "summary": f"Network control is cooling down for this target for about {remaining} more second(s).",
            "execution": {"availability_status": status, "cooldown_remaining_seconds": remaining},
            "steps": [],
        }
    return {
        "ok": False,
        "result_status": "blocked",
        "error_class": "network_control_action_in_progress",
        "summary": "Another disruptive network control action is already in progress.",
        "execution": {"availability_status": status},
        "steps": [],
    }


def _record_network_control_started(control: dict[str, object], *, cooldown_seconds: int) -> None:
    request_id = str(control.get("request_id") or "").strip()
    started = {
        **control,
        "result_status": "in_progress",
        "summary": "Oracle acquired the network control execution lease.",
        "execution": {**dict(control.get("execution") or {}), "availability_status": "in_progress", "cooldown_seconds": cooldown_seconds},
    }
    safe_record_event(
        "network_control_started",
        source_id="brain",
        provider=str(control.get("provider") or ""),
        domain="network_control",
        status="in_progress",
        severity="info",
        correlation_id=request_id or None,
        payload=build_network_control_audit_payload(started),
    )


def _record_network_control_audit(event_type: str, control: dict[str, object]) -> None:
    request_id = str(control.get("request_id") or "").strip()
    safe_record_event(
        event_type,
        source_id="brain",
        provider=str(control.get("provider") or ""),
        domain="network_control",
        status=str(control.get("result_status") or control.get("policy_status") or ""),
        severity=_network_control_audit_severity(control),
        correlation_id=request_id or None,
        payload=build_network_control_audit_payload(control),
    )


def _network_control_audit_severity(control: dict[str, object]) -> str:
    if str(control.get("policy_status") or "") in {"blocked", "denied"}:
        return "warning"
    if str(control.get("result_status") or "") in {"not_implemented", "blocked"}:
        return "warning"
    return "info"
