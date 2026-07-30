from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from oracle_app.config import get_openclaw_settings
from oracle_app.provider_bridges.openclaw import generate_suggestions as openclaw_generate_suggestions

from .models import SuggestionGenerateRequest, SuggestionReviewRequest
from .packet import build_packet
from .redaction import redact_secrets
from .storage import (
    LAST_PACKET_PATH,
    LAST_RESPONSE_PATH,
    create_run,
    default_window,
    get_run,
    get_suggestion,
    insert_suggestions,
    list_runs,
    list_suggestions,
    review_suggestion,
    update_run,
)


def generate_suggestion_run(
    request: SuggestionGenerateRequest,
    *,
    canonical_execution=None,
    canonical_composition=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    if canonical_authority:
        if canonical_execution is None or not canonical_execution.enabled:
            raise HTTPException(status_code=409, detail="Suggestions is disabled in canonical configuration.")
        settings: dict[str, Any] = {}
        max_suggestions = canonical_execution.max_suggestions(request.max_suggestions)
    else:
        settings = get_openclaw_settings()
        max_suggestions = int(request.max_suggestions or settings.get("max_suggestions") or 10)
    window_start, window_end = _resolve_window(request)
    run_id = create_run(
        run_type=request.run_type,
        window_start=window_start,
        window_end=window_end,
        reason=request.reason,
        custom_prompt=request.custom_prompt,
        mock=request.use_mock,
    )
    packet, collector_status = build_packet(
        run_id=run_id,
        run_type=request.run_type,
        window_start=window_start,
        window_end=window_end,
        reason=request.reason,
        custom_prompt=request.custom_prompt,
        max_suggestions=max_suggestions,
        canonical_composition=canonical_composition,
        canonical_authority=canonical_authority,
    )
    _write_json(LAST_PACKET_PATH, packet)

    bridge_options = {
        **settings,
        "max_suggestions": max_suggestions,
        "use_mock": request.use_mock,
        "adapter": (
            "mock"
            if request.use_mock
            else canonical_execution.status()["adapter"]
            if canonical_execution is not None
            else settings.get("adapter", "http")
        ),
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
        name=f"openclaw-suggestions-{run_id[:8]}",
    )
    thread.start()
    return {
        "ok": True,
        "queued": True,
        "run": get_run(run_id),
        "suggestions": [],
        "errors": [],
        "provider": "openclaw",
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
    result = (
        canonical_execution.generate(
            packet,
            max_suggestions=int(bridge_options["max_suggestions"]),
            use_mock=use_mock,
        )
        if canonical_execution is not None
        else openclaw_generate_suggestions(packet, bridge_options)
    )
    redacted_result = redact_secrets(result)
    _write_json(LAST_RESPONSE_PATH, redacted_result)

    suggestions: list[dict[str, Any]] = []
    errors = [str(item) for item in result.get("errors") or []]
    if bool(result.get("ok")):
        raw_items = [item for item in result.get("suggestions") or [] if isinstance(item, dict)]
        suggestions = insert_suggestions(run_id, raw_items, mock=bool(result.get("mock") or use_mock))
        status = "completed"
        openclaw_status = "ok"
        error_text = None
    else:
        status = "failed"
        openclaw_status = "failed"
        error_text = "; ".join(errors) or "OpenClaw did not return suggestions."

    update_run(
        run_id,
        status=status,
        openclaw_status=openclaw_status,
        collector_status=collector_status,
        error=error_text,
        suggestion_count=len(suggestions),
    )
    return {
        "ok": bool(result.get("ok")),
        "run": get_run(run_id),
        "suggestions": suggestions,
        "errors": errors,
        "provider": result.get("provider", "openclaw"),
        "adapter": result.get("adapter"),
        "mock": bool(result.get("mock") or use_mock),
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
    return item


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


def read_last_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "detail": f"{path.name} has not been created yet.", "payload": None}
    with path.open("r", encoding="utf-8") as handle:
        return {"ok": True, "path": str(path), "payload": json.load(handle)}


def openclaw_status(
    *,
    canonical_execution=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    if canonical_authority:
        if canonical_execution is None:
            return {
                "ok": False,
                "provider": "openclaw",
                "adapter": "",
                "configured": False,
                "base_url_configured": False,
                "ssh_target_configured": False,
                "endpoint_path_configured": False,
                "detail": "Suggestions is disabled in canonical configuration.",
            }
        return canonical_execution.status()
    settings = get_openclaw_settings()
    adapter = str(settings.get("adapter") or "http")
    if adapter == "http":
        configured = bool(settings.get("base_url"))
    elif adapter == "ssh_cli":
        configured = bool(settings.get("ssh_target"))
    else:
        configured = adapter == "mock"
    return {
        "ok": configured,
        "provider": "openclaw",
        "adapter": adapter,
        "configured": configured,
        "base_url_configured": bool(settings.get("base_url")),
        "ssh_target_configured": bool(settings.get("ssh_target")),
        "endpoint_path_configured": bool(settings.get("endpoint_path")),
        "detail": "OpenClaw transport is configured." if configured else "OpenClaw transport is not configured. Interface discovery is still required.",
    }


def _resolve_window(request: SuggestionGenerateRequest) -> tuple[str, str]:
    default_start, default_end = default_window()
    return request.window_start or default_start, request.window_end or default_end


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(redact_secrets(payload), handle, indent=2, sort_keys=True)
        handle.write("\n")
