from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from oracle_app.capabilities.session import (
    PendingAudiobookCapability,
    PendingCalendarCapability,
    PendingConfirmationCapability,
    PendingHomeCapability,
    PendingMusicCapability,
)
from oracle_app.capabilities.media import (
    _extract_probable_audiobook_title_from_play_request,
    _looks_like_probable_audiobook_title,
)
from oracle_app.route_refinement import (
    _refine_audiobook_sleep_timer,
    _resolve_bare_transport_action,
)
from oracle_app.schemas import RouteResponse


def test_pending_confirmation_covers_absent_rejected_and_accepted_context() -> None:
    capability = PendingConfirmationCapability()
    with patch("oracle_app.capabilities.session.state.load_pending_confirmation") as load:
        load.return_value = None
        assert capability.evaluate("yes", source="source", session_id="session") is None
        load.return_value = {"action": "confirm"}
        assert capability.evaluate("later", source="source", session_id="session") is None
        assert capability.evaluate("yes please", source="source", session_id="session").normalized_text == "confirm"


def test_pending_home_covers_absent_unknown_and_known_room_reply() -> None:
    capability = PendingHomeCapability(Mock())
    with (
        patch("oracle_app.capabilities.session.state.load_pending_home_request") as load,
        patch("oracle_app.capabilities.session.canonical_pending_room_reply_name") as room,
    ):
        load.return_value = None
        assert capability.evaluate("office", source="source", session_id="session") is None
        load.return_value = {"action": "home"}
        room.return_value = None
        assert capability.evaluate("unknown", source="source", session_id="session") is None
        room.return_value = "office"
        assert capability.evaluate("office", source="source", session_id="session").target == "home_assistant"


def test_pending_calendar_rejects_each_competing_owner_before_accepting_detail() -> None:
    capability = PendingCalendarCapability(
        SimpleNamespace(),  # type: ignore[arg-type]
        SimpleNamespace(timezone="Etc/UTC"),  # type: ignore[arg-type]
    )
    with (
        patch("oracle_app.capabilities.session.state.load_pending_calendar_write_request") as pending,
        patch("oracle_app.capabilities.session.classify_system_intent") as system,
        patch("oracle_app.capabilities.session.classify_weather_intent") as weather,
        patch("oracle_app.capabilities.session.is_news_request") as news,
        patch("oracle_app.capabilities.session.parse_music_intent") as music,
        patch("oracle_app.capabilities.session.parse_audiobook_intent") as audiobook,
        patch("oracle_app.capabilities.session.has_home_keyword") as home,
        patch("oracle_app.capabilities.session.is_calendar_request") as calendar,
        patch("oracle_app.capabilities.session.parse_calendar_write_request") as calendar_write,
    ):
        pending.return_value = None
        assert capability.evaluate("detail") is None
        pending.return_value = {"draft": {}}
        assert capability.evaluate("") is None

        defaults = (system, weather, news, music, audiobook, calendar, calendar_write)
        for mocked in defaults:
            mocked.return_value = None if mocked not in {news, calendar} else False
        home.return_value = (False, None)

        for owner in (system, weather, news, music, audiobook):
            owner.return_value = True if owner in {news} else object()
            assert capability.evaluate("detail") is None
            owner.return_value = False if owner in {news} else None

        home.return_value = (True, "lights")
        assert capability.evaluate("detail") is None
        home.return_value = (False, None)
        calendar.return_value = True
        assert capability.evaluate("detail") is None
        calendar.return_value = False
        calendar_write.return_value = object()
        assert capability.evaluate("detail") is None
        calendar_write.return_value = None
        assert capability.evaluate("detail").target == "calendar"


def test_pending_audiobook_rejects_competing_intents_and_accepts_clarification() -> None:
    capability = PendingAudiobookCapability()
    with (
        patch("oracle_app.capabilities.session.state.load_pending_audiobook_request") as pending,
        patch("oracle_app.capabilities.session.parse_audiobook_intent") as audiobook,
        patch("oracle_app.capabilities.session.parse_music_intent") as music,
        patch("oracle_app.capabilities.session.classify_system_intent") as system,
        patch("oracle_app.capabilities.session.has_home_keyword") as home,
        patch("oracle_app.capabilities.session.looks_like_pending_audiobook_clarification") as clarification,
    ):
        pending.return_value = None
        assert capability.evaluate("the first one") is None
        pending.return_value = {"candidates": []}
        assert capability.evaluate("") is None
        audiobook.return_value = object()
        assert capability.evaluate("detail") is None
        audiobook.return_value = None
        music.return_value = object()
        assert capability.evaluate("detail") is None
        music.return_value = None
        system.return_value = object()
        assert capability.evaluate("detail") is None
        system.return_value = None
        home.return_value = (True, "lights")
        assert capability.evaluate("detail") is None
        home.return_value = (False, None)
        clarification.return_value = False
        assert capability.evaluate("detail") is None
        clarification.return_value = True
        assert capability.evaluate("detail").target == "audiobook"


def test_pending_music_rejects_competing_intents_and_accepts_clarification() -> None:
    capability = PendingMusicCapability()
    with (
        patch("oracle_app.capabilities.session.state.load_pending_music_request") as pending,
        patch("oracle_app.capabilities.session.parse_music_intent") as music,
        patch("oracle_app.capabilities.session.classify_system_intent") as system,
        patch("oracle_app.capabilities.session.has_home_keyword") as home,
        patch("oracle_app.capabilities.session.looks_like_pending_music_clarification") as clarification,
    ):
        pending.return_value = None
        assert capability.evaluate("the first one") is None
        pending.return_value = {"candidates": []}
        assert capability.evaluate("") is None
        music.return_value = object()
        assert capability.evaluate("detail") is None
        music.return_value = None
        system.return_value = object()
        assert capability.evaluate("detail") is None
        system.return_value = None
        home.return_value = (True, "lights")
        assert capability.evaluate("detail") is None
        home.return_value = (False, None)
        clarification.return_value = False
        assert capability.evaluate("detail") is None
        clarification.return_value = True
        assert capability.evaluate("detail").target == "music"


def test_transport_refinement_covers_all_bare_action_families() -> None:
    assert _resolve_bare_transport_action("next") == "next"
    assert _resolve_bare_transport_action("previous") == "previous"
    assert _resolve_bare_transport_action("restart") == "restart"
    assert _resolve_bare_transport_action("turn it up") == "volume_up"
    assert _resolve_bare_transport_action("turn it down") == "volume_down"


def test_audiobook_sleep_timer_refinement_covers_safe_fallbacks() -> None:
    route = RouteResponse(
        target="fallback_router",
        confidence=0.64,
        reason="baseline",
        normalized_text="baseline",
    )
    assert _refine_audiobook_sleep_timer(
        route,
        normalized_text="sleep timer sometime",
        source="source",
        playback_state=None,
    ) is None
    assert _refine_audiobook_sleep_timer(
        route,
        normalized_text="sleep timer for five minutes",
        source="source",
        playback_state=None,
    ) is route
    playback_state = Mock()
    playback_state.fetch_satellite_audiobook_context_session.side_effect = RuntimeError("offline")
    assert _refine_audiobook_sleep_timer(
        route,
        normalized_text="sleep timer for five minutes",
        source="source",
        playback_state=playback_state,
    ) is route


def test_probable_audiobook_helpers_reject_empty_and_explicit_music_requests() -> None:
    assert not _looks_like_probable_audiobook_title("")
    assert _extract_probable_audiobook_title_from_play_request("play some music") is None
