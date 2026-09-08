from __future__ import annotations

import logging
from typing import Any
from oracle_app.facts_cache import load_cached_facts_result, store_facts_result_in_cache
from oracle_app.facts_summarizer import FactsSummarizationResult, summarize_facts_result
from oracle_app.facts_wikipedia_policy import WikipediaQuestionPolicy
from oracle_app.inference import InferenceClient
from oracle_app.provider_bridges.facts_static import StaticFactsBridge
from oracle_app.provider_bridges.facts_wikipedia import WikipediaFactsBridge
from oracle_app.schemas import (
    FactsProviderInfo,
    FactsProviderRequest,
    FactsProviderResult,
    FactsRequestContext,
    FactsRequestOptions,
    FactsRetrievalInfo,
)


logger = logging.getLogger("oracle-brain.facts")


def build_facts_request(
    *,
    query: str,
    source: str | None,
    session_id: str | None,
    interface: str = "voice",
) -> FactsProviderRequest:
    return FactsProviderRequest(
        query=query,
        context=FactsRequestContext(
            interface=interface,
            conversation_id=session_id,
            room_id=source,
        ),
        options=FactsRequestOptions(
            prefer_short_answer=True,
            include_evidence=True,
            max_evidence_items=5,
        ),
    )


def lookup_facts(request: FactsProviderRequest, *, settings: dict[str, Any]) -> FactsProviderResult:
    provider = str(settings.get("provider") or "static").strip().lower()
    if not bool(settings.get("enabled", False)):
        return FactsProviderResult(
            status="disabled",
            query=request.query,
            answer=None,
            evidence=[],
            provider=FactsProviderInfo(id=provider or "none", name="Facts provider"),
            retrieval=FactsRetrievalInfo(method="disabled", notes=[]),
            detail="Facts lookup is disabled or not configured.",
        )
    cached = load_cached_facts_result(request, settings=settings)
    if cached is not None:
        return cached
    if provider == "static":
        result = StaticFactsBridge().lookup(request, settings=settings)
        store_facts_result_in_cache(request, result, settings=settings)
        return result
    if provider == "wikipedia_api":
        result = WikipediaFactsBridge(policy=WikipediaQuestionPolicy()).lookup(request, settings=settings)
        store_facts_result_in_cache(request, result, settings=settings)
        return result
    result = FactsProviderResult(
        status="provider_error",
        query=request.query,
        answer=None,
        evidence=[],
        provider=FactsProviderInfo(id=provider or "unknown", name=provider or "Unknown provider"),
        retrieval=FactsRetrievalInfo(method="unsupported_provider", notes=[]),
        detail=f"Unsupported facts provider: {provider}.",
    )
    store_facts_result_in_cache(request, result, settings=settings)
    return result


def maybe_summarize_facts_result(
    result: FactsProviderResult,
    *,
    settings: dict[str, Any],
    source: str | None = None,
    session_id: str | None = None,
    inference: InferenceClient,
) -> FactsSummarizationResult | None:
    if not bool(settings.get("summarizer_enabled", False)):
        return None
    if result.status not in {"answered", "evidence_only"}:
        return None
    if not inference.can_attempt("facts_summarizer"):
        return None
    try:
        return summarize_facts_result(result, inference=inference)
    except Exception as exc:
        logger.warning(
            "facts_summarizer_failed status=%s provider=%s error=%s",
            result.status,
            result.provider.id,
            type(exc).__name__,
        )
        return None


def facts_result_to_dispatch_payload(
    result: FactsProviderResult,
    *,
    summary: FactsSummarizationResult | str | None = None,
) -> dict[str, object]:
    summary_text = summary.summary if isinstance(summary, FactsSummarizationResult) else summary
    inference_payload = None
    summarizer_status = "not_used"
    if isinstance(summary, FactsSummarizationResult):
        summarizer_status = summary.status
        inference_payload = {
            "provider_id": summary.provider_id,
            "provider_type": summary.provider_type,
            "model": summary.model,
            "request_id": summary.request_id,
            "attempts": [
                {
                    "provider_id": attempt.provider_id,
                    "provider_type": attempt.provider_type,
                    "model": attempt.model,
                    "outcome": attempt.outcome,
                    "detail_code": attempt.detail_code,
                }
                for attempt in summary.attempts
            ],
        }
    elif isinstance(summary_text, str) and summary_text.strip():
        summarizer_status = "summary"
    return {
        "action": "facts_lookup",
        "facts_status": result.status,
        "query": result.query,
        "summary": summary_text,
        "answer": result.answer.model_dump() if result.answer is not None else None,
        "evidence": [item.model_dump() for item in result.evidence],
        "provider": result.provider.model_dump(),
        "retrieval": result.retrieval.model_dump(),
        "detail": result.detail,
        "summarized_by_model": bool(str(summary_text or "").strip()),
        "summarizer_status": summarizer_status,
        "inference": inference_payload,
    }
