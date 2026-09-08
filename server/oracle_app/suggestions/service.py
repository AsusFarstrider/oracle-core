from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException


from .models import SuggestionGenerateRequest, SuggestionReviewRequest
from .packet import build_packet
from .redaction import redact_secrets
from .storage import (
    create_run,
    default_window,
    get_run,
    get_current_exchange,
    get_suggestion,
    insert_suggestions,
    list_runs,
    list_suggestions,
    review_suggestion,
    save_current_exchange,
    update_run,
)


def generate_suggestion_run(
    request: SuggestionGenerateRequest,
    *,
    canonical_execution=None,
    canonical_composition=None,
) -> dict[str, Any]:
    if canonical_execution is None or not canonical_execution.enabled:
        raise HTTPException(status_code=409, detail="Suggestions is disabled in canonical configuration.")
    max_suggestions = canonical_execution.max_suggestions(request.max_suggestions)
    execution_status = canonical_execution.status()
    selected_provider = str(execution_status.get("provider") or "suggestions")
    window_start, window_end = _resolve_window(request)
    run_id = create_run(
        run_type=request.run_type,
        window_start=window_start,
        window_end=window_end,
        reason=request.reason,
        custom_prompt=request.custom_prompt,
        mock=request.use_mock,
    )
    try:
        packet, collector_status = build_packet(
            run_id=run_id,
            run_type=request.run_type,
            window_start=window_start,
            window_end=window_end,
            reason=request.reason,
            custom_prompt=request.custom_prompt,
            max_suggestions=max_suggestions,
            canonical_composition=canonical_composition,
        )
    except Exception as exc:
        detail = f"Suggestions evidence collection failed: {type(exc).__name__}."
        update_run(
            run_id,
            status="failed",
            openclaw_status="not_called",
            collector_status={},
            error=detail,
            suggestion_count=0,
            collection_status="failed",
            failure_class="integration_failure",
        )
        return _integration_failure(run_id, detail, request.use_mock)
    save_current_exchange(run_id, packet=packet)

    packet_collection_status = str(
        ((packet.get("collection") or {}).get("status") if isinstance(packet.get("collection"), dict) else "")
        or "unknown"
    )

    bridge_options = {
        "max_suggestions": max_suggestions,
        "use_mock": request.use_mock,
        "adapter": (
            "mock"
            if request.use_mock
            else execution_status["adapter"]
            if canonical_execution is not None
            else ""
        ),
    }
    if packet_collection_status == "unavailable":
        result = {
            "ok": False,
            "provider": selected_provider,
            "adapter": bridge_options.get("adapter"),
            "raw_response": {},
            "suggestions": [],
            "errors": ["All requested Suggestions evidence collectors are unavailable."],
            "failure_class": "collection_unavailable",
            "mock": bool(request.use_mock),
        }
        save_current_exchange(run_id, response=result)
        update_run(
            run_id,
            status="failed",
            openclaw_status="not_called",
            collector_status=collector_status,
            error=result["errors"][0],
            suggestion_count=0,
            collection_status=packet_collection_status,
            failure_class="collection_unavailable",
        )
        return {
            **result,
            "run": get_run(run_id),
        }
    if request.wait_for_completion:
        return _complete_suggestion_run(
            run_id=run_id,
            packet=packet,
            bridge_options=bridge_options,
            collector_status=collector_status,
            use_mock=request.use_mock,
            canonical_execution=canonical_execution,
        )

    thread = threading.Thread(
        target=_complete_suggestion_run,
        kwargs={
            "run_id": run_id,
            "packet": packet,
            "bridge_options": bridge_options,
            "collector_status": collector_status,
            "use_mock": request.use_mock,
            "canonical_execution": canonical_execution,
        },
        daemon=True,
        name=f"suggestions-{run_id[:8]}",
    )
    thread.start()
    return {
        "ok": True,
        "queued": True,
        "run": get_run(run_id),
        "suggestions": [],
        "errors": [],
        "provider": selected_provider,
        "adapter": bridge_options.get("adapter"),
        "mock": bool(request.use_mock),
    }


