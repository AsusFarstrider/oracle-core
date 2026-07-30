from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .admin_network_routes import admin_network_control_confirm_canonical, admin_network_status_canonical
from .memory.runtime import safe_record_event
from .network import get_network_status_snapshot
from .network_control_guard import get_network_control_availability_for_policy
from .network_control_results import get_network_control_results_snapshot
from .network_status import build_network_admin_payload
from .schemas import UiOrchestrationApprovalRequest, UiOrchestrationPreviewRequest
from .runbook_kernel import RunbookActivation, RunbookDefinitionRef, RunbookRepository


_PREVIEW_TTL_SECONDS = 15 * 60
_MAX_PREVIEWS = 64
_PREVIEWS: dict[str, dict[str, Any]] = {}
_PREVIEW_STORED_MONOTONIC: dict[str, float] = {}
_CONSUMED_PREVIEWS: set[str] = set()
_PREVIEW_LOCK = Lock()
_NETWORK_RECOVERY_CONTROLLER_VERSION = "1"
_ACTION_PRIORITY = {
    "restart_service": 10,
    "restart_ui": 20,
    "restart_runtime": 30,
    "restart_router": 40,
    "restart_host": 40,
    "power_cycle": 50,
}
_STATUS_PRIORITY = {
    "down": 60,
    "degraded": 50,
    "unavailable": 40,
    "stale": 30,
    "unknown": 20,
    "unconfigured": 10,
    "healthy": 0,
}
_ACTIONABLE_STATUSES = {"down", "degraded", "unavailable", "stale"}
_ACTION_DURATION_ESTIMATES = {
    "restart_service": (60, "about 1-2 minutes"),
    "restart_ui": (60, "about 1 minute"),
    "restart_runtime": (120, "about 1-2 minutes"),
    "restart_router": (300, "about 3-5 minutes"),
    "restart_host": (300, "about 3-5 minutes"),
    "power_cycle": (300, "about 3-5 minutes"),
}


def build_ui_internet_snapshot(
    *,
    force_refresh: bool = False,
    canonical_execution=None,
) -> dict[str, Any]:
    network = _network_payload_for(force_refresh, canonical_execution)
    categories = _build_categories(network)
    visible_categories = _health_only_categories(categories)
    return {
        "ok": True,
        "status": _aggregate_status([str(item.get("status") or "unknown") for item in visible_categories]),
        "summary": _household_summary(categories),
        "generated_at": network.get("generated_at"),
        "freshness": network.get("freshness"),
        "categories": visible_categories,
        "recovery": _recovery_summary("fix_internet", canonical_execution=canonical_execution),
        "refresh_after_seconds": 30,
    }


def create_recovery_preview(
    orchestration_id: str,
    request: UiOrchestrationPreviewRequest,
    *,
    canonical_execution=None,
) -> dict[str, Any]:
    definition = _find_recovery(orchestration_id, canonical_execution=canonical_execution)
    if definition.get("enabled") is not True:
        raise HTTPException(status_code=409, detail="Recovery runbook is disabled.")

    network = _network_payload_for(True, canonical_execution)
    categories = _build_categories(network)
    visible_categories = _health_only_categories(categories)
    findings = _build_findings(categories)
    steps = _build_preview_steps(categories)
    total_estimate_seconds = sum(int(step.get("estimated_duration_seconds") or 0) for step in steps)
    generated_at = datetime.now().astimezone()
    preview_id = f"recovery-preview-{uuid.uuid4().hex}"
    preview = {
        "preview_id": preview_id,
        "orchestration_id": orchestration_id,
        "display_name": str(definition.get("display_name") or orchestration_id),
        "client_id": request.client_id,
        "status": "ready" if steps else "no_actions",
        "generated_at": generated_at.isoformat(),
        "expires_at": (generated_at + timedelta(seconds=_PREVIEW_TTL_SECONDS)).isoformat(),
        "diagnostic_profile": definition.get("diagnostic_profile"),
        "remediation_profile": definition.get("remediation_profile"),
        "network_status": _aggregate_status(
            [str(item.get("status") or "unknown") for item in visible_categories]
        ),
        "findings": findings,
        "steps": steps,
        "estimated_total_duration_seconds": total_estimate_seconds,
        "estimated_total_duration": _duration_label(total_estimate_seconds) if steps else "",
        "approval_summary": _approval_summary(steps),
        "approval_available": bool(steps),
        "execution_available": bool(steps),
        "config_revision": canonical_execution.inventory.config_revision,
        "notice": (
            "Approval authorizes only these listed conditional actions. Oracle will "
            "re-check health and policy before every action and will not expand the plan."
        ),
    }
    preview["digest"] = _preview_digest(preview)
    _store_preview(preview)
    return {"ok": True, "preview": copy.deepcopy(preview)}


