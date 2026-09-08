from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
import time
from unittest.mock import patch

import pytest

from oracle_app.capabilities.session import PendingInformationalCapability
from oracle_app.informational_evidence import (
    InformationAvailability,
    InformationFailureKind,
    InformationFreshness,
    InformationReadMetadata,
    InformationSource,
)
from oracle_app.read_cache import BoundedReadCache, classify_read_failure
from oracle_app.session_state import (
    clear_all_sessions,
    clear_informational_context_for_topic_change,
    clear_session_state,
    describe_followup_resolution,
    get_informational_context,
    inspect_session,
    set_active_context,
    set_informational_context,
    set_pending_state,
)


def setup_function() -> None:
    clear_all_sessions()


def teardown_function() -> None:
    clear_all_sessions()


def _facts_subject() -> dict[str, object]:
    return {
        "subject_id": "fact:blue-whale",
        "subject_text": "blue whale",
        "query_text": "what is the largest animal",
        "source_ids": ["wikipedia"],
        "evidence_ids": ["wikipedia:blue-whale"],
    }


def test_informational_context_is_typed_copied_and_source_isolated() -> None:
    assert set_informational_context("office", "session-1", domain="facts", subject=_facts_subject())

    stored = get_informational_context("office", "session-1", domain="facts")
    assert stored is not None
    stored["subject"]["source_ids"].append("mutation")

    assert get_informational_context("office", "session-1", domain="weather") is None
    assert get_informational_context("kitchen", "session-1") is None
    assert get_informational_context("office", "session-1")["subject"]["source_ids"] == ["wikipedia"]


@pytest.mark.parametrize(
    "domain,subject",
    [
        ("facts", {"subject_id": "fact:1", "raw_payload": "forbidden"}),
        ("weather", {"subject_id": "weather:1", "source_ids": ["x"] * 17}),
        ("calendar", {"subject_id": "calendar:1", "event_ids": [{"uid": "raw"}]}),
        ("suggestions", {"subject_id": "suggestion:1"}),
    ],
)
def test_informational_context_rejects_unbounded_or_unsupported_shapes(domain: str, subject: dict[str, object]) -> None:
    assert not set_informational_context("office", "session-1", domain=domain, subject=subject)
    assert get_informational_context("office", "session-1") is None


def test_informational_context_has_independent_expiry_and_reset_lifecycle() -> None:
    with patch("oracle_app.session_state.time.monotonic", return_value=100.0):
        assert set_informational_context(
            "office", "session-1", domain="facts", subject=_facts_subject(), timeout_seconds=10,
        )
    with patch("oracle_app.session_state.time.monotonic", return_value=111.0):
        assert get_informational_context("office", "session-1") is None
        inspected = inspect_session("office", "session-1")
    assert inspected is not None
    assert inspected["derived"]["informational_context_active"] is False

    assert set_informational_context("office", "session-2", domain="facts", subject=_facts_subject())
    result = clear_session_state("office", "session-2")
    assert result["informational_context_cleared"] is True
    assert get_informational_context("office", "session-2") is None


def test_topic_change_replaces_only_a_different_informational_owner() -> None:
    assert set_informational_context("office", "session-1", domain="facts", subject=_facts_subject())
    assert not clear_informational_context_for_topic_change("office", "session-1", route_target="facts")
    assert not clear_informational_context_for_topic_change("office", "session-1", route_target="system")
    assert clear_informational_context_for_topic_change("office", "session-1", route_target="weather")


def test_pending_and_strong_context_precede_informational_subject() -> None:
    assert set_informational_context("office", "session-1", domain="facts", subject=_facts_subject())
    assert describe_followup_resolution("office", "session-1")["resolution_order"] == "informational_context"

    assert set_active_context(
        "office", "session-1", route_target="music", dispatch_hook="music.play",
        action="play", anchor_strength="strong",
    )
    assert describe_followup_resolution("office", "session-1")["route_target"] == "music"

    assert set_pending_state(
        "office",
        "session-1",
        pending_type="clarification",
        domain="informational",
        payload={
            "target_domain": "news",
            "clarification_kind": "article_selection",
            "prompt": "Which article?",
            "options": ["first", "second"],
            "subject_id": "news:latest",
        },
    )
    resolution = describe_followup_resolution("office", "session-1")
    assert resolution["resolution_order"] == "pending_state"
    assert resolution["route_target"] == "news"
    decision = PendingInformationalCapability().evaluate("the second one", source="office", session_id="session-1")
    assert decision is not None and decision.target == "news"


