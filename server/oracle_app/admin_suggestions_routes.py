from __future__ import annotations

from fastapi import FastAPI, Request

from .suggestions.models import SuggestionGenerateRequest, SuggestionReviewRequest
from .suggestions.service import (
    generate_suggestion_run,
    get_run_or_404,
    get_suggestion_or_404,
    list_suggestion_items,
    list_suggestion_runs,
    openclaw_status,
    read_last_json,
    review_suggestion_item,
)
from .suggestions.storage import LAST_PACKET_PATH, LAST_RESPONSE_PATH


def admin_openclaw_status() -> dict[str, object]:
    return openclaw_status()


def admin_suggestions(
    status: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    category: str | None = None,
) -> dict[str, object]:
    return list_suggestion_items(status=status, severity=severity, source=source, category=category)


def admin_suggestion_runs() -> dict[str, object]:
    return list_suggestion_runs()


def admin_suggestion_run(run_id: str) -> dict[str, object]:
    return {"ok": True, "run": get_run_or_404(run_id)}


def admin_suggestions_last_packet() -> dict[str, object]:
    return read_last_json(LAST_PACKET_PATH)


def admin_suggestions_last_response() -> dict[str, object]:
    return read_last_json(LAST_RESPONSE_PATH)


def admin_suggestion_detail(suggestion_id: str) -> dict[str, object]:
    return {"ok": True, "suggestion": get_suggestion_or_404(suggestion_id)}


def admin_generate_suggestions(payload: SuggestionGenerateRequest) -> dict[str, object]:
    return generate_suggestion_run(payload)


def admin_review_suggestion(suggestion_id: str, payload: SuggestionReviewRequest) -> dict[str, object]:
    return review_suggestion_item(suggestion_id, payload)


def register_admin_suggestions_routes(app: FastAPI) -> None:
    app.get("/api/admin/suggestions/openclaw/status")(admin_openclaw_status_http)
    app.get("/api/admin/suggestions")(admin_suggestions)
    app.get("/api/admin/suggestions/runs")(admin_suggestion_runs)
    app.get("/api/admin/suggestions/runs/{run_id}")(admin_suggestion_run)
    app.get("/api/admin/suggestions/last-packet")(admin_suggestions_last_packet)
    app.get("/api/admin/suggestions/last-response")(admin_suggestions_last_response)
    app.get("/api/admin/suggestions/{suggestion_id}")(admin_suggestion_detail)
    app.post("/api/admin/suggestions/generate")(admin_generate_suggestions_http)
    app.post("/api/admin/suggestions/{suggestion_id}/review")(admin_review_suggestion)


def admin_openclaw_status_http(request: Request) -> dict[str, object]:
    canonical = _canonical_composition(request)
    return (
        admin_openclaw_status()
        if canonical is None
        else openclaw_status(
            canonical_execution=canonical.suggestions_execution,
            canonical_authority=True,
        )
    )


def admin_generate_suggestions_http(
    request: Request,
    payload: SuggestionGenerateRequest,
) -> dict[str, object]:
    canonical = _canonical_composition(request)
    return (
        admin_generate_suggestions(payload)
        if canonical is None
        else generate_suggestion_run(
            payload,
            canonical_execution=canonical.suggestions_execution,
            canonical_composition=canonical,
            canonical_authority=True,
        )
    )


def _canonical_composition(request: Request):
    from .brain_application_composition import (
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        CanonicalBrainApplicationComposition,
    )

    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    return composition if isinstance(composition, CanonicalBrainApplicationComposition) else None
