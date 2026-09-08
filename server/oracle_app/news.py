from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .configuration.information_runtime_settings import NewsRuntimeSettings


@dataclass(frozen=True)
class NewsQuery:
    source: str | None
    original_text: str
    action: str = "headlines"
    topic: str | None = None
    article_id: str | None = None
    article_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    force_refresh: bool = False
    after_published_at: str | None = None


def is_news_request(
    text: str,
    *,
    runtime_settings: NewsRuntimeSettings,
) -> bool:
    return parse_news_query(
        text,
        runtime_settings=runtime_settings,
    ) is not None


def parse_news_query(
    text: str,
    *,
    runtime_settings: NewsRuntimeSettings,
) -> NewsQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    source = _detect_requested_source(
        normalized,
        runtime_settings=runtime_settings,
    )
    if not _contains_news_keyword(normalized) and not _looks_like_source_news_request(normalized, source):
        return None

    return NewsQuery(
        source=source,
        original_text=normalized,
        topic=_detect_topic(normalized),
        force_refresh=bool(re.search(r"\b(?:refresh|check again|news again)\b", normalized)),
    )


def check_news_health(*, canonical_execution=None) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.health()
    return {
        "status": "disabled",
        "service": "oracle-brain",
        "configured_sources": [],
        "detail": "No news feeds configured",
    }


def _contains_news_keyword(normalized: str) -> bool:
    return bool(re.search(r"\b(news|headline|headlines)\b", normalized))


def _looks_like_source_news_request(normalized: str, source: str | None) -> bool:
    if source is None:
        return False
    request_patterns = (
        r"^(catch me up on) .+$",
        r"^(fill me in on) .+$",
        r"^(read me something from) .+$",
        r"^(give me something from) .+$",
        r"^(give me the latest from) .+$",
        r"^(what's the latest from) .+$",
        r"^(what is the latest from) .+$",
    )
    return any(re.match(pattern, normalized) is not None for pattern in request_patterns)


def _detect_topic(normalized: str) -> str | None:
    patterns = (
        r"\b(?:news|headlines?)\s+(?:about|on)\s+(.+)$",
        r"\bwhat(?:'s| is)\s+happening\s+(?:with|in)\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match is not None:
            topic = match.group(1).strip(" ?.!\"")
            return topic[:128] or None
    return None


def _detect_requested_source(
    normalized: str,
    *,
    runtime_settings: NewsRuntimeSettings,
) -> str | None:
    for candidate, source_key in _iter_source_candidates(
        runtime_settings=runtime_settings,
    ):
        if _contains_phrase(normalized, candidate):
            return source_key
    return None


def _iter_source_candidates(
    *,
    runtime_settings: NewsRuntimeSettings,
) -> list[tuple[str, str]]:
    return sorted(runtime_settings.resolution_terms.items(), key=lambda item: len(item[0]), reverse=True)


def _normalize_source_phrase(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _contains_phrase(normalized: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalized) is not None


def display_source_label(source_key: str | None, settings: NewsRuntimeSettings) -> str:
    source_settings = settings.sources.get(source_key or "")
    if source_settings is not None:
        return source_settings.source.display_name
    compact = str(source_key or "").strip()
    if not compact:
        return "the news"
    if len(compact) <= 4 and compact.replace("-", "").replace("_", "").isalnum():
        return compact.upper()
    return compact.replace("_", " ").replace("-", " ").title()
