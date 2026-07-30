from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.conversation import get_home_assistant_conversation_id, set_home_assistant_conversation_id
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge, _STATE_VERIFICATION_ATTEMPTS
from oracle_app.session_state import clear_all_sessions


class HomeAssistantBridgeTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all_sessions()

    @patch("oracle_app.provider_bridges.home_assistant.request.urlopen")
    def test_execute_command_reuses_and_updates_conversation_id(self, mock_urlopen) -> None:
        captured_bodies: list[dict[str, object]] = []
        set_home_assistant_conversation_id("living_room_satellite", "home-session-1", "ha-old")

        class _FakeResponse:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "conversation_id": "ha-new",
                        "response": {"speech": {"plain": {"speech": "Done"}}},
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        def fake_urlopen(req, timeout=0):
            captured_bodies.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse()

        mock_urlopen.side_effect = fake_urlopen
        bridge = HomeAssistantBridge(base_url="http://ha.local", token="token")

        result = bridge.execute_command(
            "turn them off",
            source="living_room_satellite",
            session_id="home-session-1",
        )

        self.assertEqual(result.payload["conversation_id"], "ha-new")
        self.assertIsNone(result.verification_failure)
        self.assertEqual(captured_bodies[0]["conversation_id"], "ha-old")
        self.assertEqual(
            get_home_assistant_conversation_id("living_room_satellite", "home-session-1"),
            "ha-new",
        )

    def test_detect_failed_success_targets_returns_oracle_domain_error(self) -> None:
        bridge = HomeAssistantBridge(base_url="http://ha.local", token="token")
        payload = {
            "response": {
                "data": {
                    "success": [{"id": "light.guest_room", "name": "Guest Room"}],
                    "failed": [],
                }
            }
        }

        with patch.object(
            bridge,
            "fetch_entity_state_with_retry",
            return_value={
                "entity_id": "light.guest_room",
                "state": "unavailable",
                "attributes": {"friendly_name": "Guest Room"},
            },
        ):
            failure = bridge.detect_failed_success_targets(payload, command_text="turn on guest room lights")

        self.assertIsNotNone(failure)
        assert failure is not None
        self.assertEqual(failure["error"], "home_assistant_target_unavailable")
        self.assertEqual(failure["unavailable_targets"][0]["entity_id"], "light.guest_room")

    @patch("oracle_app.provider_bridges.home_assistant.time.sleep", return_value=None)
    def test_fetch_entity_state_with_retry_allows_slow_successful_light_updates(self, _mock_sleep) -> None:
        bridge = HomeAssistantBridge(base_url="http://ha.local", token="token")
        states = [
            {"entity_id": "light.bed", "state": "on", "attributes": {"friendly_name": "Bed Light"}},
            {"entity_id": "light.bed", "state": "on", "attributes": {"friendly_name": "Bed Light"}},
            {"entity_id": "light.bed", "state": "off", "attributes": {"friendly_name": "Bed Light"}},
        ]

        with patch.object(bridge, "fetch_entity_state", side_effect=states) as mock_fetch:
            state = bridge.fetch_entity_state_with_retry(
                "light.bed",
                expected_outcome={"kind": "state", "state": "off"},
            )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["state"], "off")
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("oracle_app.provider_bridges.home_assistant.time.sleep", return_value=None)
    def test_fetch_entity_state_with_retry_still_fails_after_bounded_retries(self, _mock_sleep) -> None:
        bridge = HomeAssistantBridge(base_url="http://ha.local", token="token")

        with patch.object(
            bridge,
            "fetch_entity_state",
            return_value={"entity_id": "light.bed", "state": "on", "attributes": {"friendly_name": "Bed Light"}},
        ) as mock_fetch:
            state = bridge.fetch_entity_state_with_retry(
                "light.bed",
                expected_outcome={"kind": "state", "state": "off"},
            )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["state"], "on")
        self.assertEqual(mock_fetch.call_count, _STATE_VERIFICATION_ATTEMPTS)

    @patch("oracle_app.provider_bridges.home_assistant.request.urlopen")
    def test_call_service_posts_provider_service_request(self, mock_urlopen) -> None:
        mock_urlopen.return_value.__enter__.return_value = object()
        bridge = HomeAssistantBridge(base_url="http://ha.local", token="token")

        bridge.call_service(
            service_domain="lock",
            service_name="unlock",
            entity_id="lock.entry_door",
        )

        request_obj = mock_urlopen.call_args.args[0]
        self.assertEqual(request_obj.full_url, "http://ha.local/api/services/lock/unlock")
        self.assertEqual(json.loads(request_obj.data.decode("utf-8")), {"entity_id": "lock.entry_door"})

    @patch("oracle_app.provider_bridges.home_assistant.request.urlopen")
    def test_typed_provider_timeout_applies_to_bridge_requests(self, mock_urlopen) -> None:
        mock_urlopen.return_value.__enter__.return_value = object()
        bridge = HomeAssistantBridge(
            base_url="http://ha.local",
            token="token",
            timeout_seconds=13,
        )

        bridge.call_service(
            service_domain="light",
            service_name="turn_on",
            entity_id="light.living_room",
        )

        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 13)


if __name__ == "__main__":
    unittest.main()
