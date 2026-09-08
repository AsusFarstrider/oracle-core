from __future__ import annotations

from datetime import UTC, datetime, timedelta
import socket
from types import MappingProxyType
from unittest.mock import Mock

import pytest

from oracle_app.capabilities.information import NewsCapability
from oracle_app.configuration.domain_models import NewsSource, RssNewsProvider
from oracle_app.configuration.information_runtime_settings import (
    NewsRuntimeSettings,
    NewsSourceRuntimeSettings,
)
from oracle_app.handlers.news import NewsHandler
from oracle_app.information_runtime import CanonicalNewsExecution
from oracle_app.news import NewsQuery, parse_news_query
from oracle_app.news_context import resolve_news_context
from oracle_app.provider_bridges.rss_news import NewsBridgeError, RssNewsBridge
from oracle_app.provider_bridges.rss_news import _ValidatedArticleRedirectHandler
from oracle_app.read_cache import BoundedReadCache
from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_all_sessions


def _source(source_id: str, label: str, *, aliases: tuple[str, ...] = ()) -> NewsSourceRuntimeSettings:
    provider = RssNewsProvider(type="rss", timeout_seconds=8)
    source = NewsSource(
        id=source_id,
        display_name=label,
        aliases=list(aliases),
        provider="rss",
        feed_url=f"https://feeds.{source_id}.example/news.xml",
        article_hosts=[f"www.{source_id}.example"],
    )
    return NewsSourceRuntimeSettings(source=source, provider=provider)


def _settings(*sources: NewsSourceRuntimeSettings, max_headlines: int = 5) -> NewsRuntimeSettings:
    source_map = {item.source.id: item for item in sources}
    terms = {
        term.casefold(): item.source.id
        for item in sources
        for term in (item.source.id, item.source.display_name, *item.source.aliases)
    }
    return NewsRuntimeSettings(
        config_revision="news-stage7",
        enabled=True,
        provider_id="rss",
        provider=RssNewsProvider(type="rss", timeout_seconds=8),
        sources=MappingProxyType(source_map),
        resolution_terms=MappingProxyType(terms),
        max_headlines=max_headlines,
        fresh_seconds=300,
        stale_if_error_seconds=1800,
    )


def _headline(title: str, link: str, published_at: str, *, summary: str = "") -> dict[str, object]:
    return {
        "title": title,
        "link": link,
        "published_at": published_at,
        "published_label": published_at,
        "summary": summary,
    }


def test_source_alias_and_topic_requests_are_bounded_to_configured_vocabulary() -> None:
    settings = _settings(_source("npr", "NPR", aliases=("public radio",)))

    query = parse_news_query("Any NPR headlines about climate policy?", runtime_settings=settings)

    assert query is not None
    assert query.source == "npr"
    assert query.topic == "climate policy"
    assert parse_news_query("catch me up on unknown wire", runtime_settings=settings) is None


def test_article_host_allowlist_requires_unique_exact_hosts() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        NewsSource(
            id="npr",
            display_name="NPR",
            provider="rss",
            feed_url="https://feeds.example/news.xml",
            article_hosts=["NEWS.EXAMPLE", "news.example."],
        )
    with pytest.raises(ValueError, match="exact DNS names"):
        NewsSource(
            id="npr",
            display_name="NPR",
            provider="rss",
            feed_url="https://feeds.example/news.xml",
            article_hosts=["*.example"],
        )


def test_general_request_combines_curated_sources_with_partial_truth_and_cache() -> None:
    npr, local = _source("npr", "NPR"), _source("local", "Local News")
    execution = CanonicalNewsExecution(_settings(npr, local))
    npr_items = [_headline("NPR story", "https://www.npr.example/one", "2026-09-06T12:00:00+00:00")]

    def fetch(*, source, **_kwargs):
        if source.id == "local":
            raise NewsBridgeError("news_fetch_failed", "local feed down")
        return npr_items

    execution.bridge.fetch_typed_headlines = Mock(side_effect=fetch)
    query = parse_news_query("what's in the news", runtime_settings=execution.settings)
    assert query is not None

    first = execution.execute(query)
    second = execution.execute(query)

    assert first["complete"] is False
    assert first["headlines"][0]["source_label"] == "NPR"
    assert first["headlines"][0]["article_id"].startswith("news-")
    assert first["source_availability"][1]["status"] == "unavailable"
    assert second["headlines"][0]["article_id"] == first["headlines"][0]["article_id"]
    assert execution.bridge.fetch_typed_headlines.call_count == 3


