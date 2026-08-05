from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import stat
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock

from oracle_app.configuration import (
    ConfigurationService,
    GenerationStore,
    StandardBrainConfigurationHostLocalRuntime,
    resolve_brain_configuration_startup,
    snapshot_candidate,
)
from oracle_app.configuration.runtime_cutover import arm_runtime_cutover
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.installation import (
    ActivationRequest,
    InstallationLayout,
    load_selected_activation,
    publish_activation,
    select_activation,
)
from oracle_app.installation_identity import environment_directory_name
from oracle_app.installation_control import StandardActivationCoordinator
from oracle_app.installation_runtime import (
    finalize_verified_startup,
    load_running_activation_id,
    record_running_activation,
    recover_after_process_exit,
    schedule_graceful_process_restart,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class StandardInstallationRuntimeTests(unittest.TestCase):
    def test_old_process_exit_retains_candidate_then_successful_startup_finalizes(self) -> None:
        with self._environment() as (layout, runtime, _bundle, store, coordinator, initial):
            previous = record_running_activation(layout, runtime_directory=runtime, pid=100)
            staged = coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="candidate-value",
                expected_secret_generation_id=initial.secrets.generation_id,
            )

            transition = recover_after_process_exit(layout, runtime_directory=runtime)

            self.assertEqual(transition.outcome, "prior_process_exited_candidate_retained")
            self.assertEqual(transition.stopped_activation_id, previous.activation_id)
            self.assertEqual(
                load_selected_activation(layout).activation_id,
                staged.candidate_activation.activation_id,
            )
            self.assertIsNone(load_running_activation_id(runtime))
            self.assertTrue(coordinator.journal_path.exists())

            record_running_activation(layout, runtime_directory=runtime, pid=101)
            verified = finalize_verified_startup(
                staged.mutation.selected.activation.generation_id,
                layout,
            )

            self.assertEqual(verified.activation_id, staged.candidate_activation.activation_id)
            self.assertEqual(
                load_selected_activation(layout, "approved").activation_id,
                staged.candidate_activation.activation_id,
            )
            self.assertEqual(
                store.secret_generation_status(initial.secrets.generation_id)["state"],
                "revoked",
            )
            self.assertFalse(coordinator.journal_path.exists())

    def test_candidate_process_failure_restores_complete_previous_activation(self) -> None:
        with self._environment() as (layout, runtime, bundle, store, coordinator, initial):
            previous = record_running_activation(layout, runtime_directory=runtime, pid=200)
            staged = coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="must-not-survive",
                expected_secret_generation_id=initial.secrets.generation_id,
            )
            recover_after_process_exit(layout, runtime_directory=runtime)
            record_running_activation(layout, runtime_directory=runtime, pid=201)

            recovered = recover_after_process_exit(layout, runtime_directory=runtime)

            self.assertEqual(recovered.outcome, "candidate_failed_previous_restored")
            self.assertEqual(recovered.stopped_activation_id, staged.candidate_activation.activation_id)
            self.assertEqual(recovered.selected_activation_id, previous.activation_id)
            self.assertEqual(load_selected_activation(layout).activation_id, previous.activation_id)
            self.assertEqual(store.load_selected().activation.generation_id, initial.activation.generation_id)
            self.assertNotIn("TOKEN", (bundle / "secrets.env").read_text(encoding="utf-8"))
            self.assertIsNone(load_running_activation_id(runtime))

    def test_candidate_failure_before_entrypoint_marker_recovers_fail_closed(self) -> None:
        with self._environment() as (layout, runtime, _bundle, _store, coordinator, initial):
            previous = load_selected_activation(layout)
            coordinator.stage_secret_mutation(
                operation="create_secret",
                logical_id="TOKEN",
                value="must-not-survive",
                expected_secret_generation_id=initial.secrets.generation_id,
            )

            recovered = recover_after_process_exit(layout, runtime_directory=runtime)

            self.assertEqual(recovered.outcome, "candidate_failed_previous_restored")
            self.assertIsNone(recovered.stopped_activation_id)
            self.assertEqual(recovered.selected_activation_id, previous.activation_id)

    def test_no_pending_transaction_only_clears_boot_lifetime_marker(self) -> None:
        with self._environment() as (layout, runtime, _bundle, _store, _coordinator, _initial):
            active = record_running_activation(layout, runtime_directory=runtime, pid=300)

            result = recover_after_process_exit(layout, runtime_directory=runtime)

            self.assertEqual(result.outcome, "no_pending_activation")
            self.assertEqual(result.selected_activation_id, active.activation_id)
            self.assertIsNone(load_running_activation_id(runtime))

    def test_graceful_restart_sends_sigterm_after_a_delay(self) -> None:
        called = threading.Event()
        observed: list[tuple[int, int]] = []

        def send(pid: int, requested_signal: int) -> None:
            observed.append((pid, requested_signal))
            called.set()

        schedule_graceful_process_restart(
            delay_seconds=0.01,
            process_id=4321,
            signal_process=send,
        )

        self.assertTrue(called.wait(1.0))
        self.assertEqual(observed, [(4321, signal.SIGTERM)])

    def test_standard_startup_uses_complete_activation_without_legacy_bootstrap(self) -> None:
        with self._environment() as (layout, _runtime, _bundle, _store, _coordinator, initial):
            startup = resolve_brain_configuration_startup(
                {"ORACLE_STANDARD_INSTALLATION": "1"},
                standard_layout=layout,
            )

            self.assertEqual(startup.mode, "canonical")
            self.assertIsNone(startup.service_settings)
            self.assertEqual(startup.installation_layout, layout)
            self.assertEqual(
                startup.effective_config.activation_generation_id,
                initial.activation.generation_id,
            )

            composition = CanonicalBrainApplicationComposition.from_startup(startup)
            self.assertEqual(composition.projection_resolver.store.root, layout.configuration)
            self.assertEqual(composition.projection_resolver.store.secret_root, layout.secrets)

    def test_standard_control_runtime_uses_split_store_and_group_socket(self) -> None:
        with self._environment() as (layout, runtime, _bundle, _store, _coordinator, _initial):
            runtime.chmod(0o2750)
            control = StandardBrainConfigurationHostLocalRuntime(
                layout,
                runtime_directory=runtime,
            )
            with mock.patch(
                "grp.getgrnam",
                return_value=SimpleNamespace(gr_gid=os.getegid()),
            ):
                try:
                    control.start()
                    socket_path = runtime / "control.sock"
                    self.assertTrue(stat.S_ISSOCK(socket_path.stat().st_mode))
                    self.assertEqual(stat.S_IMODE(socket_path.stat().st_mode), 0o660)
                    self.assertTrue(control.enabled)
                finally:
                    control.stop()
            self.assertFalse((runtime / "control.sock").exists())

    def test_standard_systemd_unit_uses_complete_selector_and_bounded_recovery(self) -> None:
        unit = (REPO_ROOT / "scripts" / "oracle-brain-standard.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("User=oracle", unit)
        self.assertIn("Group=oracle", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("app_standard:app", unit)
        self.assertIn("/srv/oracle/selection/active/environment/bin/python", unit)
        self.assertIn("oracle-standard-lifecycle.py recover-after-exit", unit)
        self.assertNotIn("sudo", unit)
        self.assertNotIn("/home/", unit)
        entrypoint = (REPO_ROOT / "server" / "app_standard.py").read_text(encoding="utf-8")
        self.assertLess(
            entrypoint.index("record_running_activation()"),
            entrypoint.index("from oracle_app.api import app"),
        )

    def _environment(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        layout = InstallationLayout(root / "oracle")
        for directory in layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        runtime = root / "run" / "oracle"
        runtime.mkdir(parents=True)
        bundle = layout.secrets
        candidate = root / "candidate"
        shutil.copytree(EXAMPLE_ROOT, candidate)
        store = GenerationStore(layout.configuration, secret_root=layout.secrets)
        store.initialize("example-home")
        service = ConfigurationService(store)
        activated = service.activate_candidate(
            candidate,
            expected_authored_revision=snapshot_candidate(candidate).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
        )
        arm_runtime_cutover(store, store.load_selected(), actor="service")
        request = ActivationRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-tree-" + "2" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
            configuration_activation_identity=activated.selected.activation.generation_id,
            service_definition_identity="systemd-unit-" + "5" * 64,
        )
        for path in (
            layout.revisions / request.application_revision_identity,
            layout.environments / environment_directory_name(request.python_environment_identity),
            layout.deployments / request.household_deployment_revision,
        ):
            path.mkdir()
        complete = publish_activation(layout, request)
        select_activation(layout, "active", complete)
        select_activation(layout, "approved", complete)
        select_activation(layout, "previous-known-good", complete)
        coordinator = StandardActivationCoordinator(
            layout,
            service,
            secret_companion_root=bundle,
        )

        class Environment:
            def __enter__(self_nonlocal):
                return layout, runtime, bundle, store, coordinator, activated.selected

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return Environment()


if __name__ == "__main__":
    unittest.main()
