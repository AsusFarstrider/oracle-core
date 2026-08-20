from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.orchestration_routine_routes import cancel_routine_run, run_routine
from oracle_app.schemas import UiRoutineCancelRequest, UiRoutineRunRequest
from oracle_app.ui_context import handle_pending_ui_context


ROUTINE = {
    "id": "child_bedtime",
    "enabled": True,
    "source_ids": ["satellite-child"],
    "triggers": {"ui": True},
}


class OrchestrationRoutineRouteTests(unittest.TestCase):
    @patch("oracle_app.orchestration_routine_routes.state.store_pending_ui_context", return_value=True)
    def test_run_control_prompts_for_declared_spoken_duration(self, mock_store) -> None:
        definition = {
            "id": "child_bedtime_prompt", "display_name": "Child Bedtime", "enabled": True,
            "source_ids": ["satellite-child"], "triggers": {"ui": True}, "steps": [],
            "inputs": {"delay": {"type": "integer", "default": 1200, "minimum": 0, "maximum": 14400, "spoken_duration": True, "no_timer_value": 0, "prompt": "How long would you like the timer to be?"}},
        }
        execution = Mock()
        execution.definition_payload.return_value = definition

        result = run_routine(
            "child_bedtime_prompt",
            UiRoutineRunRequest(client_id="ui", source="satellite-child", ui_session_id="session", inputs={}),
            routine_execution=execution,
            canonical_authority=True,
        )

        self.assertTrue(result["pending_input"])
        self.assertEqual(result["prompt"], "How long would you like the timer to be?")
        execution.start.assert_not_called()
        self.assertEqual(mock_store.call_args.args[:2], ("satellite-child", "session"))

    @patch("oracle_app.ui_context.state.clear_pending_ui_context")
    @patch("oracle_app.ui_context.state.load_pending_ui_context")
    def test_spoken_duration_and_no_timer_continue_the_prompted_routine(self, load, clear) -> None:
        pending = {
            "action": "routine_input", "client_id": "child-ui", "target_source_id": "child-room",
            "routine_id": "child_bedtime", "input_id": "bedtime_delay_seconds",
            "input_spec": {"type": "integer", "minimum": 0, "maximum": 14400, "spoken_duration": True, "no_timer_value": 0, "confirm_duration": True},
        }
        load.return_value = pending
        starter = Mock(return_value={"run_id": "routine-1", "status": "waiting"})

        duration = handle_pending_ui_context(
            "one hour and thirty minutes", "child-room", "session-1",
            audio_search=Mock(), routine_start=starter,
        )
        self.assertEqual(duration.dispatch.status, "executed")
        self.assertEqual(duration.reply_text, "Timer has been set for 1 hour, 30 minutes.")
        starter.assert_called_once_with(
            routine_id="child_bedtime", client_id="child-ui", inputs={"bedtime_delay_seconds": 5400},
        )
        clear.assert_called_once_with("child-room", "session-1")

        starter.reset_mock()
        clear.reset_mock()
        no_timer = handle_pending_ui_context(
            "no timer", "child-room", "session-2",
            audio_search=Mock(), routine_start=starter,
        )
        self.assertEqual(no_timer.reply_text, "Starting bedtime now.")
        self.assertTrue(no_timer.dispatch.result["no_timer"])
        starter.assert_called_once_with(
            routine_id="child_bedtime", client_id="child-ui", inputs={"bedtime_delay_seconds": 0},
        )

    @patch("oracle_app.ui_context.state.clear_pending_ui_context")
    @patch("oracle_app.ui_context.state.load_pending_ui_context")
    def test_invalid_prompted_duration_remains_pending(self, load, clear) -> None:
        load.return_value = {
            "action": "routine_input", "client_id": "child-ui", "target_source_id": "child-room",
            "routine_id": "child_bedtime", "input_id": "delay",
            "input_spec": {"minimum": 0, "maximum": 14400, "no_timer_value": 0},
        }
        starter = Mock()
        response = handle_pending_ui_context(
            "later", "child-room", "session-3", audio_search=Mock(), routine_start=starter,
        )
        self.assertEqual(response.dispatch.status, "pending_clarification")
        self.assertEqual(response.dispatch.result["error"], "routine_duration_required")
        starter.assert_not_called()
        clear.assert_not_called()
    @patch(
        "oracle_app.orchestration_routine_routes.get_orchestration_settings",
        side_effect=AssertionError("canonical route used V1 routine settings"),
    )
    @patch(
        "oracle_app.orchestration_routine_routes.get_source_registry",
        side_effect=AssertionError("canonical route used V1 source registry"),
    )
    def test_canonical_run_uses_typed_execution(self, _legacy_sources, _legacy_settings) -> None:
        class FakeExecution:
            def definition_payload(self, routine_id):
                self.definition_id = routine_id
                return dict(ROUTINE)

            def start(self, routine_id, **kwargs):
                self.started = (routine_id, kwargs)
                return {"run_id": "canonical-run", "status": "waiting"}

        execution = FakeExecution()
        payload = run_routine(
            "child_bedtime",
            UiRoutineRunRequest(
                client_id="canonical-ui",
                source="satellite-child",
                inputs={"sleep_minutes": 20},
            ),
            routine_execution=execution,  # type: ignore[arg-type]
            canonical_authority=True,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(execution.definition_id, "child_bedtime")
        self.assertEqual(
            execution.started,
            (
                "child_bedtime",
                {"client_id": "canonical-ui", "inputs": {"sleep_minutes": 20}},
            ),
        )

    @patch("oracle_app.orchestration_routine_routes.start_routine")
    @patch("oracle_app.orchestration_routine_routes.get_orchestration_settings")
    @patch("oracle_app.orchestration_routine_routes.get_source_registry")
    def test_run_accepts_known_source_and_declared_inputs(
        self,
        mock_sources,
        mock_settings,
        mock_start,
    ) -> None:
        mock_sources.return_value = {"satellite-child": {"source_type": "satellite"}}
        mock_settings.return_value = {"routines": [ROUTINE]}
        mock_start.return_value = {"run_id": "run-1", "status": "waiting"}

        payload = run_routine(
            "child_bedtime",
            UiRoutineRunRequest(
                client_id="satellite-ui-satellite-child",
                source="satellite-child",
                inputs={"sleep_minutes": 20},
            ),
        )

        self.assertTrue(payload["ok"])
        mock_start.assert_called_once_with(
            "child_bedtime",
            client_id="satellite-ui-satellite-child",
            inputs={"sleep_minutes": 20},
        )

    @patch("oracle_app.orchestration_routine_routes.start_routine")
    @patch("oracle_app.orchestration_routine_routes.get_orchestration_settings")
    @patch("oracle_app.orchestration_routine_routes.get_source_registry")
    def test_run_hides_kernel_private_metadata(
        self,
        mock_sources,
        mock_settings,
        mock_start,
    ) -> None:
        mock_sources.return_value = {"satellite-child": {"source_type": "satellite"}}
        mock_settings.return_value = {"routines": [ROUTINE]}
        mock_start.return_value = {
            "run_id": "run-1",
            "status": "waiting",
            "definition_domain": "composite",
            "definition_version": "sha256:test",
            "controller_version": "1",
            "controller_state": {"next_step_index": 1},
            "correlation_key": "routine:child_bedtime",
            "activation_idempotency_key": "",
            "cancellation_reason": "",
            "cancellation_requester": "",
        }

        payload = run_routine(
            "child_bedtime",
            UiRoutineRunRequest(
                client_id="satellite-ui-satellite-child",
                source="satellite-child",
            ),
        )

        self.assertEqual(payload["run"], {"run_id": "run-1", "status": "waiting"})

    @patch("oracle_app.orchestration_routine_routes.get_source_registry", return_value={})
    def test_run_rejects_unknown_source(self, _mock_sources) -> None:
        with self.assertRaises(HTTPException) as raised:
            run_routine(
                "child_bedtime",
                UiRoutineRunRequest(client_id="browser", source="unknown"),
            )

        self.assertEqual(raised.exception.status_code, 400)

    @patch(
        "oracle_app.orchestration_routine_routes.get_source_registry",
        return_value={"satellite-guest": {"source_type": "satellite"}},
    )
    @patch(
        "oracle_app.orchestration_routine_routes.get_orchestration_settings",
        return_value={"routines": [ROUTINE]},
    )
    def test_run_rejects_known_source_not_bound_to_routine(
        self,
        _mock_settings,
        _mock_sources,
    ) -> None:
        with self.assertRaises(HTTPException) as raised:
            run_routine(
                "child_bedtime",
                UiRoutineRunRequest(client_id="browser", source="satellite-guest"),
            )

        self.assertEqual(raised.exception.status_code, 409)

    @patch("oracle_app.orchestration_routine_routes.cancel_routine")
    def test_cancel_returns_durable_run(self, mock_cancel) -> None:
        mock_cancel.return_value = {"run_id": "run-1", "status": "canceled"}

        payload = cancel_routine_run(
            "run-1",
            UiRoutineCancelRequest(client_id="browser-admin-ui"),
        )

        self.assertTrue(payload["ok"])
        mock_cancel.assert_called_once_with(
            "run-1",
            cancellation_requester="browser-admin-ui",
        )


if __name__ == "__main__":
    unittest.main()
