from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping

from fastapi import HTTPException

from .capabilities.semantic import (
    CapabilityOutcome,
    ConfirmationClass,
    SEMANTIC_CAPABILITY_REGISTRY,
)
from .configuration.composite_definition import CompositeDefinition
from .configuration.composite_semantics import (
    ConditionTruth,
    evaluate_composite_condition,
    resolve_composite_inputs,
)
from .memory.runtime import safe_record_event
from .runbook_kernel import RunbookActivation, RunbookDefinitionRef, RunbookRepository
from .runbook_kernel import DuplicateRunActivationError


CompositeAdapter = Callable[..., dict[str, Any]]
_CONTROLLER_VERSION = "2"
_SUCCESS_OPERATION_STATUSES = frozenset({"completed", "skipped"})
_FAILED_OPERATION_STATUSES = frozenset({"failed", "blocked", "uncertain", "canceled"})
_TERMINAL_OPERATION_STATUSES = _SUCCESS_OPERATION_STATUSES | _FAILED_OPERATION_STATUSES
_CONTROLLER_LOCK = RLock()


def _serialized(function):
    def call(*args, **kwargs):
        with _CONTROLLER_LOCK:
            return function(*args, **kwargs)

    return call


@_serialized
def start_composite(
    definition: dict[str, Any],
    *,
    client_id: str,
    inputs: Mapping[str, Any] | None,
    config_revision: str,
    adapters: Mapping[str, CompositeAdapter],
    definition_catalog: Mapping[str, dict[str, Any]] | None = None,
    invocation: str = "manual",
    activation_idempotency_key: str = "",
    trigger: Mapping[str, Any] | None = None,
    db_path: Path | None = None,
    parent_run_id: str = "",
    parent_operation_id: str = "",
) -> dict[str, Any]:
    composition = CompositeDefinition.model_validate(definition.get("composition"))
    resolution = resolve_composite_inputs(composition, inputs or {}, invocation=invocation)
    if resolution.status != "resolved" and invocation == "manual":
        raise HTTPException(status_code=400, detail="Composite routine requires its declared bounded inputs.")
    _require_confirmation_or_preauthorization(composition)
    repository = RunbookRepository(db_path=db_path)
    definition_id = str(definition["id"])
    clean_idempotency_key = str(activation_idempotency_key or "").strip()
    if clean_idempotency_key:
        existing = repository.find_by_activation_idempotency_key(clean_idempotency_key)
        if existing is not None:
            return existing
    active = [
        run
        for status in ("running", "waiting")
        for run in repository.list_runs(
            definition_id=definition_id,
            kind="routine",
            status=status,
            limit=1,
        )
    ]
    if active and invocation == "manual":
        raise HTTPException(status_code=409, detail="This routine already has an active run.")
    run_id = f"routine-{uuid.uuid4().hex}"
    started_at = _utc_now()
    catalog = dict(definition_catalog or {definition_id: definition})
    catalog.setdefault(definition_id, definition)
    payload = {
        "definition": definition,
        "definition_catalog": catalog,
        "inputs": dict(resolution.values),
        "config_revision": str(config_revision),
        "parent_run_id": parent_run_id,
        "parent_operation_id": parent_operation_id,
        "invocation": invocation,
        "trigger": dict(trigger or {}),
    }
    initial_status = "stopped" if active or resolution.status != "resolved" else "running"
    initial_summary = (
        "Automatic trigger skipped because this routine is already active."
        if active
        else "Automatic trigger skipped because required bounded input was missing."
        if resolution.status != "resolved"
        else f"{definition.get('display_name') or definition_id} started."
    )
    try:
        repository.create_run(
            RunbookDefinitionRef(
                definition_id=definition_id,
                kind="routine",
                domain="composite",
                version=_definition_version(definition),
                controller_version=_CONTROLLER_VERSION,
            ),
            RunbookActivation(
                run_id=run_id,
                started_at=started_at,
                correlation_key=f"routine:{definition_id}",
                idempotency_key=clean_idempotency_key,
                client_id=client_id,
            ),
            status="running",
            summary=initial_summary,
            controller_state={"phase": "main", "compensation_outcome": "not_required"},
            parent_run_id=parent_run_id,
            parent_operation_id=parent_operation_id,
            payload=payload,
        )
    except DuplicateRunActivationError:
        existing = repository.find_by_activation_idempotency_key(clean_idempotency_key)
        if existing is None:
            raise
        return existing
    for ordinal, operation in enumerate(composition.model_dump(mode="json")["operations"], start=1):
        repository.record_operation(
            run_id=run_id,
            operation_id=str(operation["id"]),
            ordinal=ordinal,
            status=(
                "not_run"
                if initial_status == "stopped"
                else "pending" if operation.get("phase", "main") == "main" else "dormant"
            ),
            operation_kind=str(operation["type"]),
            target_id=_operation_target(operation),
            target_label=str(operation.get("label") or operation["id"]),
            capability_id=str(operation.get("capability_id") or ""),
            summary="Pending." if operation.get("phase", "main") == "main" else "Inactive phase.",
            payload={
                "definition": operation,
                "attempt": 0,
                "iteration": 0,
                "results": [],
            },
        )
    safe_record_event(
        "orchestration_routine_started",
        severity="info",
        source_id="brain",
        domain="orchestration",
        status=initial_status,
        correlation_id=run_id,
        db_path=db_path,
        payload={"run_id": run_id, "orchestration_id": definition_id, "client_id": client_id},
    )
    if initial_status == "stopped":
        stopped = repository.transition_run(
            run_id,
            status="stopped",
            summary=initial_summary,
            controller_state={"phase": "main", "compensation_outcome": "not_required"},
        )
        safe_record_event(
            "orchestration_routine_completed",
            severity="warning",
            source_id="brain",
            domain="orchestration",
            status="stopped",
            correlation_id=run_id,
            db_path=db_path,
            payload={"run_id": run_id, "orchestration_id": definition_id, "summary": initial_summary},
        )
        return stopped
    return advance_composite(run_id, adapters=adapters, db_path=db_path)