def _complete_suggestion_run(
    *,
    run_id: str,
    packet: dict[str, Any],
    bridge_options: dict[str, Any],
    collector_status: dict[str, Any],
    use_mock: bool,
    canonical_execution=None,
) -> dict[str, Any]:
    if canonical_execution is None:
        raise RuntimeError("Canonical Suggestions execution is unavailable.")
    try:
        result = canonical_execution.generate(
            packet,
            max_suggestions=int(bridge_options["max_suggestions"]),
            use_mock=use_mock,
        )
    except Exception as exc:
        detail = f"Suggestions provider integration failed: {type(exc).__name__}."
        execution_status = canonical_execution.status()
        result = {
            "ok": False,
            "provider": execution_status.get("provider", "suggestions"),
            "adapter": bridge_options.get("adapter"),
            "raw_response": {},
            "suggestions": [],
            "errors": [detail],
            "failure_class": "integration_failure",
            "mock": bool(use_mock),
        }
    redacted_result = redact_secrets(result)

    suggestions: list[dict[str, Any]] = []
    errors = [str(item) for item in result.get("errors") or []]
    if bool(result.get("ok")):
        raw_items = [item for item in result.get("suggestions") or [] if isinstance(item, dict)]
        intake = insert_suggestions(run_id, raw_items, mock=bool(result.get("mock") or use_mock))
        suggestions = intake.created
        suppressed = intake.suppressed
        status = "completed"
        openclaw_status = "partial" if errors else "ok"
        error_text = "; ".join(errors) if errors else None
        failure_class = "response_validation" if errors else None
    else:
        suppressed = []
        status = "failed"
        openclaw_status = "failed"
        error_text = "; ".join(errors) or "Suggestions provider did not return suggestions."
        failure_class = str(result.get("failure_class") or "provider_failure")

    redacted_result["oracle_intake"] = {
        "stored_count": len(suggestions),
        "suppressed_count": len(suppressed),
        "suppressed": suppressed,
    }
    save_current_exchange(run_id, response=redacted_result)

    update_run(
        run_id,
        status=status,
        openclaw_status=openclaw_status,
        collector_status=collector_status,
        error=error_text,
        suggestion_count=len(suggestions),
        suppressed_count=len(suppressed),
        collection_status=str(
            ((packet.get("collection") or {}).get("status") if isinstance(packet.get("collection"), dict) else "")
            or "unknown"
        ),
        failure_class=failure_class,
    )
    return {
        "ok": bool(result.get("ok")),
        "run": get_run(run_id),
        "suggestions": suggestions,
        "suppressed": suppressed,
        "suppressed_count": len(suppressed),
        "errors": errors,
        "provider": result.get("provider", "suggestions"),
        "adapter": result.get("adapter"),
        "failure_class": failure_class,
        "mock": bool(result.get("mock") or use_mock),
    }


def _integration_failure(run_id: str, detail: str, use_mock: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "run": get_run(run_id),
        "suggestions": [],
        "suppressed": [],
        "suppressed_count": 0,
        "errors": [detail],
        "provider": "suggestions",
        "adapter": None,
        "failure_class": "integration_failure",
        "mock": bool(use_mock),
    }


def review_suggestion_item(suggestion_id: str, request: SuggestionReviewRequest) -> dict[str, Any]:
    updated = review_suggestion(suggestion_id, request.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Unknown suggestion {suggestion_id}")
    return {"ok": True, "suggestion": updated}


def get_suggestion_or_404(suggestion_id: str) -> dict[str, Any]:
    item = get_suggestion(suggestion_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Unknown suggestion {suggestion_id}")
    return {**item, "run": get_run(str(item["run_id"]))}


def get_run_or_404(run_id: str) -> dict[str, Any]:
    item = get_run(run_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Unknown suggestion run {run_id}")
    return item


def list_suggestion_items(
    *,
    status: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    filters = {"status": status, "severity": severity, "source": source, "category": category}
    suggestions = list_suggestions(filters)
    return {"ok": True, "suggestions": suggestions, "count": len(suggestions)}


def list_suggestion_runs() -> dict[str, Any]:
    runs = list_runs()
    return {"ok": True, "runs": runs, "count": len(runs)}


def read_current_exchange(part: str) -> dict[str, Any]:
    if part not in {"packet", "response"}:
        raise ValueError(f"Unknown Suggestions exchange part: {part!r}")
    exchange = get_current_exchange()
    payload = exchange.get(part) if exchange is not None else None
    if payload is None:
        return {"ok": False, "detail": f"Current {part} has not been stored yet.", "payload": None}
    return {
        "ok": True,
        "run_id": exchange["run_id"],
        "updated_at": exchange["updated_at"],
        "payload": payload,
    }


def openclaw_status(
    *,
    canonical_execution=None,
) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.status()
    return {
        "ok": False,
        "provider": "suggestions",
        "adapter": "",
        "configured": False,
        "base_url_configured": False,
        "ssh_target_configured": False,
        "endpoint_path_configured": False,
        "detail": "Suggestions is disabled in canonical configuration.",
    }


def _resolve_window(request: SuggestionGenerateRequest) -> tuple[str, str]:
    default_start, default_end = default_window()
    start = _window_timestamp(request.window_start or default_start, "window_start")
    end = _window_timestamp(request.window_end or default_end, "window_end")
    if start > end:
        raise HTTPException(status_code=422, detail="Suggestions window_start must not follow window_end.")
    return start.isoformat(), end.isoformat()


def _window_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Suggestions {field} must be an ISO timestamp.") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"Suggestions {field} must include a timezone.")
    return parsed.astimezone(timezone.utc)