def test_informational_evidence_vocabulary_preserves_source_and_time_meaning() -> None:
    source = InformationSource(
        source_id="weather-station",
        source_label="Weather Station",
        source_type="weewx",
        observed_at="2026-09-05T12:00:00-04:00",
        reference="station:outdoor",
    )
    metadata = InformationReadMetadata(
        availability=InformationAvailability.AVAILABLE,
        freshness=InformationFreshness.STALE,
        retrieved_at="2026-09-05T12:05:00-04:00",
        sources=(source,),
        failure_kind=InformationFailureKind.PROVIDER,
        stale_reason="provider_refresh_failed",
    )
    assert metadata.sources[0].observed_at != metadata.retrieved_at
    with pytest.raises(ValueError, match="timezone"):
        InformationSource("bad", "Bad", "test", observed_at="2026-09-05T12:00:00")
    with pytest.raises(ValueError, match="freshness cannot claim"):
        InformationReadMetadata(
            availability=InformationAvailability.UNAVAILABLE,
            freshness=InformationFreshness.FRESH,
            retrieved_at="2026-09-05T12:05:00-04:00",
        )


def test_bounded_read_cache_coalesces_equivalent_concurrent_misses() -> None:
    cache: BoundedReadCache[dict[str, int]] = BoundedReadCache()
    callers = Barrier(8)
    counter_lock = Lock()
    calls = 0

    def request() -> object:
        nonlocal calls
        callers.wait()

        def load() -> dict[str, int]:
            nonlocal calls
            with counter_lock:
                calls += 1
            time.sleep(0.02)
            return {"value": 1}

        return cache.read("weather:current", ttl_seconds=30, stale_max_seconds=300, loader=load)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: request(), range(8)))
    assert calls == 1
    assert all(result.value == {"value": 1} for result in results)
    assert all(result.refresh_status == "succeeded" for result in results)


def test_concurrent_failed_refresh_is_single_flight_and_discloses_stale() -> None:
    now = 100.0
    cache: BoundedReadCache[dict[str, int]] = BoundedReadCache(clock=lambda: now)
    cache.read("news", ttl_seconds=10, stale_max_seconds=100, loader=lambda: {"value": 1})
    now = 111.0
    callers = Barrier(6)
    counter_lock = Lock()
    calls = 0

    def request() -> object:
        nonlocal calls
        callers.wait()

        def fail() -> dict[str, int]:
            nonlocal calls
            with counter_lock:
                calls += 1
            time.sleep(0.02)
            raise RuntimeError("provider offline")

        return cache.read("news", ttl_seconds=10, stale_max_seconds=100, loader=fail)

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: request(), range(6)))
    assert calls == 1
    assert all(result.freshness == "stale" for result in results)
    assert all(result.refresh_status == "failed" for result in results)
    assert all(result.failure_kind == "provider" for result in results)


def test_programming_and_configuration_failures_never_become_stale_success() -> None:
    now = 100.0
    cache: BoundedReadCache[dict[str, int]] = BoundedReadCache(clock=lambda: now)
    cache.read("calendar", ttl_seconds=10, stale_max_seconds=100, loader=lambda: {"value": 1})
    now = 111.0
    with pytest.raises(ValueError, match="broken mapping"):
        cache.read(
            "calendar", ttl_seconds=10, stale_max_seconds=100,
            loader=lambda: (_ for _ in ()).throw(ValueError("broken mapping")),
        )
    assert classify_read_failure(ValueError("bad")) == InformationFailureKind.PROGRAMMING


def test_invalidation_during_inflight_read_prevents_stale_repopulation() -> None:
    cache: BoundedReadCache[dict[str, int]] = BoundedReadCache()
    started = Event()
    release = Event()
    calls = 0

    def load() -> dict[str, int]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=1)
        return {"value": calls}

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: cache.read("calendar:events:a", ttl_seconds=60, stale_max_seconds=300, loader=load)
        )
        assert started.wait(timeout=1)
        cache.invalidate("calendar:events:")
        release.set()
        assert future.result().value == {"value": 1}

    second = cache.read(
        "calendar:events:a", ttl_seconds=60, stale_max_seconds=300,
        loader=lambda: {"value": 2},
    )
    assert second.value == {"value": 2}