@_serialized
def advance_composite(
    run_id: str,
    *,
    adapters: Mapping[str, CompositeAdapter],
    db_path: Path | None = None,
) -> dict[str, Any]:
    repository = RunbookRepository(db_path=db_path)
    while True:
        run = repository.require_run(run_id)
        if run["status"] not in {"running", "waiting"} or run["status"] == "waiting":
            return run
        payload = dict(run["payload"])
        definition = dict(payload["definition"])
        composition = CompositeDefinition.model_validate(definition["composition"])
        operations = composition.model_dump(mode="json")["operations"]
        steps = {str(item["step_id"]): item for item in run["steps"]}
        phase = str(run["controller_state"].get("phase") or "main")

        _block_failed_descendants(run_id, operations, steps, repository)
        run = repository.require_run(run_id)
        steps = {str(item["step_id"]): item for item in run["steps"]}
        ready = _next_ready_operation(operations, steps)
        if ready is None:
            active = [step for step in steps.values() if step["status"] in {"pending", "running", "waiting"}]
            if active:
                if any(step["status"] == "waiting" for step in active):
                    return repository.transition_run(
                        run_id,
                        status="waiting",
                        summary="Composite routine is waiting.",
                        controller_state=run["controller_state"],
                    )
                return _interrupt_uncertain(run, repository=repository, db_path=db_path)
            return _finish_phase(run, composition, repository=repository, db_path=db_path, adapters=adapters)

        operation, step = ready
        condition_action = _condition_action(operation, payload, steps, adapters)
        if condition_action != "execute":
            if condition_action.startswith("fallback:"):
                fallback_id = condition_action.split(":", 1)[1]
                _activate_operations(run_id, [fallback_id], repository)
            _record_terminal_operation(
                run_id,
                step,
                status="skipped" if condition_action in {"skip", "false"} or condition_action.startswith("fallback:") else "failed",
                summary=("Condition did not match." if condition_action == "false" else "Condition evidence was unavailable."),
                error_class="" if condition_action in {"skip", "false"} or condition_action.startswith("fallback:") else "condition_unknown",
                repository=repository,
            )
            if condition_action == "stop":
                return _begin_failure(run_id, composition, repository=repository, db_path=db_path, adapters=adapters)
            continue

        outcome = _execute_operation(
            run,
            operation,
            step,
            composition=composition,
            adapters=adapters,
            repository=repository,
            db_path=db_path,
        )
        if outcome in {"waiting", "terminal"}:
            return repository.require_run(run_id)
        if outcome == "failed":
            critical = bool(operation.get("critical", True))
            if composition.run_policy == "fail_fast" and critical:
                return _begin_failure(run_id, composition, repository=repository, db_path=db_path, adapters=adapters)
        if phase in {"on_failure", "on_cancel"}:
            continue