def test_topic_filter_uses_only_rss_title_and_summary_evidence() -> None:
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    execution.bridge.fetch_typed_headlines = Mock(return_value=[
        _headline(
            "Budget vote advances",
            "https://www.npr.example/budget",
            "2026-09-06T13:00:00+00:00",
            summary="Lawmakers debate climate policy funding.",
        ),
        _headline("Sports result", "https://www.npr.example/sport", "2026-09-06T14:00:00+00:00"),
    ])
    query = parse_news_query("news about climate policy", runtime_settings=execution.settings)
    assert query is not None

    result = execution.execute(query)

    assert [item["title"] for item in result["headlines"]] == ["Budget vote advances"]

    no_match = execution.execute(NewsQuery("npr", "news about transit", topic="transit"))
    assert no_match["headlines"] == []
    assert "couldn't find" in build_reply_text(DispatchPlan(
        target="news", hook="news.execute", payload={}, status="executed", result=no_match
    )).casefold()


def test_ordinal_followup_fetches_selected_article_and_provenance_uses_context() -> None:
    clear_all_sessions()
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    execution.bridge.fetch_typed_headlines = Mock(return_value=[
        _headline("First story", "https://www.npr.example/first", "2026-09-06T14:00:00+00:00"),
        _headline("Second story", "https://www.npr.example/second", "2026-09-06T13:00:00+00:00"),
    ])
    execution.bridge.fetch_typed_article = Mock(return_value={
        "url": "https://www.npr.example/second",
        "excerpt": "This is article evidence retrieved from the selected second story.",
    })
    handler = NewsHandler(execution)
    initial = DispatchPlan(
        target="news", hook="news.execute",
        payload={"text": "NPR headlines", "source": "kitchen", "session_id": "news-1"},
        status="pending_integration",
    )
    selection = DispatchPlan(
        target="news", hook="news.execute",
        payload={"text": "what's the second story", "source": "kitchen", "session_id": "news-1"},
        status="pending_integration",
    )

    listed = handler.handle(initial, object())
    selected = handler.handle(selection, object())
    provenance = handler.handle(DispatchPlan(
        target="news", hook="news.execute",
        payload={"text": "where did that come from", "source": "kitchen", "session_id": "news-1"},
        status="pending_integration",
    ), object())
    repeated = handler.handle(DispatchPlan(
        target="news", hook="news.execute",
        payload={"text": "read it", "source": "kitchen", "session_id": "news-1"},
        status="pending_integration",
    ), object())

    assert listed.status == "executed"
    assert selected.result["selected_headline"]["title"] == "Second story"
    assert selected.result["article"]["excerpt"].startswith("This is article evidence")
    assert provenance.result == {"action": "news_provenance", "source_labels": ["NPR"]}
    assert repeated.result["article"]["excerpt"].startswith("This is article evidence")
    assert execution.bridge.fetch_typed_headlines.call_count == 1
    assert execution.bridge.fetch_typed_article.call_count == 1


def test_article_failure_keeps_selected_rss_headline_useful() -> None:
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    item = _headline("Selected story", "https://www.npr.example/story", "2026-09-06T13:00:00+00:00")
    execution.bridge.fetch_typed_headlines = Mock(return_value=[item])
    listed = execution.execute(NewsQuery("npr", "npr news"))
    execution.bridge.fetch_typed_article = Mock(side_effect=NewsBridgeError(
        "news_article_unavailable", "article timed out"
    ))

    result = execution.execute(NewsQuery(
        None,
        "read that one",
        action="article",
        article_id=listed["article_ids"][0],
        article_ids=tuple(listed["article_ids"]),
        source_ids=("npr",),
    ))
    dispatch = DispatchPlan(
        target="news", hook="news.execute", payload={}, status="executed", result=result
    )

    assert result["selected_headline"]["title"] == "Selected story"
    assert result["error"] == "news_article_unavailable"
    assert "Selected story" in build_reply_text(dispatch)


