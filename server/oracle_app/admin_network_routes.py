from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .config import (
    get_music_settings,
    get_network_probe_settings,
    get_network_control_policy_settings,
    get_network_inventory_settings,
    get_network_router_control_settings,
    get_network_service_control_settings,
)
from .memory.runtime import safe_record_event
from .network import get_network_status_snapshot
from .network_control import (
    build_network_control_actions_diagnostics,
    build_network_control_confirm,
    build_network_control_dry_run,
    find_network_control_action_policy,
    find_network_control_target,
)
from .network_control_execution import execute_network_control_action
from .network_control_guard import (
    acquire_network_control,
    get_network_control_availability,
    get_network_control_availability_for_policy,
    network_control_cooldown_seconds,
    release_network_control,
)
from .network_control_preconditions import (
    evaluate_network_control_preconditions,
    with_inherited_host_preconditions,
)
from .network_control_results import (
    build_network_control_audit_payload,
    get_network_control_results_snapshot,
    record_network_control_result,
    safe_get_network_control_verification_snapshot,
)
from .network_status import build_network_admin_payload
from .provider_bridges.plex_music import MusicBridgeError, PlexMusicBridge
from .provider_bridges.service_control import get_host_restart_lifecycle_plan


def admin_network_status() -> dict[str, object]:
    snapshot = get_network_status_snapshot()
    control_policy = get_network_control_policy_settings()
    return {
        "ok": True,
        "network": build_network_admin_payload(
            snapshot,
            control_policy=control_policy,
            control_results=get_network_control_results_snapshot(),
            control_availability=get_network_control_availability_for_policy(control_policy),
        ),
    }


def admin_network_control_dry_run(payload: dict[str, object]) -> dict[str, object]:
    request_payload = dict(payload or {})
    inventory = get_network_inventory_settings()
    control_policy = get_network_control_policy_settings()
    action_policy = find_network_control_action_policy(
        control_policy=control_policy,
        target_type=str(request_payload.get("target_type") or ""),
        target_id=str(request_payload.get("target_id") or ""),
        action_id=str(request_payload.get("action_id") or ""),
    )
    action_policy = _with_effective_network_control_preconditions(
        request_payload,
        action_policy=action_policy,
        inventory=inventory,
        control_policy=control_policy,
    )
    service_control_settings = get_network_service_control_settings()
    control = build_network_control_dry_run(
        inventory=inventory,
        control_policy=control_policy,
        request_payload=request_payload,
        preconditions=_build_network_control_preconditions(
            request_payload,
            action_policy=action_policy,
            service_control_settings=service_control_settings,
            inventory=inventory,
            control_policy=control_policy,
        ),
        lifecycle=_build_network_control_lifecycle(
            request_payload,
            action_policy=action_policy,
            service_control_settings=service_control_settings,
        ),
    )
    control = _with_network_control_availability(control)
    _record_network_control_audit("network_control_dry_run", control)
    return {
        "ok": True,
        "control": control,
    }


def admin_network_control_actions() -> dict[str, object]:
    return {
        "ok": True,
        "diagnostics": build_network_control_actions_diagnostics(
            inventory=get_network_inventory_settings(),
            control_policy=get_network_control_policy_settings(),
            router_control_settings=get_network_router_control_settings(),
            service_control_settings=get_network_service_control_settings(),
            verification_results=safe_get_network_control_verification_snapshot(),
        ),
    }


