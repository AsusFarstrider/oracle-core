from __future__ import annotations

import sys
import types
import unittest
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "wake_tuning.py"
SPEC = importlib.util.spec_from_file_location("pi_runtime_wake_tuning", MODULE_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("pi_runtime_wake_tuning", MODULE)
SPEC.loader.exec_module(MODULE)
WAKE_MODE_IDLE = MODULE.WAKE_MODE_IDLE
WAKE_MODE_PLAYBACK = MODULE.WAKE_MODE_PLAYBACK
classify_duck_stage = MODULE.classify_duck_stage
get_wake_profile = MODULE.get_wake_profile
resolve_effective_playback_active = MODULE.resolve_effective_playback_active


class WakeTuningTests(unittest.TestCase):
    def _args(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            wake_threshold=0.07,
            wake_log_threshold=0.08,
            wake_playback_threshold=0.055,
            wake_playback_log_threshold=0.06,
            wake_playback_consecutive_frames=2,
        )

    def test_get_wake_profile_uses_idle_profile(self) -> None:
        profile = get_wake_profile(self._args(), playback_active=False)

        self.assertEqual(profile.mode, WAKE_MODE_IDLE)
        self.assertEqual(profile.wake_threshold, 0.07)
        self.assertEqual(profile.required_consecutive_frames, 1)

    def test_get_wake_profile_uses_playback_profile(self) -> None:
        profile = get_wake_profile(self._args(), playback_active=True)

        self.assertEqual(profile.mode, WAKE_MODE_PLAYBACK)
        self.assertEqual(profile.wake_threshold, 0.055)
        self.assertEqual(profile.required_consecutive_frames, 2)

    def test_classify_duck_stage_uses_fixed_trigger_threshold(self) -> None:
        self.assertIsNone(classify_duck_stage(0.039, trigger_threshold=0.04))
        self.assertEqual(classify_duck_stage(0.04, trigger_threshold=0.04), 3)
        self.assertEqual(classify_duck_stage(0.08, trigger_threshold=0.04), 3)

    def test_resolve_effective_playback_active_holds_brief_idle_transition(self) -> None:
        effective, hold_until = resolve_effective_playback_active(
            raw_playback_active=True,
            previous_effective_playback_active=False,
            previous_hold_until=0.0,
            now=10.0,
            hold_seconds=1.25,
        )
        self.assertTrue(effective)
        self.assertEqual(hold_until, 11.25)

        effective, hold_until = resolve_effective_playback_active(
            raw_playback_active=False,
            previous_effective_playback_active=True,
            previous_hold_until=11.25,
            now=10.5,
            hold_seconds=1.25,
        )
        self.assertTrue(effective)
        self.assertEqual(hold_until, 11.25)

        effective, hold_until = resolve_effective_playback_active(
            raw_playback_active=False,
            previous_effective_playback_active=True,
            previous_hold_until=11.25,
            now=12.0,
            hold_seconds=1.25,
        )
        self.assertFalse(effective)
        self.assertEqual(hold_until, 0.0)


if __name__ == "__main__":
    unittest.main()
