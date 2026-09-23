from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .brain_application_composition import BRAIN_APPLICATION_COMPOSITION_STATE_KEY, CanonicalBrainApplicationComposition
from .lists_notes_runtime import ListsNotesError


class ListOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["create_list", "read", "add", "edit", "complete", "reopen", "delete", "complete_all", "reopen_all", "delete_all"]
    list_id: str | None = Field(default=None, max_length=256)
    item_ref: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=1000)
    lookup_title: str | None = Field(default=None, max_length=1000)
    confirmed: bool = False


class NoteOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["create", "read", "search", "edit", "append", "replace", "rename", "delete"]
    note_id: str | None = Field(default=None, max_length=256)
    note_ref: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=200)
    lookup_title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=100000)
    query: str | None = Field(default=None, max_length=500)
    confirmed: bool = False


def _composition(request: Request) -> CanonicalBrainApplicationComposition:
    value = getattr(request.app.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    if not isinstance(value, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return value


def _execute(execution, operation: str, values: dict[str, Any]) -> dict[str, Any]:
    if execution is None:
        raise HTTPException(status_code=503, detail="Domain is not configured.")
    try:
        return execution.execute(operation, **values)
    except ListsNotesError as exc:
        status = 409 if exc.error_code in {"confirmation_required", "list_ambiguous", "item_ambiguous", "note_ambiguous"} else 404 if exc.error_code.endswith("_not_found") else 422
        raise HTTPException(status_code=status, detail={"error": exc.error_code, "detail": exc.detail, "options": exc.options}) from exc
    except Exception as exc:
        code = getattr(exc, "error_code", "provider_unavailable")
        raise HTTPException(status_code=503, detail={"error": code, "detail": "Provider operation could not be completed."}) from exc


def lists_snapshot(request: Request) -> dict[str, Any]:
    execution = _composition(request).lists_execution
    if execution is None:
        return {"ok": False, "domain": "lists", "status": "disabled", "lists": []}
    try:
        return execution.snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": getattr(exc, "error_code", "lists_provider_unavailable"), "detail": "Lists are unavailable."}) from exc


def notes_snapshot(request: Request) -> dict[str, Any]:
    execution = _composition(request).notes_execution
    if execution is None:
        return {"ok": False, "domain": "notes", "status": "disabled", "notes": []}
    try:
        return execution.snapshot()
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": getattr(exc, "error_code", "notes_provider_unavailable"), "detail": "Notes are unavailable."}) from exc


def lists_operation(payload: ListOperationRequest, request: Request) -> dict[str, Any]:
    values = payload.model_dump(exclude={"operation"}, exclude_none=True)
    return _execute(_composition(request).lists_execution, payload.operation, values)


def notes_operation(payload: NoteOperationRequest, request: Request) -> dict[str, Any]:
    values = payload.model_dump(exclude={"operation"}, exclude_none=True)
    return _execute(_composition(request).notes_execution, payload.operation, values)


def lists_health(request: Request) -> dict[str, Any]:
    execution = _composition(request).lists_execution
    return {"status": "disabled", "provider": None} if execution is None else execution.health()


def notes_health(request: Request) -> dict[str, Any]:
    execution = _composition(request).notes_execution
    return {"status": "disabled", "provider": None} if execution is None else execution.health()


def register_lists_notes_routes(app: FastAPI) -> None:
    app.get("/api/ui/lists")(lists_snapshot)
    app.post("/api/ui/lists/operation")(lists_operation)
    app.get("/api/ui/notes")(notes_snapshot)
    app.post("/api/ui/notes/operation")(notes_operation)
    app.get("/api/admin/health/lists")(lists_health)
    app.get("/api/admin/health/notes")(notes_health)
