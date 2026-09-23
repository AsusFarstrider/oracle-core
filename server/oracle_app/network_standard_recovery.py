"""The one closed, explicitly approved household network restart runbook."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable


def build_standard_steps(definition: dict[str, Any], execution: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    effects = (
        "Internet access may be unavailable while the modem power cycles and WAN returns.",
        "Router, LAN, and Internet access may be unavailable during restart.",
        "DNS lookups may pause briefly while Oracle-host Pi-hole restarts.",
        "Renegade edge services, including DNS and tunnel services, may be unavailable during host restart.",
    )
    for ordinal, configured in enumerate(definition.get("sequence") or [], start=1):
        action = execution.policy.action(str(configured.get("action_policy_id") or ""))
        if action is None:
            raise ValueError("Standard network restart action is unavailable.")
        policy = action.definition
        target = action.target
        target_label = str(getattr(target, "display_name", None) or getattr(getattr(target, "host", None), "display_name", None) or policy.target_id)
        readiness_timeout = int(configured.get("readiness_timeout_seconds") or 300)
        execution_policy = policy.execution
        action_estimate = max(60, sum(int(value or 0) for value in (
            execution_policy.restart_timeout_seconds,
            execution_policy.shutdown_timeout_seconds,
            execution_policy.recovery_timeout_seconds,
            execution_policy.readiness_timeout_seconds,
            execution_policy.verification_timeout_seconds,
            execution_policy.wait_seconds,
            execution_policy.off_seconds,
        )))
        steps.append({
            "step_id": f"step-{ordinal}",
            "policy_id": policy.id,
            "target_type": policy.target_type,
            "target_id": policy.target_id,
            "target_label": target_label,
            "action_id": policy.operation,
            "requires_confirmation": True,
            "plain_language_summary": str(policy.description),
            "user_effect": effects[ordinal - 1],
            "estimated_duration_seconds": action_estimate + readiness_timeout,
            "estimated_duration": f"up to {action_estimate + readiness_timeout} seconds",
            "condition": "Run in this fixed order after explicit approval, regardless of current health.",
            "readiness_evidence_ids": list(configured.get("readiness_evidence_ids") or []),
            "readiness_timeout_seconds": readiness_timeout,
            "readiness_poll_seconds": int(configured.get("readiness_poll_seconds") or 5),
        })
    return steps


def execute_standard_sequence(
    *,
    preview: dict[str, Any],
    approved_steps: list[dict[str, Any]],
    canonical_execution: Any,
    client_id: str,
    run_id: str,
    started_at: str,
    repository: Any,
    finish: Callable[..., dict[str, Any]],
    persist_running: Callable[..., None],
    persist_result: Callable[..., None],
    control_confirm: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for step in approved_steps:
        action_started_at = datetime.now().astimezone().isoformat()
        persist_running(run_id, step, started_at=action_started_at, repository=repository)
        control_payload = control_confirm(canonical_execution, {
            "target_type": step["target_type"],
            "target_id": step["target_id"],
            "action_id": step["action_id"],
            "confirmed": True,
            "actor": client_id,
            "source": "house_mode_recovery",
            "reason": f"Approved standard network restart {run_id} from preview {preview['preview_id']}.",
        })
        control = control_payload.get("control") if isinstance(control_payload.get("control"), dict) else {}
        execution = control.get("execution") if isinstance(control.get("execution"), dict) else {}
        result = {
            "step_id": step["step_id"],
            "target_label": step["target_label"],
            "action_id": step["action_id"],
            "request_id": str(control.get("request_id") or ""),
            "status": str(control.get("result_status") or "failed"),
            "summary": str(control.get("summary") or "Network action returned no result."),
            "error_class": str(control.get("error_class") or ""),
            "verification_status": str(execution.get("verification_status") or "unknown"),
        }
        if result["status"] == "executed" and result["verification_status"] == "passed":
            readiness = wait_for_evidence(
                canonical_execution,
                evidence_ids=list(step.get("readiness_evidence_ids") or []),
                timeout_seconds=int(step.get("readiness_timeout_seconds") or 300),
                poll_seconds=int(step.get("readiness_poll_seconds") or 5),
                since=action_started_at,
            )
            result["readiness"] = readiness
            if readiness["status"] != "passed":
                result.update(
                    status="failed",
                    error_class="network_recovery_readiness_failed",
                    summary="The action completed, but required network readiness did not return within the bounded wait.",
                )
        elif result["status"] == "executed":
            result.update(
                status="failed",
                error_class="network_recovery_action_unverified",
                summary="The action was sent but did not return verified completion; dependent steps were not run.",
            )
        results.append(result)
        persist_result(run_id, step, result, started_at=action_started_at, repository=repository)
        if result["status"] != "executed":
            return finish(
                run_id=run_id, preview=preview, started_at=started_at,
                status="stopped", summary="Standard network restart stopped at an action or readiness boundary.",
                step_results=results, repository=repository,
            )

    final = wait_for_evidence(
        canonical_execution,
        evidence_ids=list(preview.get("final_evidence_ids") or []),
        timeout_seconds=int(preview.get("final_timeout_seconds") or 300),
        poll_seconds=int(preview.get("final_poll_seconds") or 5),
        since=action_started_at,
    )
    status = "completed" if final["status"] == "passed" else "completed_with_issues"
    summary = (
        "The standard network restart and final network, DNS, and edge verification completed."
        if status == "completed"
        else "All approved restarts completed, but final network, DNS, or edge verification did not pass."
    )
    return finish(
        run_id=run_id, preview=preview, started_at=started_at, status=status,
        summary=summary, step_results=results, repository=repository,
        final_verification=final,
        remaining_findings=[] if status == "completed" else [{
            "category": "network_verification", "target_id": item,
            "status": "unverified", "actionable": False,
        } for item in final["unready_evidence_ids"]],
    )


def wait_for_evidence(
    execution: Any,
    *,
    evidence_ids: list[str],
    timeout_seconds: int,
    poll_seconds: int,
    since: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    required = set(evidence_ids)
    unready = sorted(required)
    observations: list[dict[str, str]] = []
    while True:
        try:
            snapshot = execution.status_snapshot(force_refresh=True)
            evidence = {str(item.get("id") or ""): item for item in snapshot.get("evidence") or [] if isinstance(item, dict)}
            observations = [{
                "id": identity,
                "status": str(evidence.get(identity, {}).get("status") or "missing"),
                "freshness": str(evidence.get(identity, {}).get("freshness") or "unknown"),
                "observed_at": str(evidence.get(identity, {}).get("observed_at") or ""),
            } for identity in sorted(required)]
            unready = sorted(
                identity for identity in required
                if not _ready(evidence.get(identity), since=since)
            )
        except Exception:
            unready = sorted(required)
        if not unready:
            return {"status": "passed", "unready_evidence_ids": [], "observations": observations}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timed_out", "unready_evidence_ids": unready, "observations": observations}
        time.sleep(min(poll_seconds, remaining))


def _ready(item: Any, *, since: str) -> bool:
    if not isinstance(item, dict) or item.get("status") != "healthy" or item.get("freshness") != "fresh":
        return False
    try:
        observed = datetime.fromisoformat(str(item.get("observed_at") or ""))
        started = datetime.fromisoformat(since)
        return observed >= started
    except (TypeError, ValueError):
        return False
