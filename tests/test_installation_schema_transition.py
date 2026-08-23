from __future__ import annotations

from pathlib import Path
import json
import shutil
import tempfile
import unittest

from oracle_app.configuration import ConfigurationService, GenerationStore, snapshot_candidate
from oracle_app.installation import InstallationLayout, load_selected_activation, select_activation
from oracle_app.installation_assembly import (
    InitialAssemblyRequest,
    assemble_initial_activation,
    assemble_update_activation,
)
from oracle_app.installation_identity import environment_directory_name
from oracle_app.installation_schema_transition import (
    SchemaTransitionError,
    build_schema_transition_plan,
    finish_schema_transition,
    load_schema_transition,
    prepare_schema_transition,
    recover_schema_transition_before_managed_activation,
)
from oracle_app.installation_systemd import (
    StandardSystemdError,
    build_update_activation_plan,
    finalize_managed_activation,
    finalize_recovered_managed_activation,
    mark_managed_service_started,
    mark_managed_verification_passed,
    prepare_managed_activation,
    recover_managed_activation,
    select_managed_activation_target,
)


ROOT = Path(__file__).resolve().parents[1]


def verification(configuration_identity: str) -> dict[str, object]:
    return {
        "passed": True,
        "systemd_active": True,
        "readiness": True,
        "health": True,
        "configuration_identity": configuration_identity,
        "deterministic_interaction": True,
        "house_ui": True,
        "system_ui": True,
        "satellite_ui": True,
    }


class InstallationSchemaTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.layout = InstallationLayout(Path(self.temporary.name) / "oracle")
        for directory in self.layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        self.initial = InitialAssemblyRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-" + "1" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
        )
        self.target = InitialAssemblyRequest(
            core_commit="6" * 40,
            core_git_tree="7" * 40,
            application_revision_identity="core-" + "6" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "8" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "9" * 64,
        )
        for request in (self.initial, self.target):
            application = self.layout.revisions / request.application_revision_identity
            (application / "scripts").mkdir(parents=True)
            shutil.copy2(
                ROOT / "scripts/oracle-brain-standard.service",
                application / "scripts/oracle-brain-standard.service",
            )
            (self.layout.environments / environment_directory_name(request.python_environment_identity)).mkdir()
            deployment = self.layout.deployments / request.household_deployment_revision
            shutil.copytree(ROOT / "examples/config", deployment / "configuration")
        target_brain = (
            self.layout.deployments
            / self.target.household_deployment_revision
            / "configuration/brain.yaml"
        )
        target_brain.write_text(
            target_brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
            encoding="utf-8",
        )
        self.unit = Path(self.temporary.name) / "oracle-brain.service"
        shutil.copy2(ROOT / "scripts/oracle-brain-standard.service", self.unit)
        current = assemble_initial_activation(self.layout, self.initial)
        for selection in ("active", "approved", "previous-known-good"):
            select_activation(self.layout, selection, current)
        (self.layout.selection / "staged").unlink()
        self.previous_installation = current
        self.store = GenerationStore(self.layout.configuration, secret_root=self.layout.secrets)
        self.previous_configuration = self.store.load_selected()
        self.target_candidate = (
            self.layout.deployments
            / self.target.household_deployment_revision
            / "configuration"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _prepare_and_select_target_configuration(self) -> dict[str, object]:
        plan = build_schema_transition_plan(
            self.layout,
            self.target_candidate,
            target_core_commit=self.target.core_commit,
            target_core_git_tree=self.target.core_git_tree,
            target_python_environment_identity=self.target.python_environment_identity,
        )
        capsule = prepare_schema_transition(
            self.layout,
            self.target_candidate,
            target_core_commit=self.target.core_commit,
            target_core_git_tree=self.target.core_git_tree,
            target_python_environment_identity=self.target.python_environment_identity,
            approved_plan=str(plan["identity"]),
        )
        ConfigurationService(self.store).activate_candidate(
            self.target_candidate,
            expected_authored_revision=snapshot_candidate(self.target_candidate).authored_revision,
            expected_secret_generation_id=self.previous_configuration.secrets.generation_id,
            actor="host_local_cli",
        )
        return capsule

    def test_transition_is_forbidden_without_explicit_capsule(self) -> None:
        ConfigurationService(self.store).activate_candidate(
            self.target_candidate,
            expected_authored_revision=snapshot_candidate(self.target_candidate).authored_revision,
            expected_secret_generation_id=self.previous_configuration.secrets.generation_id,
            actor="host_local_cli",
        )
        with self.assertRaisesRegex(RuntimeError, "capsule"):
            assemble_update_activation(self.layout, self.target)

    def test_capsule_is_forbidden_for_normal_configuration_noop(self) -> None:
        initial_candidate = (
            self.layout.deployments
            / self.initial.household_deployment_revision
            / "configuration"
        )
        with self.assertRaisesRegex(SchemaTransitionError, "no-op"):
            build_schema_transition_plan(
                self.layout,
                initial_candidate,
                target_core_commit=self.target.core_commit,
                target_core_git_tree=self.target.core_git_tree,
                target_python_environment_identity=self.target.python_environment_identity,
            )

    def test_verified_transition_seals_target_and_removes_capsule(self) -> None:
        capsule = self._prepare_and_select_target_configuration()
        target = assemble_update_activation(self.layout, self.target)
        staged_capsule = load_schema_transition(self.layout)
        self.assertEqual(staged_capsule["target_installation_activation_id"], target.activation_id)
        self.assertNotEqual(staged_capsule["identity"], "")

        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        self.assertTrue(plan["configuration_change"])
        self.assertEqual(plan["schema_transition_capsule_identity"], staged_capsule["identity"])
        prepare_managed_activation(self.layout, plan)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        mark_managed_verification_passed(
            self.layout,
            verification(str(target.record["configuration_activation_identity"])),
        )
        result = finalize_managed_activation(self.layout)

        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(load_selected_activation(self.layout).activation_id, target.activation_id)
        self.assertEqual(
            self.store.load_selected().activation.generation_id,
            target.record["configuration_activation_identity"],
        )
        self.assertFalse((self.layout.control_state / "schema-transition-capsule.json").exists())
        self.assertTrue(capsule["identity"].startswith("oracle-schema-transition-capsule-v1:sha256:"))
        repeated = finish_schema_transition(
            self.layout, str(capsule["identity"]), outcome="verified",
        )
        self.assertEqual(repeated["outcome"], "verified")

    def test_failed_transition_restores_configuration_projections_and_installation(self) -> None:
        self._prepare_and_select_target_configuration()
        target = assemble_update_activation(self.layout, self.target)
        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, plan)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)

        pending = recover_managed_activation(self.layout, reason="injected_readiness_failure")
        recovered = self.store.load_selected()
        self.assertEqual(pending["state"], "recovered_previous")
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.previous_installation.activation_id)
        self.assertEqual(recovered.activation.generation_id, self.previous_configuration.activation.generation_id)
        self.assertEqual(
            dict(recovered.satellite_projection_activation_ids),
            dict(self.previous_configuration.satellite_projection_activation_ids),
        )
        capsule = load_schema_transition(self.layout)
        self.assertEqual(capsule["state"], "configuration_recovered")
        result = finalize_recovered_managed_activation(
            self.layout,
            verification(str(self.previous_installation.record["configuration_activation_identity"])),
        )
        self.assertEqual(result["outcome"], "recovered_previous")
        self.assertFalse((self.layout.control_state / "schema-transition-capsule.json").exists())
        self.assertFalse((self.layout.selection / "staged").exists())
        self.assertNotEqual(target.activation_id, self.previous_installation.activation_id)

    def test_recovery_resumes_after_configuration_and_installation_restore(self) -> None:
        self._prepare_and_select_target_configuration()
        assemble_update_activation(self.layout, self.target)
        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, plan)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        recover_managed_activation(self.layout, reason="injected_process_interruption")

        result = finalize_recovered_managed_activation(
            self.layout,
            verification(str(self.previous_installation.record["configuration_activation_identity"])),
        )

        self.assertEqual(result["outcome"], "recovered_previous")
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.previous_installation.activation_id)
        self.assertEqual(
            self.store.load_selected().activation.generation_id,
            self.previous_configuration.activation.generation_id,
        )

    def test_capsule_content_tampering_is_rejected(self) -> None:
        self._prepare_and_select_target_configuration()
        path = self.layout.control_state / "schema-transition-capsule.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["previous_configuration_selection"]["config_revision"] = "oracle-config-v2:sha256:" + "0" * 64
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

        with self.assertRaisesRegex(SchemaTransitionError, "content integrity"):
            load_schema_transition(self.layout)

    def test_pre_managed_recovery_restores_previous_complete_set(self) -> None:
        capsule = self._prepare_and_select_target_configuration()
        recovered = recover_schema_transition_before_managed_activation(self.layout)
        self.assertEqual(recovered["capsule_identity"], capsule["identity"])
        self.assertEqual(load_selected_activation(self.layout).activation_id, self.previous_installation.activation_id)
        self.assertEqual(
            self.store.load_selected().activation.generation_id,
            self.previous_configuration.activation.generation_id,
        )
        finish_schema_transition(self.layout, str(capsule["identity"]), outcome="recovered_previous")

    def test_cross_configuration_rollback_remains_forbidden_without_transition(self) -> None:
        self._prepare_and_select_target_configuration()
        target = assemble_update_activation(self.layout, self.target)
        plan = build_update_activation_plan(self.layout, unit_path=self.unit)
        prepare_managed_activation(self.layout, plan)
        select_managed_activation_target(self.layout)
        mark_managed_service_started(self.layout)
        mark_managed_verification_passed(
            self.layout,
            verification(str(target.record["configuration_activation_identity"])),
        )
        finalize_managed_activation(self.layout)
        with self.assertRaisesRegex(StandardSystemdError, "Rollback cannot cross"):
            from oracle_app.installation_systemd import build_rollback_activation_plan

            build_rollback_activation_plan(
                self.layout,
                self.previous_installation.activation_id,
                unit_path=self.unit,
            )


if __name__ == "__main__":
    unittest.main()