def get_recovery_preview(preview_id: str) -> dict[str, Any]:
    _prune_previews()
    preview = _PREVIEWS.get(str(preview_id or "").strip())
    if not isinstance(preview, dict):
        raise HTTPException(status_code=404, detail="Recovery preview was not found or has expired.")
    return {"ok": True, "preview": copy.deepcopy(preview)}


def approve_recovery_preview(
    orchestration_id: str,
    request: UiOrchestrationApprovalRequest,
    *,
    canonical_execution=None,
) -> dict[str, Any]:
    if request.approved is not True:
        raise HTTPException(status_code=400, detail="Explicit recovery approval is required.")
    preview = _get_preview_for_approval(request.preview_id)
    if str(preview.get("orchestration_id") or "") != str(orchestration_id or "").strip():
        raise HTTPException(status_code=409, detail="Recovery preview does not match this runbook.")
    if str(preview.get("client_id") or "") != request.client_id:
        raise HTTPException(status_code=409, detail="Recovery preview belongs to a different UI client.")
    if str(preview.get("digest") or "") != request.digest:
        raise HTTPException(status_code=409, detail="Recovery preview digest does not match.")
    if canonical_execution is not None and str(preview.get("config_revision") or "") != canonical_execution.inventory.config_revision:
        raise HTTPException(status_code=409, detail="Recovery preview belongs to another configuration revision.")
    approved_steps = [item for item in preview.get("steps") or [] if isinstance(item, dict)]
    if not approved_steps:
        raise HTTPException(status_code=409, detail="Recovery preview contains no actions to approve.")
    _claim_preview(request.preview_id)

    run_id = f"recovery-run-{uuid.uuid4().hex}"
    started_at = datetime.now().astimezone().isoformat()
    repository = _repository()
    try:
        definition_id = str(preview.get("orchestration_id") or orchestration_id)
        repository.create_run(
            RunbookDefinitionRef(
                definition_id=definition_id,
                kind="recovery",
                domain="network",
                version=_recovery_definition_version(preview),
                controller_version=_NETWORK_RECOVERY_CONTROLLER_VERSION,
            ),
            RunbookActivation(
                run_id=run_id,
                started_at=started_at,
                correlation_key=f"recovery:{definition_id}:{preview.get('preview_id')}",
                idempotency_key=f"recovery-preview:{preview.get('preview_id')}",
                client_id=request.client_id,
            ),
            status="running",
            preview_id=str(preview.get("preview_id") or ""),
            digest=str(preview.get("digest") or ""),
            summary="Approved recovery is running.",
            approval_consumed=True,
            controller_state={
                "approved_step_count": len(approved_steps),
                "completed_step_count": 0,
            },
            payload={
                "display_name": preview.get("display_name"),
                "approved_step_count": len(approved_steps),
            },
        )
        for ordinal, step in enumerate(approved_steps, start=1):
            repository.record_operation(
                run_id=run_id,
                operation_id=str(step.get("step_id") or f"step-{ordinal}"),
                ordinal=ordinal,
                status="pending",
                operation_kind=str(step.get("target_type") or ""),
                target_id=str(step.get("target_id") or ""),
                target_label=str(step.get("target_label") or ""),
                capability_id=str(step.get("action_id") or ""),
                policy_id=str(step.get("policy_id") or ""),
                summary="Approved and waiting for a fresh safety check.",
                payload={
                    "observed_target_type": step.get("observed_target_type"),
                    "observed_target_id": step.get("observed_target_id"),
                    "condition": step.get("condition"),
                },
            )
    except Exception as exc:
        try:
            repository.delete_run(run_id)
        finally:
            _release_preview_claim(request.preview_id)
        raise HTTPException(
            status_code=503,
            detail="Oracle could not create durable recovery history. No action was executed.",
        ) from exc
    _record_recovery_event(
        "orchestration_recovery_started",
        run_id=run_id,
        preview=preview,
        client_id=request.client_id,
        status="running",
    )

    approved_identities = {_step_identity(step) for step in approved_steps}
    current_categories = _build_categories(_network_payload_for(True, canonical_execution))
    expanded = _current_step_identities(current_categories) - approved_identities
    if expanded:
        return _finish_recovery(
            run_id=run_id,
            preview=preview,
            started_at=started_at,
            status="plan_changed",
            summary="Network conditions or policy changed. Review a new Fix Internet plan.",
            step_results=[],
            repository=repository,
        )

    step_results: list[dict[str, Any]] = []
    for step in approved_steps:
        current_categories = _build_categories(_network_payload_for(True, canonical_execution))
        item = _find_observed_item(current_categories, step)
        current_status = str(item.get("status") or "unknown") if item else "unknown"
        if current_status not in _ACTIONABLE_STATUSES:
            result = _skipped_step_result(step, current_status)
            step_results.append(result)
            _persist_step_result(run_id, step, result, repository=repository)
            continue

        current_steps = _build_preview_steps(current_categories)
        current_identities = {_step_identity(item) for item in current_steps}
        if current_identities - approved_identities or _step_identity(step) not in current_identities:
            return _finish_recovery(
                run_id=run_id,
                preview=preview,
                started_at=started_at,
                status="plan_changed",
                summary="Network conditions or policy changed. Review a new Fix Internet plan.",
                step_results=step_results,
                repository=repository,
            )

        step_started_at = datetime.now().astimezone().isoformat()
        _persist_step_running(
            run_id,
            step,
            started_at=step_started_at,
            repository=repository,
        )
        control_request = {
                "target_type": step.get("target_type"),
                "target_id": step.get("target_id"),
                "action_id": step.get("action_id"),
                "confirmed": True,
                "actor": request.client_id,
                "source": "house_mode_recovery",
                "reason": f"Approved recovery {run_id} from preview {request.preview_id}.",
            }
        control_payload = admin_network_control_confirm_canonical(
            canonical_execution,
            control_request,
        )
        control = control_payload.get("control") if isinstance(control_payload.get("control"), dict) else {}
        result = _control_step_result(step, control)
        step_results.append(result)
        _persist_step_result(
            run_id,
            step,
            result,
            started_at=step_started_at,
            repository=repository,
        )
        if result["status"] not in {"executed", "skipped"}:
            return _finish_recovery(
                run_id=run_id,
                preview=preview,
                started_at=started_at,
                status="stopped",
                summary="Oracle stopped because an approved recovery action did not complete safely.",
                step_results=step_results,
                repository=repository,
            )

    final_categories = _build_categories(_network_payload_for(True, canonical_execution))
    remaining_findings = _build_findings(final_categories)
    remaining_actionable = [item for item in remaining_findings if item.get("actionable") is True]
    status = "completed" if not remaining_actionable else "completed_with_issues"
    summary = (
        "Oracle completed the approved recovery plan."
        if status == "completed"
        else "Oracle completed the approved plan, but some network problems remain."
    )
    return _finish_recovery(
        run_id=run_id,
        preview=preview,
        started_at=started_at,
        status=status,
        summary=summary,
        step_results=step_results,
        remaining_findings=remaining_findings,
        repository=repository,
    )


