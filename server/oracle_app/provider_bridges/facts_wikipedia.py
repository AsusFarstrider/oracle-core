from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, parse, request

from oracle_app.configuration.domain_models import WikipediaFactsProvider
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
            search_plan = self._build_search_plan(query)
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
        search_plan: "_SearchPlan",
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
            score = self._score_summary(query=query, summary=summary, search_plan=search_plan)
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

    def _build_search_plan(self, query: str) -> "_SearchPlan":
        normalized_query = _normalize_words(query)
        authorship_match = re.match(r"^(?:who\s+wrote|who\s+is\s+the\s+author\s+of)\s+(.+?)\??$", normalized_query)
        if authorship_match:
            subject = authorship_match.group(1).strip(" '\"")
            if subject:
                return _SearchPlan(
                    search_query=subject,
                    subject=subject,
                    intent="authorship",
                    accept_score=8,
                )
        lifespan_match = re.match(
            r"^(?:how\s+long\s+(?:do|does|can|could)?\s*)(.+?)\s+(?:live|lives|living)(?:\s+for)?\??$",
            normalized_query,
        )
        if lifespan_match is None:
            lifespan_match = re.match(
                r"^(?:what\s+is\s+the\s+(?:life\s+span|lifespan|life\s+expectancy)\s+of)\s+(.+?)\??$",
                normalized_query,
            )
        if lifespan_match is None:
            lifespan_match = re.match(
                r"^(?:what\s+is\s+)?(.+?)\s+(?:life\s+span|lifespan|life\s+expectancy)\??$",
                normalized_query,
            )
        if lifespan_match:
            subject = _singularize_subject(lifespan_match.group(1).strip(" '\""))
            if subject:
                return _SearchPlan(
                    search_query=f"{subject} lifespan",
                    subject=subject,
                    intent="lifespan",
                    accept_score=7,
                )
        location_match = re.match(r"^(?:where\s+(?:is|are|was|were))\s+(.+?)(?:\s+located)?\??$", normalized_query)
        if location_match:
            subject = location_match.group(1).strip(" '\"")
            if subject:
                return _SearchPlan(
                    search_query=subject,
                    subject=subject,
                    intent="location",
                    accept_score=7,
                )
        date_match = re.match(
            r"^(?:when\s+(?:is|was|were|did))\s+(.+?)\s+(?:built|born|founded|created|published|made|opened|start|started|begin|began|release|released)\??$",
            normalized_query,
        )
        if date_match:
            subject = _strip_leading_article(date_match.group(1).strip(" '\""))
            if subject:
                return _SearchPlan(
                    search_query=subject,
                    subject=subject,
                    intent="date",
                    accept_score=7,
                )
        return _SearchPlan(
            search_query=query,
            subject=normalized_query,
            intent="general",
            accept_score=4,
        )

    def _score_summary(self, *, query: str, summary: dict[str, Any], search_plan: "_SearchPlan") -> int:
        title = _normalize_words(str(summary.get("title") or ""))
        extract = _normalize_words(str(summary.get("extract") or ""))
        subject = search_plan.subject
        score = 0
        if subject and subject == title:
            score += 8
        elif subject and subject in title:
            score += 5
        elif subject and subject in extract:
            score += 3

        if search_plan.intent == "authorship":
            if "written by" in extract or "author" in extract or "wrote" in extract:
                score += 3
            if title.startswith("the man who wrote") or "authorship question" in extract or "fringe theory" in extract:
                score -= 8
        elif search_plan.intent == "lifespan":
            if "lifespan" in extract or "life span" in extract or "life expectancy" in extract:
                score += 4
            if "years" in extract or "oldest" in extract:
                score += 2
        elif search_plan.intent == "location":
            if "located" in extract or "situated" in extract:
                score += 2
        elif search_plan.intent == "date":
            if any(term in extract for term in ("built", "born", "founded", "created", "published", "opened", "released")):
                score += 2
            if re.search(r"\b\d{3,4}\b", extract):
                score += 2
        else:
            query_words = {word for word in _normalize_words(query).split() if len(word) > 3}
            title_words = set(title.split())
            score += len(query_words & title_words)
        return score

    def _maybe_add_lifespan_extract(
        self,
        summary: dict[str, Any],
        *,
        search_plan: "_SearchPlan",
        language: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if search_plan.intent != "lifespan":
            return summary
        summary_extract = str(summary.get("extract") or "")
        if _has_lifespan_answer(summary_extract):
            return summary
        title = str(summary.get("title") or "").strip()
        if not title:
            return summary
        full_extract = self._fetch_page_extract(title, language=language, timeout_seconds=timeout_seconds)
        lifespan_sentence = _select_lifespan_sentence(full_extract)
        if not lifespan_sentence:
            return summary
        enriched = dict(summary)
        enriched["extract"] = lifespan_sentence
        enriched["_oracle_retrieval_notes"] = ["selected lifespan sentence from wikipedia page extract"]
        return enriched

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


class _SearchPlan:
    def __init__(self, *, search_query: str, subject: str, intent: str, accept_score: int) -> None:
        self.search_query = search_query
        self.subject = subject
        self.intent = intent
        self.accept_score = accept_score


def _normalize_words(value: str) -> str:
    normalized = value.lower().strip()
    normalized = re.sub(r"[\u2018\u2019]", "'", normalized)
    normalized = re.sub(r"[\u201c\u201d]", '"', normalized)
    normalized = re.sub(r"[^a-z0-9' ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _singularize_subject(subject: str) -> str:
    normalized = re.sub(r"\s+", " ", subject).strip()
    if len(normalized) > 3 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if len(normalized) > 3 and normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _strip_leading_article(subject: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", re.sub(r"\s+", " ", subject).strip())


def _has_lifespan_answer(text: str) -> bool:
    normalized = _normalize_words(text)
    if "lifespan" in normalized or "life span" in normalized or "life expectancy" in normalized:
        return True
    return bool(re.search(r"\b\d+\s+(?:to\s+\d+\s+)?years\b", normalized))


def _select_lifespan_sentence(text: str) -> str:
    sentences = _split_sentences(text)
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "years" in normalized and any(term in normalized for term in ("life expectancy", "lifespan", "life span")):
            return sentence
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "years" in normalized and not _is_historical_lifespan_decoy(normalized) and any(
            term in normalized for term in ("live", "lives", "age", "oldest")
        ):
            return sentence
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "lifespan" in normalized or "life span" in normalized or "life expectancy" in normalized:
            return sentence
    return ""


def _is_historical_lifespan_decoy(normalized_sentence: str) -> bool:
    if "years ago" in normalized_sentence:
        return True
    return any(term in normalized_sentence for term in ("extinct", "extinction", "formerly lived"))


def _split_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return []
    protected = re.sub(
        r"\b([A-Z])\.",
        lambda match: f"{match.group(1)}__ORACLE_INITIAL__",
        normalized,
    )
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", protected)
    return [piece.replace("__ORACLE_INITIAL__", ".").strip() for piece in pieces if piece.strip()]
