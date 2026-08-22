from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.runbook_kernel import (
    DuplicateRunActivationError,
    InvalidRunTransitionError,
    RunbookActivation,
    RunbookDefinitionRef,
    RunbookRepository,
    validate_run_transition,
)


class RunbookKernelModelTests(unittest.TestCase):
    def test_definition_and_activation_require_stable_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "definition_id"):
            RunbookDefinitionRef(definition_id="", kind="routine", domain="composite")
        with self.assertRaisesRegex(ValueError, "run_id"):
            RunbookActivation(run_id="", started_at="2026-06-22T12:00:00+00:00")

    def test_transition_validation_preserves_terminal_immutability(self) -> None:
        validate_run_transition("running", "waiting")
        validate_run_transition("waiting", "running")
        validate_run_transition("running", "completed")
        validate_run_transition("running", "completed_with_issues")
        validate_run_transition("running", "stopped")

        with self.assertRaises(InvalidRunTransitionError):
            validate_run_transition("completed", "running")
        with self.assertRaisesRegex(InvalidRunTransitionError, "cannot be rewritten"):
            validate_run_transition("completed", "completed")
        with self.assertRaisesRegex(InvalidRunTransitionError, "Unknown target"):
            validate_run_transition("running", "invented")


class RunbookRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.repository = RunbookRepository(db_path=self.db_path)
        self.definition = RunbookDefinitionRef(
            definition_id="child_bedtime",
            kind="routine",
            domain="composite",
            version="sha256:def1",
            controller_version="1",
        )

    def activation(self, run_id: str, key: str) -> RunbookActivation:
        return RunbookActivation(
            run_id=run_id,
            started_at="2026-06-22T12:00:00+00:00",
            correlation_key="household:child:bedtime",
            idempotency_key=key,
            client_id="test-client",
        )

    def test_create_round_trips_kernel_metadata_and_operations(self) -> None:
        run = self.repository.create_run(
            self.definition,
            self.activation("run-1", "activation-1"),
            controller_state={"next_operation": 0},
            payload={"inputs": {"sleep_minutes": 20}},
        )
        operation = self.repository.record_operation(
            run_id="run-1",
            operation_id="lights_off",
            ordinal=1,
            status="pending",
            operation_kind="capability_call",
            target_id="child_room",
            capability_id="home.lights.set",
            payload={"state": "off"},
        )

        self.assertEqual(run["definition_domain"], "composite")
        self.assertEqual(run["definition_version"], "sha256:def1")
        self.assertEqual(run["controller_version"], "1")
        self.assertEqual(run["controller_state"], {"next_operation": 0})
        self.assertEqual(run["correlation_key"], "household:child:bedtime")
        self.assertEqual(run["activation_idempotency_key"], "activation-1")
        self.assertEqual(operation["target_type"], "capability_call")
        self.assertEqual(operation["action_id"], "home.lights.set")
        self.assertEqual(operation["payload"], {"state": "off"})

    def test_activation_idempotency_is_unique_and_queryable(self) -> None:
        original = self.repository.create_run(
            self.definition,
            self.activation("run-1", "activation-1"),
        )

        with self.assertRaises(DuplicateRunActivationError):
            self.repository.create_run(
                self.definition,
                self.activation("run-2", "activation-1"),
            )

        found = self.repository.find_by_activation_idempotency_key("activation-1")
        self.assertEqual(found["run_id"], original["run_id"])

    def test_domain_and_correlation_filters_are_isolated(self) -> None:
        self.repository.create_run(
            self.definition,
            self.activation("run-1", "activation-1"),
        )
        network = RunbookDefinitionRef(
            definition_id="fix_internet",
            kind="recovery",
            domain="network",
            version="sha256:def2",
            controller_version="1",
        )
        self.repository.create_run(
            network,
            RunbookActivation(
                run_id="run-2",
                started_at="2026-06-22T12:01:00+00:00",
                correlation_key="network:recovery:1",
                idempotency_key="activation-2",
            ),
        )

        self.assertEqual(
            [run["run_id"] for run in self.repository.list_runs(domain="composite")],
            ["run-1"],
        )
        self.assertEqual(
            [
                run["run_id"]
                for run in self.repository.list_runs(
                    correlation_key="network:recovery:1"
                )
            ],
            ["run-2"],
        )

    def test_transitions_preserve_payload_and_record_cancellation(self) -> None:
        self.repository.create_run(
            self.definition,
            self.activation("run-1", "activation-1"),
            controller_state={"next_operation": 0},
            payload={"frozen": True},
        )
        waiting = self.repository.transition_run(
            "run-1",
            status="waiting",
            summary="Waiting.",
            controller_state={"next_operation": 1},
        )
        canceled = self.repository.transition_run(
            "run-1",
            status="canceled",
            summary="Canceled by caller.",
            cancellation_reason="user_requested",
            cancellation_requester="test-client",
        )

        self.assertEqual(waiting["payload"], {"frozen": True})
        self.assertEqual(canceled["controller_state"], {"next_operation": 1})
        self.assertEqual(canceled["cancellation_reason"], "user_requested")
        self.assertEqual(canceled["cancellation_requester"], "test-client")
        self.assertTrue(canceled["completed_at"])
        with self.assertRaises(InvalidRunTransitionError):
            self.repository.transition_run(
                "run-1",
                status="running",
                summary="Cannot resume.",
            )

    def test_cancellation_requires_reason(self) -> None:
        self.repository.create_run(
            self.definition,
            self.activation("run-1", "activation-1"),
        )

        with self.assertRaisesRegex(ValueError, "cancellation reason"):
            self.repository.transition_run(
                "run-1",
                status="canceled",
                summary="Canceled.",
            )

    def test_new_kinds_wait_for_compatibility_store_migration(self) -> None:
        definition = RunbookDefinitionRef(
            definition_id="door_left_open",
            kind="home_automation",
            domain="home",
        )

        with self.assertRaisesRegex(ValueError, "compatibility store"):
            self.repository.create_run(
                definition,
                self.activation("run-1", "activation-1"),
            )


if __name__ == "__main__":
    unittest.main()
