from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .config import get_facts_settings
from .inference import InferenceClient
from .schemas import FactsProviderResult
from .information_runtime import CanonicalFactsExecution


logger = logging.getLogger("oracle-brain.facts")


def admin_facts_lookup(
    query: str,
    summarize: bool | None = None,
    *,
    canonical_execution: CanonicalFactsExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, object]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise HTTPException(status_code=400, detail="query cannot be empty")

    from .facts import build_facts_request, facts_result_to_dispatch_payload, lookup_facts

    request = build_facts_request(
        query=normalized_query,
        source=None,
        session_id=None,
        interface="admin",
    )
    if canonical_execution is not None:
        result = canonical_execution.lookup(request)
        configured = canonical_execution.settings.summarizer_enabled
    elif canonical_authority:
        from .handlers.facts import _disabled_result

        result = _disabled_result(normalized_query)
        configured = False
    else:
        settings = get_facts_settings()
        result = lookup_facts(request, settings=settings)
        configured = bool(settings.get("summarizer_enabled", False))
    summary, summarizer = _run_admin_summarizer(
        result,
        summarizer_configured=configured,
        summarize=summarize,
        inference=None if canonical_execution is None else canonical_execution.inference,
    )
    payload = _redact_sensitive_fields(facts_result_to_dispatch_payload(result, summary=summary))
    return {
        "ok": result.status not in {"provider_error", "disabled"},
        "query": normalized_query,
        "facts": payload,
        "summarizer": summarizer,
    }


def _run_admin_summarizer(
    result: FactsProviderResult,
    *,
    summarizer_configured: bool,
    summarize: bool | None,
    inference: InferenceClient | None,
) -> tuple[str | None, dict[str, object]]:
    configured = summarizer_configured
    requested = configured if summarize is None else bool(summarize)
    supported_status = result.status in {"answered", "evidence_only"}
    status: dict[str, object] = {
        "configured": configured,
        "requested": requested,
        "attempted": False,
        "succeeded": False,
        "reason": None,
    }
    if not requested:
        status["reason"] = "not_requested"
        return None, status
    if not configured:
        status["reason"] = "not_configured"
        return None, status
    if not supported_status:
        status["reason"] = "unsupported_status"
        return None, status

    status["attempted"] = True
    try:
        summary = summarize_facts_result(result, inference=inference)
    except Exception as exc:
        logger.warning(
            "admin_facts_summarizer_failed status=%s provider=%s error=%s",
            result.status,
            result.provider.id,
            type(exc).__name__,
        )
        status["reason"] = "summarizer_error"
        return None, status
    if summary is None:
        status["reason"] = "rejected_or_empty"
        return None, status
    status["succeeded"] = True
    status["reason"] = "summarized"
    return summary, status


def summarize_facts_result(
    result: FactsProviderResult,
    *,
    inference: InferenceClient | None,
) -> str | None:
    if inference is None:
        raise RuntimeError("Canonical inference is required for facts summarization.")
    from .facts_summarizer import summarize_facts_result as summarize

    return summarize(result, inference=inference)


def _redact_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_sensitive_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    sensitive_terms = ("token", "secret", "password", "api_key", "authorization", "credential")
    return any(term in normalized for term in sensitive_terms)


def register_admin_facts_routes(app: FastAPI) -> None:
    app.get("/api/admin/facts/lookup")(admin_facts_lookup_http)


def admin_facts_lookup_http(
    request: Request,
    query: str,
    summarize: bool | None = None,
) -> dict[str, object]:
    from .brain_application_composition import (
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        CanonicalBrainApplicationComposition,
    )

    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return admin_facts_lookup(
        query,
        summarize,
        canonical_execution=composition.facts_execution,
        canonical_authority=True,
    )
