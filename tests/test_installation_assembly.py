from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import GenerationStore, load_runtime_cutover_marker
from oracle_app.installation import InstallationLayout, load_selected_activation
from oracle_app.installation_assembly import (
    InitialAssemblyError,
    InitialAssemblyRequest,
    assemble_initial_activation,
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


if __name__ == "__main__":
    unittest.main()
