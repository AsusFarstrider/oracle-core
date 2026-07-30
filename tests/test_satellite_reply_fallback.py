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


class SatelliteReplyFallbackTests(unittest.TestCase):
    def test_uses_reply_text_when_present(self) -> None:
        payload = {
            "reply_text": "Playing Dune by Frank Herbert.",
            "dispatch": {
                "target": "audiobook",
                "status": "executed",
                "result": {"action": "play"},
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "Playing Dune by Frank Herbert.")

    def test_pending_confirmation_uses_prompt_when_reply_text_missing(self) -> None:
        payload = {
            "reply_text": "",
            "dispatch": {
                "target": "system",
                "status": "pending_confirmation",
                "result": {"prompt": "Please confirm before I proceed."},
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "Please confirm before I proceed.")

    def test_pending_clarification_uses_prompt_when_reply_text_missing(self) -> None:
        payload = {
            "dispatch": {
                "target": "music",
                "status": "pending_clarification",
                "result": {"prompt": "I found multiple matches. Which one did you want?"},
            },
        }

        self.assertEqual(
            extract_spoken_reply(payload),
            "I found multiple matches. Which one did you want?",
        )

    def test_ignore_stays_silent_when_reply_text_missing(self) -> None:
        payload = {
            "dispatch": {
                "target": "system",
                "status": "executed",
                "result": {"action": "ignore"},
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "")

    def test_failed_request_uses_generic_failure_when_reply_text_missing(self) -> None:
        payload = {
            "dispatch": {
                "target": "music",
                "status": "failed",
                "result": {"action": "play", "error": "satellite_command_failed"},
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "I couldn't complete that request.")

    def test_success_without_reply_text_uses_minimal_done_fallback(self) -> None:
        payload = {
            "dispatch": {
                "target": "calendar",
                "status": "executed",
                "result": {
                    "action": "list_events",
                    "events": [
                        {"summary": "Event one"},
                        {"summary": "Event two"},
                    ],
                },
            },
        }

        self.assertEqual(extract_spoken_reply(payload), "Done.")


if __name__ == "__main__":
    unittest.main()
