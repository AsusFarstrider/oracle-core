from __future__ import annotations

import sys
import unittest
import importlib.util
from pathlib import Path


REPLIES_PATH = Path(__file__).resolve().parents[1] / "satellite" / "pi_runtime" / "replies.py"
SPEC = importlib.util.spec_from_file_location("pi_runtime_replies", REPLIES_PATH)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
extract_spoken_reply = MODULE.extract_spoken_reply


class SatelliteReplyContractTests(unittest.TestCase):
    def test_uses_reply_text_for_finite_status(self) -> None:
        payload = {
            "reply_text": "Playing Dune by Frank Herbert.",
            "status": "executed",
            "effects": {
                "follow_up": None, "satellite_playback": None,
                "deferred_satellite_playback": None, "ui_presentation": None,
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "Playing Dune by Frank Herbert.")

    def test_nonignored_status_rejects_missing_reply(self) -> None:
        payload = {
            "reply_text": "",
            "status": "pending_confirmation",
            "effects": {
                "follow_up": {"expected": True, "kind": "confirmation"},
                "satellite_playback": None, "deferred_satellite_playback": None,
                "ui_presentation": None,
            },
        }

        with self.assertRaisesRegex(RuntimeError, "without reply text"):
            extract_spoken_reply(payload)

    def test_unknown_status_is_rejected(self) -> None:
        payload = {
            "reply_text": "Maybe.",
            "status": "pending_integration",
            "effects": {
                "follow_up": None, "satellite_playback": None,
                "deferred_satellite_playback": None, "ui_presentation": None,
            },
        }

        with self.assertRaisesRegex(RuntimeError, "unknown conversation status"):
            extract_spoken_reply(payload)

    def test_ignore_stays_silent_when_reply_text_missing(self) -> None:
        payload = {
            "reply_text": "",
            "status": "ignored",
            "effects": {
                "follow_up": None, "satellite_playback": None,
                "deferred_satellite_playback": None, "ui_presentation": None,
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "")

    def test_effect_vocabulary_is_exact(self) -> None:
        payload = {
            "reply_text": "Could not play that.",
            "status": "failed",
            "effects": {"follow_up": None, "unexpected": {}},
        }

        with self.assertRaisesRegex(RuntimeError, "invalid conversation effects"):
            extract_spoken_reply(payload)

    def test_effects_must_be_an_object(self) -> None:
        payload = {
            "reply_text": "Done.",
            "status": "executed",
            "effects": [],
        }

        with self.assertRaisesRegex(RuntimeError, "invalid conversation effects"):
            extract_spoken_reply(payload)


if __name__ == "__main__":
    unittest.main()
