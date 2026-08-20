from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping

from fastapi import HTTPException

from .config import get_orchestration_settings
from .memory.runtime import safe_record_event
from .runbook_kernel import RunbookActivation, RunbookDefinitionRef, RunbookRepository


logger = logging.getLogger("oracle-brain.orchestration.routines")
RoutineAdapter = Callable[..., dict[str, Any]]
_START_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_ADAPTERS: dict[str, RoutineAdapter] = {}
_COMPOSITE_CONTROLLER_VERSION = "1"


def configure_routine_adapters(
    *,
    ui_action: RoutineAdapter,
    audiobook_start: RoutineAdapter,
    sleep_timer: RoutineAdapter,
    state_check: RoutineAdapter,
    playback_check: RoutineAdapter,
    notification: RoutineAdapter | None = None,
    timer_sound: RoutineAdapter | None = None,
) -> None:
    _ADAPTERS.update(
        {
            "ui_action": ui_action,
            "audiobook_start": audiobook_start,
            "audiobook_resume": audiobook_start,
            "sleep_timer": sleep_timer,
            "state_check": state_check,
            "playback_check": playback_check,
            **({"notification": notification} if notification is not None else {}),
            **({"timer_sound": timer_sound} if timer_sound is not None else {}),
        }
    )


def start_routine(
    orchestration_id: str,
    *,
    client_id: str,
    inputs: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    definition: dict[str, Any] | None = None,
    adapters: Mapping[str, RoutineAdapter] | None = None,
    config_revision: str | None = None,
    defer_audible_start: bool = False,
    db_path: Path | None = None,
) -> dict[str, Any]:
    definition = dict(definition) if definition is not None else _find_routine(orchestration_id, settings=settings)
    if str(definition.get("id") or "") != str(orchestration_id or "").strip():
        raise HTTPException(status_code=409, detail="Routine definition identity does not match the requested routine.")
    if definition.get("enabled") is not True:
        raise HTTPException(status_code=409, detail="Routine is disabled.")
    resolved_inputs = _resolve_inputs(definition, inputs or {})
    repository = _repository(db_path)
    with _START_LOCK:
        active = [
            run
            for status in ("running", "waiting")
            for run in repository.list_runs(
                definition_id=str(definition["id"]),
                kind="routine",
                status=status,
                limit=1,
            )
        ]
        if active:
            raise HTTPException(status_code=409, detail="This routine already has an active run.")
        run_id = f"routine-{uuid.uuid4().hex}"
        started_at = _utc_now()
        payload = {
            "definition": definition,
            "inputs": resolved_inputs,
            "next_step_index": 0,
            "defer_audible_start": defer_audible_start,
        }
        if config_revision:
            payload["config_revision"] = str(config_revision)
        repository.create_run(
            RunbookDefinitionRef(
                definition_id=str(definition["id"]),
                kind="routine",
                domain="composite",
                version=_definition_version(definition),
                controller_version=_COMPOSITE_CONTROLLER_VERSION,
            ),
            RunbookActivation(
                run_id=run_id,
                started_at=started_at,
                correlation_key=f"routine:{definition['id']}",
                client_id=client_id,
            ),
            status="running",
            summary=f"{definition.get('display_name') or definition['id']} started.",
            controller_state={"next_step_index": 0},
            payload=payload,
        )
        for ordinal, step in enumerate(definition.get("steps") or [], start=1):
            repository.record_operation(
                run_id=run_id,
                operation_id=str(step["id"]),
                ordinal=ordinal,
                status="pending",
                operation_kind=str(step.get("type") or ""),
                target_id=str(step.get("source_id") or step.get("check_id") or ""),
                target_label=str(step.get("label") or step["id"]),
                capability_id=str(step.get("action_id") or ""),
                summary="Pending.",
                payload={"definition": step},
            )
    safe_record_event(
        "orchestration_routine_started",
        severity="info",
        source_id="brain",
        domain="orchestration",
        status="running",
        correlation_id=run_id,
        db_path=db_path,
        payload={
            "run_id": run_id,
            "orchestration_id": definition["id"],
            "client_id": client_id,
        },
    )
    return advance_routine(run_id, db_path=db_path, adapters=adapters)


