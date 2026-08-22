from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from .config import get_orchestration_settings
from .memory.orchestrations import list_orchestration_runs


def admin_orchestrations(
    *,
    routine_execution=None,
    network_execution=None,
    canonical_authority: bool = False,
) -> dict[str, object]:
    settings = (
        _canonical_orchestration_settings(routine_execution, network_execution)
        if canonical_authority
        else get_orchestration_settings()
    )
    recent_runs = list_orchestration_runs(limit=100)
    runs_by_definition: dict[str, list[dict[str, object]]] = {}
    for run in recent_runs:
        runs_by_definition.setdefault(str(run.get("orchestration_id") or ""), []).append(
            _public_run(run)
        )
    recoveries = [
        _definition_summary(
            definition,
            kind="recovery",
            runs=runs_by_definition.get(str(definition.get("id") or ""), []),
            execution_available=definition.get("enabled") is True,
        )
        for definition in settings.get("recoveries") or []
        if isinstance(definition, dict)
    ]
    routines = [
        _definition_summary(
            definition,
            kind="routine",
            runs=runs_by_definition.get(str(definition.get("id") or ""), []),
            execution_available=(
                definition.get("enabled") is True
                and ((definition.get("triggers") or {}).get("ui")) is True
            ),
        )
        for definition in settings.get("routines") or []
        if isinstance(definition, dict)
    ]
    definitions = [*recoveries, *routines]
    return {
        "ok": True,
        "version": settings.get("version", 1),
        "summary": {
            "total": len(definitions),
            "recoveries": len(recoveries),
            "routines": len(routines),
            "enabled": sum(1 for item in definitions if item.get("enabled") is True),
            "execution_available": any(item.get("execution_available") is True for item in definitions),
            "run_count": len(recent_runs),
            "active_runs": sum(1 for run in recent_runs if _run_is_active(run)),
        },
        "definitions": definitions,
    }


def admin_orchestration_detail(
    orchestration_id: str,
    *,
    routine_execution=None,
    network_execution=None,
    canonical_authority: bool = False,
) -> dict[str, object]:
    settings = (
        _canonical_orchestration_settings(routine_execution, network_execution)
        if canonical_authority
        else get_orchestration_settings()
    )
    for kind, collection in (
        ("recovery", settings.get("recoveries") or []),
        ("routine", settings.get("routines") or []),
    ):
        for definition in collection:
            if not isinstance(definition, dict):
                continue
            if str(definition.get("id") or "") != orchestration_id:
                continue
            runs = [
                _public_run(run)
                for run in list_orchestration_runs(
                    orchestration_id=orchestration_id,
                    limit=25,
                )
            ]
            return {
                "ok": True,
                "definition": {
                    **definition,
                    "kind": kind,
                    "preview_available": kind == "recovery" and definition.get("enabled") is True,
                    "execution_available": (
                        definition.get("enabled") is True
                        and (
                            kind == "recovery"
                            or ((definition.get("triggers") or {}).get("ui")) is True
                        )
                    ),
                    "configuration_available": False,
                },
                "runs": runs,
                "summary": {
                    "run_count": len(runs),
                    "active_runs": sum(1 for run in runs if _run_is_active(run)),
                    "last_status": runs[0].get("status") if runs else "never_run",
                },
            }
    raise HTTPException(status_code=404, detail="Orchestration definition was not found.")


def register_admin_orchestration_routes(app: FastAPI) -> None:
    app.get("/api/admin/orchestrations")(admin_orchestrations_http)
    app.get("/api/admin/orchestrations/{orchestration_id}")(admin_orchestration_detail_http)


def admin_orchestrations_http(request: Request) -> dict[str, object]:
    canonical, routine, network = _canonical_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return admin_orchestrations(
        routine_execution=routine,
        network_execution=network,
        canonical_authority=True,
    )


def admin_orchestration_detail_http(
    request: Request,
    orchestration_id: str,
) -> dict[str, object]:
    canonical, routine, network = _canonical_context(request)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return admin_orchestration_detail(
        orchestration_id,
        routine_execution=routine,
        network_execution=network,
        canonical_authority=True,
    )


def _canonical_context(request: Request):
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
    return (
        canonical,
        composition.routine_execution if canonical else None,
        composition.network_execution if canonical else None,
    )


def _canonical_orchestration_settings(routine_execution, network_execution) -> dict[str, object]:
    routines = []
    if routine_execution is not None:
        routines = [
            routine_execution.definition_payload(routine_id)
            for routine_id in routine_execution.settings.definitions
        ]
    recoveries = []
    if network_execution is not None:
        for runtime in network_execution.policy.recoveries.values():
            definition = runtime.definition
            recoveries.append({
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
            })
    return {"version": 1, "routines": routines, "recoveries": recoveries}


def _definition_summary(
    definition: dict[str, object],
    *,
    kind: str,
    runs: list[dict[str, object]],
    execution_available: bool,
) -> dict[str, object]:
    active_run = next((run for run in runs if _run_is_active(run)), None)
    return {
        **definition,
        "kind": kind,
        "preview_available": kind == "recovery" and execution_available,
        "execution_available": execution_available,
        "configuration_available": False,
        "run_count": len(runs),
        "active_run": active_run,
        "latest_run": runs[0] if runs else None,
    }


def _public_run(run: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in run.items()
        if key
        in {
            "run_id",
            "orchestration_id",
            "kind",
            "status",
            "summary",
            "started_at",
            "completed_at",
            "approval_consumed",
            "steps",
        }
    }


def _run_is_active(run: dict[str, object]) -> bool:
    return str(run.get("status") or "") in {"running", "waiting"}