def register_orchestration_recovery_routes(app: FastAPI) -> None:
    app.get("/api/ui/internet")(build_ui_internet_snapshot_http)
    app.post("/api/ui/orchestrations/{orchestration_id}/preview")(create_recovery_preview_http)
    app.post("/api/ui/orchestrations/{orchestration_id}/approve")(approve_recovery_preview_http)
    app.get("/api/ui/orchestration-previews/{preview_id}")(get_recovery_preview)


def clear_recovery_previews() -> None:
    with _PREVIEW_LOCK:
        _PREVIEWS.clear()
        _PREVIEW_STORED_MONOTONIC.clear()
        _CONSUMED_PREVIEWS.clear()


def _build_network_payload(*, force_refresh: bool, canonical_execution) -> dict[str, Any]:
    del force_refresh
    return dict(admin_network_status_canonical(canonical_execution)["network"])


def _network_payload_for(force_refresh: bool, canonical_execution) -> dict[str, Any]:
    if canonical_execution is None:
        raise HTTPException(status_code=409, detail="Canonical network recovery is not configured.")
    return _build_network_payload(
        force_refresh=force_refresh,
        canonical_execution=canonical_execution,
    )


def _build_categories(network: dict[str, Any]) -> list[dict[str, Any]]:
    hosts = [item for item in network.get("hosts") or [] if isinstance(item, dict)]
    satellites = [_public_item(item, item_type="satellite") for item in hosts if item.get("kind") == "satellite"]
    infrastructure = [
        _public_item(item, item_type="infrastructure")
        for item in hosts
        if item.get("kind") != "satellite"
    ]
    services = [_public_item(item, item_type="service") for item in _flatten_services(network)]
    internet_evidence = next(
        (
            item
            for item in network.get("evidence") or []
            if isinstance(item, dict) and str(item.get("id") or "") == "probe.internet"
        ),
        {},
    )
    internet = [
        {
            "id": "internet",
            "display_name": "Internet connection",
            "type": "internet",
            "status": str(internet_evidence.get("status") or network.get("status") or "unknown"),
            "severity": str(internet_evidence.get("severity") or network.get("severity") or "unknown"),
            "freshness": str(internet_evidence.get("freshness") or network.get("freshness") or "unknown"),
            "summary": str(internet_evidence.get("summary") or network.get("summary") or "Status unknown."),
            "control_actions": [],
        }
    ]
    return [
        _category("internet", "Internet", internet),
        _category("satellites", "Satellites", satellites),
        _category("services", "Services", services),
        _category("infrastructure", "Infrastructure", infrastructure),
    ]