def advance_routine(
    run_id: str,
    *,
    db_path: Path | None = None,
    adapters: Mapping[str, RoutineAdapter] | None = None,
) -> dict[str, Any]:
    repository = _repository(db_path)
    with _RUN_LOCK:
        run = _require_run(run_id, repository=repository)
        if run["status"] not in {"running", "waiting"}:
            return run
        payload = dict(run.get("payload") or {})
        definition = payload.get("definition") or {}
        steps = definition.get("steps") or []
        index = int(payload.get("next_step_index") or 0)
        if run["status"] == "waiting":
            return run

        while index < len(steps):
            step = steps[index]
            step_id = str(step["id"])
            if not _step_enabled(step, payload.get("inputs") or {}):
                _complete_step(
                    run_id,
                    index,
                    step,
                    {"ok": True, "skipped": True, "detail": "Condition did not match."},
                    repository=repository,
                )
                index += 1
                payload["next_step_index"] = index
                repository.transition_run(
                    run_id,
                    status="running",
                    summary=f"{definition.get('display_name') or definition.get('id') or 'Routine'} is running.",
                    controller_state={"next_step_index": index},
                    payload=payload,
                )
                continue
            if str(step.get("type") or "") == "wait":
                duration_seconds = _duration_seconds(step, payload.get("inputs") or {})
                if duration_seconds > 0:
                    due_at = _utc_datetime() + timedelta(seconds=duration_seconds)
                    step_payload = {
                        "definition": step,
                        "due_at": due_at.isoformat(),
                        "max_lateness_seconds": int(step.get("max_lateness_seconds") or 0),
                    }
                    repository.record_operation(
                        run_id=run_id,
                        operation_id=step_id,
                        ordinal=index + 1,
                        status="waiting",
                        operation_kind="wait",
                        target_label=str(step.get("label") or step_id),
                        summary=f"Waiting until {due_at.isoformat()}.",
                        started_at=_utc_now(),
                        payload=step_payload,
                    )
                    payload["next_step_index"] = index
                    updated = repository.transition_run(
                        run_id,
                        status="waiting",
                        summary=str(step.get("label") or "Routine is waiting."),
                        controller_state={"next_step_index": index},
                        payload=payload,
                    )
                    safe_record_event(
                        "orchestration_routine_waiting",
                        severity="info",
                        source_id="brain",
                        domain="orchestration",
                        status="waiting",
                        correlation_id=run_id,
                        db_path=db_path,
                        payload={
                            "run_id": run_id,
                            "step_id": step_id,
                            "due_at": due_at.isoformat(),
                            "client_id": str(run.get("client_id") or ""),
                        },
                    )
                    return updated
                _complete_step(
                    run_id,
                    index,
                    step,
                    {"ok": True, "duration_seconds": 0},
                    repository=repository,
                )
            else:
                outcome = _execute_step(
                    run,
                    index,
                    step,
                    payload.get("inputs") or {},
                    defer_audible_start=payload.get("defer_audible_start") is True,
                    repository=repository,
                    adapters=adapters,
                )
                if not outcome["ok"] and _stops_on_failure(step):
                    return _finish_run(
                        run_id,
                        status="failed",
                        summary=f"{step.get('label') or step_id} failed.",
                        payload=payload,
                        repository=repository,
                        db_path=db_path,
                    )
            index += 1
            payload["next_step_index"] = index
            repository.transition_run(
                run_id,
                status="running",
                summary=f"{definition.get('display_name') or definition.get('id') or 'Routine'} is running.",
                controller_state={"next_step_index": index},
                payload=payload,
            )

        return _finish_run(
            run_id,
            status="completed",
            summary=f"{definition.get('display_name') or definition.get('id') or 'Routine'} completed.",
            payload=payload,
            repository=repository,
            db_path=db_path,
        )


