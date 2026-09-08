from __future__ import annotations

import logging
from typing import Any

from oracle_app.command_events import append_command_interim_event
from oracle_app.information_runtime import CanonicalFactsExecution
from oracle_app.inference import InferenceClient
from oracle_app.memory.correlation import get_correlation_id
from oracle_app.facts import (
    build_facts_request,
    facts_result_to_dispatch_payload,
)
from oracle_app.facts_context import resolve_facts_context, retain_facts_context
from oracle_app.session_state import clear_informational_context
from oracle_app.schemas import DispatchPlan


logger = logging.getLogger("oracle-brain.facts")


class FactsHandler:
    target = "facts"

    def __init__(
        self,
        canonical_execution: CanonicalFactsExecution | None = None,
        *,
        inference: InferenceClient | None = None,
    ) -> None:
        self.canonical_execution = canonical_execution
        self.inference = inference

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        del registry
        source = str(dispatch.payload.get("source") or "-")
        session_id = str(dispatch.payload.get("session_id") or "-")
        query = str(dispatch.payload.get("query") or dispatch.payload.get("prompt") or "").strip()
        logger.info("facts_requested source=%s session_id=%s", source, session_id)

        context_resolution = resolve_facts_context(
            query,
            source=None if source == "-" else source,
            session_id=None if session_id == "-" else session_id,
        )
        if context_resolution.kind == "clarification":
            dispatch.status = "pending_clarification"
            dispatch.result = {
                "action": "facts_subject_clarification",
                "prompt": context_resolution.prompt,
                "query": query,
            }
            return dispatch
        if context_resolution.kind == "provenance":
            dispatch.status = "executed"
            dispatch.result = {
                "action": "facts_provenance",
                "query": query,
                "source_names": list(context_resolution.source_names),
                "context_used": True,
            }
            return dispatch
        if context_resolution.kind == "no_provenance":
            dispatch.status = "executed"
            dispatch.result = {
                "action": "facts_provenance",
                "query": query,
                "source_names": [],
                "context_used": False,
            }
            return dispatch
        if context_resolution.kind in {"age", "no_age"}:
            dispatch.status = "executed"
            dispatch.result = {
                "action": "facts_age",
                "query": query,
                "age_seconds": context_resolution.age_seconds,
                "context_used": context_resolution.context_used,
            }
            return dispatch
        resolved_query = context_resolution.query
        if not context_resolution.context_used:
            clear_informational_context(
                None if source == "-" else source,
                None if session_id == "-" else session_id,
                reason="explicit_facts_query",
            )

        if (
            self.canonical_execution is not None
            and self.canonical_execution.settings.enabled
            and self.canonical_execution.settings.acknowledgement_enabled
        ):
            append_command_interim_event(
                source=None if source == "-" else source,
                session_id=None if session_id == "-" else session_id,
                correlation_id=get_correlation_id(),
                event_type="facts_summarizer_ack",
                domain="facts",
                message="One second while I look that up.",
            )

        request = build_facts_request(
            query=resolved_query,
            source=None if source == "-" else source,
            session_id=None if session_id == "-" else session_id,
        )
        if self.canonical_execution is not None:
            result = self.canonical_execution.lookup(request)
            summary = self.canonical_execution.maybe_summarize(
                result,
                source=None if source == "-" else source,
                session_id=None if session_id == "-" else session_id,
            )
        else:
            result = _disabled_result(request.query)
            summary = None
        dispatch.status = "failed" if result.status == "provider_error" else "executed"
        dispatch.result = facts_result_to_dispatch_payload(result, summary=summary)
        if self.canonical_execution is not None:
            if not self.canonical_execution.settings.summarizer_enabled:
                dispatch.result["summarizer_status"] = "disabled"
            elif result.status not in {"answered", "evidence_only"}:
                dispatch.result["summarizer_status"] = "not_applicable"
            elif summary is None:
                dispatch.result["summarizer_status"] = "unavailable"
        dispatch.result["original_query"] = query
        dispatch.result["context_used"] = context_resolution.context_used
        retain_facts_context(
            result,
            source=None if source == "-" else source,
            session_id=None if session_id == "-" else session_id,
        )
        logger.info("facts_finished source=%s session_id=%s status=%s", source, session_id, result.status)
        return dispatch


def _disabled_result(query: str):
    from oracle_app.schemas import FactsProviderInfo, FactsProviderResult, FactsRetrievalInfo

    return FactsProviderResult(
        status="disabled",
        query=query,
        answer=None,
        evidence=[],
        provider=FactsProviderInfo(id="none", name="Facts provider"),
        retrieval=FactsRetrievalInfo(method="disabled", notes=[]),
        detail="Facts lookup is disabled or not configured.",
    )