def _flatten_services(network: dict[str, Any]) -> list[dict[str, Any]]:
    services: dict[str, dict[str, Any]] = {}
    for item in network.get("ungrouped_services") or []:
        if isinstance(item, dict) and item.get("id"):
            services[str(item["id"])] = item
    for host in network.get("hosts") or []:
        if not isinstance(host, dict):
            continue
        for item in host.get("services") or []:
            if isinstance(item, dict) and item.get("id"):
                services[str(item["id"])] = item
        for group in host.get("service_groups") or []:
            if not isinstance(group, dict):
                continue
            for item in group.get("services") or []:
                if isinstance(item, dict) and item.get("id"):
                    services[str(item["id"])] = item
    return list(services.values())


def _public_item(item: dict[str, Any], *, item_type: str) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "display_name": str(item.get("display_name") or item.get("id") or "Unknown"),
        "type": item_type,
        "status": str(item.get("status") or "unknown"),
        "severity": str(item.get("severity") or "unknown"),
        "freshness": str(item.get("freshness") or "unknown"),
        "summary": str(item.get("summary") or "Status unknown."),
        "control_actions": [
            _public_action(action)
            for action in item.get("control_actions") or []
            if isinstance(action, dict)
        ],
    }


def _public_action(action: dict[str, Any]) -> dict[str, Any]:
    availability = action.get("availability") if isinstance(action.get("availability"), dict) else {}
    return {
        "policy_id": str(action.get("id") or ""),
        "target_type": str(action.get("target_type") or ""),
        "target_id": str(action.get("target_id") or ""),
        "action_id": str(action.get("action_id") or ""),
        "enabled": action.get("enabled") is True,
        "requires_confirmation": action.get("requires_confirmation") is True,
        "description": str(action.get("description") or ""),
        "availability": str(availability.get("status") or "available"),
    }


def _category(category_id: str, label: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [str(item.get("status") or "unknown") for item in items]
    unhealthy = sum(1 for status in statuses if status in _ACTIONABLE_STATUSES)
    return {
        "id": category_id,
        "label": label,
        "status": _aggregate_status(statuses),
        "total": len(items),
        "unhealthy": unhealthy,
        "items": items,
    }


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    return max(statuses, key=lambda status: _STATUS_PRIORITY.get(status, 20))


def _build_findings(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for category in categories:
        for item in category.get("items") or []:
            status = str(item.get("status") or "unknown")
            if status not in _ACTIONABLE_STATUSES:
                continue
            findings.append(
                {
                    "category": category.get("id"),
                    "target_type": item.get("type"),
                    "target_id": item.get("id"),
                    "display_name": item.get("display_name"),
                    "status": status,
                    "summary": item.get("summary"),
                    "actionable": status in _ACTIONABLE_STATUSES
                    and any(action.get("enabled") is True for action in item.get("control_actions") or []),
                }
            )
    return findings


def _health_only_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **{
                key: _house_category_status(category) if key == "status" else value
                for key, value in category.items()
                if key != "items"
            },
            "items": [
                {key: value for key, value in item.items() if key != "control_actions"}
                for item in category.get("items") or []
                if str(item.get("status") or "unknown") in _ACTIONABLE_STATUSES
            ],
        }
        for category in categories
    ]


def _house_category_status(category: dict[str, Any]) -> str:
    status = str(category.get("status") or "unknown")
    if int(category.get("unhealthy") or 0) == 0 and status in {"unknown", "unconfigured"}:
        return "healthy"
    return status


