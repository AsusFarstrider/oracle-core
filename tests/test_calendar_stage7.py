from __future__ import annotations

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from oracle_app.calendar import CalendarQuery, parse_calendar_query
from oracle_app.calendar_models import CalendarEvent
from oracle_app.calendar_runtime import CalendarReadUnavailableError, CanonicalCalendarExecution
from oracle_app.capabilities.information import CalendarCapability
from oracle_app.handlers.calendar import CalendarHandler
from oracle_app.provider_bridges.nextcloud_calendar import CalendarBridgeError, NextcloudCalendarBridge
from oracle_app.read_cache import BoundedReadCache
from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_all_sessions
from oracle_app.ui_calendar import build_ui_calendar_page_snapshot


ZONE = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 6, 10, 0, tzinfo=ZONE)


def _event(
    uid: str,
    summary: str,
    start: datetime,
    *,
    end: datetime | None = None,
    location: str = "",
) -> CalendarEvent:
    return CalendarEvent(
        uid=uid,
        summary=summary,
        start=start,
        end=end or start + timedelta(hours=1),
        all_day=False,
        location=location,
    )


def _feed(feed_id: str, *, users: tuple[str, ...] = ()):
    return SimpleNamespace(
        id=feed_id,
        label=feed_id.replace("_", " ").title(),
        user_ids=users,
        resolved_url=f"https://calendar.invalid/{feed_id}.ics",
        read_user=None,
        read_credential=None,
    )


def _execution(*feeds) -> CanonicalCalendarExecution:
    read = SimpleNamespace(
        enabled=True,
        feeds={feed.id: feed for feed in feeds},
        fresh_seconds=300,
        stale_if_error_seconds=600,
        feeds_for_kind=lambda kind: tuple(feeds) if kind == "events" else (),
    )
    settings = SimpleNamespace(
        enabled=True,
        read=read,
        write=SimpleNamespace(enabled=False),
        config_revision="calendar-stage7",
        timezone="America/New_York",
        timeout_seconds=2,
        people={},
    )
    return CanonicalCalendarExecution(settings)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "intent", "start_hour"),
    [
        ("what's going on today", "list_events", 0),
        ("anything after 4", "list_events", 16),
        ("am I free Saturday morning", "availability", 6),
        ("anything after school", "after_event", 0),
    ],
)
def test_natural_calendar_windows_are_deterministic(text: str, intent: str, start_hour: int) -> None:
    query = parse_calendar_query(text, timezone_name="America/New_York", now=NOW, person_id="phil")

    assert query is not None
    assert query.intent == intent
    assert query.person_id == "phil"
    assert query.start is not None and query.start.hour == start_hour


def test_next_week_is_a_calendar_week_not_a_rolling_seven_days() -> None:
    query = parse_calendar_query("what is on my calendar next week", timezone_name="America/New_York", now=NOW)

    assert query is not None
    assert query.start == datetime(2026, 9, 7, 0, 0, tzinfo=ZONE)
    assert query.end == datetime(2026, 9, 14, 0, 0, tzinfo=ZONE)


def test_location_question_preserves_location_evidence() -> None:
    query = parse_calendar_query("when do I need to be in Sayre", timezone_name="America/New_York", now=NOW)

    assert query is not None
    assert query.intent == "find_location"
    assert query.location_text == "sayre"


def test_provider_translation_preserves_all_day_timezone_and_overlap_semantics() -> None:
    events = NextcloudCalendarBridge()._parse_events(
        "\r\n".join([
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "UID:all-day",
            "SUMMARY:School closed",
            "DTSTART;VALUE=DATE:20260907",
            "DTEND;VALUE=DATE:20260908",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:overlap",
            "SUMMARY:Late shift",
            "DTSTART;TZID=America/New_York:20260906T153000",
            "DTEND;TZID=America/New_York:20260906T163000",
            "END:VEVENT",
            "END:VCALENDAR",
        ]),
        "America/New_York",
    )
    query = CalendarQuery(
        "list_events",
        datetime(2026, 9, 6, 16, 0, tzinfo=ZONE),
        datetime(2026, 9, 6, 17, 0, tzinfo=ZONE),
        None,
        "anything after 4",
    )
    from oracle_app import calendar as calendar_module

    result = calendar_module._list_events(query, events, "America/New_York")

    assert events[0].all_day is True
    assert events[0].start.tzinfo == ZONE
    assert result["events"][0]["uid"] == "overlap"


def test_calendar_does_not_claim_unrelated_or_unsupported_mutation_as_a_read() -> None:
    assert parse_calendar_query("paint my calendar", timezone_name="America/New_York", now=NOW) is None
    query = parse_calendar_query("delete my calendar event Friday", timezone_name="America/New_York", now=NOW)
    assert query is not None
    assert query.intent == "unsupported_mutation"


