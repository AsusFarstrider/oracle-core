from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.system_intents import (
    build_system_hook,
    classify_system_intent,
    system_action_requires_text,
)


class SystemIntentTests(unittest.TestCase):
    def test_empty_text_classifies_as_ignore(self) -> None:
        intent = classify_system_intent("")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "ignore")
        self.assertEqual(intent.reason, "Ignored empty transcript after wake-word cleanup")
        self.assertEqual(intent.confidence, 1.0)

    def test_confirm_classifies_as_confirm_pending(self) -> None:
        intent = classify_system_intent("confirm")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "confirm_pending")
        self.assertEqual(intent.reason, "Matched internal confirmation command")

    def test_start_over_classifies_as_cancel_pending(self) -> None:
        intent = classify_system_intent("start over")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "cancel_pending")
        self.assertEqual(intent.reason, "Matched internal cancel command")

    def test_forget_it_classifies_as_cancel_pending(self) -> None:
        intent = classify_system_intent("forget it")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "cancel_pending")
        self.assertEqual(intent.reason, "Matched internal cancel command")

    def test_stop_does_not_classify_as_cancel_pending(self) -> None:
        self.assertIsNone(classify_system_intent("stop"))

    def test_time_and_date_classifies_as_combined_clock_query(self) -> None:
        intent = classify_system_intent("what time and date is it")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "current_time_date")
        self.assertEqual(intent.reason, "Matched time/date query")

    def test_unit_conversion_classifies_as_calculation(self) -> None:
        intent = classify_system_intent("convert 10 miles to kilometers")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "calculation")
        self.assertEqual(intent.reason, "Matched unit conversion query")
        self.assertEqual(intent.confidence, 0.92)

    def test_date_calculation_classifies_as_calculation(self) -> None:
        intent = classify_system_intent("how many days until christmas")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "calculation")
        self.assertEqual(intent.reason, "Matched date calculation query")
        self.assertEqual(intent.confidence, 0.91)

    def test_home_assistant_devices_and_rooms_cache_update_is_refresh_cache(self) -> None:
        intent = classify_system_intent("update your cache of devices and rooms from home assistant")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.action, "refresh_cache")
        self.assertEqual(intent.reason, "Matched internal cache refresh command")

    def test_update_does_not_match_date_query_by_substring(self) -> None:
        self.assertIsNone(classify_system_intent("update your local notes"))

    def test_non_system_text_returns_none(self) -> None:
        self.assertIsNone(classify_system_intent("tell me a short joke"))

    def test_build_system_hook_maps_known_actions(self) -> None:
        self.assertEqual(build_system_hook("alerts"), "system.alerts")
        self.assertEqual(build_system_hook("refresh_cache"), "system.refresh_cache")

    def test_system_action_requires_text_only_for_textual_actions(self) -> None:
        self.assertTrue(system_action_requires_text("calculation"))
        self.assertFalse(system_action_requires_text("confirm_pending"))
        self.assertFalse(system_action_requires_text("refresh_cache"))


if __name__ == "__main__":
    unittest.main()
