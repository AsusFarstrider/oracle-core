from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from oracle_app.installation import (
    ActivationRequest,
    InstallationLayout,
    InstallationLayoutError,
    activation_record,
    load_activation,
    load_selected_activation,
    publish_activation,
    select_activation,
)


class StandardInstallationActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.layout = InstallationLayout(Path(self.temporary.name) / "oracle")
        for directory in self.layout.required_directories():
            directory.mkdir(parents=True, exist_ok=True)
        (self.layout.configuration / "activations").mkdir()
        self.request = ActivationRequest(
            core_commit="1" * 40,
            core_git_tree="2" * 40,
            application_revision_identity="core-tree-" + "2" * 40,
            python_environment_identity="python-env-" + "3" * 64,
            household_deployment_revision="oracle-household-deployment-v1:sha256:" + "4" * 64,
            configuration_activation_identity="activation_" + "5" * 32,
            service_definition_identity="systemd-unit-" + "6" * 64,
        )
        self._make_components(self.request)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_components(self, request: ActivationRequest) -> None:
        for path in (
            self.layout.revisions / request.application_revision_identity,
            self.layout.environments / request.python_environment_identity,
            self.layout.deployments / request.household_deployment_revision,
            self.layout.configuration / "activations" / request.configuration_activation_identity,
        ):
            path.mkdir()

    def test_layout_is_the_ratified_single_root(self) -> None:
        relative = {path.relative_to(self.layout.root).as_posix() for path in self.layout.required_directories()}
        self.assertEqual(
            relative,
            {
                "revisions", "environments", "deployments", "configuration", "secrets",
                "activations", "selection", "state/installation", "state/control", "data", "cache", "tmp",
            },
        )

    def test_record_identity_is_deterministic_and_binds_complete_combination(self) -> None:
        first = activation_record(self.request)
        second = activation_record(self.request)
        self.assertEqual(first, second)
        changed = ActivationRequest(**{**self.request.__dict__, "python_environment_identity": "python-env-" + "7" * 64})
        self.assertNotEqual(first["activation_id"], activation_record(changed)["activation_id"])

    def test_publish_creates_immutable_record_and_confined_relative_links(self) -> None:
        installed = publish_activation(self.layout, self.request)
        self.assertEqual(load_activation(self.layout, installed.directory).activation_id, installed.activation_id)
        self.assertFalse(installed.directory.stat().st_mode & 0o200)
        self.assertFalse((installed.directory / "activation.json").stat().st_mode & 0o200)
        for name in ("application", "environment", "deployment", "configuration"):
            link = installed.directory / name
            self.assertTrue(link.is_symlink())
            self.assertFalse(Path(os.readlink(link)).is_absolute())

    def test_selection_is_one_atomic_link_to_complete_activation(self) -> None:
        installed = publish_activation(self.layout, self.request)
        selected = select_activation(self.layout, "active", installed)
        self.assertTrue(selected.is_symlink())
        self.assertEqual(load_selected_activation(self.layout).activation_id, installed.activation_id)
        self.assertEqual(selected.resolve().parent, self.layout.activations.resolve())

    def test_record_tampering_fails_integrity_validation(self) -> None:
        installed = publish_activation(self.layout, self.request)
        record_path = installed.directory / "activation.json"
        installed.directory.chmod(0o700)
        record_path.chmod(0o600)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["service_definition_identity"] = "systemd-unit-" + "9" * 64
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(InstallationLayoutError, "identity"):
            load_activation(self.layout, installed.directory)

    def test_absolute_or_escaping_component_reference_is_rejected(self) -> None:
        installed = publish_activation(self.layout, self.request)
        installed.directory.chmod(0o700)
        link = installed.directory / "application"
        link.unlink()
        link.symlink_to("/tmp")
        with self.assertRaises(InstallationLayoutError):
            load_activation(self.layout, installed.directory)

    def test_selector_cannot_target_a_component_or_another_selector(self) -> None:
        installed = publish_activation(self.layout, self.request)
        bad = self.layout.selection / "active"
        bad.symlink_to(os.path.relpath(self.layout.revisions / self.request.application_revision_identity, self.layout.selection))
        with self.assertRaisesRegex(InstallationLayoutError, "direct activation"):
            load_selected_activation(self.layout)
        bad.unlink()
        select_activation(self.layout, "staged", installed)
        bad.symlink_to("staged")
        with self.assertRaisesRegex(InstallationLayoutError, "direct activation"):
            load_selected_activation(self.layout)


if __name__ == "__main__":
    unittest.main()
