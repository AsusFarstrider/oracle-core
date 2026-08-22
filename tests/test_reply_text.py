from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.replies import build_reply_text
from oracle_app.schemas import DispatchPlan


class ReplyTextTests(unittest.TestCase):
    def test_home_assistant_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "turn on lights"},
            status="executed",
            result={
                "response": {
                    "speech": {
                        "plain": {"speech": "Turned on the lights"}
                    }
                }
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Turned on the lights")

    def test_home_assistant_unavailable_target_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "turn on guest room lights"},
            status="failed",
            result={
                "error": "home_assistant_target_unavailable",
                "unavailable_targets": [{"entity_id": "light.guest_room", "name": "Guest Room"}],
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Guest Room is unavailable right now.")

    def test_home_assistant_state_verification_failed_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "turn on guest room lights"},
            status="failed",
            result={
                "error": "home_assistant_state_verification_failed",
                "verification_failed_targets": [
                    {
                        "entity_id": "light.guest_room",
                        "name": "Guest Room",
                        "state": "off",
                        "expected_state": "on",
                    }
                ],
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Guest Room did not turn on.")

    def test_home_assistant_lock_state_verification_failed_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "unlock the side entry"},
            status="failed",
            result={
                "error": "home_assistant_state_verification_failed",
                "verification_failed_targets": [
                    {
                        "entity_id": "lock.side_entry",
                        "name": "Side Entry",
                        "state": "locked",
                        "expected_state": "unlocked",
                    }
                ],
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Side Entry did not unlock.")

    def test_home_assistant_climate_state_verification_failed_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "set the thermostat in the bedroom to 68"},
            status="failed",
            result={
                "error": "home_assistant_state_verification_failed",
                "verification_failed_targets": [
                    {
                        "entity_id": "climate.bedroom",
                        "name": "Bedroom Thermostat",
                        "state": "heat",
                        "expected_attribute": "temperature",
                        "expected_description": "68 degrees",
                    }
                ],
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Bedroom Thermostat did not reach 68 degrees.")

    def test_home_assistant_brightness_state_verification_failed_reply(self) -> None:
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "set the kitchen lights to 75 percent brightness"},
            status="failed",
            result={
                "error": "home_assistant_state_verification_failed",
                "verification_failed_targets": [
                    {
                        "entity_id": "light.kitchen",
                        "name": "Kitchen",
                        "state": "on",
                        "expected_attribute": "brightness",
                        "expected_description": "75 percent brightness",
                    }
                ],
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Kitchen did not reach 75 percent brightness.")

    def test_weather_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.current_weather",
            payload={"action": "current_weather"},
            status="executed",
            result={"action": "current_weather", "speech": "It is 41 degrees."},
        )
        self.assertEqual(build_reply_text(dispatch), "It is 41 degrees.")

    def test_network_reply(self) -> None:
        dispatch = DispatchPlan(
            target="network",
            hook="network.execute",
            payload={"action": "network_summary"},
            status="executed",
            result={"action": "network_summary", "speech": "The network looks healthy."},
        )
        self.assertEqual(build_reply_text(dispatch), "The network looks healthy.")

    def test_system_ignore_reply_is_silent(self) -> None:
        dispatch = DispatchPlan(
            target="system",
            hook="system.ignore",
            payload={"action": "ignore"},
            status="executed",
            result={"action": "ignore", "ignored": True},
        )
        self.assertEqual(build_reply_text(dispatch), "")

    def test_system_time_date_reply(self) -> None:
        dispatch = DispatchPlan(
            target="system",
            hook="system.current_time_date",
            payload={"action": "current_time_date"},
            status="executed",
            result={
                "action": "current_time_date",
                "speech": "It is 3:09 PM on Saturday, March 14, 2026.",
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "It is 3:09 PM on Saturday, March 14, 2026.",
        )

    def test_weather_forecast_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.weather_forecast",
            payload={"action": "weather_forecast"},
            status="executed",
            result={
                "action": "weather_forecast",
                "speech": "Tomorrow will be sunny with a high near 58.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Tomorrow will be sunny with a high near 58.")

    def test_remote_weather_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.remote_current_weather",
            payload={"action": "remote_current_weather"},
            status="executed",
            result={
                "action": "remote_current_weather",
                "speech": "In Boston, MA, it is currently 39 degrees.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "In Boston, MA, it is currently 39 degrees.")

    def test_remote_forecast_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.remote_weather_forecast",
            payload={"action": "remote_weather_forecast"},
            status="executed",
            result={
                "action": "remote_weather_forecast",
                "speech": "In Boston, MA, tomorrow will be sunny with a high near 54.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "In Boston, MA, tomorrow will be sunny with a high near 54.")

    def test_forecast_out_of_range_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.weather_forecast",
            payload={"action": "weather_forecast"},
            status="failed",
            result={
                "error": "forecast_out_of_range",
                "detail": "That time is outside the current forecast window.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "That time is outside the current forecast window.")

    def test_remote_forecast_out_of_range_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.remote_weather_forecast",
            payload={"action": "remote_weather_forecast"},
            status="failed",
            result={
                "error": "remote_forecast_out_of_range",
                "detail": "That time is outside the current forecast window.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "That time is outside the current forecast window.")

    def test_calendar_find_event_reply(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "when is church service and breakfast"},
            status="executed",
            result={
                "action": "find_event",
                "query": "church service and breakfast",
                "events": [
                    {
                        "summary": "Church service and breakfast",
                        "start": "2026-04-05T08:00:00-04:00",
                        "end": "2026-04-05T12:00:00-04:00",
                        "all_day": False,
                        "location": "",
                        "timezone": "America/New_York",
                    }
                ],
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "Church service and breakfast is on Sunday at 8 AM.",
        )

    def test_calendar_find_event_not_found_reply(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "when is oracle soak calendar test"},
            status="executed",
            result={"action": "find_event", "query": "oracle soak calendar test", "events": [], "not_found": True},
        )
        self.assertEqual(build_reply_text(dispatch), "I couldn't find that on your calendar.")

    def test_weather_history_reply(self) -> None:
        dispatch = DispatchPlan(
            target="weather",
            hook="weather.weather_history",
            payload={"action": "weather_history"},
            status="executed",
            result={
                "action": "weather_history",
                "speech": "On Friday, April 3, 2026, the temperature ranged from 42 to 63 degrees.",
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "On Friday, April 3, 2026, the temperature ranged from 42 to 63 degrees.",
        )

    def test_system_calculation_reply(self) -> None:
        dispatch = DispatchPlan(
            target="system",
            hook="system.calculation",
            payload={"action": "calculation"},
            status="executed",
            result={
                "action": "calculation",
                "speech": "The answer is 4.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "The answer is 4.")

    def test_system_alerts_reply(self) -> None:
        dispatch = DispatchPlan(
            target="system",
            hook="system.alerts",
            payload={"action": "alerts"},
            status="executed",
            result={
                "action": "alerts",
                "speech": "Timer set for 5 minutes.",
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Timer set for 5 minutes.")

    def test_failed_reply(self) -> None:
        dispatch = DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "x"},
            status="failed",
            result={"facts_status": "provider_error"},
        )
        self.assertEqual(build_reply_text(dispatch), "I couldn't look that up right now.")

    def test_facts_reply_selects_lifespan_sentence(self) -> None:
        dispatch = DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "How long do sloths live for?"},
            status="executed",
            result={
                "facts_status": "answered",
                "query": "How long do sloths live for?",
                "answer": {
                    "text": (
                        "Sloths are a Neotropical group of arboreal xenarthran mammals. "
                        "Sloths can live for about 20 years in the wild and longer in human care. "
                        "They are noted for slow movement."
                    )
                },
                "summarized_by_model": False,
            },
        )

        self.assertEqual(
            build_reply_text(dispatch),
            "Sloths can live for about 20 years in the wild and longer in human care.",
        )

    def test_facts_reply_preserves_initials_when_splitting_sentences(self) -> None:
        dispatch = DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Who wrote The Hobbit?"},
            status="executed",
            result={
                "facts_status": "answered",
                "query": "Who wrote The Hobbit?",
                "answer": {
                    "text": "The Hobbit is a children's fantasy novel by J. R. R. Tolkien. It was published in 1937."
                },
                "summarized_by_model": False,
            },
        )

        self.assertEqual(
            build_reply_text(dispatch),
            "The Hobbit is a children's fantasy novel by J. R. R. Tolkien.",
        )

    def test_facts_reply_trims_location_fallback_sentence(self) -> None:
        dispatch = DispatchPlan(
            target="facts",
            hook="facts.lookup",
            payload={"query": "Where is Machu Picchu?"},
            status="executed",
            result={
                "facts_status": "answered",
                "query": "Where is Machu Picchu?",
                "answer": {
                    "text": (
                        "Machu Picchu is a 15th-century Inca citadel located in the Eastern Cordillera of southern Peru "
                        "on a mountain ridge at 2,430 meters. It is northwest of Cusco."
                    )
                },
                "summarized_by_model": False,
            },
        )

        self.assertEqual(
            build_reply_text(dispatch),
            "Machu Picchu is a 15th-century Inca citadel located in the Eastern Cordillera of southern Peru.",
        )

    def test_calendar_reply_states_count_and_lists_all_events(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "what's on my calendar tomorrow"},
            status="executed",
            result={
                "action": "list_events",
                "events": [
                    {"summary": "Event one", "start": "2026-03-16T11:00:00-04:00", "all_day": False},
                    {"summary": "Event two", "start": "2026-03-16T14:30:00-04:00", "all_day": False},
                    {"summary": "Event three", "start": "2026-03-16T17:30:00-04:00", "all_day": False},
                    {"summary": "Event four", "start": "2026-03-16T19:00:00-04:00", "all_day": False},
                ],
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "You have 4 things on your calendar. At 11 AM, Event one. At 2:30 PM, Event two. At 5:30 PM, Event three. At 7 PM, Event four.",
        )

    def test_calendar_unspeakable_events_does_not_crash(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "what's on my calendar tomorrow"},
            status="executed",
            result={
                "action": "list_events",
                "events": [
                    {"summary": "", "start": "2026-03-16T11:00:00-04:00", "all_day": False},
                    {"summary": "   ", "start": "2026-03-16T14:30:00-04:00", "all_day": False},
                ],
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "I couldn't find anything speakable on your calendar for that time.",
        )

    def test_calendar_all_day_event_sounds_natural(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "what's on my calendar tomorrow"},
            status="executed",
            result={
                "action": "list_events",
                "events": [
                    {"summary": "Work onsite", "start": "2026-03-17T00:00:00-04:00", "all_day": True},
                    {"summary": "Gymnastics", "start": "2026-03-17T16:30:00-04:00", "all_day": False},
                ],
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "You have 2 things on your calendar. All day, Work onsite. At 4:30 PM, Gymnastics.",
        )

    def test_calendar_write_unavailable_reply(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.commit_event",
            payload={"action": "commit_event"},
            status="failed",
            result={"error": "calendar_write_unavailable"},
        )
        self.assertEqual(build_reply_text(dispatch), "I can't add calendar events right now.")

    def test_calendar_dense_day_truncates_long_titles_without_omitting_events(self) -> None:
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.execute",
            payload={"text": "what's on my calendar today"},
            status="executed",
            result={
                "action": "list_events",
                "events": [
                    {
                        "summary": "Quarterly planning meeting with the regional operations team",
                        "start": "2026-03-17T08:00:00-04:00",
                        "all_day": False,
                    },
                    {
                        "summary": "Very long event title that should still stay concise in spoken form",
                        "start": "2026-03-17T13:30:00-04:00",
                        "all_day": False,
                    },
                ],
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "You have 2 things on your calendar. At 8 AM, Quarterly planning meeting with the regional.... At 1:30 PM, Very long event title that should....",
        )

    def test_failed_music_play_does_not_sound_successful(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "play hamilton"},
            status="failed",
            result={
                "action": "play",
                "error": "satellite_command_failed",
                "selected": {"title": "Hamilton", "artist": "Lin-Manuel Miranda"},
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "I couldn't reach the playback satellite.",
        )

    def test_audiobook_resume_reply(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={"text": "resume my book"},
            status="executed",
            result={
                "action": "play",
                "selected": {
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "start_position_seconds": 812.0,
                },
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Resuming Dune by Frank Herbert.")

    def test_audiobook_now_playing_reply(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={"text": "what audiobook is playing"},
            status="executed",
            result={
                "action": "what_is_playing",
                "now_playing": {
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "state": "playing",
                },
            },
        )
        self.assertEqual(build_reply_text(dispatch), "You're listening to Dune by Frank Herbert.")

    def test_audiobook_series_lookup_reply(self) -> None:
        dispatch = DispatchPlan(
            target="audiobook",
            hook="audiobook.execute",
            payload={"text": "what book 3 of the harry potter series was"},
            status="executed",
            result={
                "action": "series_lookup",
                "ordinal": 3,
                "match": {
                    "title": "Harry Potter and the Prisoner of Azkaban, Book 3",
                    "author": "J.K. Rowling",
                },
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "Book 3 is Harry Potter and the Prisoner of Azkaban, Book 3 by J.K. Rowling.",
        )

    def test_music_artist_play_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "play songs by david bowie"},
            status="executed",
            result={
                "action": "play",
                "selected": {
                    "type": "artist",
                    "title": "David Bowie",
                    "artist": "David Bowie",
                },
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Playing songs by David Bowie.")

    def test_music_album_play_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "play music from hamilton"},
            status="executed",
            result={
                "action": "play",
                "selected": {
                    "type": "album",
                    "title": "Hamilton: An American Musical",
                    "artist": "Lin-Manuel Miranda",
                },
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "Playing the album Hamilton: An American Musical by Lin-Manuel Miranda.",
        )

    def test_music_lookup_album_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "what album is thunderstruck on"},
            status="executed",
            result={
                "action": "lookup_album",
                "selected": {
                    "title": "Thunderstruck",
                    "artist": "AC/DC",
                    "album": "The Razors Edge",
                },
            },
        )
        self.assertEqual(
            build_reply_text(dispatch),
            "Thunderstruck is on The Razors Edge by AC/DC.",
        )

    def test_music_playlist_play_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "play my road trip playlist"},
            status="executed",
            result={
                "action": "play",
                "selected": {
                    "type": "playlist",
                    "title": "Road Trip",
                },
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Playing the playlist Road Trip.")

    def test_music_stop_dual_active_degraded_fallback_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "stop"},
            status="executed",
            result={
                "action": "stop",
                "degraded_state_fallback": "dual_active_stop_all",
                "satellite": {"ok": True, "state": "stopped"},
                "longform": {"ok": True, "state": "stopped"},
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Stopping all active media.")

    def test_music_pause_dual_active_degraded_fallback_reply(self) -> None:
        dispatch = DispatchPlan(
            target="music",
            hook="music.execute",
            payload={"text": "pause"},
            status="executed",
            result={
                "action": "pause",
                "degraded_state_fallback": "dual_active_pause_all",
                "satellite": {"ok": True, "state": "paused"},
                "longform": {"ok": True, "state": "paused"},
            },
        )
        self.assertEqual(build_reply_text(dispatch), "Pausing all active media.")


if __name__ == "__main__":
    unittest.main()