def test_calendar_capability_preserves_cross_domain_and_unknown_person_boundaries() -> None:
    settings = SimpleNamespace(
        enabled=True,
        timezone="America/New_York",
        person_id_for_query=lambda text, source_id=None: (
            "phil" if "phil" in text.casefold() or " my " in f" {text.casefold()} " else None
        ),
        calendar_id_for_query=lambda text: "family" if "family calendar" in text.casefold() else None,
    )
    capability = CalendarCapability(settings)  # type: ignore[arg-type]

    assert capability.evaluate("anything after 4", source="kitchen") is not None
    assert capability.evaluate("what is phil doing tuesday", source="kitchen") is not None
    assert capability.evaluate("turn on the lights after 4", source="kitchen") is None
    assert capability.evaluate("what is bob doing tuesday", source="kitchen") is None
    assert capability.evaluate("what's next", source="kitchen", session_id="none") is None


def test_per_person_read_combines_shared_single_and_multi_person_feeds_and_retains_duplicate_uids() -> None:
    shared = _feed("family")
    phil = _feed("phil", users=("phil",))
    parents = _feed("parents", users=("phil", "molly"))
    molly = _feed("molly", users=("molly",))
    execution = _execution(shared, phil, parents, molly)
    events = {
        shared.resolved_url: [_event("same", "Dinner", NOW + timedelta(hours=8))],
        phil.resolved_url: [_event("same", "Dentist", NOW + timedelta(days=1))],
        parents.resolved_url: [_event("trip", "Parent conference", NOW + timedelta(days=2))],
        molly.resolved_url: [_event("molly", "Practice", NOW + timedelta(days=1))],
    }
    execution.bridge.fetch_typed_events = Mock(side_effect=lambda **kwargs: events[kwargs["feed_url"]])

    snapshot = execution.load_calendar(person_id="phil")

    assert [event.summary for event in snapshot.events] == ["Dinner", "Dentist", "Parent conference"]
    assert [event.source_id for event in snapshot.events] == ["family", "phil", "parents"]
    assert [event.uid for event in snapshot.events] == ["same", "same", "trip"]
    assert execution.bridge.fetch_typed_events.call_count == 3


def test_named_calendar_read_selects_only_that_canonical_source() -> None:
    family, work = _feed("family"), _feed("work")
    execution = _execution(family, work)
    execution.bridge.fetch_typed_events = Mock(return_value=[])
    query = parse_calendar_query(
        "what is on the family calendar tomorrow",
        timezone_name="America/New_York",
        now=NOW,
        calendar_id="family",
    )

    assert query is not None
    result = execution.execute(query)

    assert result["source_ids"] == ["family"]
    execution.bridge.fetch_typed_events.assert_called_once_with(
        feed_url=family.resolved_url,
        timeout_seconds=2,
        timezone_name="America/New_York",
        auth_user=None,
        auth_password=None,
    )


def test_partial_source_failure_preserves_available_events_and_does_not_claim_free() -> None:
    shared, personal = _feed("family"), _feed("phil", users=("phil",))
    execution = _execution(shared, personal)
    execution.bridge.fetch_typed_events = Mock(side_effect=[
        [], CalendarBridgeError("calendar_query_failed", "personal feed down")
    ])
    query = CalendarQuery(
        "availability", NOW, NOW + timedelta(hours=2), None,
        "am i free", person_id="phil",
    )

    result = execution.execute(query)
    dispatch = DispatchPlan(target="calendar", hook="calendar.execute", payload={}, status="executed", result=result)

    assert result["complete"] is False
    assert result["source_availability"][1]["status"] == "unavailable"
    assert "can't confirm that you're free" in build_reply_text(dispatch)


def test_partial_source_health_and_ui_remain_useful_and_explicit() -> None:
    shared, personal = _feed("family"), _feed("phil", users=("phil",))
    execution = _execution(shared, personal)
    dinner = _event("dinner", "Dinner", datetime.now(ZONE) + timedelta(hours=2))

    def fetch(**kwargs):
        if kwargs["feed_url"] == personal.resolved_url:
            raise CalendarBridgeError("calendar_query_failed", "personal feed down")
        return [dinner]

    execution.bridge.fetch_typed_events = Mock(side_effect=fetch)

    health = execution.health()
    page = build_ui_calendar_page_snapshot(canonical_execution=execution)

    assert health["status"] == "degraded"
    assert page["status"] == "partial"
    assert page["complete"] is False
    assert page["upcoming"]["events"][0]["source_id"] == "family"
    assert execution.bridge.fetch_typed_events.call_count == 3


def test_equivalent_reads_cache_each_selected_feed_independently() -> None:
    shared, personal = _feed("family"), _feed("phil", users=("phil",))
    execution = _execution(shared, personal)
    execution.bridge.fetch_typed_events = Mock(return_value=[])

    execution.load_calendar(person_id="phil")
    execution.load_calendar(person_id="phil")

    assert execution.bridge.fetch_typed_events.call_count == 2


def test_explicit_refresh_bypasses_recent_feed_cache() -> None:
    execution = _execution(_feed("family"))
    execution.bridge.fetch_typed_events = Mock(return_value=[])

    execution.load_calendar()
    execution.load_calendar(force_refresh=True)

    assert execution.bridge.fetch_typed_events.call_count == 2


