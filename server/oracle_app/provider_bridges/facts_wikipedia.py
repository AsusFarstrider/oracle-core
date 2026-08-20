from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from oracle_app.configuration.domain_models import WikipediaFactsProvider
from oracle_app.facts_wikipedia_policy import WikipediaQuestionPolicy, WikipediaSearchPlan
from oracle_app.schemas import (
    FactsAnswer,
    FactsEvidence,
    FactsProviderInfo,
    FactsProviderRequest,
    FactsProviderResult,
    FactsRetrievalInfo,
)


class WikipediaFactsBridge:
    provider_id = "wikipedia_api"
    provider_name = "Wikipedia API"

    def __init__(self, *, policy: WikipediaQuestionPolicy) -> None:
        self._policy = policy

    def lookup(self, request_payload: FactsProviderRequest, *, settings: dict[str, Any]) -> FactsProviderResult:
        language = str(settings.get("wikipedia_language") or "en").strip().lower() or "en"
        timeout_seconds = int(settings.get("wikipedia_timeout_seconds") or settings.get("timeout_seconds") or 8)
        return self._lookup(request_payload, language=language, timeout_seconds=timeout_seconds)

    def lookup_provider(
        self,
        request_payload: FactsProviderRequest,
        *,
        provider: WikipediaFactsProvider,
    ) -> FactsProviderResult:
        return self._lookup(
            request_payload,
            language=provider.language,
            timeout_seconds=provider.timeout_seconds,
        )

    def _lookup(
        self,
        request_payload: FactsProviderRequest,
        *,
        language: str,
        timeout_seconds: int,
    ) -> FactsProviderResult:
        query = request_payload.query.strip()
        try:
            search_plan = self._policy.build_search_plan(query)
            search_results = self._search_titles(
                search_plan.search_query,
                language=language,
                timeout_seconds=timeout_seconds,
            )
            if not search_results:
                return self._no_result(query, detail="Wikipedia search returned no result.")
            summary = self._select_summary(
                query=query,
                search_results=search_results,
                search_plan=search_plan,
                language=language,
                timeout_seconds=timeout_seconds,
            )
            if summary is None:
                return self._no_result(query, detail="Wikipedia search returned no usable page summary.")
            return self._normalize_summary(query, summary, request_payload=request_payload, language=language)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                return self._no_result(query, detail="Wikipedia page summary was not found.")
            return self._provider_error(query, f"Wikipedia returned HTTP {exc.code}: {detail}".strip())
        except error.URLError as exc:
            return self._provider_error(query, f"Wikipedia request failed: {exc.reason}")
        except (TimeoutError, OSError) as exc:
            return self._provider_error(query, f"Wikipedia request failed: {exc}")
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            return self._provider_error(query, f"Wikipedia returned malformed data: {exc}")

    def _search_titles(self, query: str, *, language: str, timeout_seconds: int) -> list[str]:
        params = parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": "5",
                "format": "json",
                "utf8": "1",
            }
        )
        payload = self._get_json(
            f"https://{language}.wikipedia.org/w/api.php?{params}",
            timeout_seconds=timeout_seconds,
        )
        results = ((payload.get("query") or {}).get("search") or [])
        if not isinstance(results, list) or not results:
            return []
        titles: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if title:
                titles.append(title)
        return titles

    def _select_summary(
        self,
        *,
        query: str,
        search_results: list[str],
        search_plan: WikipediaSearchPlan,
        language: str,
        timeout_seconds: int,
    ) -> dict[str, Any] | None:
        fallback: dict[str, Any] | None = None
        fallback_score = -1
        for title in search_results:
            try:
                summary = self._fetch_summary(title, language=language, timeout_seconds=timeout_seconds)
            except error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            score = self._policy.score_summary(query=query, summary=summary, search_plan=search_plan)
            if score > fallback_score:
                fallback = summary
                fallback_score = score
            if score >= search_plan.accept_score:
                return self._maybe_add_lifespan_extract(
                    summary,
                    search_plan=search_plan,
                    language=language,
                    timeout_seconds=timeout_seconds,
                )
        if fallback is None:
            return None
        return self._maybe_add_lifespan_extract(
            fallback,
            search_plan=search_plan,
            language=language,
            timeout_seconds=timeout_seconds,
        )

    def _fetch_summary(self, title: str, *, language: str, timeout_seconds: int) -> dict[str, Any]:
        encoded_title = parse.quote(title.replace(" ", "_"), safe="")
        payload = self._get_json(
            f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{encoded_title}",
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(payload, dict):
            raise ValueError("summary payload is not an object")
        return payload

    def _fetch_page_extract(self, title: str, *, language: str, timeout_seconds: int) -> str:
        params = parse.urlencode(
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "exsectionformat": "plain",
                "titles": title,
                "format": "json",
                "utf8": "1",
            }
        )
        payload = self._get_json(
            f"https://{language}.wikipedia.org/w/api.php?{params}",
            timeout_seconds=timeout_seconds,
        )
        pages = ((payload.get("query") or {}).get("pages") or {})
        if not isinstance(pages, dict):
            return ""
        for page in pages.values():
            if isinstance(page, dict):
                extract = str(page.get("extract") or "").strip()
                if extract:
                    return extract
        return ""

    def _get_json(self, url: str, *, timeout_seconds: int) -> dict[str, Any]:
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Oracle facts bridge (https://oracle.local)",
            },
            method="GET",
        )
        with request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        return payload

    def _normalize_summary(
        self,
        query: str,
        payload: dict[str, Any],
        *,
        request_payload: FactsProviderRequest,
        language: str,
    ) -> FactsProviderResult:
        extract = str(payload.get("extract") or "").strip()
        title = str(payload.get("title") or "").strip() or "Wikipedia"
        page_url = str(((payload.get("content_urls") or {}).get("desktop") or {}).get("page") or "").strip()
        if not page_url:
            page_url = f"https://{language}.wikipedia.org/wiki/{parse.quote(title.replace(' ', '_'))}"
        if not extract:
            raise ValueError("summary payload missing extract")

        evidence: list[FactsEvidence] = []
        if request_payload.options.include_evidence and request_payload.options.max_evidence_items > 0:
            evidence.append(
                FactsEvidence(
                    title=title,
                    snippet=extract,
                    source_name="Wikipedia",
                    source_type="wikipedia",
                    provenance={
                        "url": page_url,
                        "page_title": title,
                        "language": language,
                    },
                )
            )

        notes = [str(item).strip() for item in payload.get("_oracle_retrieval_notes") or [] if str(item).strip()]
        return FactsProviderResult(
            status="answered",
            query=query,
            answer=FactsAnswer(text=extract, answer_type="extractive"),
            evidence=evidence[: request_payload.options.max_evidence_items],
            provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
            retrieval=FactsRetrievalInfo(method="wikipedia_summary_lookup", notes=notes),
        )

    def _maybe_add_lifespan_extract(
        self,
        summary: dict[str, Any],
        *,
        search_plan: WikipediaSearchPlan,
        language: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if not self._policy.needs_lifespan_extract(summary, search_plan):
            return summary
        title = str(summary.get("title") or "").strip()
        if not title:
            return summary
        full_extract = self._fetch_page_extract(title, language=language, timeout_seconds=timeout_seconds)
        return self._policy.enrich_lifespan(summary, full_extract)

    def _no_result(self, query: str, *, detail: str) -> FactsProviderResult:
        return FactsProviderResult(
            status="no_result",
            query=query,
            answer=None,
            evidence=[],
            provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
            retrieval=FactsRetrievalInfo(method="wikipedia_summary_lookup", notes=[]),
            detail=detail,
        )

    def _provider_error(self, query: str, detail: str) -> FactsProviderResult:
        return FactsProviderResult(
            status="provider_error",
            query=query,
            answer=None,
            evidence=[],
            provider=FactsProviderInfo(id=self.provider_id, name=self.provider_name),
            retrieval=FactsRetrievalInfo(method="wikipedia_summary_lookup", notes=[]),
            detail=detail,
        )