def admin_network_control_confirm(payload: dict[str, object]) -> dict[str, object]:
    request_payload = dict(payload or {})
    inventory = get_network_inventory_settings()
    control_policy = get_network_control_policy_settings()
    action_policy = find_network_control_action_policy(
        control_policy=control_policy,
        target_type=str(request_payload.get("target_type") or ""),
        target_id=str(request_payload.get("target_id") or ""),
        action_id=str(request_payload.get("action_id") or ""),
    )
    action_policy = _with_effective_network_control_preconditions(
        request_payload,
        action_policy=action_policy,
        inventory=inventory,
        control_policy=control_policy,
    )
    service_control_settings = get_network_service_control_settings()
    preconditions = _build_network_control_preconditions(
        request_payload,
        action_policy=action_policy,
        service_control_settings=service_control_settings,
        inventory=inventory,
        control_policy=control_policy,
    )
    lifecycle = _build_network_control_lifecycle(
        request_payload,
        action_policy=action_policy,
        service_control_settings=service_control_settings,
    )
    control = build_network_control_confirm(
        inventory=inventory,
        control_policy=control_policy,
        request_payload=request_payload,
        preconditions=preconditions,
        lifecycle=lifecycle,
    )
    if (
        control.get("allowed") is True
        and control.get("confirmation_status") == "confirmed"
        and control.get("result_status") == "not_implemented"
    ):
        request_id = str(control.get("request_id") or "")
        requested_at = str(control.get("requested_at") or "")
        target = find_network_control_target(
            inventory=inventory,
            target_type=str(control.get("target_type") or ""),
            target_id=str(control.get("target_id") or ""),
        )
        lease = acquire_network_control(
            target_type=str(control.get("target_type") or ""),
            target_id=str(control.get("target_id") or ""),
            action_id=str(control.get("action_id") or ""),
        )
        if lease.get("acquired") is not True:
            execution_result = _network_control_guard_blocked_result(lease.get("state"))
        else:
            cooldown_seconds = network_control_cooldown_seconds(action_policy)
            _record_network_control_started(control, cooldown_seconds=cooldown_seconds)
            execution_result: dict[str, object] | None = None
            try:
                execution_result = execute_network_control_action(
                    action_policy=action_policy,
                    target=_with_power_target_host(target=target, inventory=inventory),
                    control_context={
                        "request_id": request_id,
                        "requested_at": requested_at,
                        "actor": str(control.get("actor") or ""),
                        "source": str(control.get("source") or ""),
                        "reason": str(control.get("reason") or ""),
                    },
                    router_control_settings=get_network_router_control_settings(),
                    service_control_settings=service_control_settings,
                    network_probe_settings=get_network_probe_settings(),
                    verify_available=_build_network_control_verifier(request_payload),
                )
            except Exception:
                execution_result = {
                    "ok": False,
                    "result_status": "failed",
                    "error_class": "network_control_execution_failed",
                    "summary": "Network control execution failed unexpectedly.",
                    "execution": {
                        "verification_status": "failed",
                    },
                    "steps": [
                        {
                            "id": "execution_failed",
                            "kind": "execution",
                            "summary": "The provider adapter failed before it could return a normal result.",
                        }
                    ],
                }
            finally:
                cooldown_state = release_network_control(
                    token=str(lease.get("token") or ""),
                    cooldown_seconds=cooldown_seconds,
                )
            if execution_result is not None:
                execution_result = {
                    **execution_result,
                    "execution": {
                        **dict(execution_result.get("execution") or {}),
                        "availability_status": str(cooldown_state.get("status") or "ready"),
                        "cooldown_seconds": cooldown_seconds,
                        "cooldown_until": str(cooldown_state.get("cooldown_until") or ""),
                    },
                }
        completed_control = build_network_control_confirm(
            inventory=inventory,
            control_policy=control_policy,
            request_payload=request_payload,
            preconditions=preconditions,
            lifecycle=lifecycle,
            execution_result=execution_result,
        )
        control = {
            **completed_control,
            "request_id": request_id,
            "requested_at": requested_at,
        }
    record_network_control_result(control)
    _record_network_control_audit("network_control_confirm", control)
    return {
        "ok": True,
        "control": control,
    }


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
            "execution": {
                "availability_status": status,
                "cooldown_remaining_seconds": remaining,
                "cooldown_until": str(state.get("cooldown_until") or ""),
            },
            "steps": [
                {
                    "id": "action_cooldown",
                    "kind": "policy",
                    "summary": "Oracle blocked the command because this target is in its post-action cooldown.",
                }
            ],
        }
    return {
        "ok": False,
        "result_status": "blocked",
        "error_class": "network_control_action_in_progress",
        "summary": "Another disruptive network control action is already in progress.",
        "execution": {
            "availability_status": status,
            "active_target_type": str(state.get("active_target_type") or ""),
            "active_target_id": str(state.get("active_target_id") or ""),
            "active_action_id": str(state.get("active_action_id") or ""),
            "active_started_at": str(state.get("active_started_at") or ""),
        },
        "steps": [
            {
                "id": "action_in_progress",
                "kind": "policy",
                "summary": "Oracle blocked the command because another disruptive action is still running.",
            }
        ],
    }


def _record_network_control_started(control: dict[str, object], *, cooldown_seconds: int) -> None:
    request_id = str(control.get("request_id") or "").strip()
    started_control = {
        **control,
        "result_status": "in_progress",
        "summary": "Oracle acquired the network control execution lease.",
        "execution": {
            **dict(control.get("execution") or {}),
            "availability_status": "in_progress",
            "cooldown_seconds": cooldown_seconds,
        },
    }
    safe_record_event(
        "network_control_started",
        source_id="brain",
        provider=str(control.get("provider") or ""),
        domain="network_control",
        status="in_progress",
        severity="info",
        correlation_id=request_id or None,
        payload=build_network_control_audit_payload(started_control),
    )