def test_publication_followup_does_not_claim_event_time() -> None:
    clear_all_sessions()
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    execution.bridge.fetch_typed_headlines = Mock(return_value=[
        _headline("Bridge report", "https://www.npr.example/bridge", "2026-09-06T13:00:00+00:00")
    ])
    execution.bridge.fetch_typed_article = Mock(return_value={
        "url": "https://www.npr.example/bridge",
        "excerpt": "The report describes the bridge inspection and its evidence.",
    })
    handler = NewsHandler(execution)
    base = {"source": "office", "session_id": "news-time"}
    handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "NPR news"},
        status="pending_integration",
    ), object())
    handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "what's the first story"},
        status="pending_integration",
    ), object())

    result = handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "when did that happen"},
        status="pending_integration",
    ), object())
    reply = build_reply_text(result)

    assert result.result["action"] == "news_publication_time"
    assert "article was published" in reply
    assert "does not establish when the event happened" in reply


def test_explicit_update_refreshes_feed_and_reports_only_newer_related_evidence() -> None:
    clear_all_sessions()
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    old = _headline(
        "Bridge safety report released",
        "https://www.npr.example/old",
        "2026-09-06T12:00:00+00:00",
    )
    new = _headline(
        "New bridge safety findings",
        "https://www.npr.example/new",
        "2026-09-06T15:00:00+00:00",
    )
    execution.bridge.fetch_typed_headlines = Mock(side_effect=[[old], [new, old]])
    execution.bridge.fetch_typed_article = Mock(return_value={
        "url": "https://www.npr.example/old",
        "excerpt": "The bridge safety report contains enough article evidence for selection.",
    })
    handler = NewsHandler(execution)
    base = {"source": "office", "session_id": "news-update"}
    handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "NPR news"},
        status="pending_integration",
    ), object())
    handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "what's the first story"},
        status="pending_integration",
    ), object())

    updated = handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "any updates on that"},
        status="pending_integration",
    ), object())

    assert [item["title"] for item in updated.result["headlines"]] == ["New bridge safety findings"]
    assert execution.bridge.fetch_typed_headlines.call_count == 2


def test_explicit_lexical_negation_can_surface_cross_source_disagreement() -> None:
    one, two = _source("one", "Source One"), _source("two", "Source Two")
    execution = CanonicalNewsExecution(_settings(one, two))
    stories = {
        "one": [_headline(
            "Agency says river bridge remains safe",
            "https://www.one.example/story",
            "2026-09-06T13:00:00+00:00",
        )],
        "two": [_headline(
            "Agency says river bridge is not safe",
            "https://www.two.example/story",
            "2026-09-06T13:05:00+00:00",
        )],
    }
    execution.bridge.fetch_typed_headlines = Mock(
        side_effect=lambda *, source, **_kwargs: stories[source.id]
    )

    result = execution.execute(NewsQuery(None, "what's in the news"))

    assert len(result["disagreements"]) == 1
    assert {result["disagreements"][0]["left_source"], result["disagreements"][0]["right_source"]} == {
        "Source One", "Source Two"
    }


def test_article_url_policy_blocks_unconfigured_and_non_public_destinations(monkeypatch) -> None:
    bridge = RssNewsBridge()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    ])

    with pytest.raises(NewsBridgeError, match="outside its configured source"):
        bridge._validate_article_url(
            "https://unapproved.example/story", allowed_hosts={"www.npr.example"}
        )
    with pytest.raises(NewsBridgeError, match="non-public address"):
        bridge._validate_article_url(
            "https://www.npr.example/story", allowed_hosts={"www.npr.example"}
        )