@_serialized
def resume_composite_runs(
    *,
    adapters: Mapping[str, CompositeAdapter],
    required_config_revision: str,
    now: datetime | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    repository = RunbookRepository(db_path=db_path)
    current = now or _utc_datetime()
    resumed: list[dict[str, Any]] = []
    runs = repository.list_runs(kind="routine", status="waiting", limit=100)
    runs.extend(repository.list_runs(kind="routine", status="running", limit=100))
    for run in runs:
        if str(run.get("controller_version") or "") != _CONTROLLER_VERSION:
            continue
        payload = dict(run["payload"])
        if str(payload.get("config_revision") or "") != str(required_config_revision):
            resumed.append(_terminal_run(
                run,
                "failed",
                "Composite continuation configuration revision no longer matches the frozen run.",
                repository=repository,
                db_path=db_path,
            ))
            continue
        if run["status"] == "running":
            resumed.append(advance_composite(str(run["run_id"]), adapters=adapters, db_path=db_path))
            continue
        progressed = False
        for step in run["steps"]:
            if step["status"] != "waiting":
                continue
            state = dict(step["payload"])
            operation = dict(state["definition"])
            kind = str(operation["type"])
            if kind == "child":
                child_id = str(state.get("child_run_id") or "")
                child = repository.get_run(child_id) if child_id else None
                if child is None or child["status"] in {"running", "waiting"}:
                    continue
                success = child["status"] in {"completed", "completed_with_issues"}
                child_result = {"ok": success, "child_run_id": child_id, "child_status": child["status"]}
                if success:
                    _finish_iteration(step, operation, child_result, repository)
                else:
                    _record_terminal_operation(
                        str(run["run_id"]),
                        step,
                        status="failed",
                        summary="Child run did not complete successfully.",
                        error_class="child_run_failed",
                        repository=repository,
                        result=child_result,
                    )
                progressed = True
                continue
            due_at = _parse_datetime(state.get("due_at"))
            if due_at is None or current < due_at:
                continue
            if kind == "wait":
                lateness = max(0, int((current - due_at).total_seconds()))
                if lateness > int(operation.get("max_lateness_seconds") or 0):
                    _record_terminal_operation(str(run["run_id"]), step, status="failed", summary="Durable wait exceeded its maximum lateness.", error_class="max_lateness_exceeded", repository=repository)
                else:
                    _finish_iteration(step, operation, {"ok": True, "lateness_seconds": lateness}, repository)
            elif kind == "wait_until":
                deadline = _parse_datetime(state.get("deadline"))
                if deadline is not None and current > deadline:
                    _record_terminal_operation(str(run["run_id"]), step, status="failed", summary="Wait-until timed out.", error_class="wait_until_timeout", repository=repository)
                else:
                    _record_step(str(run["run_id"]), step, status="pending", summary="Ready to poll.", payload=state, repository=repository)
            else:
                _record_step(str(run["run_id"]), step, status="pending", summary="Retry delay completed.", payload=state, repository=repository)
            progressed = True
        if progressed or not any(step["status"] == "waiting" for step in run["steps"]):
            current_run = repository.require_run(str(run["run_id"]))
            repository.transition_run(
                str(run["run_id"]),
                status="running",
                summary="Composite routine resumed.",
                controller_state=current_run["controller_state"],
            )
            resumed.append(advance_composite(str(run["run_id"]), adapters=adapters, db_path=db_path))
    return resumed


def cancel_composite(
    run_id: str,
    *,
    requester: str,
    adapters: Mapping[str, CompositeAdapter],
    db_path: Path | None = None,
) -> dict[str, Any]:
    repository = RunbookRepository(db_path=db_path)
    run = repository.require_run(run_id)
    if run["status"] not in {"running", "waiting"}:
        return run
    payload = dict(run["payload"])
    composition = CompositeDefinition.model_validate(payload["definition"]["composition"])
    cancellation_state = dict(run["controller_state"])
    cancellation_state.update(cancellation_requested=True, cancellation_requester=requester)
    run = repository.transition_run(
        run_id,
        status=str(run["status"]),
        summary="Composite cancellation requested.",
        controller_state=cancellation_state,
    )
    for step in run["steps"]:
        if step["status"] not in {"running", "waiting"}:
            continue
        operation = dict(step["payload"].get("definition") or {})
        if operation.get("type") == "child":
            child_run_id = str(step["payload"].get("child_run_id") or "")
            if child_run_id:
                cancel_composite(child_run_id, requester=requester, adapters=adapters, db_path=db_path)
        elif step["status"] == "running":
            capability_id = str(operation.get("capability_id") or "")
            capability = SEMANTIC_CAPABILITY_REGISTRY.require(capability_id)
            if capability.cancellation.value != "active_operation":
                raise HTTPException(status_code=409, detail="The active capability cannot be reported canceled safely.")
            cancel_adapter = adapters.get(f"cancel:{capability_id}")
            if cancel_adapter is None or cancel_adapter(
                run_id=run_id,
                operation_id=str(step["step_id"]),
                arguments=_resolve_arguments(operation.get("arguments") or {}, payload.get("inputs") or {}),
            ).get("ok") is not True:
                raise HTTPException(status_code=409, detail="The active capability could not be canceled safely.")
            _record_step(
                run_id,
                step,
                status="canceled",
                summary="Active capability cancellation was accepted.",
                payload=dict(step["payload"]),
                repository=repository,
                completed_at=_utc_now(),
            )
    compensation_ids = set(composition.on_cancel)
    if compensation_ids:
        _activate_operations(run_id, composition.on_cancel, repository)
    now = _utc_now()
    for step in repository.require_run(run_id)["steps"]:
        if step["status"] in {"pending", "waiting", "dormant"} and str(step["step_id"]) not in compensation_ids:
            _record_step(run_id, step, status="canceled", summary="Canceled before execution.", payload=dict(step["payload"]), repository=repository, completed_at=now)
    if composition.on_cancel:
        repository.transition_run(
            run_id,
            status="running",
            summary="Running explicit cancellation compensation.",
            controller_state={"phase": "on_cancel", "terminal_after_compensation": "canceled", "compensation_outcome": "running", "cancellation_requester": requester},
            cancellation_reason=None,
            cancellation_requester=None,
        )
        return advance_composite(run_id, adapters=adapters, db_path=db_path)
    return _terminal_run(run, "canceled", "Composite routine canceled.", repository=repository, db_path=db_path, cancellation_requester=requester)


def _execute_operation(run: dict[str, Any], operation: dict[str, Any], step: dict[str, Any], *, composition: CompositeDefinition, adapters: Mapping[str, CompositeAdapter], repository: RunbookRepository, db_path: Path | None) -> str:
    run_id = str(run["run_id"])
    state = dict(step["payload"])
    state["attempt"] = int(state.get("attempt") or 0) + 1
    state.setdefault("iteration", 0)
    kind = str(operation["type"])
    if kind == "wait":
        due = _utc_datetime() + timedelta(seconds=int(operation["duration_seconds"]))
        state["due_at"] = due.isoformat()
        _record_step(run_id, step, status="waiting", summary=f"Waiting until {due.isoformat()}.", payload=state, repository=repository, started_at=step.get("started_at") or _utc_now())
        return _wait_run(run, repository)
    if kind == "wait_until":
        adapter = adapters.get(f"state:{operation['predicate_id']}")
        observed = _read_state(adapter, str(operation["target_id"]))
        actual = str((observed or {}).get("state") or "")
        if actual == str(operation["expected_value"]):
            _finish_iteration(step, operation, {"ok": True, "state": actual}, repository, state=state)
            return "continue"
        now = _utc_datetime()
        deadline = _parse_datetime(state.get("deadline")) or now + timedelta(seconds=int(operation["timeout_seconds"]))
        if now >= deadline:
            _record_terminal_operation(run_id, step, status="failed", summary="Wait-until timed out.", error_class="wait_until_timeout", repository=repository)
            return "failed"
        state.update(deadline=deadline.isoformat(), due_at=(now + timedelta(seconds=int(operation["poll_seconds"]))).isoformat())
        _record_step(run_id, step, status="waiting", summary="Waiting for registered state evidence.", payload=state, repository=repository, started_at=step.get("started_at") or _utc_now())
        return _wait_run(run, repository)
    if kind == "child":
        child_definition = dict(run["payload"]["definition_catalog"][str(operation["definition_id"])])
        child_inputs = _resolve_arguments(operation.get("inputs") or {}, run["payload"].get("inputs") or {})
        child = start_composite(
            child_definition,
            client_id=str(run.get("client_id") or "orchestration"),
            inputs=child_inputs,
            config_revision=str(run["payload"].get("config_revision") or ""),
            adapters=adapters,
            definition_catalog=run["payload"]["definition_catalog"],
            db_path=db_path,
            parent_run_id=run_id,
            parent_operation_id=str(operation["id"]),
        )
        state["child_run_id"] = child["run_id"]
        if child["status"] in {"running", "waiting"}:
            _record_step(run_id, step, status="waiting", summary="Waiting for child run.", payload=state, repository=repository, started_at=step.get("started_at") or _utc_now())
            return _wait_run(run, repository)
        result = {"ok": child["status"] in {"completed", "completed_with_issues"}, "child_run_id": child["run_id"], "child_status": child["status"]}
    else:
        capability_id = str(operation["capability_id"])
        adapter = adapters.get(capability_id)
        if adapter is None:
            result = {"ok": False, "outcome": CapabilityOutcome.UNAVAILABLE.value, "error": "capability_adapter_unavailable"}
        else:
            arguments = _resolve_arguments(operation.get("arguments") or {}, run["payload"].get("inputs") or {})
            repository.record_operation(
                run_id=run_id,
                operation_id=str(step["step_id"]),
                ordinal=int(step["ordinal"]),
                status="running",
                operation_kind=kind,
                target_id=_operation_target(operation),
                target_label=str(step["target_label"]),
                capability_id=capability_id,
                summary="Dispatched.",
                started_at=step.get("started_at") or _utc_now(),
                payload=state,
            )
            try:
                result = adapter(
                    arguments=arguments,
                    run_id=run_id,
                    operation_id=str(operation["id"]),
                    preauthorized=composition.preauthorize_consequential,
                )
            except Exception as exc:
                _record_terminal_operation(run_id, repository.require_run(run_id)["steps"][int(step["ordinal"]) - 1], status="uncertain", summary="Capability outcome is uncertain after dispatch.", error_class=type(exc).__name__, repository=repository)
                _interrupt_uncertain(repository.require_run(run_id), repository=repository, db_path=db_path)
                return "terminal"
            current = repository.require_run(run_id)
            current_step = current["steps"][int(step["ordinal"]) - 1]
            if current["controller_state"].get("cancellation_requested") is True:
                if current_step["status"] == "running":
                    _record_step(
                        run_id,
                        current_step,
                        status="canceled",
                        summary="Capability returned after cancellation was accepted.",
                        payload=dict(current_step["payload"]),
                        repository=repository,
                        completed_at=_utc_now(),
                    )
                return "terminal"
            if current["status"] not in {"running", "waiting"} or current_step["status"] == "canceled":
                return "terminal"
    if _result_ok(result):
        _finish_iteration(step, operation, result, repository, state=state)
        return "continue"
    capability = SEMANTIC_CAPABILITY_REGISTRY.require(str(operation.get("capability_id") or "")) if kind == "capability" else None
    retry = operation.get("retry") or {}
    if capability is not None and capability.safe_for_bounded_retry and int(state["attempt"]) < int(retry.get("max_attempts") or 1):
        delay = int(retry.get("delay_seconds") or 0)
        state["due_at"] = (_utc_datetime() + timedelta(seconds=delay)).isoformat()
        state.setdefault("results", []).append(_sanitized_result(result))
        _record_step(run_id, step, status="waiting" if delay else "pending", summary="Bounded retry scheduled.", payload=state, repository=repository)
        return _wait_run(run, repository) if delay else "continue"
    _record_terminal_operation(run_id, step, status="failed", summary=str(result.get("detail") or result.get("message") or "Operation failed."), error_class=str(result.get("error") or result.get("outcome") or "operation_failed"), repository=repository, result=result)
    return "failed"


def _finish_iteration(step: dict[str, Any], operation: dict[str, Any], result: dict[str, Any], repository: RunbookRepository, *, state: dict[str, Any] | None = None) -> None:
    state = dict(step["payload"]) if state is None else dict(state)
    state.setdefault("results", []).append(_sanitized_result(result))
    state["iteration"] = int(state.get("iteration") or 0) + 1
    state["attempt"] = 0
    repeats = int((operation.get("repeat") or {}).get("count") or 1)
    status = "completed" if state["iteration"] >= repeats else "pending"
    _record_step(str(step["run_id"]), step, status=status, summary="Completed." if status == "completed" else "Next bounded repetition is ready.", payload=state, repository=repository, completed_at=_utc_now() if status == "completed" else "")


def _finish_phase(run: dict[str, Any], composition: CompositeDefinition, *, repository: RunbookRepository, db_path: Path | None, adapters: Mapping[str, CompositeAdapter]) -> dict[str, Any]:
    state = dict(run["controller_state"])
    phase = str(state.get("phase") or "main")
    failed = [step for step in run["steps"] if step["status"] in _FAILED_OPERATION_STATUSES]
    if phase == "main" and failed:
        if composition.on_failure:
            _activate_operations(str(run["run_id"]), composition.on_failure, repository)
            repository.transition_run(
                str(run["run_id"]),
                status="running",
                summary="Running explicit failure compensation.",
                controller_state={"phase": "on_failure", "terminal_after_compensation": "completed_with_issues", "compensation_outcome": "running"},
            )
            return advance_composite(str(run["run_id"]), adapters=adapters, db_path=db_path)
        return _terminal_run(run, "completed_with_issues", "Composite routine completed with issues.", repository=repository, db_path=db_path)
    if phase in {"on_failure", "on_cancel"}:
        compensation_failed = any(step["status"] in _FAILED_OPERATION_STATUSES and (step["payload"].get("definition") or {}).get("phase") == phase for step in run["steps"])
        state["compensation_outcome"] = "failed" if compensation_failed else "completed"
        terminal = str(state.get("terminal_after_compensation") or "failed")
        summary = f"Composite routine {terminal}; compensation {state['compensation_outcome']}."
        return _terminal_run(run, terminal, summary, repository=repository, db_path=db_path, controller_state=state, cancellation_requester=str(state.get("cancellation_requester") or ""))
    status = "completed_with_issues" if any(step["status"] in {"failed", "blocked"} for step in run["steps"]) else "completed"
    return _terminal_run(run, status, "Composite routine completed." if status == "completed" else "Composite routine completed with issues.", repository=repository, db_path=db_path)


def _begin_failure(run_id: str, composition: CompositeDefinition, *, repository: RunbookRepository, db_path: Path | None, adapters: Mapping[str, CompositeAdapter]) -> dict[str, Any]:
    run = repository.require_run(run_id)
    for step in run["steps"]:
        if step["status"] == "pending":
            _record_step(run_id, step, status="blocked", summary="Stopped after a critical failure.", payload=dict(step["payload"]), repository=repository, completed_at=_utc_now())
    if composition.on_failure:
        _activate_operations(run_id, composition.on_failure, repository)
        repository.transition_run(run_id, status="running", summary="Running explicit failure compensation.", controller_state={"phase": "on_failure", "terminal_after_compensation": "failed", "compensation_outcome": "running"})
        return advance_composite(run_id, adapters=adapters, db_path=db_path)
    return _terminal_run(run, "failed", "Composite routine failed.", repository=repository, db_path=db_path)


def _terminal_run(run: dict[str, Any], status: str, summary: str, *, repository: RunbookRepository, db_path: Path | None, controller_state: dict[str, Any] | None = None, cancellation_requester: str = "") -> dict[str, Any]:
    current = repository.require_run(str(run["run_id"]))
    if current["status"] == "waiting":
        repository.transition_run(str(run["run_id"]), status="running", summary=summary, controller_state=controller_state or current["controller_state"])
    updated = repository.transition_run(
        str(run["run_id"]),
        status=status,
        summary=summary,
        controller_state=controller_state or current["controller_state"],
        cancellation_reason="routine_cancel_requested" if status == "canceled" else None,
        cancellation_requester=cancellation_requester if status == "canceled" else None,
    )
    safe_record_event("orchestration_routine_completed", severity="info" if status == "completed" else "warning", source_id="brain", domain="orchestration", status=status, correlation_id=str(run["run_id"]), db_path=db_path, payload={"run_id": run["run_id"], "orchestration_id": run["orchestration_id"], "summary": summary})
    return updated


def _interrupt_uncertain(run: dict[str, Any], *, repository: RunbookRepository, db_path: Path | None) -> dict[str, Any]:
    return _terminal_run(run, "interrupted", "Composite routine stopped because an operation outcome is uncertain.", repository=repository, db_path=db_path)


def _wait_run(run: dict[str, Any], repository: RunbookRepository) -> str:
    repository.transition_run(str(run["run_id"]), status="waiting", summary="Composite routine is waiting.", controller_state=run["controller_state"])
    return "waiting"


def _next_ready_operation(operations: list[dict[str, Any]], steps: Mapping[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for operation in operations:
        step = steps[str(operation["id"])]
        if step["status"] != "pending":
            continue
        dependencies = [steps[str(item)]["status"] for item in operation.get("depends_on") or []]
        if all(status in _SUCCESS_OPERATION_STATUSES for status in dependencies):
            return operation, step
    return None


def _block_failed_descendants(run_id: str, operations: list[dict[str, Any]], steps: Mapping[str, dict[str, Any]], repository: RunbookRepository) -> None:
    changed = True
    while changed:
        changed = False
        for operation in operations:
            step = steps[str(operation["id"])]
            if step["status"] != "pending":
                continue
            if any(steps[str(item)]["status"] in _FAILED_OPERATION_STATUSES for item in operation.get("depends_on") or []):
                _record_step(run_id, step, status="blocked", summary="A required dependency did not complete.", payload=dict(step["payload"]), repository=repository, completed_at=_utc_now())
                steps[str(operation["id"])] = repository.require_run(run_id)["steps"][int(step["ordinal"]) - 1]
                changed = True


def _condition_action(operation: dict[str, Any], payload: dict[str, Any], steps: Mapping[str, dict[str, Any]], adapters: Mapping[str, CompositeAdapter]) -> str:
    condition = operation.get("condition")
    if not condition:
        return "execute"
    source = str(condition["source"])
    if source == "input":
        observed = (payload.get("inputs") or {}).get(str(condition["reference_id"]))
    elif source == "result":
        result_step = steps[str(condition["reference_id"])]
        results = result_step["payload"].get("results") or []
        observed = (results[-1] if results else {}).get(str(condition["field"]))
    else:
        adapter = adapters.get(f"state:{condition['reference_id']}")
        result = _read_state(adapter, str(condition["target_id"]))
        observed = (result or {}).get(str(condition["field"]))
    truth = _evaluate_raw_condition(condition, observed)
    if truth is ConditionTruth.TRUE:
        return "execute"
    if truth is ConditionTruth.FALSE:
        return "false"
    policy = str(condition.get("on_unknown") or "stop")
    return f"fallback:{condition['fallback_operation_id']}" if policy == "fallback" else policy


def _evaluate_raw_condition(condition: dict[str, Any], observed: object) -> ConditionTruth:
    from .configuration.composite_definition import CompositeCondition
    return evaluate_composite_condition(CompositeCondition.model_validate(condition), observed)


def _read_state(adapter: CompositeAdapter | None, target_id: str) -> dict[str, Any] | None:
    if adapter is None:
        return None
    try:
        result = adapter(target_id=target_id)
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def _activate_operations(run_id: str, operation_ids: list[str], repository: RunbookRepository) -> None:
    run = repository.require_run(run_id)
    by_id = {str(step["step_id"]): step for step in run["steps"]}
    for operation_id in operation_ids:
        step = by_id[str(operation_id)]
        if step["status"] == "dormant":
            _record_step(run_id, step, status="pending", summary="Explicit compensation is ready.", payload=dict(step["payload"]), repository=repository)


def _record_terminal_operation(run_id: str, step: dict[str, Any], *, status: str, summary: str, error_class: str, repository: RunbookRepository, result: dict[str, Any] | None = None) -> None:
    payload = dict(step["payload"])
    if result is not None:
        payload.setdefault("results", []).append(_sanitized_result(result))
    _record_step(run_id, step, status=status, summary=summary, error_class=error_class, payload=payload, repository=repository, completed_at=_utc_now())


def _record_step(run_id: str, step: dict[str, Any], *, status: str, summary: str, payload: dict[str, Any], repository: RunbookRepository, error_class: str = "", started_at: str = "", completed_at: str = "") -> None:
    repository.record_operation(run_id=run_id, operation_id=str(step["step_id"]), ordinal=int(step["ordinal"]), status=status, operation_kind=str(step.get("target_type") or ""), target_id=str(step.get("target_id") or ""), target_label=str(step.get("target_label") or ""), capability_id=str(step.get("action_id") or ""), summary=summary, error_class=error_class, started_at=started_at or str(step.get("started_at") or ""), completed_at=completed_at, payload=payload)


def _resolve_arguments(values: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    return {name: inputs[str(value["input_id"])] if isinstance(value, dict) and set(value) == {"input_id"} else value for name, value in values.items()}


def _operation_target(operation: Mapping[str, Any]) -> str:
    arguments = operation.get("arguments") or {}
    return str(arguments.get("target_id") or arguments.get("source_id") or operation.get("target_id") or operation.get("definition_id") or "")


def _require_confirmation_or_preauthorization(composition: CompositeDefinition) -> None:
    if composition.preauthorize_consequential:
        return
    for operation in composition.operations:
        if operation.type != "capability":
            continue
        arguments = {name: value for name, value in operation.arguments.items() if not hasattr(value, "input_id")}
        if len(arguments) != len(operation.arguments):
            continue
        _direction, confirmation = SEMANTIC_CAPABILITY_REGISTRY.require(operation.capability_id).risk(arguments)
        if confirmation is ConfirmationClass.CONSEQUENTIAL:
            raise HTTPException(status_code=409, detail="This composite contains a consequential action and requires reviewed preauthorization or ordinary runtime confirmation.")


def _result_ok(result: Mapping[str, Any]) -> bool:
    if result.get("ok") is True:
        return True
    outcome = str(result.get("outcome") or ((result.get("result") or {}).get("outcome") if isinstance(result.get("result"), dict) else ""))
    return outcome in {CapabilityOutcome.VERIFIED.value, CapabilityOutcome.ACCEPTED.value}


def _sanitized_result(result: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"outcome", "verified_state", "verified_setpoint", "message_code", "operation_id", "child_run_id", "child_status", "state", "lateness_seconds", "ok"}
    value = dict(result.get("result") or {}) if isinstance(result.get("result"), dict) else dict(result)
    return {key: item for key, item in value.items() if key in allowed}


def _definition_version(definition: Mapping[str, Any]) -> str:
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_datetime().isoformat()
