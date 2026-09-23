from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from oracle_app.configuration.domain_models import HomeAssistantObjectMapping
from oracle_app.home_assistant_actions import (
    HomeSemanticRequest,
    ResolvedHomeSemanticRequest,
    execute_home_assistant_ui_action,
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