def test_article_bridge_extracts_bounded_article_html(monkeypatch) -> None:
    bridge = RssNewsBridge()
    source = _source("npr", "NPR").source

    class Response:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return "https://www.npr.example/story"

        def read(self, _limit):
            return (
                b"<html><p>Navigation text that is long enough to be tempting.</p>"
                b"<article><p>The selected article contains verified source text for Oracle.</p>"
                b"<p>A second article paragraph adds enough evidence for a useful excerpt.</p>"
                b"</article></html>"
            )

    opener = Mock()
    opener.open.return_value = Response()
    monkeypatch.setattr("oracle_app.provider_bridges.rss_news.request.build_opener", lambda *_: opener)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ])

    article = bridge.fetch_typed_article(
        article_url="https://www.npr.example/story",
        source=source,
        provider=RssNewsProvider(type="rss", timeout_seconds=8),
    )

    assert article["url"] == "https://www.npr.example/story"
    assert article["excerpt"].startswith("The selected article")
    assert "Navigation text" not in article["excerpt"]
    opener.open.assert_called_once()


def test_article_redirect_handler_revalidates_each_destination() -> None:
    validator = Mock(side_effect=NewsBridgeError("news_article_blocked", "blocked redirect"))
    handler = _ValidatedArticleRedirectHandler(validator)

    with pytest.raises(NewsBridgeError, match="blocked redirect"):
        handler.redirect_request(
            Mock(), Mock(), 302, "Found", {}, "https://unapproved.example/story"
        )

    validator.assert_called_once_with("https://unapproved.example/story")


def test_changed_or_missing_selected_article_evidence_degrades_explicitly() -> None:
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    execution.bridge.fetch_typed_headlines = Mock(return_value=[
        _headline("Current story", "", "2026-09-06T13:00:00+00:00")
    ])
    listed = execution.execute(NewsQuery("npr", "npr news"))

    missing_link = execution.execute(NewsQuery(
        None,
        "read that one",
        action="article",
        article_id=listed["article_ids"][0],
        article_ids=tuple(listed["article_ids"]),
        source_ids=("npr",),
    ))
    expired = execution.execute(NewsQuery(
        None,
        "read that one",
        action="article",
        article_id="news-no-longer-present",
        article_ids=("news-no-longer-present",),
        source_ids=("npr",),
    ))

    assert missing_link["error"] == "news_article_unavailable"
    assert expired["error"] == "news_article_expired"


def test_news_followups_require_unexpired_same_source_context() -> None:
    clear_all_sessions()
    settings = _settings(_source("npr", "NPR"))
    capability = NewsCapability(settings)

    assert capability.evaluate("what's the second story", source="office", session_id="missing") is None
    assert resolve_news_context(
        "read that one", source="office", session_id="missing"
    ).query is None


def test_unselected_multi_story_followup_requires_a_selection() -> None:
    clear_all_sessions()
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    execution.bridge.fetch_typed_headlines = Mock(return_value=[
        _headline("First", "https://www.npr.example/first", "2026-09-06T14:00:00+00:00"),
        _headline("Second", "https://www.npr.example/second", "2026-09-06T13:00:00+00:00"),
    ])
    handler = NewsHandler(execution)
    base = {"source": "office", "session_id": "news-ambiguous"}
    handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "NPR news"},
        status="pending_integration",
    ), object())

    result = handler.handle(DispatchPlan(
        target="news", hook="news.execute", payload={**base, "text": "read that one"},
        status="pending_integration",
    ), object())

    assert result.result["action"] == "news_selection_required"
    assert "which story" in build_reply_text(result).casefold()


def test_feed_stale_recovery_and_force_refresh_have_bounded_call_counts() -> None:
    source = _source("npr", "NPR")
    execution = CanonicalNewsExecution(_settings(source))
    clock = [0.0]
    execution._cache = BoundedReadCache(clock=lambda: clock[0])
    item = _headline("Story", "https://www.npr.example/story", datetime.now(UTC).isoformat())
    execution.bridge.fetch_typed_headlines = Mock(return_value=[item])
    query = NewsQuery("npr", "npr news")
    execution.execute(query)
    execution.execute(query)
    clock[0] = 301.0
    execution.bridge.fetch_typed_headlines.side_effect = NewsBridgeError("news_fetch_failed", "down")

    stale = execution.execute(query)

    assert stale["freshness"] == "stale"
    assert execution.bridge.fetch_typed_headlines.call_count == 2
    execution.bridge.fetch_typed_headlines.side_effect = None
    execution.bridge.fetch_typed_headlines.return_value = [item]
    execution.execute(NewsQuery("npr", "refresh news", force_refresh=True))
    assert execution.bridge.fetch_typed_headlines.call_count == 3
