from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import core_artifact
import installation_staging


class ProtectedInstallationStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "core-repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Oracle Test")
        self._git("config", "user.email", "oracle-test@example.invalid")
        (self.repo / "README.md").write_text("Oracle\n", encoding="utf-8")
        server = self.repo / "server"
        server.mkdir()
        (server / "oracle.py").write_text("print('oracle')\n", encoding="utf-8")
        (server / "requirements.lock").write_text(
            "example-package==1.2.3 \\\n+    --hash=sha256:" + "1" * 64 + "\n",
            encoding="utf-8",
        )
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "fixture")
        self.core_archive = self.root / "core.tar"
        core_artifact.build(self.repo, "HEAD", self.core_archive)
        self.household_archive = self._household_artifact()
        self.installation = self.root / "srv-oracle"
        self.revisions = self.installation / "revisions"
        self.environments = self.installation / "environments"
        self.deployments = self.installation / "deployments"
        self.owner_uid = os.geteuid()
        self.read_gid = os.getegid()
        for path in (self.revisions, self.environments, self.deployments):
            path.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def _household_artifact(self) -> Path:
        payload = self.root / "household-payload"
        (payload / "configuration").mkdir(parents=True)
        source = payload / "configuration" / "bundle.yaml"
        source.write_text("kind: oracle_configuration_bundle\n", encoding="utf-8")
        entry = {
            "destination": "configuration/bundle.yaml",
            "mode": "100644",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "type": "file",
        }
        basis = {
            "compatibility": {"configuration_schema": 1},
            "configuration": {"authored_revision": "oracle-authored-v1:sha256:" + "3" * 64, "root": "configuration"},
            "core": {"commit": self._git("rev-parse", "HEAD"), "git_tree": self._git("rev-parse", "HEAD^{tree}")},
            "deployment_metadata": {"purpose": "test"},
            "entries": [entry],
            "generated_configuration_inputs": {"canonical_bundle": "configuration"},
            "household_id": "test-household",
            "ingress": {"posture": "host-local"},
            "installation_profiles": ["minimal-brain"],
            "logical_secret_requirements": [],
            "migrations": [],
            "template": {"manifest_git_blob": "4" * 40, "manifest_sha256": "5" * 64, "template_id": "minimal-v1"},
        }
        ledger = self.root / "household-ledger.json"
        ledger.write_text(
            json.dumps({"deployment_revision": core_artifact._household_revision(basis), "revision_basis": basis}),
            encoding="utf-8",
        )
        output = self.root / "household.tar"
        core_artifact.build_household(ledger, payload, output)
        return output

    def test_verified_pair_publishes_exact_read_only_components_and_reuses_them(self) -> None:
        first = installation_staging.stage_artifact_pair(
            self.core_archive,
            self.household_archive,
            revisions=self.revisions,
            deployments=self.deployments,
            owner_uid=self.owner_uid,
            read_gid=self.read_gid,
        )
        self.assertFalse(first["application_reused"])
        self.assertFalse(first["deployment_reused"])
        application = Path(first["application_path"])
        deployment = Path(first["deployment_path"])
        self.assertEqual(core_artifact._tree_identity(application), self._git("rev-parse", "HEAD^{tree}"))
        self.assertEqual((application / "README.md").read_text(encoding="utf-8"), "Oracle\n")
        self.assertEqual((application / "README.md").stat().st_mode & 0o222, 0)
        self.assertEqual((deployment / "configuration" / "bundle.yaml").stat().st_mode & 0o222, 0)
        self.assertEqual((application.stat().st_uid, application.stat().st_gid), (self.owner_uid, self.read_gid))
        self.assertEqual((deployment.stat().st_uid, deployment.stat().st_gid), (self.owner_uid, self.read_gid))
        second = installation_staging.stage_artifact_pair(
            self.core_archive,
            self.household_archive,
            revisions=self.revisions,
            deployments=self.deployments,
            owner_uid=self.owner_uid,
            read_gid=self.read_gid,
        )
        self.assertTrue(second["application_reused"])
        self.assertTrue(second["deployment_reused"])
        self.assertEqual(first["artifact_sha256"], second["artifact_sha256"])

    def test_reuse_rejects_immutable_component_permission_drift(self) -> None:
        first = installation_staging.stage_artifact_pair(
            self.core_archive,
            self.household_archive,
            revisions=self.revisions,
            deployments=self.deployments,
            owner_uid=self.owner_uid,
            read_gid=self.read_gid,
        )
        Path(first["application_path"]).chmod(0o500)

        with self.assertRaisesRegex(
            installation_staging.InstallationStagingError,
            "mode has drifted",
        ):
            installation_staging.stage_artifact_pair(
                self.core_archive,
                self.household_archive,
                revisions=self.revisions,
                deployments=self.deployments,
                owner_uid=self.owner_uid,
                read_gid=self.read_gid,
            )

    def test_invalid_artifact_fails_before_managed_storage_changes(self) -> None:
        corrupted = self.root / "corrupt.tar"
        content = self.core_archive.read_bytes()
        self.assertIn(b"Oracle\n", content)
        corrupted.write_bytes(content.replace(b"Oracle\n", b"Broken\n", 1))
        with self.assertRaises(installation_staging.InstallationStagingError):
            installation_staging.stage_artifact_pair(
                corrupted,
                self.household_archive,
                revisions=self.revisions,
                deployments=self.deployments,
                owner_uid=self.owner_uid,
                read_gid=self.read_gid,
            )
        self.assertEqual(list(self.revisions.iterdir()), [])
        self.assertEqual(list(self.deployments.iterdir()), [])

    def test_environment_identity_covers_interpreter_platform_profile_and_lock(self) -> None:
        lock = self.repo / "server" / "requirements.lock"
        facts = {
            "implementation": "CPython",
            "version": "3.13.5",
            "abi": "cpython-313-x86_64-linux-gnu",
            "platform": "linux-x86_64",
            "system": "Linux",
            "architecture": "x86_64",
        }
        first = installation_staging.environment_record(Path("/usr/bin/python3"), "minimal-brain", lock, facts=facts)
        second = installation_staging.environment_record(Path("/different/python3"), "minimal-brain", lock, facts=facts)
        self.assertEqual(first, second)
        changed = dict(facts)
        changed["version"] = "3.13.6"
        third = installation_staging.environment_record(Path("/usr/bin/python3"), "minimal-brain", lock, facts=changed)
        self.assertNotEqual(first["environment_identity"], third["environment_identity"])
        self.assertEqual(
            installation_staging.environment_directory_name(str(first["environment_identity"])),
            "environment-" + str(first["environment_identity"]).rsplit(":", 1)[-1],
        )

    def test_environment_build_installs_hash_lock_validates_exact_set_and_publishes_once(self) -> None:
        application_result = installation_staging.stage_artifact_pair(
            self.core_archive,
            self.household_archive,
            revisions=self.revisions,
            deployments=self.deployments,
            owner_uid=self.owner_uid,
            read_gid=self.read_gid,
        )
        application = Path(application_result["application_path"])
        facts = {
            "implementation": "CPython",
            "version": "3.13.5",
            "abi": "cpython-313-x86_64-linux-gnu",
            "platform": "linux-x86_64",
            "system": "Linux",
            "architecture": "x86_64",
        }

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if argv[1:3] == ["-m", "venv"]:
                environment = Path(argv[3])
                (environment / "bin").mkdir(parents=True)
                python = environment / "bin" / "python"
                python.write_text("#!/bin/sh\n", encoding="utf-8")
                python.chmod(0o755)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with (
            mock.patch.object(installation_staging, "interpreter_facts", return_value=facts),
            mock.patch.object(installation_staging, "_installed_packages", return_value={"example-package": "1.2.3", "pip": "25.3"}),
            mock.patch.object(installation_staging.subprocess, "run", side_effect=fake_run) as run,
        ):
            result = installation_staging.build_python_environment(
                application,
                self.environments,
                Path("/usr/bin/python3"),
                owner_uid=self.owner_uid,
                read_gid=self.read_gid,
            )
        self.assertFalse(result["reused"])
        environment = Path(result["path"])
        self.assertEqual(
            environment.name,
            installation_staging.environment_directory_name(str(result["environment_identity"])),
        )
        self.assertTrue((environment / "oracle-environment.json").is_file())
        self.assertEqual((environment / "oracle-environment.json").stat().st_mode & 0o222, 0)
        self.assertEqual((environment.stat().st_uid, environment.stat().st_gid), (self.owner_uid, self.read_gid))
        commands = [call.args[0] for call in run.call_args_list]
        venv = next(command for command in commands if command[1:3] == ["-m", "venv"])
        self.assertEqual(Path(venv[3]), environment)
        install = next(command for command in commands if command[1:4] == ["-m", "pip", "install"])
        self.assertIn("--require-hashes", install)
        with (
            mock.patch.object(installation_staging, "interpreter_facts", return_value=facts),
            mock.patch.object(installation_staging, "_installed_packages", return_value={"example-package": "1.2.3", "pip": "25.3"}),
            mock.patch.object(installation_staging.subprocess, "run") as rerun,
        ):
            reused = installation_staging.build_python_environment(
                application,
                self.environments,
                Path("/usr/bin/python3"),
                owner_uid=self.owner_uid,
                read_gid=self.read_gid,
            )
        self.assertTrue(reused["reused"])
        self.assertEqual(rerun.call_count, 1)

    def test_published_environment_has_a_read_only_complete_validation_path(self) -> None:
        application = self.repo
        identity = installation_staging.ENVIRONMENT_PREFIX + "8" * 64
        environment = self.environments / installation_staging.environment_directory_name(identity)
        environment.mkdir()
        with mock.patch.object(
            installation_staging,
            "environment_record",
            return_value={"environment_identity": identity},
        ), mock.patch.object(
            installation_staging,
            "_validate_environment",
            return_value={"environment_identity": identity, "validated": True},
        ) as validate:
            result = installation_staging.validate_python_environment(application, environment)
        self.assertTrue(result["validated"])
        validate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