def resume_due_routines(
    *,
    now: datetime | None = None,
    db_path: Path | None = None,
    adapters: Mapping[str, RoutineAdapter] | None = None,
    required_config_revision: str | None = None,
) -> list[dict[str, Any]]:
    current = now or _utc_datetime()
    resumed: list[dict[str, Any]] = []
    repository = _repository(db_path)
    for run in repository.list_runs(kind="routine", status="waiting", limit=100):
        if str(run.get("definition_domain") or "") == "home_automation":
            continue
        payload = dict(run.get("payload") or {})
        if required_config_revision is not None and str(payload.get("config_revision") or "") != required_config_revision:
            resumed.append(
                _finish_run(
                    str(run["run_id"]),
                    status="failed",
                    summary="Routine continuation configuration revision no longer matches the frozen run.",
                    payload=payload,
                    repository=repository,
                    db_path=db_path,
                )
            )
            continue
        index = int(payload.get("next_step_index") or 0)
        steps = run.get("steps") or []
        if index >= len(steps):
            continue
        durable_step = steps[index]
        due_at = _parse_datetime((durable_step.get("payload") or {}).get("due_at"))
        if due_at is None or current < due_at:
            continue
        max_lateness = int((durable_step.get("payload") or {}).get("max_lateness_seconds") or 0)
        lateness = max(0, int((current - due_at).total_seconds()))
        if lateness > max_lateness:
            repository.record_operation(
                run_id=str(run["run_id"]),
                operation_id=str(durable_step["step_id"]),
                ordinal=int(durable_step["ordinal"]),
                status="failed",
                operation_kind="wait",
                target_label=str(durable_step.get("target_label") or ""),
                summary=f"Continuation was {lateness} seconds late; limit is {max_lateness}.",
                error_class="max_lateness_exceeded",
                started_at=str(durable_step.get("started_at") or ""),
                completed_at=current.isoformat(),
                payload=dict(durable_step.get("payload") or {}),
            )
            resumed.append(
                _finish_run(
                    str(run["run_id"]),
                    status="failed",
                    summary="Routine continuation exceeded its maximum lateness.",
                    payload=payload,
                    repository=repository,
                    db_path=db_path,
                )
            )
            continue
        repository.record_operation(
            run_id=str(run["run_id"]),
            operation_id=str(durable_step["step_id"]),
            ordinal=int(durable_step["ordinal"]),
            status="completed",
            operation_kind="wait",
            target_label=str(durable_step.get("target_label") or ""),
            summary=f"Wait completed with {lateness} seconds of lateness.",
            started_at=str(durable_step.get("started_at") or ""),
            completed_at=current.isoformat(),
            payload={**dict(durable_step.get("payload") or {}), "lateness_seconds": lateness},
        )
        payload["next_step_index"] = index + 1
        repository.transition_run(
            str(run["run_id"]),
            status="running",
            summary="Routine resumed after its durable wait.",
            controller_state={"next_step_index": index + 1},
            payload=payload,
        )
        safe_record_event(
            "orchestration_routine_resumed",
            severity="info",
            source_id="brain",
            domain="orchestration",
            status="running",
            correlation_id=str(run["run_id"]),
            db_path=db_path,
            payload={
                "run_id": run["run_id"],
                "lateness_seconds": lateness,
                "client_id": str(run.get("client_id") or ""),
            },
        )
        resumed.append(advance_routine(str(run["run_id"]), db_path=db_path, adapters=adapters))
    return resumed


