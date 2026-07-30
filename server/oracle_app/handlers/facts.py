from __future__ import annotations

import logging
from typing import Any

from oracle_app.config import get_facts_settings
from oracle_app.information_runtime import CanonicalFactsExecution
from oracle_app.facts import (
    build_facts_request,
    facts_result_to_dispatch_payload,
    lookup_facts,
    maybe_summarize_facts_result,
)
from oracle_app.schemas import DispatchPlan


logger = logging.getLogger("oracle-brain.facts")


class FactsHandler:
    target = "facts"

    def __init__(
        self,
        canonical_execution: CanonicalFactsExecution | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.canonical_execution = canonical_execution
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        del registry
        source = str(dispatch.payload.get("source") or "-")
        session_id = str(dispatch.payload.get("session_id") or "-")
        query = str(dispatch.payload.get("query") or dispatch.payload.get("prompt") or "").strip()
        logger.info("facts_requested source=%s session_id=%s", source, session_id)

        request = build_facts_request(
            query=query,
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
        elif self.canonical_authority:
            result = _disabled_result(request.query)
            summary = None
        else:
            settings = get_facts_settings()
            result = lookup_facts(request, settings=settings)
            summary = maybe_summarize_facts_result(
                result,
                settings=settings,
                source=None if source == "-" else source,
                session_id=None if session_id == "-" else session_id,
            )
        dispatch.status = "failed" if result.status == "provider_error" else "executed"
        dispatch.result = facts_result_to_dispatch_payload(result, summary=summary)
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