def _household_summary(categories: list[dict[str, Any]]) -> str:
    by_id = {str(category.get("id") or ""): category for category in categories}
    internet_status = str(by_id.get("internet", {}).get("status") or "unknown")
    non_internet_statuses = [
        str(category.get("status") or "unknown")
        for category in categories
        if category.get("id") != "internet"
    ]
    if internet_status == "down":
        return "The internet connection appears to be down."
    if internet_status in _ACTIONABLE_STATUSES:
        return "The internet connection is degraded."
    if any(status in _ACTIONABLE_STATUSES for status in non_internet_statuses):
        return "The internet is working, but Oracle found a network problem."
    if internet_status == "healthy":
        return "The internet and monitored network systems look healthy."
    return "Oracle could not determine the current internet status."


def _build_preview_steps(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    category_order = {"infrastructure": 10, "services": 20, "satellites": 30, "internet": 40}
    candidates: list[tuple[int, int, str, dict[str, Any], dict[str, Any]]] = []
    for category in categories:
        for item in category.get("items") or []:
            if str(item.get("status") or "unknown") not in _ACTIONABLE_STATUSES:
                continue
            for action in item.get("control_actions") or []:
                if action.get("enabled") is not True:
                    continue
                candidates.append(
                    (
                        category_order.get(str(category.get("id") or ""), 99),
                        _ACTION_PRIORITY.get(str(action.get("action_id") or ""), 99),
                        str(item.get("display_name") or ""),
                        item,
                        action,
                    )
                )
    candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    steps: list[dict[str, Any]] = []
    for index, (_, _, _, item, action) in enumerate(candidates, start=1):
        effect = _action_user_effect(item, action)
        estimated_seconds, estimated_label = _action_duration_estimate(action)
        steps.append(
            {
                "step_id": f"step-{index}",
                "target_type": action.get("target_type") or item.get("type"),
                "target_id": action.get("target_id") or item.get("id"),
                "target_label": item.get("display_name"),
                "observed_target_type": item.get("type"),
                "observed_target_id": item.get("id"),
                "action_id": action.get("action_id"),
                "policy_id": action.get("policy_id"),
                "description": action.get("description"),
                "plain_language_summary": _plain_action_summary(item, action, effect, estimated_label),
                "user_effect": effect,
                "estimated_duration_seconds": estimated_seconds,
                "estimated_duration": estimated_label,
                "requires_confirmation": action.get("requires_confirmation") is True,
                "availability": action.get("availability"),
                "condition": "Run only if the target remains unhealthy after prior steps and fresh verification.",
            }
        )
    return steps


def _action_duration_estimate(action: dict[str, Any]) -> tuple[int, str]:
    action_id = str(action.get("action_id") or "")
    seconds, label = _ACTION_DURATION_ESTIMATES.get(action_id, (120, "about 1-2 minutes"))
    execution = action.get("execution") if isinstance(action.get("execution"), dict) else {}
    wait_seconds = _positive_int(execution.get("wait_seconds"))
    off_seconds = _positive_int(execution.get("off_seconds"))
    recovery_seconds = _positive_int(execution.get("recovery_timeout_seconds"))
    readiness_seconds = _positive_int(execution.get("readiness_timeout_seconds"))
    configured = wait_seconds + off_seconds
    if recovery_seconds:
        configured = max(configured, recovery_seconds)
    if readiness_seconds:
        configured += readiness_seconds
    if configured:
        seconds = max(seconds, configured)
        label = _duration_label(seconds)
    return seconds, label


def _positive_int(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _duration_label(seconds: int) -> str:
    if seconds <= 0:
        return ""
    if seconds <= 60:
        return "about 1 minute"
    minutes = max(1, round(seconds / 60))
    if minutes <= 2:
        return "about 1-2 minutes"
    if minutes <= 5:
        return "about 3-5 minutes"
    return f"about {minutes} minutes"


def _action_user_effect(item: dict[str, Any], action: dict[str, Any]) -> str:
    action_id = str(action.get("action_id") or "")
    target_id = str(action.get("target_id") or item.get("id") or "")
    target_label = str(item.get("display_name") or action.get("target_id") or "this device")
    target_type = str(action.get("target_type") or item.get("type") or "")
    normalized = f"{target_id} {target_label} {target_type}".lower()

    if action_id in {"restart_router", "power_cycle"} and any(
        token in normalized for token in ("router", "modem", "deco", "internet")
    ):
        return "The internet may be completely down while this restarts."
    if action_id == "restart_host":
        return f"{target_label} will be unavailable while it restarts."
    if "audiobook" in normalized:
        return "Audiobooks may be unavailable while this restarts."
    if "plex" in normalized:
        return "Plex music and video may be unavailable while this restarts."
    if "home assistant" in normalized or "home_assistant" in normalized:
        return "House controls in Oracle and Home Assistant may be unavailable while this restarts."
    if "pihole" in normalized or "pi-hole" in normalized:
        return "Some internet lookups may pause briefly while DNS restarts."
    if "oracle brain" in normalized or "oracle_brain" in normalized:
        return "Oracle may be unavailable while its brain service restarts."
    if target_type == "host" and action_id in {"restart_ui", "restart_runtime"}:
        return f"Oracle in {target_label} may be unavailable while this restarts."
    if action_id == "restart_service":
        return f"{target_label} may be unavailable while this service restarts."
    return f"{target_label} may be unavailable while Oracle tries this fix."


def _plain_action_summary(
    item: dict[str, Any],
    action: dict[str, Any],
    effect: str,
    estimated_label: str,
) -> str:
    action_id = str(action.get("action_id") or "").replace("_", " ")
    target_label = str(item.get("display_name") or action.get("target_id") or "this target")
    time_part = f" Estimated time: {estimated_label}." if estimated_label else ""
    return f"Oracle may {action_id} for {target_label}. {effect}{time_part}"


def _approval_summary(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "Oracle did not find anything it can safely fix right now."
    effects = [str(step.get("user_effect") or "").strip() for step in steps if step.get("user_effect")]
    total = sum(int(step.get("estimated_duration_seconds") or 0) for step in steps)
    time_part = f" Estimated total time: {_duration_label(total)}." if total else ""
    if len(steps) == 1:
        return f"Approve 1 possible fix. {effects[0] if effects else ''}{time_part}".strip()
    return f"Approve {len(steps)} possible fixes. {effects[0] if effects else ''}{time_part}".strip()


def _recovery_summary(orchestration_id: str, *, canonical_execution=None) -> dict[str, Any]:
    try:
        definition = _find_recovery(orchestration_id, canonical_execution=canonical_execution)
    except HTTPException:
        return {
            "id": orchestration_id,
            "enabled": False,
            "preview_available": False,
            "execution_available": False,
        }
    return {
        "id": orchestration_id,
        "display_name": definition.get("display_name"),
        "description": definition.get("description"),
        "enabled": definition.get("enabled") is True,
        "preview_available": definition.get("enabled") is True,
        "execution_available": definition.get("enabled") is True,
    }


def _find_recovery(orchestration_id: str, *, canonical_execution) -> dict[str, Any]:
    normalized_id = str(orchestration_id or "").strip()
    if canonical_execution is None:
        raise HTTPException(status_code=409, detail="Canonical network recovery is not configured.")
    runtime = canonical_execution.policy.recovery(normalized_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Recovery runbook was not found.")
    definition = runtime.definition
    return {
        "id": definition.id,
        "enabled": definition.enabled,
        "display_name": definition.display_name,
        "description": definition.description,
        "approval_mode": definition.approval_mode,
        "diagnostic_profile": definition.diagnostic_profile,
        "remediation_profile": definition.remediation_profile,
        "triggers": {
            "ui": definition.triggers.ui,
            "voice": definition.triggers.voice,
            "global_phrases": list(definition.triggers.global_phrases),
        },
    }


def build_ui_internet_snapshot_http(request: Request) -> dict[str, Any]:
    canonical, execution = _canonical_network_context(request)
    if canonical and execution is None:
        return {"ok": False, "status": "unconfigured", "categories": []}
    return build_ui_internet_snapshot(canonical_execution=execution)


def create_recovery_preview_http(
    request: Request,
    orchestration_id: str,
    payload: UiOrchestrationPreviewRequest,
) -> dict[str, Any]:
    canonical, execution = _canonical_network_context(request)
    if canonical and execution is None:
        raise HTTPException(status_code=409, detail="Canonical network recovery is not configured.")
    return create_recovery_preview(
        orchestration_id,
        payload,
        canonical_execution=execution,
    )


def approve_recovery_preview_http(
    request: Request,
    orchestration_id: str,
    payload: UiOrchestrationApprovalRequest,
) -> dict[str, Any]:
    canonical, execution = _canonical_network_context(request)
    if canonical and execution is None:
        raise HTTPException(status_code=409, detail="Canonical network recovery is not configured.")
    return approve_recovery_preview(
        orchestration_id,
        payload,
        canonical_execution=execution,
    )


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


def _preview_digest(preview: dict[str, Any]) -> str:
    encoded = json.dumps(preview, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _store_preview(preview: dict[str, Any]) -> None:
    _prune_previews()
    preview_id = str(preview["preview_id"])
    with _PREVIEW_LOCK:
        _PREVIEWS[preview_id] = copy.deepcopy(preview)
        _PREVIEW_STORED_MONOTONIC[preview_id] = time.monotonic()
        if len(_PREVIEWS) <= _MAX_PREVIEWS:
            return
        oldest = min(_PREVIEW_STORED_MONOTONIC, key=_PREVIEW_STORED_MONOTONIC.get)
        _PREVIEWS.pop(oldest, None)
        _PREVIEW_STORED_MONOTONIC.pop(oldest, None)
        _CONSUMED_PREVIEWS.discard(oldest)


def _prune_previews() -> None:
    now = time.monotonic()
    with _PREVIEW_LOCK:
        expired = [
            preview_id
            for preview_id, stored_at in _PREVIEW_STORED_MONOTONIC.items()
            if now - stored_at > _PREVIEW_TTL_SECONDS
        ]
        for preview_id in expired:
            _PREVIEWS.pop(preview_id, None)
            _PREVIEW_STORED_MONOTONIC.pop(preview_id, None)
            _CONSUMED_PREVIEWS.discard(preview_id)


def _get_preview_for_approval(preview_id: str) -> dict[str, Any]:
    _prune_previews()
    normalized_id = str(preview_id or "").strip()
    with _PREVIEW_LOCK:
        if normalized_id in _CONSUMED_PREVIEWS:
            raise HTTPException(status_code=409, detail="Recovery preview approval was already used.")
        preview = _PREVIEWS.get(normalized_id)
        if not isinstance(preview, dict):
            raise HTTPException(status_code=404, detail="Recovery preview was not found or has expired.")
        return copy.deepcopy(preview)


def _claim_preview(preview_id: str) -> None:
    normalized_id = str(preview_id or "").strip()
    with _PREVIEW_LOCK:
        if normalized_id in _CONSUMED_PREVIEWS:
            raise HTTPException(status_code=409, detail="Recovery preview approval was already used.")
        if normalized_id not in _PREVIEWS:
            raise HTTPException(status_code=404, detail="Recovery preview was not found or has expired.")
        _CONSUMED_PREVIEWS.add(normalized_id)


def _release_preview_claim(preview_id: str) -> None:
    with _PREVIEW_LOCK:
        _CONSUMED_PREVIEWS.discard(str(preview_id or "").strip())


def _step_identity(step: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(step.get("policy_id") or ""),
        str(step.get("target_type") or ""),
        str(step.get("target_id") or ""),
        str(step.get("action_id") or ""),
    )


def _current_step_identities(categories: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {_step_identity(step) for step in _build_preview_steps(categories)}


def _find_observed_item(categories: list[dict[str, Any]], step: dict[str, Any]) -> dict[str, Any]:
    observed_id = str(step.get("observed_target_id") or step.get("target_id") or "")
    observed_type = str(step.get("observed_target_type") or "")
    for category in categories:
        for item in category.get("items") or []:
            if (
                str(item.get("id") or "") == observed_id
                and (not observed_type or str(item.get("type") or "") == observed_type)
            ):
                return item
    return {}


def _skipped_step_result(step: dict[str, Any], current_status: str) -> dict[str, Any]:
    return {
        "step_id": step.get("step_id"),
        "target_label": step.get("target_label"),
        "action_id": step.get("action_id"),
        "status": "skipped",
        "summary": f"Skipped because the target is now {current_status}.",
        "verification_status": "not_required",
    }


def _control_step_result(step: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    execution = control.get("execution") if isinstance(control.get("execution"), dict) else {}
    return {
        "step_id": step.get("step_id"),
        "target_label": step.get("target_label"),
        "action_id": step.get("action_id"),
        "request_id": str(control.get("request_id") or ""),
        "status": str(control.get("result_status") or "failed"),
        "summary": str(control.get("summary") or "Network action returned no summary."),
        "error_class": str(control.get("error_class") or ""),
        "verification_status": str(execution.get("verification_status") or "unknown"),
    }


def _finish_recovery(
    *,
    run_id: str,
    preview: dict[str, Any],
    started_at: str,
    status: str,
    summary: str,
    step_results: list[dict[str, Any]],
    repository: RunbookRepository,
    remaining_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "preview_id": preview.get("preview_id"),
        "orchestration_id": preview.get("orchestration_id"),
        "status": status,
        "summary": summary,
        "started_at": started_at,
        "completed_at": datetime.now().astimezone().isoformat(),
        "steps": step_results,
        "remaining_findings": list(remaining_findings or []),
        "approval_consumed": True,
    }
    completed_step_ids = {str(item.get("step_id") or "") for item in step_results}
    for ordinal, step in enumerate(preview.get("steps") or [], start=1):
        step_id = str(step.get("step_id") or f"step-{ordinal}")
        if step_id in completed_step_ids:
            continue
        repository.record_operation(
            run_id=run_id,
            operation_id=step_id,
            ordinal=ordinal,
            status="not_run",
            operation_kind=str(step.get("target_type") or ""),
            target_id=str(step.get("target_id") or ""),
            target_label=str(step.get("target_label") or ""),
            capability_id=str(step.get("action_id") or ""),
            policy_id=str(step.get("policy_id") or ""),
            summary="This approved step was not run because the recovery stopped earlier.",
            completed_at=result["completed_at"],
        )
    repository.transition_run(
        run_id,
        status=status,
        summary=summary,
        completed_at=result["completed_at"],
        controller_state={
            "approved_step_count": len(preview.get("steps") or []),
            "completed_step_count": len(step_results),
        },
        payload={
            "preview_id": preview.get("preview_id"),
            "remaining_finding_count": len(result["remaining_findings"]),
            "completed_step_count": len(step_results),
        },
    )
    _record_recovery_event(
        "orchestration_recovery_completed",
        run_id=run_id,
        preview=preview,
        client_id=str(preview.get("client_id") or ""),
        status=status,
        result=result,
    )
    return {"ok": status in {"completed", "completed_with_issues"}, "run": result}


def _persist_step_running(
    run_id: str,
    step: dict[str, Any],
    *,
    started_at: str,
    repository: RunbookRepository,
) -> None:
    repository.record_operation(
        run_id=run_id,
        operation_id=str(step.get("step_id") or ""),
        ordinal=_step_ordinal(step),
        status="running",
        operation_kind=str(step.get("target_type") or ""),
        target_id=str(step.get("target_id") or ""),
        target_label=str(step.get("target_label") or ""),
        capability_id=str(step.get("action_id") or ""),
        policy_id=str(step.get("policy_id") or ""),
        summary="Running the approved action through network control.",
        started_at=started_at,
    )


def _persist_step_result(
    run_id: str,
    step: dict[str, Any],
    result: dict[str, Any],
    *,
    started_at: str = "",
    repository: RunbookRepository,
) -> None:
    repository.record_operation(
        run_id=run_id,
        operation_id=str(step.get("step_id") or ""),
        ordinal=_step_ordinal(step),
        status=str(result.get("status") or "failed"),
        operation_kind=str(step.get("target_type") or ""),
        target_id=str(step.get("target_id") or ""),
        target_label=str(step.get("target_label") or ""),
        capability_id=str(step.get("action_id") or ""),
        policy_id=str(step.get("policy_id") or ""),
        summary=str(result.get("summary") or ""),
        request_id=str(result.get("request_id") or ""),
        error_class=str(result.get("error_class") or ""),
        verification_status=str(result.get("verification_status") or ""),
        started_at=started_at,
        completed_at=datetime.now().astimezone().isoformat(),
    )


def _step_ordinal(step: dict[str, Any]) -> int:
    raw = str(step.get("step_id") or "").removeprefix("step-")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _repository() -> RunbookRepository:
    return RunbookRepository()


def _recovery_definition_version(preview: dict[str, Any]) -> str:
    identity = {
        "orchestration_id": preview.get("orchestration_id"),
        "display_name": preview.get("display_name"),
        "diagnostic_profile": preview.get("diagnostic_profile"),
        "remediation_profile": preview.get("remediation_profile"),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _record_recovery_event(
    event_type: str,
    *,
    run_id: str,
    preview: dict[str, Any],
    client_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    safe_record_event(
        event_type,
        source_id="brain",
        correlation_id=run_id,
        domain="orchestration",
        status=status,
        payload={
            "run_id": run_id,
            "preview_id": preview.get("preview_id"),
            "orchestration_id": preview.get("orchestration_id"),
            "digest": preview.get("digest"),
            "client_id": client_id,
            "approved_step_count": len(preview.get("steps") or []),
            **({"result": result} if result else {}),
        },
    )