def test_concurrent_equivalent_reads_coalesce_per_feed() -> None:
    execution = _execution(_feed("family"))
    entered = threading.Event()
    release = threading.Event()

    def fetch(**_kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return []

    execution.bridge.fetch_typed_events = Mock(side_effect=fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execution.load_calendar)
        assert entered.wait(timeout=2)
        second = pool.submit(execution.load_calendar)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert execution.bridge.fetch_typed_events.call_count == 1


def test_provider_failure_uses_only_that_feeds_bounded_stale_value() -> None:
    clock = [0.0]
    shared, personal = _feed("family"), _feed("phil", users=("phil",))
    execution = _execution(shared, personal)
    execution._cache = BoundedReadCache(clock=lambda: clock[0])
    execution.bridge.fetch_typed_events = Mock(return_value=[])
    execution.load_calendar(person_id="phil")
    clock[0] = 301.0
    execution.bridge.fetch_typed_events.side_effect = [
        CalendarBridgeError("calendar_query_failed", "family down"), []
    ]

    snapshot = execution.load_calendar(person_id="phil")

    assert snapshot.freshness == "stale"
    assert snapshot.sources[0].freshness == "stale"
    assert snapshot.sources[1].freshness == "fresh"


def test_calendar_context_answers_provenance_without_another_provider_read() -> None:
    clear_all_sessions()
    execution = Mock()
    execution.settings.timezone = "America/New_York"
    execution.settings.person_id_for_query.return_value = "phil"
    execution.settings.calendar_id_for_query.return_value = None
    execution.execute.return_value = {
        "action": "list_events",
        "query": {"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()},
        "events": [{"uid": "dentist", "summary": "Dentist"}],
        "person_id": "phil",
        "source_ids": ["family", "phil"],
        "source_availability": [
            {"source_id": "family", "source_label": "Family"},
            {"source_id": "phil", "source_label": "Phil"},
        ],
    }
    handler = CalendarHandler(execution)
    first = DispatchPlan(
        target="calendar", hook="calendar.execute",
        payload={"text": "what's on my calendar today", "source": "kitchen", "session_id": "calendar-context"},
        status="pending_integration",
    )
    followup = DispatchPlan(
        target="calendar", hook="calendar.execute",
        payload={"text": "which calendar was that from", "source": "kitchen", "session_id": "calendar-context"},
        status="pending_integration",
    )

    handler.handle(first, object())
    assert CalendarCapability(execution.settings).evaluate(
        "what's next", source="kitchen", session_id="calendar-context"
    ) is not None
    result = handler.handle(followup, object())

    assert result.result["action"] == "calendar_provenance"
    assert result.result["source_labels"] == ["Family", "Phil"]
    assert execution.execute.call_count == 1


def test_ambiguous_named_event_opens_bounded_clarification() -> None:
    clear_all_sessions()
    execution = Mock()
    execution.settings.timezone = "America/New_York"
    execution.settings.person_id_for_query.return_value = "phil"
    execution.settings.calendar_id_for_query.return_value = None
    execution.execute.return_value = {
        "action": "find_event",
        "ambiguous": True,
        "events": [
            {"uid": "team", "summary": "Team Meeting"},
            {"uid": "school", "summary": "School Meeting"},
        ],
    }
    handler = CalendarHandler(execution)
    dispatch = DispatchPlan(
        target="calendar", hook="calendar.execute",
        payload={"text": "when is meeting", "source": "kitchen", "session_id": "calendar-choice"},
        status="pending_integration",
    )

    result = handler.handle(dispatch, object())

    assert result.status == "pending_clarification"
    assert result.result["options"] == ["Team Meeting", "School Meeting"]

    execution.execute.side_effect = [
        {
            "action": "find_event",
            "events": [{"uid": "team", "summary": "Team Meeting"}],
            "query": "Team Meeting",
            "source_ids": ["family"],
            "source_availability": [{"source_id": "family", "source_label": "Family"}],
        }
    ]
    selection = DispatchPlan(
        target="calendar", hook="calendar.execute",
        payload={"text": "Team Meeting", "source": "kitchen", "session_id": "calendar-choice"},
        status="pending_integration",
    )

    selected = handler.handle(selection, object())

    assert selected.status == "executed"
    assert selected.result["events"][0]["summary"] == "Team Meeting"


def test_calendar_ui_separates_provider_age_from_snapshot_generation_time(monkeypatch) -> None:
    execution = _execution(_feed("family"))
    execution.bridge.fetch_typed_events = Mock(return_value=[
        _event("dinner", "Dinner", datetime.now(ZONE) + timedelta(hours=2))
    ])

    payload = build_ui_calendar_page_snapshot(canonical_execution=execution)

    assert payload["generated_at"]
    assert payload["provider_age_seconds"] == 0.0
    assert payload["source_availability"][0]["retrieved_at"]
    assert payload["status"] == "available"
