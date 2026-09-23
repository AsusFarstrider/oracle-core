from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .orchestration_routine_canonical import CanonicalRoutineExecution
from .orchestration_routines import cancel_routine
from . import state
from .schemas import UiRoutineCancelRequest, UiRoutineRunRequest
from .admin_orchestration_routes import _public_run


def run_routine(
    orchestration_id: str,
    request: UiRoutineRunRequest,
    *,
    routine_execution: CanonicalRoutineExecution | None = None,
) -> dict[str, object]:
    source = str(request.source or "").strip()
    definition = _find_routine(
        orchestration_id,
        routine_execution=routine_execution,
    )
    if ((definition.get("triggers") or {}).get("ui")) is not True:
        raise HTTPException(status_code=409, detail="This routine is not available from UI controls.")
    if source and source not in (definition.get("source_ids") or []):
        raise HTTPException(status_code=409, detail="This routine is not available from that source.")
    conversational = _missing_conversational_input(definition, request.inputs)
    if conversational is not None:
        input_id, spec = conversational
        if not source or not request.ui_session_id:
            raise HTTPException(status_code=400, detail="Routine conversational input requires source and ui_session_id.")
        prompt = str(spec["prompt"])
        if not state.store_pending_ui_context(
            source,
            request.ui_session_id,
            {
                "action": "routine_input",
                "client_id": str(request.client_id),
                "target_source_id": source,
                "routine_id": orchestration_id,
                "input_id": input_id,
                "input_spec": spec,
                "prompt": prompt,
            },
        ):
            raise HTTPException(status_code=400, detail="Unable to start routine input conversation.")
        return {
            "ok": True,
            "pending_input": True,
            "orchestration_id": orchestration_id,
            "prompt": prompt,
        }
    if routine_execution is None:
        raise HTTPException(status_code=404, detail="Routine definition was not found.")
    run = routine_execution.start(
        orchestration_id,
        client_id=str(request.client_id),
        inputs=request.inputs,
    )
    return {
        "ok": run.get("status") in {"completed", "waiting"},
        "run": _public_run(run),
    }


def cancel_routine_run(
    run_id: str,
    request: UiRoutineCancelRequest,
    *,
    routine_execution: CanonicalRoutineExecution | None = None,
) -> dict[str, object]:
    run = (
        cancel_routine(run_id, cancellation_requester=str(request.client_id))
        if routine_execution is None
        else routine_execution.cancel(run_id, requester=str(request.client_id))
    )
    return {"ok": run.get("status") == "canceled", "run": _public_run(run)}


def register_orchestration_routine_routes(app: FastAPI) -> None:
    app.post("/api/ui/orchestrations/{orchestration_id}/run")(run_routine_http)
    app.get("/api/ui/orchestration-runs")(list_routine_runs_http)
    app.get("/api/ui/orchestration-runs/{run_id}")(get_routine_run_http)
    app.post("/api/ui/orchestration-runs/{run_id}/cancel")(cancel_routine_run_http)


def list_routine_runs_http(
    request: Request,
    source: str | None = None,
    active_only: bool = False,
) -> dict[str, object]:
    execution = _canonical_execution(request)
    runs = execution.list_runs(source_id=source, active_only=active_only)
    return {"ok": True, "runs": [_public_run(run) for run in runs]}


def get_routine_run_http(run_id: str, request: Request) -> dict[str, object]:
    execution = _canonical_execution(request)
    matches = [run for run in execution.list_runs() if str(run.get("run_id") or "") == run_id]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="Routine run was not found.")
    return {"ok": True, "run": _public_run(matches[0])}


def _canonical_execution(request: Request) -> CanonicalRoutineExecution:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition) or composition.routine_execution is None:
        raise HTTPException(status_code=503, detail="Canonical routine execution is unavailable.")
    return composition.routine_execution


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
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return run_routine(
        orchestration_id,
        payload,
        routine_execution=composition.routine_execution,
    )


def cancel_routine_run_http(
    run_id: str,
    payload: UiRoutineCancelRequest,
    request: Request,
) -> dict[str, object]:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return cancel_routine_run(
        run_id,
        payload,
        routine_execution=composition.routine_execution,
    )


def _find_routine(
    orchestration_id: str,
    *,
    routine_execution: CanonicalRoutineExecution | None = None,
) -> dict[str, object]:
    if routine_execution is None:
        raise HTTPException(status_code=404, detail="Routine definition was not found.")
    try:
        return routine_execution.definition_payload(orchestration_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Routine definition was not found.") from exc


def _missing_conversational_input(
    definition: dict[str, object],
    provided: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    composition = definition.get("composition")
    inputs = (
        composition.get("inputs")
        if isinstance(composition, dict)
        else definition.get("inputs")
    ) or {}
    for input_id, raw_spec in inputs.items():
        spec = dict(raw_spec)
        if (
            spec.get("spoken_duration") is True
            or (spec.get("required") is True and spec.get("prompt"))
        ) and input_id not in provided:
            return str(input_id), spec
    return None
