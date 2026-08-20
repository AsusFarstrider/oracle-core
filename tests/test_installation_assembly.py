from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import GenerationStore, SecretSnapshot, load_runtime_cutover_marker
from oracle_app.installation import InstallationLayout, load_selected_activation, select_activation
from oracle_app.installation_assembly import (
    InitialAssemblyError,
    InitialAssemblyRequest,
    assemble_initial_activation,
    assemble_update_activation,
)
from oracle_app.installation_identity import environment_directory_name


REPO_ROOT = Path(__file__).resolve().parents[1]


class InitialInstallationAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.layout = InstallationLayout(Path(self.temporary.name) / "oracle")
        for directory in self.layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        self.request = InitialAssemblyRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-" + "1" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
        )
        application = self.layout.revisions / self.request.application_revision_identity
        (application / "scripts").mkdir(parents=True)
        shutil.copy2(
            REPO_ROOT / "scripts" / "oracle-brain-standard.service",
            application / "scripts" / "oracle-brain-standard.service",
        )
        (
            self.layout.environments
            / environment_directory_name(self.request.python_environment_identity)
        ).mkdir()
        deployment = self.layout.deployments / self.request.household_deployment_revision
        shutil.copytree(REPO_ROOT / "examples" / "config", deployment / "configuration")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initial_assembly_publishes_empty_secret_configuration_and_staged_complete_record(self) -> None:
        complete = assemble_initial_activation(self.layout, self.request)
        store = GenerationStore(self.layout.configuration, secret_root=self.layout.secrets)
        selected_configuration = store.load_selected()
        self.assertEqual(
            complete.record["configuration_activation_identity"],
            selected_configuration.activation.generation_id,
        )
        self.assertEqual(selected_configuration.secrets.snapshot.present_ids, frozenset())
        self.assertEqual(load_runtime_cutover_marker(store).activation_generation_id, selected_configuration.activation.generation_id)
        self.assertEqual(load_selected_activation(self.layout, "staged").activation_id, complete.activation_id)
        self.assertFalse((self.layout.selection / "active").exists())
        self.assertFalse((self.layout.selection / "approved").exists())
        self.assertFalse((self.layout.selection / "previous-known-good").exists())

    def test_initial_assembly_refuses_to_replace_existing_lifecycle_state(self) -> None:
        complete = assemble_initial_activation(self.layout, self.request)
        self.assertIsNotNone(complete)
        with self.assertRaisesRegex(InitialAssemblyError, "empty staged"):
            assemble_initial_activation(self.layout, self.request)

    def test_initial_assembly_accepts_separately_supplied_secret_authority(self) -> None:
        request = InitialAssemblyRequest(
            **{**self.request.__dict__, "initial_secret_snapshot": SecretSnapshot({"TOKEN": "value"})}
        )

        assemble_initial_activation(self.layout, request)

        selected = GenerationStore(self.layout.configuration, secret_root=self.layout.secrets).load_selected()
        self.assertEqual(selected.secrets.snapshot.present_ids, frozenset({"TOKEN"}))
        self.assertTrue(selected.secrets.raw_present)

    def _update_request(self) -> InitialAssemblyRequest:
        request = InitialAssemblyRequest(
            core_commit="6" * 40,
            core_git_tree="7" * 40,
            application_revision_identity="core-" + "6" * 40,
            python_environment_identity="oracle-python-environment-v1:sha256:" + "8" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "9" * 64,
        )
        application = self.layout.revisions / request.application_revision_identity
        (application / "scripts").mkdir(parents=True)
        shutil.copy2(
            REPO_ROOT / "scripts" / "oracle-brain-standard.service",
            application / "scripts" / "oracle-brain-standard.service",
        )
        (self.layout.environments / environment_directory_name(request.python_environment_identity)).mkdir()
        deployment = self.layout.deployments / request.household_deployment_revision
        shutil.copytree(REPO_ROOT / "examples" / "config", deployment / "configuration")
        return request

    def test_update_assembly_reuses_exact_selected_configuration_and_stages_complete_record(self) -> None:
        current = assemble_initial_activation(self.layout, self.request)
        for selection in ("active", "approved", "previous-known-good"):
            select_activation(self.layout, selection, current)
        (self.layout.selection / "staged").unlink()
        selected = GenerationStore(self.layout.configuration, secret_root=self.layout.secrets).load_selected()

        candidate = assemble_update_activation(self.layout, self._update_request())

        self.assertNotEqual(candidate.activation_id, current.activation_id)
        self.assertEqual(
            candidate.record["configuration_activation_identity"],
            selected.activation.generation_id,
        )
        self.assertEqual(load_selected_activation(self.layout, "staged").activation_id, candidate.activation_id)
        self.assertEqual(load_selected_activation(self.layout).activation_id, current.activation_id)

    def test_update_assembly_rejects_implicit_configuration_change(self) -> None:
        current = assemble_initial_activation(self.layout, self.request)
        for selection in ("active", "approved", "previous-known-good"):
            select_activation(self.layout, selection, current)
        (self.layout.selection / "staged").unlink()
        request = self._update_request()
        brain = self.layout.deployments / request.household_deployment_revision / "configuration" / "brain.yaml"
        brain.write_text(
            brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(InitialAssemblyError, "cannot implicitly change"):
            assemble_update_activation(self.layout, request)

    def test_update_assembly_allows_approved_to_differ_after_explicit_rollback(self) -> None:
        current = assemble_initial_activation(self.layout, self.request)
        for selection in ("active", "previous-known-good"):
            select_activation(self.layout, selection, current)
        (self.layout.selection / "staged").unlink()
        request = self._update_request()
        approved = assemble_update_activation(self.layout, request)
        select_activation(self.layout, "approved", approved)
        (self.layout.selection / "staged").unlink()

        candidate = assemble_update_activation(self.layout, request)

        self.assertEqual(load_selected_activation(self.layout).activation_id, current.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "approved").activation_id, approved.activation_id)
        self.assertEqual(load_selected_activation(self.layout, "staged").activation_id, candidate.activation_id)


if __name__ == "__main__":
    unittest.main()
