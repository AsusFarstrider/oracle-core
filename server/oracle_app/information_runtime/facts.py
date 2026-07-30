from __future__ import annotations

import logging

from oracle_app.command_events import append_command_interim_event
from oracle_app.configuration.domain_models import StaticFactsProvider, WikipediaFactsProvider
from oracle_app.configuration.information_runtime_settings import FactsRuntimeSettings
from oracle_app.facts_cache import load_cached_facts_result, store_facts_result_in_cache
from oracle_app.provider_bridges.facts_static import StaticFactsBridge
from oracle_app.provider_bridges.facts_wikipedia import WikipediaFactsBridge
from oracle_app.schemas import (
    FactsProviderInfo,
    FactsProviderRequest,
    FactsProviderResult,
    FactsRetrievalInfo,
)


logger = logging.getLogger("oracle-brain.facts")


class CanonicalFactsExecution:
    """Facts provider behavior bound to one immutable configuration snapshot."""

    def __init__(self, settings: FactsRuntimeSettings) -> None:
        self.settings = settings
        if settings.enabled and (settings.provider_id is None or settings.provider is None):
            raise ValueError("Enabled canonical facts requires one selected provider.")

    def lookup(self, request: FactsProviderRequest) -> FactsProviderResult:
        provider = self.settings.provider
        if not self.settings.enabled or provider is None:
            return FactsProviderResult(
                status="disabled",
                query=request.query,
                answer=None,
                evidence=[],
                provider=FactsProviderInfo(id="none", name="Facts provider"),
                retrieval=FactsRetrievalInfo(method="disabled", notes=[]),
                detail="Facts lookup is disabled or not configured.",
            )
        cached = load_cached_facts_result(request, settings=self.settings)
        if cached is not None:
            return cached
        if isinstance(provider, StaticFactsProvider):
            result = StaticFactsBridge().lookup_provider(request, provider=provider)
        elif isinstance(provider, WikipediaFactsProvider):
            result = WikipediaFactsBridge().lookup_provider(request, provider=provider)
        else:  # pragma: no cover - executable schema closes this union
            raise TypeError("Canonical facts selected an unsupported typed provider.")
        store_facts_result_in_cache(request, result, settings=self.settings)
        return result

    def maybe_summarize(
        self,
        result: FactsProviderResult,
        *,
        source: str | None = None,
        session_id: str | None = None,
    ) -> str | None:
        if not self.settings.summarizer_enabled:
            return None
        if result.status not in {"answered", "evidence_only"}:
            return None
        if self.settings.acknowledgement_enabled:
            append_command_interim_event(
                source=source,
                session_id=session_id,
                event_type="facts_summarizer_ack",
                domain="facts",
                message="One second while I look that up.",
            )
        try:
            from oracle_app.facts_summarizer import summarize_facts_result

            return summarize_facts_result(result)
        except Exception as exc:
            logger.warning(
                "facts_summarizer_failed status=%s provider=%s error=%s",
                result.status,
                result.provider.id,
                type(exc).__name__,
            )
            return None
