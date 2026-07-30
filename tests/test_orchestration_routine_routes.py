from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.orchestration_routine_routes import cancel_routine_run, run_routine
from oracle_app.schemas import UiRoutineCancelRequest, UiRoutineRunRequest


ROUTINE = {
    "id": "child_bedtime",
    "enabled": True,
    "source_ids": ["satellite-child"],
    "triggers": {"ui": True},
}


class OrchestrationRoutineRouteTests(unittest.TestCase):
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
