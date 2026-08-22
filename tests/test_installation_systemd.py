from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from oracle_app.installation import (
    ActivationRequest,
    InstallationLayout,
    load_selected_activation,
    publish_activation,
    select_activation,
)
from oracle_app.installation_assembly import service_definition_identity
from oracle_app.installation_identity import environment_directory_name
from oracle_app.installation_systemd import (
    StandardSystemdError,
    build_initial_activation_plan,
    build_rollback_activation_plan,
    build_systemd_install_plan,
    build_update_activation_plan,
    fail_initial_activation,
    finalize_managed_activation,
    finalize_initial_activation,
    install_systemd_unit,
    mark_initial_service_started,
    mark_initial_verification_passed,
    mark_managed_service_started,
    mark_managed_verification_passed,
    prepare_initial_activation,
    prepare_managed_activation,
    recover_managed_activation,
    select_managed_activation_target,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class StandardSystemdInstallationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.layout = InstallationLayout(self.root / "oracle")
        for directory in self.layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        application_id = "core-" + "1" * 40
        application = self.layout.revisions / application_id
        (application / "scripts").mkdir(parents=True)
        self.source_unit = application / "scripts" / "oracle-brain-standard.service"
        shutil.copy2(REPO_ROOT / "scripts" / "oracle-brain-standard.service", self.source_unit)
        environment_id = "oracle-python-environment-v1:sha256:" + "3" * 64
        deployment_id = "oracle-household-deployment-v1:sha256:" + "4" * 64
        configuration_id = "activation_" + "5" * 32
        (self.layout.environments / environment_directory_name(environment_id)).mkdir()
        (self.layout.deployments / deployment_id).mkdir()
        (self.layout.configuration / "activations" / configuration_id).mkdir(parents=True)
        request = ActivationRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity=application_id,
            python_environment_identity=environment_id,
            household_deployment_revision=deployment_id,
            configuration_activation_identity=configuration_id,
            service_definition_identity=service_definition_identity(self.source_unit),
        )
        self.activation = publish_activation(self.layout, request)
        select_activation(self.layout, "staged", self.activation)
        self.unit = self.root / "etc" / "systemd" / "system" / "oracle-brain.service"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_unit_plan_is_exact_and_does_not_enable_or_start(self) -> None:
        plan = build_systemd_install_plan(self.layout, unit_path=self.unit)
        self.assertEqual(plan.disposition, "install")
        self.assertEqual(plan.service_definition_identity, self.activation.record["service_definition_identity"])
        self.assertFalse(self.unit.exists())

    def test_unit_validation_does_not_require_unestablished_selectors(self) -> None:
        unit = self.source_unit.read_text(encoding="utf-8")
        self.assertFalse((self.layout.selection / "active").exists())
        self.assertFalse((self.layout.selection / "previous-known-good").exists())
        self.assertIn(
            "ExecStart=/usr/bin/env /srv/oracle/selection/active/environment/bin/python",
            unit,
        )
        self.assertIn(
            "ExecStopPost=/bin/sh -c 'if [ -x "
            "/srv/oracle/selection/previous-known-good/environment/bin/python ]",
            unit,
        )
        self.assertIn(
            "else exec /srv/oracle/selection/active/environment/bin/python",
            unit,
        )

    def test_install_publishes_exact_unit_and_only_reloads_systemd(self) -> None:
        plan = build_systemd_install_plan(self.layout, unit_path=self.unit)
        commands: list[list[str]] = []
        with (
            mock.patch("oracle_app.installation_systemd.os.geteuid", return_value=0),
            mock.patch("oracle_app.installation_systemd.os.chown"),
            mock.patch(
                "oracle_app.installation_systemd.subprocess.run",
                side_effect=lambda command, **_kwargs: commands.append(command),
            ),
        ):
            result = install_systemd_unit(plan)
        self.assertEqual(self.unit.read_bytes(), self.source_unit.read_bytes())
        self.assertEqual(commands, [["systemd-analyze", "verify", str(self.source_unit)], ["systemctl", "daemon-reload"]])
        self.assertFalse(result["enabled"])
        self.assertFalse(result["started"])

    def test_initial_activation_plan_requires_installed_unit_and_changes_nothing(self) -> None:
        self.unit.parent.mkdir(parents=True)
        shutil.copy2(self.source_unit, self.unit)
        plan = build_initial_activation_plan(self.layout, unit_path=self.unit)
        self.assertEqual(plan["candidate_activation_id"], self.activation.activation_id)
        self.assertFalse(plan["rollback_available"])
        self.assertEqual(plan["failure_posture"], "stop_service_remove_unverified_active_retain_staged_for_diagnostics")
        self.assertFalse((self.layout.selection / "active").exists())

    def test_initial_activation_plan_rejects_unit_drift(self) -> None:
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("[Unit]\nDescription=drifted\n", encoding="utf-8")
        with self.assertRaisesRegex(StandardSystemdError, "does not match"):
            build_initial_activation_plan(self.layout, unit_path=self.unit)

    @staticmethod
    def _verification() -> dict[str, object]:
        return {
            "passed": True,
            "systemd_active": True,
            "readiness": True,
            "health": True,
            "configuration_identity": True,
            "deterministic_interaction": True,
            "house_ui": True,
            "system_ui": True,
            "satellite_ui": True,
        }

    def test_successful_initial_lifecycle_marks_known_good_only_after_verification(self) -> None:
        self.unit.parent.mkdir(parents=True)
        shutil.copy2(self.source_unit, self.unit)
        plan = build_initial_activation_plan(self.layout, unit_path=self.unit)
        prepare_initial_activation(self.layout, plan)
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.activation.activation_id)
        self.assertFalse((self.layout.selection / "approved").exists())
        self.assertFalse((self.layout.selection / "previous-known-good").exists())
        mark_initial_service_started(self.layout)
        mark_initial_verification_passed(self.layout, self._verification())
        result = finalize_initial_activation(self.layout)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "previous-known-good").activation_id, self.activation.activation_id)
        self.assertFalse((self.layout.selection / "staged").exists())

    def test_failed_initial_lifecycle_removes_unverified_active_and_retains_staged(self) -> None:
        self.unit.parent.mkdir(parents=True)
        shutil.copy2(self.source_unit, self.unit)
        plan = build_initial_activation_plan(self.layout, unit_path=self.unit)
        prepare_initial_activation(self.layout, plan)
        mark_initial_service_started(self.layout)
        result = fail_initial_activation(self.layout, reason="readiness_failed")
        self.assertEqual(result["state"], "failed")
        self.assertFalse((self.layout.selection / "active").exists())
        self.assertEqual(load_selected_activation(self.layout, "staged").activation_id, self.activation.activation_id)
        self.assertFalse((self.layout.selection / "approved").exists())

    def test_recovery_completes_durably_verified_initial_activation(self) -> None:
        self.unit.parent.mkdir(parents=True)
        shutil.copy2(self.source_unit, self.unit)
        plan = build_initial_activation_plan(self.layout, unit_path=self.unit)
        prepare_initial_activation(self.layout, plan)
        mark_initial_service_started(self.layout)
        mark_initial_verification_passed(self.layout, self._verification())
        result = fail_initial_activation(self.layout, reason="recovery")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, self.activation.activation_id)

    def _establish_update_candidate(self):
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source_unit, self.unit)
        for selection in ("active", "approved", "previous-known-good"):
            select_activation(self.layout, selection, self.activation)
        (self.layout.selection / "staged").unlink()

        application_id = "core-" + "6" * 40
        application = self.layout.revisions / application_id
        (application / "scripts").mkdir(parents=True)
        shutil.copy2(self.source_unit, application / "scripts" / "oracle-brain-standard.service")
        environment_id = "oracle-python-environment-v1:sha256:" + "7" * 64
        deployment_id = "oracle-household-deployment-v1:sha256:" + "8" * 64
        (self.layout.environments / environment_directory_name(environment_id)).mkdir()
        (self.layout.deployments / deployment_id).mkdir()
        request = ActivationRequest(
            core_commit="6" * 40,
            core_git_tree="9" * 40,
            application_revision_identity=application_id,
            python_environment_identity=environment_id,
            household_deployment_revision=deployment_id,
            configuration_activation_identity=self.activation.record["configuration_activation_identity"],
            service_definition_identity=self.activation.record["service_definition_identity"],
        )
        candidate = publish_activation(self.layout, request)
        select_activation(self.layout, "staged", candidate)
        return candidate

    def test_successful_update_selects_complete_candidate_only_after_verification(self) -> None:
        candidate = self._establish_update_candidate()
        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, plan)
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.activation.activation_id)
        select_managed_activation_target(self.layout)
        self.assertEqual(load_selected_activation(self.layout).activation_id, candidate.activation_id)
        mark_managed_service_started(self.layout)
        mark_managed_verification_passed(self.layout, self._verification())
        result = finalize_managed_activation(self.layout)
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, candidate.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "previous-known-good").activation_id, candidate.activation_id)
        self.assertFalse((self.layout.selection / "staged").exists())

    def test_failed_update_restores_complete_previous_and_retains_candidate_staged(self) -> None:
        candidate = self._establish_update_candidate()
        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, plan)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        result = recover_managed_activation(self.layout, reason="readiness_failed")
        self.assertEqual(result["outcome"], "recovered_previous")
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "previous-known-good").activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "staged").activation_id, candidate.activation_id)

    def test_explicit_rollback_selects_prior_but_keeps_latest_approved_record(self) -> None:
        candidate = self._establish_update_candidate()
        update = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, update)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        mark_managed_verification_passed(self.layout, self._verification())
        finalize_managed_activation(self.layout)

        rollback = build_rollback_activation_plan(
            self.layout,
            self.activation.activation_id,
            unit_path=self.unit,
        )
        prepare_managed_activation(self.layout, rollback)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        mark_managed_verification_passed(self.layout, self._verification())
        result = finalize_managed_activation(self.layout)

        self.assertEqual(result["operation"], "rollback")
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "previous-known-good").activation_id, self.activation.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, candidate.activation_id)


if __name__ == "__main__":
    unittest.main()
