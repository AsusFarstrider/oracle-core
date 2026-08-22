from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import get_news_settings
from .configuration.information_runtime_settings import NewsRuntimeSettings
from .provider_bridges import get_news_bridge
from .read_cache import BoundedReadCache


_NEWS_CACHE: BoundedReadCache[list[dict[str, Any]]] = BoundedReadCache()
NEWS_TTL_SECONDS = 5 * 60
NEWS_STALE_MAX_SECONDS = 30 * 60


@dataclass(frozen=True)
class NewsQuery:
    source: str | None
    original_text: str


def is_news_request(
    text: str,
    *,
    runtime_settings: NewsRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> bool:
    return parse_news_query(
        text,
        runtime_settings=runtime_settings,
        canonical_authority=canonical_authority,
    ) is not None


def parse_news_query(
    text: str,
    *,
    runtime_settings: NewsRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> NewsQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    source = _detect_requested_source(
        normalized,
        runtime_settings=runtime_settings,
        canonical_authority=canonical_authority,
    )
    if not _contains_news_keyword(normalized) and not _looks_like_source_news_request(normalized, source):
        return None

    return NewsQuery(source=source, original_text=normalized)


def check_news_health(*, canonical_execution=None) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.health()
    return {
        "status": "disabled",
        "service": "oracle-brain",
        "configured_sources": [],
        "detail": "No news feeds configured",
    }


def execute_news_query(query: NewsQuery) -> dict[str, Any]:
    settings = get_news_settings()
    sources = settings["sources"]
    source_key = query.source or _default_source_key(sources)
    source = sources.get(source_key)
    if source is None:
        return {
            "action": "headlines",
            "source": source_key,
            "source_label": _display_source_label(source_key, sources),
            "headlines": [],
            "error": "news_source_unavailable",
        }

    bridge = get_news_bridge(source, settings)
    cached = _NEWS_CACHE.read(
        f"news:{source_key}:{source.get('url')}",
        ttl_seconds=NEWS_TTL_SECONDS,
        stale_max_seconds=NEWS_STALE_MAX_SECONDS,
        loader=lambda: bridge.fetch_headlines(
            source_definition=source,
            timeout_seconds=int(settings["timeout_seconds"]),
            limit=int(settings["max_headlines"]),
        ),
    )
    return {
        "action": "headlines",
        "source": source_key,
        "source_label": source["label"],
        "headlines": cached.value,
        "freshness": cached.freshness,
        "age_seconds": round(cached.age_seconds, 3),
        "stale_reason": cached.stale_reason,
        "stale_notice": (
            "I couldn't refresh the news, so these are the latest saved headlines."
            if cached.freshness == "stale"
            else None
        ),
    }


def _default_source_key(sources: dict[str, dict[str, str]]) -> str:
    if "npr" in sources:
        return "npr"
    return next(iter(sources.keys()))


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


def _detect_requested_source(
    normalized: str,
    *,
    runtime_settings: NewsRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> str | None:
    for candidate, source_key in _iter_source_candidates(
        runtime_settings=runtime_settings,
        canonical_authority=canonical_authority,
    ):
        if _contains_phrase(normalized, candidate):
            return source_key
    return None


def _iter_source_candidates(
    *,
    runtime_settings: NewsRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> list[tuple[str, str]]:
    if runtime_settings is not None:
        return sorted(runtime_settings.resolution_terms.items(), key=lambda item: len(item[0]), reverse=True)
    if canonical_authority:
        return []
    settings = get_news_settings()
    sources = settings["sources"]
    candidates: dict[str, str] = {
        "npr": "npr",
        "ap": "ap",
        "associated press": "ap",
        "reuters": "reuters",
        "reuters news": "reuters",
    }
    for source_key, source in sources.items():
        normalized_key = _normalize_source_phrase(source_key)
        if normalized_key:
            candidates[normalized_key] = source_key
        normalized_label = _normalize_source_phrase(source.get("label"))
        if normalized_label:
            candidates[normalized_label] = source_key
    return sorted(candidates.items(), key=lambda item: len(item[0]), reverse=True)


def _normalize_source_phrase(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _contains_phrase(normalized: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalized) is not None


def _display_source_label(source_key: str, sources: dict[str, dict[str, str]]) -> str:
    source = sources.get(source_key)
    if source is not None:
        label = str(source.get("label") or "").strip()
        if label:
            return label
    compact = str(source_key or "").strip()
    if not compact:
        return "the news"
    if len(compact) <= 4 and compact.replace("-", "").replace("_", "").isalnum():
        return compact.upper()
    return compact.replace("_", " ").replace("-", " ").title()


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
