from __future__ import annotations

from typing import Any

from oracle_app.configuration.information_runtime_settings import NewsRuntimeSettings
from oracle_app.news import NewsQuery, display_source_label
from oracle_app.provider_bridges.rss_news import RssNewsBridge
from oracle_app.read_cache import BoundedReadCache


class CanonicalNewsExecution:
    """News source selection and RSS execution bound to one applied snapshot."""

    def __init__(self, settings: NewsRuntimeSettings) -> None:
        self.settings = settings
        if settings.enabled and (settings.provider_id is None or settings.provider is None):
            raise ValueError("Enabled canonical news requires one selected provider.")
        self._cache: BoundedReadCache[list[dict[str, Any]]] = BoundedReadCache()

    def execute(self, query: NewsQuery) -> dict[str, Any]:
        source_key = query.source or self._default_source_id()
        source_settings = self.settings.sources.get(source_key or "")
        if not self.settings.enabled or source_settings is None:
            return {
                "action": "headlines",
                "source": source_key,
                "source_label": display_source_label(source_key, self.settings),
                "headlines": [],
                "error": "news_source_unavailable",
            }

        source = source_settings.source
        provider = source_settings.provider
        cached = self._cache.read(
            f"news:{source.id}:{source.feed_url}",
            ttl_seconds=self.settings.fresh_seconds,
            stale_max_seconds=self.settings.stale_if_error_seconds,
            loader=lambda: RssNewsBridge().fetch_typed_headlines(
                source=source,
                provider=provider,
                limit=self.settings.max_headlines,
            ),
        )
        return {
            "action": "headlines",
            "source": source.id,
            "source_label": source.display_name,
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

    def health(self) -> dict[str, Any]:
        configured_sources = sorted(self.settings.sources) if self.settings.enabled else []
        return {
            "status": "ok" if configured_sources else "disabled",
            "service": "oracle-brain",
            "configured_sources": configured_sources,
            "detail": "News feeds configured" if configured_sources else "No news feeds configured",
        }

    def _default_source_id(self) -> str | None:
        if not self.settings.sources:
            return None
        return next(iter(self.settings.sources))
