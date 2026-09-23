from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from canonical_test_support import neutral_household_runtime_settings
from oracle_app.configuration.domain_models import HomeAssistantObjectMapping
from oracle_app.handlers.system import SystemHandler
from oracle_app.home_assistant_presence import (
    normalize_person_presence,
    read_home_assistant_presence,
)
from oracle_app.presence_intents import PresenceQuery, parse_presence_query
from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan
from oracle_app.session_state import clear_all_sessions, get_active_user_id, set_user_context
from oracle_app.system_intents import classify_system_intent
from oracle_app.user_context import describe_effective_user


def _presence_mapping(user_id: str, entity_id: str) -> HomeAssistantObjectMapping:
    return HomeAssistantObjectMapping(
        kind="person_presence",
        oracle_id=user_id,
        entity_id=entity_id,
        allowed_operations=["read"],
    )


def _settings(*mappings: HomeAssistantObjectMapping):
    return SimpleNamespace(
        enabled=True,
        base_url="http://ha.test",
        credential="secret",
        timeout_seconds=3,
        mappings_for_kind=lambda kind: tuple(mappings) if kind == "person_presence" else (),
    )


def _dispatch(action: str, *, text: str = "", source: str = "test-source") -> DispatchPlan:
    return DispatchPlan(
        target="system",
        hook=f"system.{action}",
        payload={"action": action, "text": text, "source": source, "session_id": "presence-session"},
        status="pending_integration",
    )


def setup_function() -> None:
    clear_all_sessions()


def teardown_function() -> None:
    clear_all_sessions()


def test_presence_intents_are_closed_and_system_owned() -> None:
    assert parse_presence_query("who's home") == PresenceQuery("list", "home")
    assert parse_presence_query("who is away") == PresenceQuery("list", "away")
    assert parse_presence_query("is resident one currently at home") == PresenceQuery(
        "person", "home", "resident one"
    )
    assert parse_presence_query("where is resident one") is None
    assert classify_system_intent("who is home").action == "presence"
    assert classify_system_intent("who am i").action == "effective_user"


def test_provider_presence_collapses_named_zones_without_exposing_location() -> None:
    assert normalize_person_presence("home") == "home"
    assert normalize_person_presence("not_home") == "away"
    assert normalize_person_presence("Work") == "away"
    assert normalize_person_presence("unavailable") == "unknown"
    assert normalize_person_presence(None) == "unknown"


def test_person_presence_uses_only_explicit_person_mapping_and_sanitizes_result() -> None:
    household = neutral_household_runtime_settings()
    mapping = _presence_mapping("resident_one", "person.provider_resident_42")
    with patch("oracle_app.home_assistant_presence.HomeAssistantBridge") as bridge_type:
        bridge_type.return_value.fetch_entity_state.return_value = {
            "entity_id": "person.provider_resident_42",
            "state": "Work",
            "attributes": {"latitude": 12.34, "longitude": 56.78},
        }
        result = read_home_assistant_presence(
            PresenceQuery("person", "home", "resident one"),
            household_settings=household,
            home_assistant_settings=_settings(mapping),
        )

    assert result["ok"] is True
    assert result["person"] == {
        "user_id": "resident_one",
        "display_name": "Resident One",
        "state": "away",
    }
    assert "provider_resident_42" not in repr(result)
    assert "latitude" not in repr(result)
    assert "Work" not in repr(result)


def test_presence_never_falls_back_to_device_tracker_or_unmapped_user() -> None:
    household = neutral_household_runtime_settings()
    result = read_home_assistant_presence(
        PresenceQuery("person", "home", "resident one"),
        household_settings=household,
        home_assistant_settings=_settings(),
    )
    assert result["ok"] is False
    assert result["error"] == "presence_mapping_unavailable"


