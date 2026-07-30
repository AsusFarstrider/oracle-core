from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.memory.orchestrations import (
    complete_orchestration_run,
    create_orchestration_run,
    delete_orchestration_run,
    get_orchestration_run,
    list_orchestration_runs,
    reconcile_interrupted_orchestration_runs,
    upsert_orchestration_step,
)


class MemoryOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"

    def test_run_and_steps_round_trip(self) -> None:
        create_orchestration_run(
            run_id="run-1",
            orchestration_id="fix_internet",
            kind="recovery",
            status="running",
            started_at="2026-06-12T12:00:00+00:00",
            preview_id="preview-1",
            digest="a" * 64,
            client_id="browser-house-ui",
            approval_consumed=True,
            db_path=self.db_path,
        )
        upsert_orchestration_step(
            run_id="run-1",
            step_id="step-1",
            ordinal=1,
            status="executed",
            target_type="service",
            target_id="plex",
            target_label="Plex",
            action_id="restart_service",
            policy_id="plex_restart",
            summary="Restart completed.",
            request_id="netctl-1",
            verification_status="passed",
            started_at="2026-06-12T12:00:01+00:00",
            completed_at="2026-06-12T12:00:02+00:00",
            db_path=self.db_path,
        )
        complete_orchestration_run(
            "run-1",
            status="completed",
            summary="Recovery completed.",
            completed_at="2026-06-12T12:00:03+00:00",
            db_path=self.db_path,
        )

        run = get_orchestration_run("run-1", db_path=self.db_path)

        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "completed")
        self.assertTrue(run["approval_consumed"])
        self.assertEqual(run["steps"][0]["request_id"], "netctl-1")
        self.assertEqual(run["steps"][0]["verification_status"], "passed")
        self.assertEqual(list_orchestration_runs(orchestration_id="fix_internet", db_path=self.db_path)[0]["run_id"], "run-1")
        delete_orchestration_run("run-1", db_path=self.db_path)
        self.assertIsNone(get_orchestration_run("run-1", db_path=self.db_path))

    def test_reconcile_marks_running_run_and_step_interrupted(self) -> None:
        create_orchestration_run(
            run_id="run-interrupted",
            orchestration_id="fix_internet",
            kind="recovery",
            status="running",
            started_at="2026-06-12T12:00:00+00:00",
            db_path=self.db_path,
        )
        upsert_orchestration_step(
            run_id="run-interrupted",
            step_id="step-1",
            ordinal=1,
            status="running",
            db_path=self.db_path,
        )

        reconciled = reconcile_interrupted_orchestration_runs(db_path=self.db_path)
        run = get_orchestration_run("run-interrupted", db_path=self.db_path)

        self.assertEqual(reconciled, 1)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["steps"][0]["status"], "interrupted")
        self.assertTrue(run["completed_at"])
        self.assertEqual(reconcile_interrupted_orchestration_runs(db_path=self.db_path), 0)

    def test_shared_store_preserves_run_kind_boundaries(self) -> None:
        for run_id, orchestration_id, kind in (
            ("routine-1", "evening_wind_down", "routine"),
            ("recovery-1", "fix_internet", "recovery"),
        ):
            create_orchestration_run(
                run_id=run_id,
                orchestration_id=orchestration_id,
                kind=kind,
                status="waiting" if kind == "routine" else "completed",
                started_at="2026-06-22T12:00:00+00:00",
                db_path=self.db_path,
            )

        routine_runs = list_orchestration_runs(kind="routine", db_path=self.db_path)
        recovery_runs = list_orchestration_runs(kind="recovery", db_path=self.db_path)

        self.assertEqual([run["run_id"] for run in routine_runs], ["routine-1"])
        self.assertEqual([run["run_id"] for run in recovery_runs], ["recovery-1"])
        self.assertEqual(routine_runs[0]["kind"], "routine")
        self.assertEqual(recovery_runs[0]["kind"], "recovery")


if __name__ == "__main__":
    unittest.main()
