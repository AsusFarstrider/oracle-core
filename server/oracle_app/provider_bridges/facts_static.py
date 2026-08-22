from __future__ import annotations

from typing import Any

from oracle_app.text_normalization import normalize_text
from oracle_app.configuration.domain_models import StaticFactsProvider
from oracle_app.schemas import (
    FactsAnswer,
    FactsEvidence,
    FactsProviderInfo,
    FactsProviderRequest,
    FactsProviderResult,
    FactsRetrievalInfo,
)


class StaticFactsBridge:
    provider_id = "static"
    provider_name = "Static Facts"

    def lookup(self, request: FactsProviderRequest, *, settings: dict[str, Any]) -> FactsProviderResult:
        items = settings.get("static_items") or settings.get("facts_static_items") or []
        if not isinstance(items, list):
            items = []
        return self._lookup_items(request, items)

    def lookup_provider(
        self,
        request: FactsProviderRequest,
        *,
        provider: StaticFactsProvider,
    ) -> FactsProviderResult:
        return self._lookup_items(request, provider.items)

    def _lookup_items(self, request: FactsProviderRequest, items: list[object]) -> FactsProviderResult:
        query = request.query.strip()
        normalized_query = self._normalize_query(query)

        for item in items:
            if not isinstance(item, dict) and not hasattr(item, "queries"):
                continue
            queries = [str(value or "").strip() for value in list(_field(item, "queries") or [])]
            normalized_queries = [self._normalize_query(value) for value in queries if value]
            if normalized_query not in normalized_queries:
                continue

            requested_status = str(_field(item, "status") or "").strip().lower()
            if requested_status == "provider_error":
                return FactsProviderResult(
                    status="provider_error",
                    query=query,
                    answer=None,
                    evidence=[],
                    provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
                    retrieval=FactsRetrievalInfo(method="static_fixture", notes=["fixture requested provider_error"]),
                    detail=str(_field(item, "detail") or "Static facts fixture simulated a provider error.").strip(),
                )
            if requested_status == "no_result":
                return self._no_result(query, detail=str(_field(item, "detail") or "Static fixture returned no result.").strip())

            answer_text = self._normalize_answer_text(_field(item, "answer"))
            evidence = self._normalize_evidence(_field(item, "evidence"), request=request)
            if requested_status == "evidence_only" or (evidence and not answer_text):
                return FactsProviderResult(
                    status="evidence_only",
                    query=query,
                    answer=None,
                    evidence=evidence,
                    provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
                    retrieval=FactsRetrievalInfo(method="static_fixture", notes=["fixture omitted direct answer"]),
                )
            if answer_text:
                return FactsProviderResult(
                    status="answered",
                    query=query,
                    answer=FactsAnswer(text=answer_text, answer_type=str(_field(item, "answer_type") or "extractive")),
                    evidence=evidence,
                    provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
                    retrieval=FactsRetrievalInfo(method="static_fixture", notes=[]),
                )

        return self._no_result(query, detail="No static fact matched the query.")

    def _no_result(self, query: str, *, detail: str) -> FactsProviderResult:
        return FactsProviderResult(
            status="no_result",
            query=query,
            answer=None,
            evidence=[],
            provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
            retrieval=FactsRetrievalInfo(method="static_fixture", notes=[]),
            detail=detail,
        )

    def _normalize_answer_text(self, raw_answer: object) -> str:
        if isinstance(raw_answer, dict):
            return str(raw_answer.get("text") or "").strip()
        if raw_answer is not None and hasattr(raw_answer, "text"):
            return str(getattr(raw_answer, "text") or "").strip()
        return str(raw_answer or "").strip()

    def _normalize_evidence(self, raw_evidence: object, *, request: FactsProviderRequest) -> list[FactsEvidence]:
        if not request.options.include_evidence:
            return []
        output: list[FactsEvidence] = []
        if not isinstance(raw_evidence, (list, tuple)):
            return output
        for item in raw_evidence:
            if not isinstance(item, dict) and not hasattr(item, "snippet"):
                continue
            snippet = str(_field(item, "snippet") or "").strip()
            if not snippet:
                continue
            provenance = _field(item, "provenance")
            if provenance is not None and not isinstance(provenance, dict):
                provenance = provenance.model_dump(exclude_none=True)
            output.append(
                FactsEvidence(
                    title=str(_field(item, "title") or "Static fact").strip(),
                    snippet=snippet,
                    source_name=str(_field(item, "source_name") or self.provider_name).strip(),
                    source_type=str(_field(item, "source_type") or "static").strip(),
                    provenance=dict(provenance or {}),
                )
            )
        return output[: request.options.max_evidence_items]

    def _normalize_query(self, value: str) -> str:
        return normalize_text(value).strip(" ?!.")


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)
