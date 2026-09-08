from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import re
from typing import Any

from oracle_app.configuration.information_runtime_settings import (
    NewsRuntimeSettings,
    NewsSourceRuntimeSettings,
)
from oracle_app.news import NewsQuery, display_source_label
from oracle_app.provider_bridges.rss_news import NewsBridgeError, RssNewsBridge
from oracle_app.read_cache import BoundedReadCache


class CanonicalNewsExecution:
    """Curated News selection, RSS evidence, and selected-article execution."""

    def __init__(self, settings: NewsRuntimeSettings) -> None:
        self.settings = settings
        if settings.enabled and (settings.provider_id is None or settings.provider is None):
            raise ValueError("Enabled canonical news requires one selected provider.")
        self.bridge = RssNewsBridge()
        self._cache: BoundedReadCache[Any] = BoundedReadCache()

    def execute(
        self,
        query: NewsQuery,
        *,
        force_refresh: bool | None = None,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        if not self.settings.enabled:
            return self._unavailable_result(query)
        selected_sources = self._selected_sources(query)
        if not selected_sources:
            return self._unavailable_result(query)
        refresh = query.force_refresh if force_refresh is None else force_refresh
        headlines, statuses = self._load_headlines(
            selected_sources,
            force_refresh=refresh,
            allow_stale=allow_stale,
        )
        complete = all(item["status"] == "available" for item in statuses)
        freshness = "stale" if any(item["freshness"] == "stale" for item in statuses) else "fresh"
        retrieved_at = min(
            (str(item["retrieved_at"]) for item in statuses if item.get("retrieved_at")),
            default=datetime.now(UTC).isoformat(),
        )

        if query.action == "article":
            return self._article_result(
                query,
                headlines=headlines,
                statuses=statuses,
                complete=complete,
                freshness=freshness,
                retrieved_at=retrieved_at,
                force_refresh=refresh,
                allow_stale=allow_stale,
            )

        filtered = (
            self._filter_related(headlines, query.topic)
            if query.action == "updates"
            else self._filter_topic(headlines, query.topic)
        )
        if query.action == "updates":
            filtered = self._newer_updates(filtered, query.after_published_at, query.article_id)
        limited = filtered[: self.settings.max_headlines]
        source_ids = [source.source.id for source in selected_sources]
        source_labels = [source.source.display_name for source in selected_sources]
        return {
            "action": query.action,
            "source": query.source,
            "source_label": (
                display_source_label(query.source, self.settings)
                if query.source
                else (
                    selected_sources[0].source.display_name
                    if len(selected_sources) == 1
                    else "configured news sources"
                )
            ),
            "source_ids": source_ids,
            "source_labels": source_labels,
            "topic": query.topic,
            "headlines": limited,
            "article_ids": [item["article_id"] for item in limited],
            "source_availability": statuses,
            "complete": complete,
            "freshness": freshness,
            "retrieved_at": retrieved_at,
            "disagreements": self._find_explicit_disagreements(filtered),
            "stale_notice": (
                "I couldn't refresh every news source, so some items are the latest saved headlines."
                if freshness == "stale"
                else None
            ),
            "partial_notice": (
                "I couldn't reach every configured news source, so this may be incomplete."
                if not complete
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

    def _selected_sources(self, query: NewsQuery) -> list[NewsSourceRuntimeSettings]:
        if query.source:
            selected = self.settings.sources.get(query.source)
            return [selected] if selected is not None else []
        if query.source_ids:
            return [
                self.settings.sources[source_id]
                for source_id in query.source_ids
                if source_id in self.settings.sources
            ]
        return list(self.settings.sources.values())

    def _load_headlines(
        self,
        sources: list[NewsSourceRuntimeSettings],
        *,
        force_refresh: bool,
        allow_stale: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        headlines: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        first_error: NewsBridgeError | None = None
        for settings in sources:
            source = settings.source
            try:
                cached = self._cache.read(
                    f"news:feed:{self.settings.config_revision}:{source.id}",
                    ttl_seconds=self.settings.fresh_seconds,
                    stale_max_seconds=self.settings.stale_if_error_seconds,
                    loader=lambda settings=settings: self.bridge.fetch_typed_headlines(
                        source=settings.source,
                        provider=settings.provider,
                        limit=self.settings.max_headlines,
                    ),
                    force_refresh=force_refresh,
                    allow_stale=allow_stale,
                )
            except NewsBridgeError as exc:
                first_error = first_error or exc
                statuses.append({
                    "source_id": source.id,
                    "source_label": source.display_name,
                    "status": "unavailable",
                    "freshness": "unknown",
                    "retrieved_at": None,
                    "age_seconds": None,
                    "error_code": exc.error_code,
                })
                continue
            source_retrieved_at = (
                datetime.now(UTC) - timedelta(seconds=cached.age_seconds)
            ).isoformat()
            statuses.append({
                "source_id": source.id,
                "source_label": source.display_name,
                "status": "available",
                "freshness": cached.freshness,
                "retrieved_at": source_retrieved_at,
                "age_seconds": round(cached.age_seconds, 3),
                "stale_reason": cached.stale_reason,
            })
            for item in cached.value:
                headline = dict(item)
                headline.update({
                    "source_id": source.id,
                    "source_label": source.display_name,
                    "article_id": self._article_id(source.id, item),
                    "retrieved_at": source_retrieved_at,
                })
                headlines.append(headline)
        if not headlines and first_error is not None and not any(
            status["status"] == "available" for status in statuses
        ):
            raise first_error
        headlines.sort(key=self._headline_sort_key, reverse=True)
        return headlines, statuses

    def _article_result(
        self,
        query: NewsQuery,
        *,
        headlines: list[dict[str, Any]],
        statuses: list[dict[str, Any]],
        complete: bool,
        freshness: str,
        retrieved_at: str,
        force_refresh: bool,
        allow_stale: bool,
    ) -> dict[str, Any]:
        selected = next(
            (item for item in headlines if item.get("article_id") == query.article_id),
            None,
        )
        base = {
            "action": "article",
            "source_ids": list(query.source_ids),
            "source_labels": [
                self.settings.sources[source_id].source.display_name
                for source_id in query.source_ids
                if source_id in self.settings.sources
            ],
            "article_ids": list(query.article_ids),
            "selected_article_id": query.article_id,
            "selected_headline": selected,
            "headlines": [],
            "source_availability": statuses,
            "complete": complete,
            "freshness": freshness,
            "retrieved_at": retrieved_at,
        }
        if selected is None:
            return {**base, "error": "news_article_expired"}
        link = str(selected.get("link") or "").strip()
        source_settings = self.settings.sources.get(str(selected.get("source_id") or ""))
        if not link or source_settings is None:
            return {**base, "error": "news_article_unavailable"}
        try:
            cached = self._cache.read(
                f"news:article:{self.settings.config_revision}:{selected['article_id']}",
                ttl_seconds=self.settings.fresh_seconds,
                stale_max_seconds=self.settings.stale_if_error_seconds,
                loader=lambda: self.bridge.fetch_typed_article(
                    article_url=link,
                    source=source_settings.source,
                    provider=source_settings.provider,
                ),
                force_refresh=force_refresh,
                allow_stale=allow_stale,
            )
        except NewsBridgeError as exc:
            return {**base, "error": exc.error_code, "article_error_detail": exc.detail}
        article_retrieved_at = (
            datetime.now(UTC) - timedelta(seconds=cached.age_seconds)
        ).isoformat()
        return {
            **base,
            "source_ids": [str(selected.get("source_id") or "")],
            "source_labels": [str(selected.get("source_label") or "")],
            "article": {
                **cached.value,
                "article_id": selected["article_id"],
                "title": selected.get("title"),
                "published_at": selected.get("published_at"),
                "source_id": selected.get("source_id"),
                "source_label": selected.get("source_label"),
                "retrieved_at": article_retrieved_at,
                "freshness": cached.freshness,
            },
            "stale_notice": (
                "I couldn't refresh the article, so this is the latest saved copy."
                if cached.freshness == "stale"
                else None
            ),
        }

    def _filter_topic(
        self, headlines: list[dict[str, Any]], topic: str | None
    ) -> list[dict[str, Any]]:
        tokens = self._topic_tokens(topic or "")
        if not tokens:
            return headlines
        return [
            item
            for item in headlines
            if tokens.issubset(
                self._topic_tokens(f"{item.get('title') or ''} {item.get('summary') or ''}")
            )
        ]

    def _newer_updates(
        self,
        headlines: list[dict[str, Any]],
        after_published_at: str | None,
        selected_article_id: str | None,
    ) -> list[dict[str, Any]]:
        threshold = self._parse_timestamp(after_published_at)
        updates = []
        for item in headlines:
            if item.get("article_id") == selected_article_id:
                continue
            published = self._parse_timestamp(item.get("published_at"))
            if threshold is None or (published is not None and published > threshold):
                updates.append(item)
        return updates

    def _filter_related(
        self, headlines: list[dict[str, Any]], topic: str | None
    ) -> list[dict[str, Any]]:
        tokens = self._topic_tokens(topic or "")
        if not tokens:
            return headlines
        minimum = min(2, len(tokens))
        return [
            item
            for item in headlines
            if len(
                tokens
                & self._topic_tokens(f"{item.get('title') or ''} {item.get('summary') or ''}")
            )
            >= minimum
        ]

    def _find_explicit_disagreements(
        self, headlines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        disagreements: list[dict[str, Any]] = []
        for index, left in enumerate(headlines):
            for right in headlines[index + 1 :]:
                if left.get("source_id") == right.get("source_id"):
                    continue
                left_tokens = self._topic_tokens(str(left.get("title") or ""))
                right_tokens = self._topic_tokens(str(right.get("title") or ""))
                if (
                    len(left_tokens & right_tokens) < 3
                    or self._is_negated(left) == self._is_negated(right)
                ):
                    continue
                disagreements.append({
                    "left_article_id": left.get("article_id"),
                    "right_article_id": right.get("article_id"),
                    "left_source": left.get("source_label"),
                    "right_source": right.get("source_label"),
                    "left_title": left.get("title"),
                    "right_title": right.get("title"),
                })
                if len(disagreements) == 2:
                    return disagreements
        return disagreements

    def _unavailable_result(self, query: NewsQuery) -> dict[str, Any]:
        return {
            "action": query.action,
            "source": query.source,
            "source_label": display_source_label(query.source, self.settings),
            "headlines": [],
            "error": "news_source_unavailable",
        }

    @staticmethod
    def _article_id(source_id: str, item: dict[str, Any]) -> str:
        identity = "\0".join((
            source_id,
            str(item.get("link") or ""),
            str(item.get("title") or ""),
            str(item.get("published_at") or ""),
        ))
        return f"news-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _headline_sort_key(item: dict[str, Any]) -> float:
        parsed = CanonicalNewsExecution._parse_timestamp(item.get("published_at"))
        return parsed.timestamp() if parsed is not None else float("-inf")

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _topic_tokens(value: str) -> set[str]:
        stop = {
            "a", "an", "and", "are", "as", "at", "be", "for", "from", "in", "is",
            "it", "of", "on", "says", "the", "to", "was", "with",
        }
        return {
            token for token in re.findall(r"[a-z0-9]+", value.casefold())
            if len(token) > 1 and token not in stop and token not in {"not", "no"}
        }

    @staticmethod
    def _is_negated(item: dict[str, Any]) -> bool:
        text = f" {str(item.get('title') or '').casefold()} "
        return any(marker in text for marker in (" not ", " no ", " denies ", " rejects ", " false "))
