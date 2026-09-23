from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import ModuleType, SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_stub.__all__ = []
python_multipart_stub.__author__ = ""
python_multipart_stub.__copyright__ = ""
python_multipart_stub.__license__ = ""
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from oracle_app.conversation import (
    append_turn,
    build_ollama_prompt,
    clear_all_conversations,
    clear_conversation,
    get_conversation,
    get_home_assistant_conversation_id,
    set_home_assistant_conversation_id,
    should_include_ollama_history,
)
from oracle_app.handlers.home_assistant import execute_home_assistant
from oracle_app.configuration.domain_models import HomeAssistantObjectMapping
from oracle_app.schemas import DispatchPlan


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class ConversationTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all_conversations()

    def test_build_ollama_prompt_uses_recent_history(self) -> None:
        source = "test_satellite_bravo"
        session_id = "session-1"
        append_turn(source, session_id, "user", "Turn on the kitchen lights")
        append_turn(source, session_id, "assistant", "Turned on the kitchen lights.")

        prompt = build_ollama_prompt(source, session_id, "What about the dining room?")

        self.assertIn("Recent conversation:", prompt)
        self.assertIn("User: Turn on the kitchen lights", prompt)
        self.assertIn("Oracle: Turned on the kitchen lights.", prompt)
        self.assertIn("User: What about the dining room?", prompt)

    def test_build_ollama_prompt_skips_history_for_new_topic(self) -> None:
        source = "test_satellite_bravo"
        session_id = "session-new-topic"
        append_turn(source, session_id, "user", "What is Minecraft?")
        append_turn(source, session_id, "assistant", "I'm not sure what you're asking about Minecraft.")

        prompt = build_ollama_prompt(source, session_id, "What is a llama?")

        self.assertEqual(prompt, "What is a llama?")

    def test_should_include_ollama_history_only_for_followups(self) -> None:
        self.assertTrue(should_include_ollama_history("What about the dining room?"))
        self.assertTrue(should_include_ollama_history("How does that work?"))
        self.assertFalse(should_include_ollama_history("What is a llama?"))
        self.assertFalse(should_include_ollama_history("Tell me about Minecraft"))

    def test_get_conversation_returns_snapshot_copy(self) -> None:
        source = "test_satellite_bravo"
        session_id = "session-copy"
        append_turn(source, session_id, "user", "Turn on the kitchen lights")

        snapshot = get_conversation(source, session_id)

        assert snapshot is not None
        snapshot["history"].append({"role": "assistant", "text": "Injected"})
        fresh_snapshot = get_conversation(source, session_id)

        assert fresh_snapshot is not None
        self.assertEqual(len(fresh_snapshot["history"]), 1)
        self.assertEqual(fresh_snapshot["history"][0]["text"], "Turn on the kitchen lights")

    def test_clear_conversation_removes_session_state(self) -> None:
        source = "test_satellite_bravo"
        session_id = "session-clear"
        append_turn(source, session_id, "user", "Turn on the kitchen lights")
        set_home_assistant_conversation_id(source, session_id, "ha-123")

        cleared = clear_conversation(source, session_id)

        self.assertTrue(cleared)
        self.assertIsNone(get_conversation(source, session_id))
        self.assertIsNone(get_home_assistant_conversation_id(source, session_id))

    @patch("oracle_app.home_assistant_actions.HomeAssistantBridge")
    def test_home_assistant_mutation_uses_finite_mapping_not_conversation(self, bridge_type) -> None:
        set_home_assistant_conversation_id("source-a", "session-a", "ha-a")
        set_home_assistant_conversation_id("source-b", "session-b", "ha-b")
        bridge_type.return_value.wait_for_entity_state.return_value = {"state": "on"}
        dispatch = DispatchPlan(
            target="home_assistant",
            hook="home_assistant.execute",
            payload={"text": "turn on the porch light", "source": "source-b", "session_id": "session-b"},
            status="planned",
        )

        result = execute_home_assistant(dispatch, home_assistant_settings=_home_assistant_settings())

        self.assertEqual(result.status, "executed")
        bridge_type.return_value.set_power.assert_called_once_with(
            entity_id="light.porch", enabled=True
        )
        self.assertEqual(get_home_assistant_conversation_id("source-a", "session-a"), "ha-a")
        self.assertEqual(get_home_assistant_conversation_id("source-b", "session-b"), "ha-b")
        self.assertEqual(get_home_assistant_conversation_id(None, "session-b"), None)

    def test_home_assistant_conversation_id_is_isolated_by_source_plus_session(self) -> None:
        set_home_assistant_conversation_id("source-a", "shared", "ha-a")
        set_home_assistant_conversation_id("source-b", "shared", "ha-b")

        self.assertEqual(get_home_assistant_conversation_id("source-a", "shared"), "ha-a")
        self.assertEqual(get_home_assistant_conversation_id("source-b", "shared"), "ha-b")

def _home_assistant_settings():
    mapping = HomeAssistantObjectMapping(
        kind="action", oracle_id="porch", entity_id="light.porch", allowed_operations=["turn_on"]
    )
    return SimpleNamespace(
        enabled=True,
        base_url="http://ha.local",
        credential="token",
        timeout_seconds=5,
        mappings={"porch_on": mapping},
        callable_alias_collisions=frozenset(),
        mapping=lambda mapping_id: mapping if mapping_id == "porch_on" else None,
    )


if __name__ == "__main__":
    unittest.main()