def _with_power_target_host(
    *,
    target: dict[str, object],
    inventory: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    if not target or not str(target.get("host_id") or "").strip():
        return target
    host_id = str(target.get("host_id") or "").strip()
    host = next(
        (
            item
            for item in inventory.get("hosts") or []
            if isinstance(item, dict) and str(item.get("id") or "").strip() == host_id
        ),
        {},
    )
    addresses = host.get("addresses") if isinstance(host.get("addresses"), list) else []
    address = next((str(item).strip() for item in addresses if str(item).strip()), "")
    return {
        **target,
        "host_display_name": str(host.get("display_name") or host_id).strip(),
        "host_address": address,
    }


def register_admin_network_routes(app: FastAPI) -> None:
    app.get("/api/admin/network/status")(admin_network_status_http)
    app.get("/api/admin/network/control/actions")(admin_network_control_actions_http)
    app.post("/api/admin/network/control/dry-run")(admin_network_control_dry_run_http)
    app.post("/api/admin/network/control/confirm")(admin_network_control_confirm_http)


def admin_network_status_http(request: Request) -> dict[str, object]:
    canonical, execution = _canonical_network_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    if execution is None:
        return {"ok": False, "network": {"status": "unconfigured"}}
    return admin_network_status_canonical(execution)


def admin_network_status_canonical(execution) -> dict[str, object]:
    snapshot = execution.status_snapshot()
    verification = safe_get_network_control_verification_snapshot()
    diagnostics = execution.control_diagnostics(verification)
    control_policy = {"actions": list(diagnostics.get("actions") or [])}
    payload = build_network_admin_payload(
        snapshot,
        control_policy=control_policy,
        control_results=verification,
        control_availability=get_network_control_availability_for_policy(control_policy),
    )
    return {"ok": True, "network": payload}


def admin_network_control_actions_http(request: Request) -> dict[str, object]:
    canonical, execution = _canonical_network_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
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
    canonical, execution = _canonical_network_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    if execution is None:
        return {"ok": False, "control": {"allowed": False, "error_class": "network_control_not_configured"}}
    control = _with_network_control_availability(execution.control_dry_run(dict(payload or {})))
    _record_network_control_audit("network_control_dry_run", control)
    return {"ok": True, "control": control}


def admin_network_control_confirm_http(
    request: Request,
    payload: dict[str, object],
) -> dict[str, object]:
    canonical, execution = _canonical_network_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
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


def _canonical_network_context(request: Request):
    from .brain_application_composition import (
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        CanonicalBrainApplicationComposition,
    )

    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    canonical = isinstance(composition, CanonicalBrainApplicationComposition)
    return canonical, composition.network_execution if canonical else None


def _build_network_control_preconditions(
    payload: dict[str, object],
    *,
    action_policy: dict[str, object],
    service_control_settings: dict[str, object],
    inventory: dict[str, object],
    control_policy: dict[str, object],
) -> list[dict[str, object]]:
    target_type = str(payload.get("target_type") or "").strip().lower()
    target_id = str(payload.get("target_id") or "").strip()
    return evaluate_network_control_preconditions(
        action_policy=action_policy,
        target_type=target_type,
        target_id=target_id,
        music_settings=get_music_settings(),
        service_control_settings=service_control_settings,
        inventory=inventory,
        control_policy=control_policy,
    )


def _with_effective_network_control_preconditions(
    payload: dict[str, object],
    *,
    action_policy: dict[str, object],
    inventory: dict[str, object],
    control_policy: dict[str, object],
) -> dict[str, object]:
    return with_inherited_host_preconditions(
        action_policy=action_policy,
        target_type=str(payload.get("target_type") or "").strip().lower(),
        target_id=str(payload.get("target_id") or "").strip(),
        inventory=inventory,
        control_policy=control_policy,
    )


def _build_network_control_lifecycle(
    payload: dict[str, object],
    *,
    action_policy: dict[str, object],
    service_control_settings: dict[str, object],
) -> dict[str, object]:
    if action_policy.get("requires_graceful_lifecycle") is not True:
        return {}
    if str(payload.get("target_type") or "").strip().lower() != "host":
        return {}
    return get_host_restart_lifecycle_plan(
        settings=service_control_settings,
        host_id=str(payload.get("target_id") or "").strip(),
    )


def _build_network_control_verifier(payload: dict[str, object]):
    target_type = str(payload.get("target_type") or "").strip().lower()
    target_id = str(payload.get("target_id") or "").strip()
    action_id = str(payload.get("action_id") or "").strip()
    if target_type == "service" and target_id == "plex" and action_id == "restart_service":
        return _verify_plex_available
    return None


def _verify_plex_available() -> dict[str, object]:
    try:
        PlexMusicBridge(settings=get_music_settings()).get_active_sessions_status()
    except MusicBridgeError as exc:
        return {
            "status": "failed",
            "summary": f"Plex availability check failed after restart: {exc.detail}",
        }
    return {
        "status": "passed",
        "summary": "Plex availability check passed after restart.",
    }


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
    policy_status = str(control.get("policy_status") or "")
    result_status = str(control.get("result_status") or "")
    if policy_status == "blocked":
        return "warning"
    if policy_status == "denied":
        return "warning"
    if result_status == "not_implemented":
        return "warning"
    if result_status == "blocked":
        return "warning"
    return "info"