def cancel_routine(
    run_id: str,
    *,
    cancellation_requester: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    repository = _repository(db_path)
    with _RUN_LOCK:
        run = _require_run(run_id, repository=repository)
        if run["kind"] != "routine":
            raise HTTPException(status_code=409, detail="Only task routines can be canceled.")
        if run["status"] == "running":
            raise HTTPException(status_code=409, detail="A currently executing step cannot be canceled safely.")
        if run["status"] != "waiting":
            return run
        now = _utc_now()
        for step in run.get("steps") or []:
            if step["status"] not in {"pending", "waiting"}:
                continue
            repository.record_operation(
                run_id=run_id,
                operation_id=str(step["step_id"]),
                ordinal=int(step["ordinal"]),
                status="canceled",
                operation_kind=str(step.get("target_type") or ""),
                target_id=str(step.get("target_id") or ""),
                target_label=str(step.get("target_label") or ""),
                capability_id=str(step.get("action_id") or ""),
                summary="Canceled before execution.",
                started_at=str(step.get("started_at") or ""),
                completed_at=now,
                payload=dict(step.get("payload") or {}),
            )
        canceled = _finish_run(
            run_id,
            status="canceled",
            summary="Routine canceled.",
            payload=dict(run.get("payload") or {}),
            repository=repository,
            db_path=db_path,
            cancellation_reason="routine_cancel_requested",
            cancellation_requester=cancellation_requester,
        )
        return canceled


async def routine_scheduler_loop(
    *,
    poll_seconds: float = 5.0,
    adapters: Mapping[str, RoutineAdapter] | None = None,
    required_config_revision: str | None = None,
) -> None:
    while True:
        try:
            await asyncio.to_thread(
                resume_due_routines,
                adapters=adapters,
                required_config_revision=required_config_revision,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("orchestration_routine_scheduler_failed")
        await asyncio.sleep(max(0.25, poll_seconds))


def _execute_step(
    run: dict[str, Any],
    index: int,
    step: dict[str, Any],
    inputs: dict[str, Any],
    *,
    defer_audible_start: bool,
    repository: RunbookRepository,
    adapters: Mapping[str, RoutineAdapter] | None = None,
) -> dict[str, Any]:
    run_id = str(run["run_id"])
    step_id = str(step["id"])
    started_at = _utc_now()
    repository.record_operation(
        run_id=run_id,
        operation_id=step_id,
        ordinal=index + 1,
        status="running",
        operation_kind=str(step.get("type") or ""),
        target_id=str(step.get("source_id") or step.get("check_id") or ""),
        target_label=str(step.get("label") or step_id),
        capability_id=str(step.get("action_id") or ""),
        summary="Executing.",
        started_at=started_at,
        payload={"definition": step},
    )
    started = monotonic()
    try:
        result = _call_adapter(
            run,
            step,
            inputs,
            defer_audible_start=defer_audible_start,
            adapters=adapters,
        )
    except Exception as exc:
        logger.exception("orchestration_routine_step_failed run_id=%s step_id=%s", run_id, step_id)
        result = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
    elapsed = monotonic() - started
    timeout = int(step.get("timeout_seconds") or 0)
    if timeout and elapsed > timeout:
        result = {
            **result,
            "ok": False,
            "error": "step_timeout_exceeded",
            "detail": f"Step returned after {elapsed:.1f} seconds; limit is {timeout}.",
        }
    if not _result_ok(result) and step.get("remediation_action_id"):
        remediation = _adapter_for("ui_action", adapters)(
            action_id=str(step["remediation_action_id"]),
            client_id=str(run.get("client_id") or "orchestration"),
            source_id=str(step.get("source_id") or "") or None,
        )
        result = {**result, "remediation": remediation}
        if _result_ok(remediation):
            recheck = _call_adapter(
                run,
                step,
                inputs,
                defer_audible_start=defer_audible_start,
                adapters=adapters,
            )
            result["recheck"] = recheck
            result["ok"] = _result_ok(recheck)
    return _complete_step(
        run_id,
        index,
        step,
        result,
        started_at=started_at,
        repository=repository,
    )


def _call_adapter(
    run: dict[str, Any],
    step: dict[str, Any],
    inputs: dict[str, Any],
    *,
    defer_audible_start: bool,
    adapters: Mapping[str, RoutineAdapter] | None = None,
) -> dict[str, Any]:
    step_type = str(step.get("type") or "")
    adapter = _adapter_for(step_type, adapters)
    common = {"client_id": str(run.get("client_id") or "orchestration")}
    if step_type == "ui_action":
        return adapter(action_id=str(step["action_id"]), source_id=step.get("source_id"), **common)
    if step_type in {"audiobook_start", "audiobook_resume"}:
        return adapter(
            source_id=str(step["source_id"]),
            user_id=str(step["user_id"]),
            defer_audible_start=defer_audible_start,
            sleep_timer_seconds=(
                _duration_seconds(step, inputs)
                if step.get("duration_input") or step.get("duration_seconds") is not None
                else None
            ),
            **common,
        )
    if step_type == "sleep_timer":
        return adapter(
            source_id=str(step["source_id"]),
            duration_seconds=_duration_seconds(step, inputs),
            **common,
        )
    if step_type == "state_check":
        return adapter(check_id=str(step["check_id"]), expected_state=str(step["expected_state"]), **common)
    if step_type == "playback_check":
        return adapter(source_id=str(step["source_id"]), check_id=str(step["check_id"]), **common)
    if step_type == "notification":
        return adapter(
            notification_id=str(step["notification_id"]),
            occurrence_id=f"{run['run_id']}:{step['id']}",
            correlation_id=str(run["run_id"]),
            **common,
        )
    if step_type == "timer_sound":
        return adapter(
            source_id=str(step["source_id"]),
            occurrence_id=f"{run['run_id']}:{step['id']}",
            **common,
        )
    raise RuntimeError(f"Unsupported routine step type {step_type!r}")


def _adapter_for(
    step_type: str,
    adapters: Mapping[str, RoutineAdapter] | None,
) -> RoutineAdapter:
    adapter = (adapters or _ADAPTERS).get(step_type)
    if adapter is None:
        raise RuntimeError(f"Routine adapter {step_type!r} is not configured")
    return adapter


def _complete_step(
    run_id: str,
    index: int,
    step: dict[str, Any],
    result: dict[str, Any],
    *,
    started_at: str | None = None,
    repository: RunbookRepository,
) -> dict[str, Any]:
    ok = _result_ok(result)
    repository.record_operation(
        run_id=run_id,
        operation_id=str(step["id"]),
        ordinal=index + 1,
        status="completed" if ok else "failed",
        operation_kind=str(step.get("type") or ""),
        target_id=str(step.get("source_id") or step.get("check_id") or ""),
        target_label=str(step.get("label") or step["id"]),
        capability_id=str(step.get("action_id") or ""),
        summary=str(result.get("detail") or result.get("message") or ("Completed." if ok else "Failed.")),
        error_class="" if ok else str(result.get("error") or "step_failed"),
        verification_status="passed" if ok else "failed",
        started_at=started_at or _utc_now(),
        completed_at=_utc_now(),
        payload={"definition": step, "result": result},
    )
    return {"ok": ok, "result": result}


def _finish_run(
    run_id: str,
    *,
    status: str,
    summary: str,
    payload: dict[str, Any],
    repository: RunbookRepository,
    db_path: Path | None,
    cancellation_reason: str | None = None,
    cancellation_requester: str | None = None,
) -> dict[str, Any]:
    if status == "failed":
        current = _require_run(run_id, repository=repository)
        completed_at = _utc_now()
        for step in current.get("steps") or []:
            if step.get("status") != "pending":
                continue
            repository.record_operation(
                run_id=run_id,
                operation_id=str(step["step_id"]),
                ordinal=int(step["ordinal"]),
                status="not_run",
                operation_kind=str(step.get("target_type") or ""),
                target_id=str(step.get("target_id") or ""),
                target_label=str(step.get("target_label") or ""),
                capability_id=str(step.get("action_id") or ""),
                summary="Not run because an earlier required step failed.",
                completed_at=completed_at,
                payload=dict(step.get("payload") or {}),
            )
    run = repository.transition_run(
        run_id,
        status=status,
        summary=summary,
        payload=payload,
        cancellation_reason=cancellation_reason,
        cancellation_requester=cancellation_requester,
    )
    if run is None:
        raise RuntimeError(f"Routine run {run_id} disappeared")
    event_type = "orchestration_routine_canceled" if status == "canceled" else "orchestration_routine_completed"
    safe_record_event(
        event_type,
        severity="info" if status == "completed" else "warning",
        source_id="brain",
        domain="orchestration",
        status=status,
        correlation_id=run_id,
        db_path=db_path,
        payload={
            "run_id": run_id,
            "orchestration_id": run.get("orchestration_id"),
            "summary": summary,
            "client_id": str(run.get("client_id") or ""),
        },
    )
    return run


def _find_routine(orchestration_id: str, *, settings: dict[str, Any] | None) -> dict[str, Any]:
    for definition in (settings or get_orchestration_settings()).get("routines") or []:
        if str(definition.get("id") or "") == str(orchestration_id or "").strip():
            return dict(definition)
    raise HTTPException(status_code=404, detail="Routine definition was not found.")


def find_routine_trigger(
    text: str,
    *,
    source: str | None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_text = _normalize_phrase(text)
    if not normalized_text:
        return None
    source_id = str(source or "").strip()
    definitions = (settings or get_orchestration_settings()).get("routines") or []
    for definition in definitions:
        if not isinstance(definition, dict) or definition.get("enabled") is not True:
            continue
        triggers = definition.get("triggers") or {}
        if triggers.get("voice") is not True:
            continue
        global_phrases = {_normalize_phrase(item) for item in triggers.get("global_phrases") or []}
        if normalized_text in global_phrases:
            return dict(definition)
        source_phrases = {_normalize_phrase(item) for item in triggers.get("source_phrases") or []}
        if source_id in (definition.get("source_ids") or []) and normalized_text in source_phrases:
            return dict(definition)
    return None


def extract_deferred_session(run: dict[str, Any]) -> dict[str, Any] | None:
    for step in run.get("steps") or []:
        result = ((step.get("payload") or {}).get("result") or {})
        operation = result.get("result") if isinstance(result.get("result"), dict) else result
        deferred = operation.get("deferred_session") if isinstance(operation, dict) else None
        if isinstance(deferred, dict) and deferred.get("resume_action"):
            return dict(deferred)
    return None


def _resolve_inputs(definition: dict[str, Any], provided: dict[str, Any]) -> dict[str, Any]:
    unknown = set(provided) - set(definition.get("inputs") or {})
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown routine input: {sorted(unknown)[0]}")
    resolved: dict[str, Any] = {}
    for input_id, spec in (definition.get("inputs") or {}).items():
        value = provided.get(input_id, spec.get("default"))
        if spec.get("type") == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"Routine input {input_id} must be an integer.")
            if value < int(spec.get("minimum", value)) or value > int(spec.get("maximum", value)):
                raise HTTPException(status_code=400, detail=f"Routine input {input_id} is outside its allowed range.")
        elif not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"Routine input {input_id} must be a string.")
        resolved[input_id] = value
    return resolved


def _duration_seconds(step: dict[str, Any], inputs: dict[str, Any]) -> int:
    if step.get("duration_seconds") is not None:
        return int(step["duration_seconds"])
    value = int(inputs[str(step["duration_input"])])
    return value * 60 if step.get("duration_unit") == "minutes" else value


def _step_enabled(step: dict[str, Any], inputs: dict[str, Any]) -> bool:
    condition = step.get("when")
    if not isinstance(condition, dict):
        return True
    actual = inputs.get(str(condition.get("input_id") or ""))
    expected = condition.get("value")
    operator = str(condition.get("operator") or "")
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "greater_than":
        try:
            return float(actual) > float(expected)
        except (TypeError, ValueError):
            return False
    return False


def _stops_on_failure(step: dict[str, Any]) -> bool:
    return step.get("required") is not False or str(step.get("on_failure") or "stop") == "stop"


def _result_ok(result: dict[str, Any]) -> bool:
    if "ok" in result:
        return result.get("ok") is True
    return str(result.get("status") or "").lower() in {"executed", "completed", "passed", "ok"}


def _require_run(run_id: str, *, repository: RunbookRepository) -> dict[str, Any]:
    try:
        return repository.require_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Routine run was not found.") from exc


def _repository(db_path: Path | None) -> RunbookRepository:
    return RunbookRepository(db_path=db_path)


def _definition_version(definition: dict[str, Any]) -> str:
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now() -> str:
    return _utc_datetime().isoformat()


def _normalize_phrase(value: Any) -> str:
    return " ".join(
        str(value or "")
        .strip()
        .lower()
        .replace("’", "'")
        .rstrip("!?.")
        .split()
    )
