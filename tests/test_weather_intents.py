from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.weather_intents import (
    build_weather_hook,
    classify_weather_intent,
    detect_current_weather_query,
    detect_forecast_weather_query,
)


class WeatherIntentTests(unittest.TestCase):
    def test_current_weather_detection_matches_now_query(self) -> None:
        self.assertTrue(detect_current_weather_query("what's the weather right now"))

    def test_current_weather_detection_rejects_forecast_language(self) -> None:
        self.assertFalse(detect_current_weather_query("what is the weather tomorrow"))

    def test_forecast_detection_matches_future_weather_query(self) -> None:
        self.assertTrue(detect_forecast_weather_query("what is the weather tomorrow"))

    def test_forecast_detection_matches_weekday_weather_query(self) -> None:
        self.assertTrue(detect_forecast_weather_query("what is the weather on tuesday"))

    def test_forecast_detection_matches_weekday_night_query(self) -> None:
        self.assertTrue(detect_forecast_weather_query("what is the weather tuesday night"))

    def test_classify_weather_intent_current_weather(self) -> None:
        intent = classify_weather_intent("what is the wind")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "current_weather")
        self.assertEqual(intent.reason, "Matched weather query")

    def test_classify_weather_intent_forecast(self) -> None:
        intent = classify_weather_intent("what is the weather tomorrow")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "weather_forecast")
        self.assertEqual(intent.reason, "Matched forecast query")

    def test_classify_weather_intent_weekday_forecast(self) -> None:
        intent = classify_weather_intent("what is the weather on tuesday")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "weather_forecast")
        self.assertEqual(intent.reason, "Matched forecast query")

    def test_classify_weather_intent_weekday_night_forecast(self) -> None:
        intent = classify_weather_intent("what is the weather tuesday night")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "weather_forecast")
        self.assertEqual(intent.reason, "Matched forecast query")

    def test_classify_weather_intent_history(self) -> None:
        intent = classify_weather_intent("what was the weather yesterday")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "weather_history")
        self.assertEqual(intent.reason, "Matched historical weather query")

    def test_classify_weather_intent_remote_current_weather(self) -> None:
        intent = classify_weather_intent("what is the weather in boston")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "remote_current_weather")
        self.assertEqual(intent.reason, "Matched remote weather query")

    def test_classify_weather_intent_remote_forecast(self) -> None:
        intent = classify_weather_intent("what is the weather tomorrow in boston")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "remote_weather_forecast")
        self.assertEqual(intent.reason, "Matched remote forecast query")

    def test_classify_weather_intent_remote_practical_forecast(self) -> None:
        intent = classify_weather_intent("do i need a coat in boston tomorrow")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "remote_weather_forecast")
        self.assertEqual(intent.reason, "Matched remote forecast query")

    def test_classify_weather_intent_location_first_remote_forecast(self) -> None:
        intent = classify_weather_intent("what will boston weather be tomorrow")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "remote_weather_forecast")
        self.assertEqual(intent.reason, "Matched remote forecast query")

    def test_classify_weather_intent_routes_speech_disfluency_to_local_forecast(self) -> None:
        intent = classify_weather_intent("what it is is the weather tomorrow")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "weather_forecast")
        self.assertEqual(intent.reason, "Matched forecast query")

    def test_classify_weather_intent_non_weather_text(self) -> None:
        self.assertIsNone(classify_weather_intent("tell me a short joke"))

    def test_build_weather_hook_maps_known_actions(self) -> None:
        self.assertEqual(build_weather_hook("current_weather"), "weather.current_weather")
        self.assertEqual(build_weather_hook("remote_current_weather"), "weather.remote_current_weather")
        self.assertEqual(build_weather_hook("weather_forecast"), "weather.weather_forecast")
        self.assertEqual(build_weather_hook("remote_weather_forecast"), "weather.remote_weather_forecast")
        self.assertEqual(build_weather_hook("weather_history"), "weather.weather_history")


if __name__ == "__main__":
    unittest.main()
