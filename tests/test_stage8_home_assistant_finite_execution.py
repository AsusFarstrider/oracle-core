from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from oracle_app.configuration.domain_models import HomeAssistantObjectMapping
from oracle_app.home_assistant_actions import (
    HomeSemanticRequest,
    ResolvedHomeSemanticRequest,
    execute_home_assistant_ui_action,
    execute_home_semantic_capability,
    execute_resolved_home_semantic_request,
    resolve_home_semantic_request,
)


def _settings(*mappings: tuple[str, HomeAssistantObjectMapping]):
    values = dict(mappings)
    return SimpleNamespace(
        enabled=True,
        base_url="http://ha.test",
        credential="secret",
        timeout_seconds=3,
        mappings=values,
        callable_alias_collisions=frozenset(),
        mapping=lambda mapping_id: values.get(mapping_id),
    )


def _action(mapping_id: str, target: str, entity: str, operation: str, **extra):
    return mapping_id, HomeAssistantObjectMapping(
        kind="action",
        oracle_id=target,
        entity_id=entity,
        allowed_operations=[operation],
        **extra,
    )


def test_voice_resolves_provider_neutral_light_request() -> None:
    resolved = resolve_home_semantic_request(
        "turn on the reading room lights",
        home_assistant_settings=_settings(_action("reading_room_on", "reading_room", "light.provider_42", "turn_on")),
    )
    assert isinstance(resolved, ResolvedHomeSemanticRequest)
    assert resolved.request == HomeSemanticRequest(
        "home.lights.set", "reading_room", {"target_id": "reading_room", "state": "on"}
    )
    assert "provider_42" not in repr(resolved.request)


def test_characterized_word_orders_and_power_targets_remain_finite() -> None:
    settings = _settings(
        _action("reading_room_on", "reading_room", "light.reading", "turn_on"),
        _action("reading_room_off", "reading_room", "light.reading", "turn_off"),
        _action("purifier_on", "purifier", "fan.purifier", "turn_on"),
    )
    cases = (
        ("turn on the reading room lights", "home.lights.set", "on"),
        ("turn the reading room lights off", "home.lights.set", "off"),
        ("switch on the purifier", "home.power.set", "on"),
    )
    for text, capability_id, state in cases:
        resolved = resolve_home_semantic_request(text, home_assistant_settings=settings)
        assert isinstance(resolved, ResolvedHomeSemanticRequest)
        assert resolved.request.capability_id == capability_id
        assert resolved.request.arguments["state"] == state


def test_target_ambiguity_fails_before_mutation() -> None:
    result = resolve_home_semantic_request(
        "turn on reading room lights",
        home_assistant_settings=_settings(
            _action("reading_room_on", "reading_room", "light.room", "turn_on"),
            _action("reading_lamp_on", "reading_room", "light.lamp", "turn_on"),
        ),
    )
    assert result["error"] == "home_target_ambiguous"


@pytest.mark.parametrize("state,operation", [("on", "turn_on"), ("off", "turn_off")])
@patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
def test_equivalent_action_names_execute_once_across_interfaces(bridge_type, state, operation) -> None:
    settings = _settings(
        _action("z_room_action", "reading_room", "light.room", operation, aliases=["reading room lights"]),
        _action("a_room_action", "reading_room", "light.room", operation),
    )
    resolved = resolve_home_semantic_request(
        f"turn {state} reading room", home_assistant_settings=settings,
    )
    assert isinstance(resolved, ResolvedHomeSemanticRequest)
    assert resolved.mapping_id == "a_room_action"
    bridge = bridge_type.return_value
    bridge.wait_for_entity_state.return_value = {"state": state}
    for execute in (
        lambda: execute_resolved_home_semantic_request(resolved, home_assistant_settings=settings, interface="voice"),
        lambda: execute_home_assistant_ui_action("z_room_action", home_assistant_settings=settings),
        lambda: execute_home_semantic_capability("home.lights.set", {"target_id": "reading_room", "state": state},
                                                home_assistant_settings=settings, confirmed=False),
    ):
        bridge.reset_mock()
        assert execute()["status"] == "verified"
        bridge.set_power.assert_called_once_with(entity_id="light.room", enabled=state == "on")


@patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
def test_different_provider_bindings_remain_ambiguous_in_typed_execution(bridge_type) -> None:
    result = execute_home_semantic_capability(
        "home.lights.set", {"target_id": "reading_room", "state": "on"},
        home_assistant_settings=_settings(
            _action("a_room_on", "reading_room", "light.room", "turn_on"),
            _action("b_room_on", "reading_room", "light.lamp", "turn_on"),
        ), confirmed=False,
    )
    assert result["error"] == "home_action_mapping_ambiguous"
    bridge_type.assert_not_called()


@patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
def test_alias_coalescing_does_not_remove_consequential_confirmation(bridge_type) -> None:
    result = execute_home_semantic_capability(
        "home.access.set", {"target_id": "side_entry", "state": "unlocked"},
        home_assistant_settings=_settings(
            _action("a_entry_unlock", "side_entry", "lock.side", "unlock"),
            _action("z_entry_unlock", "side_entry", "lock.side", "unlock"),
        ), confirmed=False,
    )
    assert result["status"] == "pending_confirmation"
    assert result["ok"] is False
    bridge_type.assert_not_called()


def test_same_provider_target_with_different_climate_risk_policy_is_not_equivalent() -> None:
    settings = _settings(*(
        _action(mapping_id, "reading_room_thermostat", "climate.reading_room", "cooler",
                normal_temperature_min=59, normal_temperature_max=maximum, temperature_unit="fahrenheit")
        for mapping_id, maximum in (("a_climate_cooler", 68), ("b_climate_cooler", 75))
    ))
    result = resolve_home_semantic_request(
        "set the reading room thermostat to 70 degrees", home_assistant_settings=settings,
    )
    assert result["error"] == "home_target_ambiguous"


def test_read_mapping_and_opposite_direction_do_not_authorize_typed_action() -> None:
    settings = _settings(
        ("room_state", HomeAssistantObjectMapping(kind="entity", oracle_id="reading_room", entity_id="light.room", allowed_operations=["read"])),
        _action("room_off", "reading_room", "light.room", "turn_off"),
    )
    result = execute_home_semantic_capability(
        "home.lights.set", {"target_id": "reading_room", "state": "on"},
        home_assistant_settings=settings, confirmed=False,
    )
    assert result["error"] == "home_action_mapping_ambiguous"


def test_raw_provider_identifier_and_discovery_do_not_create_write_authority() -> None:
    settings = _settings(
        _action("reading_room_on", "reading_room", "light.provider_42", "turn_on")
    )
    result = resolve_home_semantic_request(
        "turn on light.provider_42", home_assistant_settings=settings
    )
    assert result["error"] == "home_action_unconfigured"


@patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
def test_unlock_requires_confirmation_on_voice_and_ui(bridge_type) -> None:
    settings = _settings(_action("side_entry_unlock", "side_entry", "lock.side", "unlock"))
    resolved = resolve_home_semantic_request("unlock the side entry", home_assistant_settings=settings)
    assert isinstance(resolved, ResolvedHomeSemanticRequest)
    voice = execute_resolved_home_semantic_request(
        resolved, home_assistant_settings=settings, interface="voice"
    )
    ui = execute_home_assistant_ui_action("side_entry_unlock", home_assistant_settings=settings)
    runbook = execute_home_assistant_ui_action(
        "side_entry_unlock", home_assistant_settings=settings, interface="runbook"
    )
    assert voice["status"] == ui["status"] == "pending_confirmation"
    assert runbook["status"] == "pending_confirmation"
    assert runbook["ok"] is False
    bridge_type.return_value.set_access.assert_not_called()


@patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
def test_climate_uses_configured_normal_and_provider_bounds(bridge_type) -> None:
    settings = _settings(
        _action(
            "reading_room_cooler", "reading_room_thermostat", "climate.reading_room", "cooler",
            normal_temperature_min=59, normal_temperature_max=68, temperature_unit="fahrenheit",
        )
    )
    bridge = bridge_type.return_value
    bridge.fetch_entity_state.return_value = {
        "state": "heat", "attributes": {"temperature": 70, "min_temp": 50, "max_temp": 90}
    }
    consequential = resolve_home_semantic_request(
        "set the reading room thermostat to 70 degrees", home_assistant_settings=settings
    )
    assert isinstance(consequential, ResolvedHomeSemanticRequest)
    assert execute_resolved_home_semantic_request(
        consequential, home_assistant_settings=settings, interface="voice"
    )["status"] == "pending_confirmation"
    outside = ResolvedHomeSemanticRequest(
        HomeSemanticRequest(
            "home.environment.setpoint", "reading_room_thermostat",
            {"target_id": "reading_room_thermostat", "setpoint": 95, "temperature_unit": "fahrenheit"},
        ),
        "reading_room_cooler", "set_temperature",
    )
    assert execute_resolved_home_semantic_request(
        outside, home_assistant_settings=settings, interface="voice", confirmed=True
    )["error"] == "home_climate_provider_bound"
    bridge.set_climate_temperature.assert_not_called()


def test_configured_provider_unit_is_opaque_and_collision_clarifies() -> None:
    mapping = _action(
        "movie_scene", "movie_time", "scene.provider_movie", "invoke", aliases=["start movie time"]
    )
    settings = _settings(mapping)
    settings.callable_alias_collisions = frozenset({"start movie time"})
    result = resolve_home_semantic_request("start movie time", home_assistant_settings=settings)
    assert result["error"] == "home_callable_alias_ambiguous"
