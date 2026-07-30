from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .config import get_orchestration_settings, get_source_registry
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .orchestration_routines import cancel_routine, start_routine
from .schemas import UiRoutineCancelRequest, UiRoutineRunRequest


_KERNEL_PRIVATE_RUN_FIELDS = {
    "activation_idempotency_key",
    "cancellation_reason",
    "cancellation_requester",
    "controller_state",
    "controller_version",
    "correlation_key",
    "definition_domain",
    "definition_version",
}


def run_routine(
    orchestration_id: str,
    request: UiRoutineRunRequest,
    *,
    routine_execution: CanonicalRoutineExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, object]:
    source = str(request.source or "").strip()
    if source and not canonical_authority and source not in get_source_registry():
        raise HTTPException(status_code=400, detail="Routine source is not a known Oracle source.")
    definition = _find_routine(
        orchestration_id,
        routine_execution=routine_execution,
        canonical_authority=canonical_authority,
    )
    if ((definition.get("triggers") or {}).get("ui")) is not True:
        raise HTTPException(status_code=409, detail="This routine is not available from UI controls.")
    if source and source not in (definition.get("source_ids") or []):
        raise HTTPException(status_code=409, detail="This routine is not available from that source.")
    if routine_execution is None:
        run = start_routine(
            orchestration_id,
            client_id=str(request.client_id),
            inputs=request.inputs,
        )
    else:
        run = routine_execution.start(
            orchestration_id,
            client_id=str(request.client_id),
            inputs=request.inputs,
        )
    return {
        "ok": run.get("status") in {"completed", "waiting"},
        "run": _compatibility_run(run),
    }


def cancel_routine_run(run_id: str, request: UiRoutineCancelRequest) -> dict[str, object]:
    run = cancel_routine(run_id, cancellation_requester=str(request.client_id))
    return {"ok": run.get("status") == "canceled", "run": _compatibility_run(run)}


def register_orchestration_routine_routes(app: FastAPI) -> None:
    app.post("/api/ui/orchestrations/{orchestration_id}/run")(run_routine_http)
    app.post("/api/ui/orchestration-runs/{run_id}/cancel")(cancel_routine_run)


def run_routine_http(
    orchestration_id: str,
    payload: UiRoutineRunRequest,
    request: Request,
) -> dict[str, object]:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    canonical = isinstance(composition, CanonicalBrainApplicationComposition)
    return run_routine(
        orchestration_id,
        payload,
        routine_execution=composition.routine_execution if canonical else None,
        canonical_authority=canonical,
    )


def _find_routine(
    orchestration_id: str,
    *,
    routine_execution: CanonicalRoutineExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, object]:
    if canonical_authority:
        if routine_execution is None:
            raise HTTPException(status_code=404, detail="Routine definition was not found.")
        try:
            return routine_execution.definition_payload(orchestration_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Routine definition was not found.") from exc
    for definition in get_orchestration_settings().get("routines") or []:
        if str(definition.get("id") or "") == str(orchestration_id or "").strip():
            return definition
    raise HTTPException(status_code=404, detail="Routine definition was not found.")


def _compatibility_run(run: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in run.items()
        if key not in _KERNEL_PRIVATE_RUN_FIELDS
    }
