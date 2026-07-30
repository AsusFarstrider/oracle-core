from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.memory.orchestrations import (
    create_orchestration_run,
    get_orchestration_run,
    list_orchestration_runs,
    reconcile_interrupted_orchestration_runs,
    upsert_orchestration_step,
)
from oracle_app.orchestration_routines import (
    cancel_routine,
    configure_routine_adapters,
    find_routine_trigger,
    resume_due_routines,
    start_routine,
)


def routine_definition(*steps: dict[str, object]) -> dict[str, object]:
    return {
        "id": "test_routine",
        "display_name": "Test Routine",
        "enabled": True,
        "user_id": "test",
        "source_ids": ["test-source"],
        "inputs": {
            "delay_seconds": {
                "type": "integer",
                "default": 5,
                "minimum": 0,
                "maximum": 60,
            }
        },
        "steps": list(steps),
    }


class OrchestrationRoutineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "memory.db"
        self.calls: list[tuple[str, dict[str, object]]] = []

        def adapter(name: str):
            def execute(**kwargs):
                self.calls.append((name, kwargs))
                return {"ok": True, "status": "executed", "detail": f"{name} completed"}

            return execute

        configure_routine_adapters(
            ui_action=adapter("ui_action"),
            audiobook_start=adapter("audiobook_start"),
            sleep_timer=adapter("sleep_timer"),
            state_check=adapter("state_check"),
            playback_check=adapter("playback_check"),
        )
        self.event_patch = patch("oracle_app.orchestration_routines.safe_record_event", return_value=True)
        self.event_patch.start()

    def tearDown(self) -> None:
        self.event_patch.stop()
        self.temp_dir.cleanup()

    def start(self, definition: dict[str, object], *, inputs=None):
        return start_routine(
            str(definition["id"]),
            client_id="test-client",
            inputs=inputs,
            settings={"routines": [definition]},
            db_path=self.db_path,
        )

    def test_typed_steps_execute_in_order_and_complete(self) -> None:
        definition = routine_definition(
            {
                "id": "lights",
                "type": "ui_action",
                "label": "Lights",
                "action_id": "room_off",
                "required": True,
            },
            {
                "id": "book",
                "type": "audiobook_start",
                "label": "Book",
                "source_id": "test-source",
                "user_id": "test",
                "duration_input": "delay_seconds",
                "duration_unit": "seconds",
                "required": True,
            },
        )

        run = self.start(definition)

        self.assertEqual(run["status"], "completed")
        self.assertEqual([name for name, _args in self.calls], ["ui_action", "audiobook_start"])
        self.assertEqual(self.calls[1][1]["sleep_timer_seconds"], 5)
        self.assertTrue(all(step["status"] == "completed" for step in run["steps"]))
        self.assertEqual(run["definition_domain"], "composite")
        self.assertEqual(run["controller_version"], "1")
        self.assertTrue(run["definition_version"].startswith("sha256:"))
        self.assertEqual(run["correlation_key"], "routine:test_routine")
        self.assertEqual(run["controller_state"], {"next_step_index": 2})

    def test_all_steps_are_durable_before_first_adapter_action(self) -> None:
        observed_statuses: list[list[str]] = []

        def inspect_first_action(**_kwargs):
            active = list_orchestration_runs(
                kind="routine",
                status="running",
                db_path=self.db_path,
            )
            observed_statuses.append([str(step["status"]) for step in active[0]["steps"]])
            return {"ok": True, "status": "executed"}

        configure_routine_adapters(
            ui_action=inspect_first_action,
            audiobook_start=lambda **_kwargs: {"ok": True},
            sleep_timer=lambda **_kwargs: {"ok": True},
            state_check=lambda **_kwargs: {"ok": True},
            playback_check=lambda **_kwargs: {"ok": True},
        )
        definition = routine_definition(
            {
                "id": "first",
                "type": "ui_action",
                "label": "First",
                "action_id": "first_action",
                "required": True,
            },
            {
                "id": "second",
                "type": "ui_action",
                "label": "Second",
                "action_id": "second_action",
                "required": True,
            },
        )

        run = self.start(definition)

        self.assertEqual(run["status"], "completed")
        self.assertEqual(observed_statuses[0], ["running", "pending"])

    def test_waiting_run_uses_frozen_definition_after_config_changes(self) -> None:
        definition = routine_definition(
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "duration_seconds": 1,
                "max_lateness_seconds": 10,
                "required": True,
            },
            {
                "id": "action",
                "type": "ui_action",
                "label": "Action",
                "action_id": "original_action",
                "required": True,
            },
        )
        run = self.start(definition)
        due_at = datetime.fromisoformat(run["steps"][0]["payload"]["due_at"])
        definition["steps"][1]["action_id"] = "changed_after_start"

        completed = resume_due_routines(
            now=due_at + timedelta(seconds=1),
            db_path=self.db_path,
        )[0]

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.calls[0][1]["action_id"], "original_action")

    def test_voice_start_defers_audiobook_audible_start(self) -> None:
        definition = routine_definition(
            {
                "id": "book",
                "type": "audiobook_start",
                "label": "Book",
                "source_id": "test-source",
                "user_id": "test",
                "required": True,
            },
        )

        start_routine(
            str(definition["id"]),
            client_id="test-client",
            settings={"routines": [definition]},
            defer_audible_start=True,
            db_path=self.db_path,
        )

        self.assertTrue(self.calls[0][1]["defer_audible_start"])

    def test_legacy_audiobook_resume_step_uses_start_adapter(self) -> None:
        definition = routine_definition(
            {
                "id": "book",
                "type": "audiobook_resume",
                "label": "Book",
                "source_id": "test-source",
                "user_id": "test",
                "required": True,
            },
        )

        self.start(definition)

        self.assertEqual([name for name, _args in self.calls], ["audiobook_start"])

    def test_duplicate_active_routine_is_rejected(self) -> None:
        definition = routine_definition(
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "duration_seconds": 30,
                "max_lateness_seconds": 10,
                "required": True,
            },
        )
        self.start(definition)

        with self.assertRaisesRegex(Exception, "already has an active run"):
            self.start(definition)

    def test_voice_triggers_are_source_bound_or_exact_global(self) -> None:
        definition = {
            **routine_definition(),
            "triggers": {
                "voice": True,
                "source_phrases": ["bedtime"],
                "global_phrases": ["child's bedtime"],
            },
        }
        settings = {"routines": [definition]}

        self.assertEqual(
            find_routine_trigger("Bedtime.", source="test-source", settings=settings)["id"],
            "test_routine",
        )
        self.assertIsNone(find_routine_trigger("bedtime", source="other-source", settings=settings))
        self.assertEqual(
            find_routine_trigger("Child's bedtime!", source="other-source", settings=settings)["id"],
            "test_routine",
        )

    def test_wait_is_durable_and_resumes_after_due_time(self) -> None:
        definition = routine_definition(
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "duration_input": "delay_seconds",
                "duration_unit": "seconds",
                "max_lateness_seconds": 10,
                "required": True,
            },
            {
                "id": "check",
                "type": "state_check",
                "label": "Check",
                "check_id": "room",
                "expected_state": "off",
                "required": True,
            },
        )
        run = self.start(definition, inputs={"delay_seconds": 5})
        due_at = datetime.fromisoformat(run["steps"][0]["payload"]["due_at"])

        self.assertEqual(run["status"], "waiting")
        self.assertEqual(resume_due_routines(now=due_at - timedelta(seconds=1), db_path=self.db_path), [])

        resumed = resume_due_routines(now=due_at + timedelta(seconds=2), db_path=self.db_path)

        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["status"], "completed")
        self.assertEqual(resumed[0]["steps"][0]["payload"]["lateness_seconds"], 2)
        self.assertEqual([name for name, _args in self.calls], ["state_check"])

    def test_legacy_waiting_routine_resumes_through_kernel_repository(self) -> None:
        definition = routine_definition(
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "duration_seconds": 1,
                "max_lateness_seconds": 10,
                "required": True,
            },
            {
                "id": "action",
                "type": "ui_action",
                "label": "Action",
                "action_id": "legacy_followup",
                "required": True,
            },
        )
        due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        create_orchestration_run(
            run_id="legacy-waiting",
            orchestration_id="test_routine",
            kind="routine",
            status="waiting",
            started_at=(due_at - timedelta(seconds=1)).isoformat(),
            client_id="legacy-client",
            payload={
                "definition": definition,
                "inputs": {"delay_seconds": 5},
                "next_step_index": 0,
                "defer_audible_start": False,
            },
            db_path=self.db_path,
        )
        upsert_orchestration_step(
            run_id="legacy-waiting",
            step_id="wait",
            ordinal=1,
            status="waiting",
            target_type="wait",
            target_label="Wait",
            started_at=(due_at - timedelta(seconds=1)).isoformat(),
            payload={
                "definition": definition["steps"][0],
                "due_at": due_at.isoformat(),
                "max_lateness_seconds": 10,
            },
            db_path=self.db_path,
        )
        upsert_orchestration_step(
            run_id="legacy-waiting",
            step_id="action",
            ordinal=2,
            status="pending",
            target_type="ui_action",
            target_label="Action",
            action_id="legacy_followup",
            payload={"definition": definition["steps"][1]},
            db_path=self.db_path,
        )

        resumed = resume_due_routines(
            now=due_at + timedelta(seconds=2),
            db_path=self.db_path,
        )[0]

        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["definition_domain"], "")
        self.assertEqual(self.calls[0][1]["action_id"], "legacy_followup")

    def test_timed_audiobook_routine_advances_across_two_durable_waits(self) -> None:
        definition = routine_definition(
            {
                "id": "light_on",
                "type": "ui_action",
                "label": "Light on",
                "action_id": "office_on",
                "required": True,
            },
            {
                "id": "initial_wait",
                "type": "wait",
                "label": "Initial wait",
                "duration_seconds": 30,
                "max_lateness_seconds": 120,
                "required": True,
            },
            {
                "id": "book",
                "type": "audiobook_start",
                "label": "Book",
                "source_id": "satellite-operator",
                "user_id": "operator",
                "duration_seconds": 60,
                "required": True,
            },
            {
                "id": "timer_wait",
                "type": "wait",
                "label": "Timer wait",
                "duration_seconds": 60,
                "max_lateness_seconds": 120,
                "required": True,
            },
            {
                "id": "light_off",
                "type": "ui_action",
                "label": "Light off",
                "action_id": "office_off",
                "required": True,
            },
        )

        run = self.start(definition)
        first_due_at = datetime.fromisoformat(run["steps"][1]["payload"]["due_at"])

        self.assertEqual(run["status"], "waiting")
        self.assertEqual([name for name, _args in self.calls], ["ui_action"])
        self.assertEqual(self.calls[0][1]["action_id"], "office_on")

        resumed = resume_due_routines(now=first_due_at + timedelta(seconds=1), db_path=self.db_path)[0]
        second_due_at = datetime.fromisoformat(resumed["steps"][3]["payload"]["due_at"])

        self.assertEqual(resumed["status"], "waiting")
        self.assertEqual(
            [name for name, _args in self.calls],
            ["ui_action", "audiobook_start"],
        )
        self.assertEqual(self.calls[1][1]["source_id"], "satellite-operator")
        self.assertEqual(self.calls[1][1]["user_id"], "operator")
        self.assertEqual(self.calls[1][1]["sleep_timer_seconds"], 60)

        completed = resume_due_routines(now=second_due_at + timedelta(seconds=1), db_path=self.db_path)[0]

        self.assertEqual(completed["status"], "completed")
        self.assertEqual([name for name, _args in self.calls], ["ui_action", "audiobook_start", "ui_action"])
        self.assertEqual(self.calls[-1][1]["action_id"], "office_off")

    def test_late_continuation_fails_without_running_followup(self) -> None:
        definition = routine_definition(
            {
                "id": "wait",
                "type": "wait",
                "label": "Wait",
                "duration_seconds": 1,
                "max_lateness_seconds": 3,
                "required": True,
            },
            {
                "id": "action",
                "type": "ui_action",
                "label": "Action",
                "action_id": "room_off",
                "required": True,
            },
        )
        run = self.start(definition)
        due_at = datetime.fromisoformat(run["steps"][0]["payload"]["due_at"])

        resumed = resume_due_routines(now=due_at + timedelta(seconds=4), db_path=self.db_path)

        self.assertEqual(resumed[0]["status"], "failed")
        self.assertEqual(resumed[0]["steps"][0]["error_class"], "max_lateness_exceeded")
        self.assertEqual(resumed[0]["steps"][1]["status"], "not_run")
        self.assertEqual(self.calls, [])

    def test_waiting_routine_can_be_canceled(self) -> None:
        run = self.start(
            routine_definition(
                {
                    "id": "wait",
                    "type": "wait",
                    "label": "Wait",
                    "duration_seconds": 30,
                    "max_lateness_seconds": 10,
                    "required": True,
                },
                {
                    "id": "action",
                    "type": "ui_action",
                    "label": "Action",
                    "action_id": "room_off",
                    "required": True,
                },
            )
        )

        canceled = cancel_routine(
            run["run_id"],
            cancellation_requester="test-client",
            db_path=self.db_path,
        )

        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual([step["status"] for step in canceled["steps"]], ["canceled", "canceled"])
        self.assertEqual(canceled["cancellation_reason"], "routine_cancel_requested")
        self.assertEqual(canceled["cancellation_requester"], "test-client")

    def test_best_effort_failure_runs_one_remediation_and_continues(self) -> None:
        check_calls = 0

        def failed_check(**_kwargs):
            nonlocal check_calls
            check_calls += 1
            return {"ok": False, "error": "state_mismatch", "detail": "still on"}

        configure_routine_adapters(
            ui_action=lambda **kwargs: (
                self.calls.append(("ui_action", kwargs))
                or {"ok": True, "status": "executed"}
            ),
            audiobook_start=lambda **_kwargs: {"ok": True},
            sleep_timer=lambda **_kwargs: {"ok": True},
            state_check=failed_check,
            playback_check=lambda **_kwargs: {"ok": True},
        )
        definition = routine_definition(
            {
                "id": "check",
                "type": "state_check",
                "label": "Check",
                "check_id": "room",
                "expected_state": "off",
                "required": False,
                "on_failure": "continue",
                "remediation_action_id": "room_off",
            },
            {
                "id": "followup",
                "type": "ui_action",
                "label": "Followup",
                "action_id": "done",
                "required": True,
            },
        )

        run = self.start(definition)

        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["steps"][0]["status"], "failed")
        self.assertEqual(check_calls, 2)
        self.assertEqual([args["action_id"] for _name, args in self.calls], ["room_off", "done"])

    def test_required_failure_stops_and_marks_later_steps_not_run(self) -> None:
        configure_routine_adapters(
            ui_action=lambda **kwargs: (
                self.calls.append(("ui_action", kwargs))
                or {"ok": kwargs["action_id"] != "fail", "error": "failed"}
            ),
            audiobook_start=lambda **_kwargs: {"ok": True},
            sleep_timer=lambda **_kwargs: {"ok": True},
            state_check=lambda **_kwargs: {"ok": True},
            playback_check=lambda **_kwargs: {"ok": True},
        )
        definition = routine_definition(
            {
                "id": "required",
                "type": "ui_action",
                "label": "Required",
                "action_id": "fail",
                "required": True,
                "on_failure": "stop",
            },
            {
                "id": "later",
                "type": "ui_action",
                "label": "Later",
                "action_id": "later",
                "required": True,
            },
        )

        run = self.start(definition)

        self.assertEqual(run["status"], "failed")
        self.assertEqual([step["status"] for step in run["steps"]], ["failed", "not_run"])
        self.assertEqual([args["action_id"] for _name, args in self.calls], ["fail"])

    def test_restart_interrupts_running_but_preserves_waiting(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        create_orchestration_run(
            run_id="running",
            orchestration_id="test_routine",
            kind="routine",
            status="running",
            started_at=now,
            db_path=self.db_path,
        )
        create_orchestration_run(
            run_id="waiting",
            orchestration_id="test_routine",
            kind="routine",
            status="waiting",
            started_at=now,
            db_path=self.db_path,
        )
        with patch("oracle_app.memory.orchestrations.record_event"):
            count = reconcile_interrupted_orchestration_runs(db_path=self.db_path)

        self.assertEqual(count, 1)
        self.assertEqual(get_orchestration_run("running", db_path=self.db_path)["status"], "interrupted")
        self.assertEqual(get_orchestration_run("waiting", db_path=self.db_path)["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
