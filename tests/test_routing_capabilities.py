from __future__ import annotations

from dataclasses import replace
import sys
from types import MappingProxyType
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.text_normalization import normalize_text
from oracle_app import state
from oracle_app.routing import build_route_capability_registry, choose_route
from oracle_app.session_state import clear_all_sessions, set_active_context
from canonical_test_support import neutral_brain_runtime_settings


_NEUTRAL_RUNTIME = neutral_brain_runtime_settings()
_NEUTRAL_HOUSEHOLD = _NEUTRAL_RUNTIME.household
_TEST_NEWS_SETTINGS = replace(
    _NEUTRAL_RUNTIME.information.news,
    enabled=True,
    resolution_terms=MappingProxyType({"npr": "example_news"}),
)
_TEST_CALENDAR_SETTINGS = replace(_NEUTRAL_RUNTIME.calendar, enabled=True)
_CANONICAL_ROUTE_ARGUMENTS = {
    "facts_enabled": False,
    "news_settings": _TEST_NEWS_SETTINGS,
    "canonical_information": True,
    "calendar_settings": _TEST_CALENDAR_SETTINGS,
    "canonical_calendar": True,
}
_NEUTRAL_ROUTE_REGISTRY = build_route_capability_registry(
    _NEUTRAL_HOUSEHOLD,
    **_CANONICAL_ROUTE_ARGUMENTS,
)
_BASELINE_ROUTE_REGISTRY = build_route_capability_registry(
    **_CANONICAL_ROUTE_ARGUMENTS,
)
_FACTS_ROUTE_REGISTRY = build_route_capability_registry(
    **(_CANONICAL_ROUTE_ARGUMENTS | {"facts_enabled": True}),
)
_BASE_CHOOSE_ROUTE = choose_route


def _choose_canonical_home_route(text: str, **kwargs):
    """Evaluate routing with explicit neutral canonical household authority."""

    kwargs.setdefault("registry", _NEUTRAL_ROUTE_REGISTRY)
    kwargs.setdefault("household_settings", _NEUTRAL_HOUSEHOLD)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


def _choose_canonical_route(text: str, **kwargs):
    """Evaluate routing with explicit canonical non-household dependencies."""

    kwargs.setdefault("registry", _BASELINE_ROUTE_REGISTRY)
    kwargs.setdefault("household_settings", _NEUTRAL_HOUSEHOLD)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


def _choose_canonical_facts_route(text: str, **kwargs):
    kwargs.setdefault("registry", _FACTS_ROUTE_REGISTRY)
    return _BASE_CHOOSE_ROUTE(text, **kwargs)


_CANONICAL_HOME_TESTS = {
    "test_home_followup_what_about_room_can_use_strong_home_session_context",
    "test_implied_home_bedroom_red_color_intent",
    "test_implied_home_colder_room_intent",
    "test_implied_home_cool_off_room_intent",
    "test_implied_home_cool_room_down_intent",
    "test_implied_home_explicit_lights_cooler_routes_to_cool_white",
    "test_implied_home_explicit_lights_softer_routes_to_warm_white",
    "test_implied_home_explicit_lights_warmer_routes_to_warm_white",
    "test_implied_home_hotter_room_intent",
    "test_implied_home_lights_back_to_normal_room_intent",
    "test_implied_home_make_lights_normal_again_room_intent",
    "test_implied_home_put_lights_back_to_normal_room_intent",
    "test_implied_home_raise_lights_intent",
    "test_implied_home_room_color_intent_with_explicit_lights",
    "test_implied_home_room_color_intent_without_explicit_lights",
    "test_implied_home_room_cooler_white_is_light_color",
    "test_implied_home_room_orange_color_intent",
    "test_implied_home_room_warm_white_is_light_color_not_climate",
    "test_implied_home_set_lights_a_little_higher_intent",
    "test_implied_home_set_lights_a_little_lower_intent",
    "test_implied_home_set_lights_lower_intent",
    "test_implied_home_set_lights_to_normal_room_intent",
    "test_implied_home_turn_down_lights_intent",
    "test_implied_home_turn_lights_down_intent",
    "test_implied_home_turn_lights_up_intent",
    "test_implied_home_turn_up_lights_intent",
    "test_implied_home_warm_room_up_intent",
}


def _fallback_target() -> str:
    return "fallback_router"


class RoutingCapabilitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._canonical_route_patcher = patch(
            f"{__name__}.choose_route",
            side_effect=(
                _choose_canonical_home_route
                if self._testMethodName in _CANONICAL_HOME_TESTS
                else _choose_canonical_route
            ),
        )
        self._canonical_route_patcher.start()

    def tearDown(self) -> None:
        self._canonical_route_patcher.stop()
        clear_all_sessions()

    def test_system_confirm_has_high_priority(self) -> None:
        route = choose_route("confirm")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched internal confirmation command")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_intent(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [{"spoken_name": "guest's room", "aliases": ["guest's room", "guest room"]}],
            "entities": [],
        }
        route = choose_route("it's dark in the guest's room")
        self.assertEqual(route.target, "home_assistant")
        self.assertIn("turn on the lights", route.normalized_text)

    def test_network_health_query_routes_to_network(self) -> None:
        route = choose_route("how's the network?")
        self.assertEqual(route.target, "network")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_brighter_room_intent(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [{"spoken_name": "living room", "aliases": ["living room"]}],
            "entities": [],
        }
        route = choose_route("make the living room brighter")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 75 percent brightness")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_brighter_room_with_modifier_intent(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [{"spoken_name": "living room", "aliases": ["living room"]}],
            "entities": [],
        }
        route = choose_route("make the living room a little brighter")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 60 percent brightness")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_less_bright_room_intent(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [{"spoken_name": "living room", "aliases": ["living room"]}],
            "entities": [],
        }
        route = choose_route("make the living room less bright")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_more_bright_room_intent(self) -> None:
        route = choose_route("make the living room more bright")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 75 percent brightness")

    def test_implied_home_more_dim_room_intent(self) -> None:
        route = choose_route("make the living room more dim")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_cooler_room_intent(self) -> None:
        route = choose_route("make the bedroom cooler")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature lower in the bedroom")

    def test_implied_home_dimmer_room_intent(self) -> None:
        route = choose_route("make the living room dimmer")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_turn_lights_down_intent(self) -> None:
        route = choose_route("turn the living room lights down")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_turn_lights_up_intent(self) -> None:
        route = choose_route("turn the living room lights up")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 75 percent brightness")

    def test_implied_home_turn_up_lights_intent(self) -> None:
        route = choose_route("turn up the living room lights")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 75 percent brightness")

    def test_implied_home_turn_down_lights_intent(self) -> None:
        route = choose_route("turn down the living room lights")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_raise_lights_intent(self) -> None:
        route = choose_route("raise the living room lights")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 75 percent brightness")

    def test_implied_home_set_lights_lower_intent(self) -> None:
        route = choose_route("set the living room lights lower")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 25 percent brightness")

    def test_implied_home_set_lights_a_little_lower_intent(self) -> None:
        route = choose_route("set the living room lights a little lower")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 40 percent brightness")

    def test_implied_home_set_lights_a_little_higher_intent(self) -> None:
        route = choose_route("set the living room lights a little higher")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the living room lights to 60 percent brightness")

    def test_implied_home_cool_room_down_intent(self) -> None:
        route = choose_route("cool the bedroom down")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature lower in the bedroom")

    def test_implied_home_cool_off_room_intent(self) -> None:
        route = choose_route("cool off the bedroom")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature lower in the bedroom")

    def test_implied_home_warm_room_up_intent(self) -> None:
        route = choose_route("warm the bedroom up")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature higher in the bedroom")

    def test_implied_home_hotter_room_intent(self) -> None:
        route = choose_route("make the bedroom hotter")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature higher in the bedroom")

    def test_implied_home_colder_room_intent(self) -> None:
        route = choose_route("make the bedroom colder")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature lower in the bedroom")

    def test_implied_home_temperature_down_intent(self) -> None:
        route = choose_route("turn the temperature down in the bedroom")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature lower in the bedroom")

    def test_implied_home_temperature_up_intent(self) -> None:
        route = choose_route("bring the temperature up in the bedroom")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the temperature higher in the bedroom")

    def test_implied_home_raise_blinds_intent(self) -> None:
        route = choose_route("raise the living room blinds")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "open the living room blinds")

    def test_implied_home_lower_blinds_intent(self) -> None:
        route = choose_route("lower the living room blinds")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "close the living room blinds")

    def test_implied_home_lights_back_to_normal_room_intent(self) -> None:
        route = choose_route("set the living room lights back to normal")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(
            route.normalized_text,
            "set the lights in the living room to 100 percent brightness and 2000 kelvin",
        )

    def test_implied_home_room_color_intent_without_explicit_lights(self) -> None:
        route = choose_route("make the living room blue")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to blue")

    def test_implied_home_room_color_intent_with_explicit_lights(self) -> None:
        route = choose_route("set the living room lights to blue")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to blue")

    def test_implied_home_room_warm_white_is_light_color_not_climate(self) -> None:
        route = choose_route("set the living room lights to warm white")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to warm white")

    def test_implied_home_room_cooler_white_is_light_color(self) -> None:
        route = choose_route("set the living room lights to cooler white")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to cooler white")

    def test_implied_home_explicit_lights_warmer_routes_to_warm_white(self) -> None:
        route = choose_route("make the living room lights warmer")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to warm white")

    def test_implied_home_explicit_lights_cooler_routes_to_cool_white(self) -> None:
        route = choose_route("make the living room lights cooler")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to cool white")

    def test_implied_home_explicit_lights_softer_routes_to_warm_white(self) -> None:
        route = choose_route("make the living room lights softer")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to warm white")

    def test_implied_home_room_orange_color_intent(self) -> None:
        route = choose_route("make the living room more orange")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the living room to orange")

    def test_implied_home_bedroom_red_color_intent(self) -> None:
        route = choose_route("make the bedroom red")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the lights in the bedroom to red")

    def test_implied_home_put_lights_back_to_normal_room_intent(self) -> None:
        route = choose_route("put the living room lights back to normal")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(
            route.normalized_text,
            "set the lights in the living room to 100 percent brightness and 2000 kelvin",
        )

    def test_implied_home_make_lights_normal_again_room_intent(self) -> None:
        route = choose_route("make the living room lights normal again")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(
            route.normalized_text,
            "set the lights in the living room to 100 percent brightness and 2000 kelvin",
        )

    def test_implied_home_set_lights_to_normal_room_intent(self) -> None:
        route = choose_route("set the bedroom lights to normal")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(
            route.normalized_text,
            "set the lights in the bedroom to 100 percent brightness and 2000 kelvin",
        )

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_lamp_back_to_normal_entity_intent(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [],
            "entities": [
                {
                    "friendly_name": "Office Lamp",
                    "domain": "light",
                    "aliases": ["office lamp", "officelamp"],
                }
            ],
        }
        route = choose_route("set the office lamp back to normal")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(
            route.normalized_text,
            "set the office lamp to 100 percent brightness and 2000 kelvin",
        )

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_implied_home_brighter_entity_uses_cached_entity_alias(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [],
            "entities": [
                {
                    "friendly_name": "Office Lamp",
                    "domain": "light",
                    "aliases": ["office lamp", "officelamp"],
                }
            ],
        }
        route = choose_route("make the office lamp brighter")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "set the office lamp to 75 percent brightness")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_keyword_home_intent_canonicalizes_room_alias(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [
                {
                    "spoken_name": "guest's room",
                    "aliases": ["guest's room", "guest room"],
                }
            ]
        }
        route = choose_route("turn on the guest room lights")
        self.assertEqual(route.target, "home_assistant")
        self.assertIn("guest's room", route.normalized_text)

    def test_fallback_to_fallback_router(self) -> None:
        route = choose_route("tell me a short joke")
        self.assertEqual(route.target, _fallback_target())
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_fallback_routes_to_fallback_router(self) -> None:
        route = choose_route("tell me a short joke")
        self.assertEqual(route.target, "fallback_router")
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_chat_about_spaceships_does_not_false_match_ac_keyword(self) -> None:
        route = choose_route("tell me a short joke about spaceships")
        self.assertEqual(route.target, _fallback_target())
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_make_it_cooler_in_here_does_not_false_match_home(self) -> None:
        route = choose_route("make it cooler in here")
        self.assertEqual(route.target, _fallback_target())
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_explain_why_the_sky_is_blue_does_not_false_match_home(self) -> None:
        route = choose_route("explain why the sky is blue")
        self.assertEqual(route.target, _fallback_target())
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_weather_query_routes_to_weather(self) -> None:
        route = choose_route("what's the weather right now")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched weather query")

    def test_forecast_query_routes_to_weather(self) -> None:
        route = choose_route("what is the weather tomorrow")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched forecast query")

    def test_weekday_forecast_routes_to_weather(self) -> None:
        route = choose_route("what is the weather on tuesday")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched forecast query")

    def test_weekday_night_forecast_routes_to_weather(self) -> None:
        route = choose_route("what is the weather tuesday night")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched forecast query")

    def test_weekend_forecast_routes_to_weather(self) -> None:
        route = choose_route("what is the weather this weekend")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched forecast query")

    def test_historical_weather_routes_to_weather(self) -> None:
        route = choose_route("what was the weather yesterday")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched historical weather query")

    def test_remote_weather_routes_to_weather(self) -> None:
        route = choose_route("what is the weather in boston")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote weather query")

    def test_location_fact_question_routes_to_facts_not_weather(self) -> None:
        route = _choose_canonical_facts_route("where is machu picchu")
        self.assertEqual(route.target, "facts")
        self.assertEqual(route.reason, "Matched factual lookup request")

    def test_located_fact_question_routes_to_facts_not_weather(self) -> None:
        route = _choose_canonical_facts_route("where is machu picchu located")
        self.assertEqual(route.target, "facts")
        self.assertEqual(route.reason, "Matched factual lookup request")

    def test_remote_forecast_routes_to_weather(self) -> None:
        route = choose_route("what is the weather tomorrow in boston")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_remote_forecast_location_first_routes_to_weather(self) -> None:
        route = choose_route("what is the weather in boston on tuesday")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_remote_forecast_forecast_for_shape_routes_to_weather(self) -> None:
        route = choose_route("forecast for boston on tuesday")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_remote_forecast_location_prefixed_forecast_for_shape_routes_to_weather(self) -> None:
        route = choose_route("boston forecast for tuesday")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_remote_practical_forecast_coat_phrase_routes_to_weather(self) -> None:
        route = choose_route("do i need a coat in boston tomorrow")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_remote_practical_forecast_umbrella_phrase_routes_to_weather(self) -> None:
        route = choose_route("should i bring an umbrella in boston tomorrow")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_location_first_remote_forecast_shape_routes_to_weather(self) -> None:
        route = choose_route("what will boston weather be tomorrow")
        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched remote forecast query")

    def test_source_only_news_phrase_routes_to_news(self) -> None:
        route = choose_route("fill me in on npr")
        self.assertEqual(route.target, "news")
        self.assertEqual(route.reason, "Matched news request")

    def test_latest_from_source_routes_to_news(self) -> None:
        route = choose_route("give me the latest from npr")
        self.assertEqual(route.target, "news")
        self.assertEqual(route.reason, "Matched news request")

    def test_read_me_something_from_npr_routes_to_news_not_audiobook(self) -> None:
        route = choose_route("read me something from npr")
        self.assertEqual(route.target, "news")
        self.assertEqual(route.reason, "Matched news request")

    def test_time_query_routes_to_system(self) -> None:
        route = choose_route("what time is it")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched time query")

    def test_date_query_routes_to_system(self) -> None:
        route = choose_route("what day is it")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched date query")

    def test_home_assistant_devices_and_rooms_cache_update_routes_to_system_refresh_cache(self) -> None:
        route = choose_route("update your cache of devices and rooms from home assistant")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched internal cache refresh command")

    def test_math_query_routes_to_system(self) -> None:
        route = choose_route("what is 2 plus 2")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched math query")

    def test_times_expression_does_not_route_as_time_query(self) -> None:
        route = choose_route("what is 12 times 13")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched math query")

    def test_conversion_query_routes_to_system(self) -> None:
        route = choose_route("convert 10 miles to kilometers")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched unit conversion query")

    def test_timer_query_routes_to_system(self) -> None:
        route = choose_route("set a timer for 5 minutes")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched timer/alarm/reminder query")

    def test_timer_status_does_not_route_as_time_query(self) -> None:
        route = choose_route("what timers do i have")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched timer/alarm/reminder query")

    def test_audiobook_request_routes_to_audiobook(self) -> None:
        route = choose_route("play audiobook the hobbit")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook request")

    def test_probable_audiobook_title_routes_to_audiobook_before_music(self) -> None:
        route = choose_route("play harry potter and the prisoner of azkaban")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")
        self.assertEqual(route.normalized_text, "play audiobook harry potter and the prisoner of azkaban")

    def test_probable_audiobook_title_from_indirect_play_prefix_routes_to_audiobook(self) -> None:
        route = choose_route("i want to hear harry potter and the prisoner of azkaban")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")

    def test_probable_audiobook_title_from_queue_up_prefix_routes_to_audiobook(self) -> None:
        route = choose_route("queue up harry potter and the prisoner of azkaban")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")

    def test_probable_audiobook_title_with_narrator_cue_routes_to_audiobook(self) -> None:
        route = choose_route("play the jim dale version of prisoner of azkaban")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")

    def test_probable_audiobook_title_with_narrated_by_cue_routes_to_audiobook(self) -> None:
        route = choose_route("play prisoner of azkaban narrated by jim dale")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")
        self.assertEqual(route.normalized_text, "play audiobook prisoner of azkaban narrated by jim dale")

    def test_probable_audiobook_title_with_regular_cue_routes_to_audiobook(self) -> None:
        route = choose_route("play the regular prisoner of azkaban")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched probable audiobook title request")

    def test_explicit_soundtrack_phrase_stays_music(self) -> None:
        route = choose_route("play harry potter soundtrack")
        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_explicit_music_from_phrase_stays_music(self) -> None:
        route = choose_route("play music from harry potter")
        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_short_generic_title_still_routes_to_music_first(self) -> None:
        route = choose_route("play dune")
        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_pause_audiobook_routes_to_audiobook(self) -> None:
        route = choose_route("pause audiobook")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook request")

    def test_stop_audiobook_routes_to_audiobook(self) -> None:
        route = choose_route("stop audiobook")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook request")

    def test_audiobook_series_lookup_routes_to_audiobook(self) -> None:
        route = choose_route("what book 3 of the harry potter series was")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook request")

    def test_audiobook_series_play_routes_to_audiobook(self) -> None:
        route = choose_route("play the third harry potter book")
        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched audiobook request")

    def test_pending_audiobook_valid_clarification_reply_routes_to_audiobook(self) -> None:
        state.store_pending_audiobook_request(
            "server-satellite-105",
            "book-clarify",
            {
                "intent": {"intent": "play", "title": "prisoner of azkaban"},
                "candidates": [
                    {
                        "library_item_id": "book-regular",
                        "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Jim Dale",
                    },
                    {
                        "library_item_id": "book-cast",
                        "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Full Cast",
                    },
                ],
            },
        )

        route = choose_route(
            "the jim dale edition",
            source="server-satellite-105",
            session_id="book-clarify",
        )

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched pending audiobook clarification context")

    def test_pending_audiobook_unrelated_turn_falls_through_to_normal_routing(self) -> None:
        state.store_pending_audiobook_request(
            "server-satellite-105",
            "book-clarify",
            {
                "intent": {"intent": "play", "title": "prisoner of azkaban"},
                "candidates": [
                    {
                        "library_item_id": "book-regular",
                        "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Jim Dale",
                    },
                    {
                        "library_item_id": "book-cast",
                        "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Full Cast",
                    },
                ],
            },
        )

        route = choose_route(
            "tell me a joke",
            source="server-satellite-105",
            session_id="book-clarify",
        )

        self.assertEqual(route.target, _fallback_target())

    def test_pending_audiobook_gate_failure_allows_system_route(self) -> None:
        state.store_pending_audiobook_request(
            "server-satellite-105",
            "book-clarify",
            {
                "intent": {"intent": "play", "title": "prisoner of azkaban"},
                "candidates": [
                    {
                        "library_item_id": "book-regular",
                        "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Jim Dale",
                    },
                    {
                        "library_item_id": "book-cast",
                        "title": "Harry Potter and the Prisoner of Azkaban (Full-Cast Edition)",
                        "author": "J. K. Rowling",
                        "subtitle": "",
                        "narrator": "Full Cast",
                    },
                ],
            },
        )

        route = choose_route(
            "what's the weather tomorrow",
            source="server-satellite-105",
            session_id="book-clarify",
        )

        self.assertEqual(route.target, "weather")
        self.assertEqual(route.reason, "Matched forecast query")

    def test_music_album_lookup_routes_to_music(self) -> None:
        route = choose_route("what album is thunderstruck on")
        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_what_song_is_playing_routes_to_music(self) -> None:
        route = choose_route("what song is playing")
        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_pending_music_clarification_routes_by_matching_source_and_session(self) -> None:
        state.store_pending_music_request(
            "pi-satellite-102",
            "music-followup-1",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [
                    {"title": "Heroes", "artist": "David Bowie", "album": "Heroes"},
                    {"title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back"},
                ],
            },
        )

        route = choose_route("the second one", source="pi-satellite-102", session_id="music-followup-1")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched pending music clarification context")

    def test_pending_music_clarification_does_not_route_for_other_source(self) -> None:
        state.store_pending_music_request(
            "pi-satellite-102",
            "music-followup-2",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [
                    {"title": "Heroes", "artist": "David Bowie", "album": "Heroes"},
                    {"title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back"},
                ],
            },
        )

        route = choose_route("the second one", source="pi-satellite-101", session_id="music-followup-2")

        self.assertNotEqual(route.reason, "Matched pending music clarification context")

    def test_pending_music_clarification_does_not_route_for_other_session(self) -> None:
        state.store_pending_music_request(
            "pi-satellite-102",
            "music-followup-3",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [
                    {"title": "Heroes", "artist": "David Bowie", "album": "Heroes"},
                    {"title": "Heroes", "artist": "Peter Gabriel", "album": "Scratch My Back"},
                ],
            },
        )

        route = choose_route("the second one", source="pi-satellite-102", session_id="other-session")

        self.assertNotEqual(route.reason, "Matched pending music clarification context")

    def test_pending_audiobook_clarification_routes_by_matching_source_and_session(self) -> None:
        state.store_pending_audiobook_request(
            "pi-satellite-102",
            "book-followup-1",
            {
                "intent": {"intent": "play", "title": "dune"},
                "candidates": [
                    {"title": "Dune", "author": "Frank Herbert", "subtitle": ""},
                    {"title": "Dune Messiah", "author": "Frank Herbert", "subtitle": ""},
                ],
            },
        )

        route = choose_route("the first one", source="pi-satellite-102", session_id="book-followup-1")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched pending audiobook clarification context")

    def test_pending_audiobook_clarification_does_not_route_for_other_source(self) -> None:
        state.store_pending_audiobook_request(
            "pi-satellite-102",
            "book-followup-2",
            {
                "intent": {"intent": "play", "title": "dune"},
                "candidates": [
                    {"title": "Dune", "author": "Frank Herbert", "subtitle": ""},
                    {"title": "Dune Messiah", "author": "Frank Herbert", "subtitle": ""},
                ],
            },
        )

        route = choose_route("the first one", source="pi-satellite-101", session_id="book-followup-2")

        self.assertNotEqual(route.reason, "Matched pending audiobook clarification context")

    def test_pending_audiobook_clarification_does_not_route_for_other_session(self) -> None:
        state.store_pending_audiobook_request(
            "pi-satellite-102",
            "book-followup-3",
            {
                "intent": {"intent": "play", "title": "dune"},
                "candidates": [
                    {"title": "Dune", "author": "Frank Herbert", "subtitle": ""},
                    {"title": "Dune Messiah", "author": "Frank Herbert", "subtitle": ""},
                ],
            },
        )

        route = choose_route("the first one", source="pi-satellite-102", session_id="other-book-session")

        self.assertNotEqual(route.reason, "Matched pending audiobook clarification context")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_stop_routes_to_active_audiobook(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": True, "state": "playing"}
        mock_now_playing.return_value = {"ok": True, "playing": False}

        route = choose_route("stop", source="pi-satellite-102")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched active audiobook transport command")
        self.assertEqual(route.normalized_text, "stop audiobook")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_pause_routes_to_active_music(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": True, "state": "playing"}

        route = choose_route("pause", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched active music transport command")
        self.assertEqual(route.normalized_text, "pause")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_stop_the_music_routes_to_active_music_transport(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": True, "state": "playing"}

        route = choose_route("stop the music", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched active music transport command")
        self.assertEqual(route.normalized_text, "stop")

    @patch("oracle_app.route_refinement.fetch_satellite_playback_authority")
    def test_bare_resume_routes_to_paused_audiobook_from_playback_authority(
        self,
        mock_fetch_authority,
    ) -> None:
        mock_fetch_authority.return_value = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "book-1",
                    "backend_type": "oracle_audiobook",
                    "media_kind": "audiobook",
                    "state": "paused",
                    "resumable": True,
                }
            ],
            "active_sessions": [],
        }

        route = choose_route("resume", source="pi-satellite-102")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.normalized_text, "resume audiobook")

    @patch("oracle_app.route_refinement.fetch_satellite_playback_authority")
    def test_bare_resume_routes_to_paused_music_from_playback_authority(
        self,
        mock_fetch_authority,
    ) -> None:
        mock_fetch_authority.return_value = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "track-1",
                    "backend_type": "oracle_native_music",
                    "media_kind": "music",
                    "state": "paused",
                    "resumable": True,
                }
            ],
            "active_sessions": [],
        }

        route = choose_route("resume", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched active music transport command")
        self.assertEqual(route.normalized_text, "resume")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_stop_routes_to_degraded_dual_active_fallback_when_both_are_active(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": True, "state": "playing"}
        mock_now_playing.return_value = {"ok": True, "playing": True, "state": "playing"}

        route = choose_route("stop", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched degraded dual-active media stop fallback")
        self.assertEqual(route.normalized_text, "stop")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_pause_routes_to_degraded_dual_active_fallback_when_both_are_active(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": True, "state": "playing"}
        mock_now_playing.return_value = {"ok": True, "playing": True, "state": "playing"}

        route = choose_route("pause", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched degraded dual-active media pause fallback")
        self.assertEqual(route.normalized_text, "pause")

    @patch("oracle_app.route_refinement.fetch_satellite_playback_authority")
    def test_bare_pause_routes_to_degraded_dual_active_fallback_from_authority_flag(
        self,
        mock_fetch_authority,
    ) -> None:
        mock_fetch_authority.return_value = {
            "ok": True,
            "degraded_state": True,
            "degraded_reasons": ["dual_active_music_audiobook"],
            "sessions": [
                {"backend_type": "oracle_audiobook", "media_kind": "audiobook", "state": "playing"},
                {"backend_type": "oracle_native_music", "media_kind": "music", "state": "playing"},
            ],
            "active_sessions": [
                {"backend_type": "oracle_audiobook", "media_kind": "audiobook", "state": "playing"},
                {"backend_type": "oracle_native_music", "media_kind": "music", "state": "playing"},
            ],
        }

        route = choose_route("pause", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched degraded dual-active media pause fallback")
        self.assertEqual(route.normalized_text, "pause")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_stop_routes_to_active_reply_audio(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": True, "kind": "tts"}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}

        route = choose_route("stop", source="pi-satellite-102")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched active reply audio transport command")
        self.assertEqual(route.normalized_text, "stop")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_stop_does_not_route_to_system_without_active_media(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}

        route = choose_route("stop", source="pi-satellite-102")

        self.assertNotEqual(route.target, "system")
        self.assertNotEqual(route.reason, "Matched internal cancel command")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_pause_can_fall_back_to_strong_music_session_context(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}
        set_active_context(
            "pi-satellite-102",
            "music-anchor-1",
            route_target="music",
            dispatch_hook="music.execute",
            action="play",
            anchor_strength="strong",
        )

        route = choose_route("pause", source="pi-satellite-102", session_id="music-anchor-1")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched strong active session context")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_pending_state_blocks_strong_active_context_transport_refinement(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}
        set_active_context(
            "pi-satellite-102",
            "music-anchor-pending-1",
            route_target="music",
            dispatch_hook="music.execute",
            action="play",
            anchor_strength="strong",
        )
        state.store_pending_music_request(
            "pi-satellite-102",
            "music-anchor-pending-1",
            {
                "intent": {"intent": "play", "title": "heroes"},
                "candidates": [{"title": "Heroes"}, {"title": "Heroes", "artist": "David Bowie"}],
            },
        )

        route = choose_route("pause", source="pi-satellite-102", session_id="music-anchor-pending-1")

        self.assertEqual(route.target, "music")
        self.assertNotEqual(route.reason, "Matched strong active session context")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_bare_resume_can_fall_back_to_strong_audiobook_session_context(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}
        set_active_context(
            "pi-satellite-102",
            "book-anchor-1",
            route_target="audiobook",
            dispatch_hook="audiobook.execute",
            action="play",
            anchor_strength="strong",
        )

        route = choose_route("resume", source="pi-satellite-102", session_id="book-anchor-1")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "resume audiobook")

    @patch("oracle_app.route_refinement.fetch_satellite_playback_authority")
    def test_bare_resume_with_ambiguous_playback_authority_uses_strong_session_context(
        self,
        mock_fetch_authority,
    ) -> None:
        mock_fetch_authority.return_value = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "track-1",
                    "backend_type": "oracle_native_music",
                    "media_kind": "music",
                    "state": "paused",
                    "resumable": True,
                },
                {
                    "session_id": "book-1",
                    "backend_type": "oracle_audiobook",
                    "media_kind": "audiobook",
                    "state": "paused",
                    "resumable": True,
                },
            ],
            "active_sessions": [],
        }
        set_active_context(
            "pi-satellite-102",
            "book-authority-anchor-1",
            route_target="audiobook",
            dispatch_hook="audiobook.execute",
            action="play",
            anchor_strength="strong",
        )

        route = choose_route("resume", source="pi-satellite-102", session_id="book-authority-anchor-1")

        self.assertEqual(route.target, "audiobook")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "resume audiobook")

    @patch("oracle_app.route_refinement.fetch_satellite_music_session")
    @patch("oracle_app.route_refinement.fetch_satellite_audiobook_session")
    @patch("oracle_app.route_refinement.fetch_satellite_reply_audio_session")
    def test_weak_ollama_session_context_does_not_hijack_bare_pause(
        self,
        mock_reply_audio_state,
        mock_longform_state,
        mock_now_playing,
    ) -> None:
        mock_reply_audio_state.return_value = {"ok": True, "playing": False}
        mock_longform_state.return_value = {"ok": True, "playing": False, "state": "stopped"}
        mock_now_playing.return_value = {"ok": True, "playing": False, "state": "stopped"}
        set_active_context(
            "pi-satellite-102",
            "weak-anchor-1",
            route_target="facts",
            dispatch_hook="facts.lookup",
            action="facts_lookup",
            anchor_strength="weak",
        )

        route = choose_route("pause", source="pi-satellite-102", session_id="weak-anchor-1")

        self.assertEqual(route.target, "music")
        self.assertEqual(route.reason, "Matched music control request")

    def test_home_followup_turn_them_off_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-1",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
        )

        route = choose_route("turn them off", source="pi-satellite-102", session_id="home-anchor-1")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn them off")

    def test_home_followup_turn_them_back_on_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-1b",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn off the guest's room lights",
        )

        route = choose_route("turn them back on", source="pi-satellite-102", session_id="home-anchor-1b")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn on the guest's room lights")

    def test_home_followup_turn_them_back_on_reuses_brightness_context_target(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-1c",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="set the kitchen lights to 60 percent brightness",
        )

        route = choose_route("turn them back on", source="pi-satellite-102", session_id="home-anchor-1c")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn on the kitchen lights")

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_followup_what_about_room_can_use_strong_home_session_context(
        self,
        mock_routing_cache,
        mock_room_cache,
    ) -> None:
        cache = {
            "rooms": [{"spoken_name": "dining room", "aliases": ["dining room"]}],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("what about the dining room", source="pi-satellite-102", session_id="home-anchor-2")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_followup_what_about_non_room_does_not_hijack_into_home_assistant(
        self,
        mock_routing_cache,
        mock_room_cache,
    ) -> None:
        cache = {
            "rooms": [{"spoken_name": "guest's room", "aliases": ["guest's room", "guest room"]}],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2d",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("what about weather", source="pi-satellite-102", session_id="home-anchor-2d")

        self.assertNotEqual(route.target, "home_assistant")

    def test_strong_home_context_preserves_explicit_cross_domain_routes(self) -> None:
        source = "desktop-satellite-110"
        session_id = "home-cross-domain-1"
        set_active_context(
            source,
            session_id,
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the lounge lights",
        )
        cases = (
            ("what about weather", "weather"),
            ("what about the calendar", "calendar"),
            ("play heroes by david bowie", "music"),
            ("resume my audiobook", "audiobook"),
            ("set a timer for ten minutes", "system"),
            ("what time is it", "system"),
            ("how is the network", "network"),
            ("give me the news", "news"),
            ("tell me about the moon", _fallback_target()),
        )

        for text, expected_target in cases:
            with self.subTest(text=text):
                route = choose_route(text, source=source, session_id=session_id)
                self.assertEqual(route.target, expected_target)

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_context_only_claims_resolvable_room_comparison(self, mock_routing_cache, mock_room_cache) -> None:
        cache = {
            "rooms": [{"spoken_name": "bedroom", "aliases": ["bedroom"]}],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        source = "desktop-satellite-110"
        session_id = "home-room-ambiguity-1"
        set_active_context(
            source,
            session_id,
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the lounge lights",
        )

        resolved = choose_route("what about the bedroom", source=source, session_id=session_id)
        unrelated = choose_route("what about weather", source=source, session_id=session_id)

        self.assertEqual(resolved.target, "home_assistant")
        self.assertEqual(resolved.normalized_text, "turn on the bedroom lights")
        self.assertEqual(unrelated.target, "weather")

    def test_home_followup_set_it_to_temperature_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-3",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
        )

        route = choose_route("set it to 72", source="pi-satellite-102", session_id="home-anchor-3")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "set it to 72")

    def test_home_followup_set_it_to_brightness_reuses_brightness_context_target(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-3b",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="set the kitchen lights to 60 percent brightness",
        )

        route = choose_route("set it to 72", source="pi-satellite-102", session_id="home-anchor-3b")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "set the kitchen lights to 72 percent brightness")

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_followup_what_about_room_reuses_brightness_context_template(
        self,
        mock_routing_cache,
        mock_room_cache,
    ) -> None:
        cache = {
            "rooms": [{"spoken_name": "lounge", "aliases": ["lounge"]}],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2b",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="set the kitchen lights to 60 percent brightness",
        )

        route = choose_route("what about the lounge", source="pi-satellite-102", session_id="home-anchor-2b")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "set the living room lights to 60 percent brightness")

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_followup_what_about_known_room_still_reuses_context(
        self,
        mock_routing_cache,
        mock_room_cache,
    ) -> None:
        cache = {
            "rooms": [
                {"spoken_name": "guest's room", "aliases": ["guest's room", "guest room"]},
                {"spoken_name": "bedroom", "aliases": ["bedroom"]},
            ],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2e",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("what about the bedroom", source="pi-satellite-102", session_id="home-anchor-2e")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn on the bedroom lights")

    @patch("oracle_app.room_context.vocabulary.load_home_assistant_cache")
    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_home_followup_room_switch_preserves_brightness_template_across_onoff_step(
        self,
        mock_routing_cache,
        mock_room_cache,
    ) -> None:
        cache = {
            "rooms": [{"spoken_name": "lounge", "aliases": ["lounge"]}],
            "entities": [],
        }
        mock_routing_cache.return_value = cache
        mock_room_cache.return_value = cache
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2c",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="set the kitchen lights to 60 percent brightness",
        )
        set_active_context(
            "pi-satellite-102",
            "home-anchor-2c",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the kitchen lights",
        )

        route = choose_route("what about the lounge", source="pi-satellite-102", session_id="home-anchor-2c")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "set the living room lights to 60 percent brightness")

    def test_home_followup_open_it_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-4",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
        )

        route = choose_route("open it", source="pi-satellite-102", session_id="home-anchor-4")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")

    def test_home_followup_and_turn_them_off_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-4b",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("and turn them off", source="pi-satellite-102", session_id="home-anchor-4b")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn off the guest's room lights")

    def test_home_followup_and_what_about_room_can_use_strong_home_session_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-4c",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("and what about the bedroom", source="pi-satellite-102", session_id="home-anchor-4c")

        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.reason, "Matched strong active session context")
        self.assertEqual(route.normalized_text, "turn on the bedroom lights")

    def test_home_followup_and_non_home_request_does_not_hijack_into_home_assistant(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "home-anchor-4d",
            route_target="home_assistant",
            dispatch_hook="home_assistant.execute",
            action="execute",
            anchor_strength="strong",
            context_text="turn on the guest's room lights",
        )

        route = choose_route("and what time is it", source="pi-satellite-102", session_id="home-anchor-4d")

        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched time query")

    def test_home_followup_does_not_use_weak_facts_context(self) -> None:
        set_active_context(
            "pi-satellite-102",
            "weak-home-anchor",
            route_target="facts",
            dispatch_hook="facts.lookup",
            action="facts_lookup",
            anchor_strength="weak",
        )

        route = choose_route("turn them off", source="pi-satellite-102", session_id="weak-home-anchor")

        self.assertEqual(route.target, _fallback_target())
        self.assertEqual(route.reason, "No deterministic capability matched")

    def test_reminder_query_routes_to_system(self) -> None:
        route = choose_route("remind me to check the oven in 10 minutes")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.reason, "Matched timer/alarm/reminder query")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_leading_oracle_prefix_is_removed_before_routing(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [
                {
                    "spoken_name": "guest's room",
                    "aliases": ["guest's room", "guest room"],
                }
            ]
        }
        route = choose_route("Oracle, turn off the guest room lights.")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "turn off the guest's room lights")

    @patch("oracle_app.routing_helpers.load_home_assistant_cache")
    def test_stt_variant_prefix_is_removed_before_routing(self, mock_cache) -> None:
        mock_cache.return_value = {
            "rooms": [
                {
                    "spoken_name": "guest's room",
                    "aliases": ["guest's room", "guest room"],
                }
            ]
        }
        route = choose_route("or go turn off the lights in the guest room")
        self.assertEqual(route.target, "home_assistant")
        self.assertEqual(route.normalized_text, "turn off the lights in the guest's room")

    def test_leading_oracle_prefix_is_removed_from_system_queries(self) -> None:
        route = choose_route("Oracle. What time is it?")
        self.assertEqual(route.target, "system")
        self.assertEqual(route.normalized_text, "what time is it")

    def test_normalize_text_strips_wake_word_fillers(self) -> None:
        self.assertEqual(normalize_text("Oracle, go turn on the lights"), "turn on the lights")

    def test_normalize_text_strips_leading_the_before_wake_word(self) -> None:
        self.assertEqual(normalize_text("the oracle resume music"), "resume music")

    def test_normalize_text_ignores_bracketed_nonverbal_descriptor(self) -> None:
        self.assertEqual(normalize_text("(eerie music)"), "")

    def test_normalize_text_ignores_bracketed_nonverbal_descriptor_after_wake_word_cleanup(self) -> None:
        self.assertEqual(normalize_text("Oracle, (eerie music)"), "")

    def test_normalize_text_ignores_any_fully_bracketed_nonverbal_transcript(self) -> None:
        self.assertEqual(normalize_text("(dramatic music)"), "")

    def test_normalize_text_ignores_repeated_wake_residue_loop(self) -> None:
        self.assertEqual(normalize_text("Oracle. Hey, Oracle."), "")

    def test_normalize_text_ignores_near_wake_repetition(self) -> None:
        self.assertEqual(normalize_text("Pay oracle. Pay oracle."), "")

    def test_normalize_text_ignores_single_near_wake_residue(self) -> None:
        self.assertEqual(normalize_text("A oracle."), "")

    def test_normalize_text_ignores_very_oracle_residue(self) -> None:
        self.assertEqual(normalize_text("Very Oracle"), "")

    def test_normalize_text_keeps_real_command_after_wake_word(self) -> None:
        self.assertEqual(normalize_text("Oracle, stop"), "stop")

    def test_normalize_text_separates_punctuated_words_after_wake_cleanup(self) -> None:
        self.assertEqual(
            normalize_text("oracle.resume.casey's audiobook."),
            "resume casey's audiobook",
        )

    def test_normalize_text_handles_adjacent_wake_and_command_variants(self) -> None:
        cases = [
            ("oracle, resume casey's audiobook", "resume casey's audiobook"),
            ("oracle.resume casey's audiobook", "resume casey's audiobook"),
            ("oracle resume.casey's audiobook", "resume casey's audiobook"),
            ("oracle... resume casey's audiobook", "resume casey's audiobook"),
            ("resume casey's audiobook.", "resume casey's audiobook"),
        ]

        for raw_text, expected in cases:
            with self.subTest(raw_text=raw_text):
                self.assertEqual(normalize_text(raw_text), expected)


if __name__ == "__main__":
    unittest.main()
