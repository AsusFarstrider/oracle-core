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
from oracle_app.installation_systemd import (
    StandardSystemdError,
    build_initial_activation_plan,
    build_systemd_install_plan,
    fail_initial_activation,
    finalize_initial_activation,
    install_systemd_unit,
    mark_initial_service_started,
    mark_initial_verification_passed,
    prepare_initial_activation,
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
        (self.layout.environments / environment_id).mkdir()
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


if __name__ == "__main__":
    unittest.main()