def test_household_presence_list_reports_known_and_unknown_coarsely() -> None:
    household = neutral_household_runtime_settings()
    mapping = _presence_mapping("resident_one", "person.provider_resident_42")
    with patch("oracle_app.home_assistant_presence.HomeAssistantBridge") as bridge_type:
        bridge_type.return_value.fetch_entity_state.return_value = None
        result = read_home_assistant_presence(
            PresenceQuery("list", "home"),
            household_settings=household,
            home_assistant_settings=_settings(mapping),
        )

    assert result["ok"] is True
    assert result["people"] == [
        {"user_id": "resident_one", "display_name": "Resident One", "state": "unknown"}
    ]
    assert "can't determine the current presence of Resident One" in result["speech"]


def test_effective_user_diagnostic_reports_each_provenance_as_non_authenticating() -> None:
    household = MagicMock()
    household.resolve_user_id.return_value = "resident_one"
    household.user.return_value = SimpleNamespace(display_name="Resident One")
    household.configured_associated_user_id.return_value = None
    household.default_user.return_value = None

    explicit = describe_effective_user(
        requested_user_name="Resident One", household_settings=household
    )
    assert explicit["resolution_source"] == "explicit_user"

    set_user_context(
        "session-source", "session-id", user_id="resident_one", resolution_source="explicit_switch"
    )
    session = describe_effective_user(
        source="session-source", session_id="session-id", household_settings=household
    )
    assert session["resolution_source"] == "session_user"

    household.user.side_effect = lambda user_id: (
        SimpleNamespace(display_name="Resident One") if user_id == "resident_one" else None
    )
    household.configured_associated_user_id.return_value = "resident_one"
    associated = describe_effective_user(source="associated-source", household_settings=household)
    assert associated["resolution_source"] == "source_association"

    household.configured_associated_user_id.return_value = None
    household.default_user.return_value = SimpleNamespace(id="resident_one")
    defaulted = describe_effective_user(source="other-source", household_settings=household)
    assert defaulted["resolution_source"] == "household_default"

    for result in (explicit, session, associated, defaulted):
        assert result["authenticates_speaker"] is False
        assert "does not authenticate the speaker" in result["speech"]


def test_effective_user_query_does_not_switch_active_user() -> None:
    household = neutral_household_runtime_settings()
    set_user_context(
        "test-source", "presence-session", user_id="resident_one", resolution_source="explicit_switch"
    )
    dispatch = SystemHandler(household).handle(_dispatch("effective_user"), MagicMock())

    assert dispatch.status == "executed"
    assert dispatch.result["resolution_source"] == "session_user"
    assert get_active_user_id("test-source", "presence-session") == "resident_one"
    assert "does not authenticate the speaker" in build_reply_text(dispatch)


def test_effective_user_handler_preserves_explicit_request_provenance() -> None:
    household = neutral_household_runtime_settings()
    set_user_context(
        "test-source", "presence-session", user_id="resident_two", resolution_source="explicit_switch"
    )
    dispatch = _dispatch("effective_user")
    dispatch.payload["requested_user_name"] = "Resident One"

    dispatch = SystemHandler(household).handle(dispatch, MagicMock())

    assert dispatch.status == "executed"
    assert dispatch.result["user_id"] == "resident_one"
    assert dispatch.result["resolution_source"] == "explicit_user"
    assert get_active_user_id("test-source", "presence-session") == "resident_two"


def test_system_presence_handler_returns_coarse_read_only_result() -> None:
    household = neutral_household_runtime_settings()
    settings = _settings(_presence_mapping("resident_one", "person.provider_resident_42"))
    with patch(
        "oracle_app.handlers.system.read_home_assistant_presence",
        return_value={
            "ok": True,
            "speech": "Resident One is home.",
            "query_kind": "person",
            "desired_state": "home",
            "person": {"user_id": "resident_one", "display_name": "Resident One", "state": "home"},
        },
    ):
        dispatch = SystemHandler(household, home_assistant_settings=settings).handle(
            _dispatch("presence", text="is resident one home"), MagicMock()
        )

    assert dispatch.status == "executed"
    assert build_reply_text(dispatch) == "Resident One is home."
