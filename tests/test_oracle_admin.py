from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
import os
import json
import pwd
import shutil
from contextlib import nullcontext

from oracle_app.configuration.secrets import SecretSnapshot


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("oracle_admin", ROOT / "scripts" / "oracle-admin.py")
assert SPEC is not None and SPEC.loader is not None
oracle_admin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle_admin)

ARTIFACT_SPEC = importlib.util.spec_from_file_location("core_artifact", ROOT / "scripts" / "core_artifact.py")
assert ARTIFACT_SPEC is not None and ARTIFACT_SPEC.loader is not None
core_artifact = importlib.util.module_from_spec(ARTIFACT_SPEC)
ARTIFACT_SPEC.loader.exec_module(core_artifact)


class OracleAdminPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.operator_account = pwd.getpwuid(os.getuid()).pw_name
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Oracle Test")
        self._git("config", "user.email", "oracle-test@example.invalid")
        (self.repo / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
        (self.repo / "README.md").write_text("Oracle\n", encoding="utf-8")
        scripts = self.repo / "scripts"
        scripts.mkdir()
        (scripts / "core_artifact.py").write_text("# fixture\n", encoding="utf-8")
        (scripts / "installation_staging.py").write_text("# fixture\n", encoding="utf-8")
        (scripts / "oracle-admin.py").write_text("# fixture\n", encoding="utf-8")
        oracle_app = self.repo / "server" / "oracle_app"
        oracle_app.mkdir(parents=True)
        (oracle_app / "installation_assembly.py").write_text("# fixture\n", encoding="utf-8")
        (oracle_app / "installation_identity.py").write_text("# fixture\n", encoding="utf-8")
        (oracle_app / "installation_systemd.py").write_text("# fixture\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "fixture")
        self.core = self.root / "core.tar"
        core_artifact.build(self.repo, "HEAD", self.core)
        self.household = self.root / "household.tar"
        payload = self.root / "payload"
        (payload / "configuration").mkdir(parents=True)
        source = payload / "configuration" / "bundle.yaml"
        source.write_text("kind: oracle_configuration_bundle\n", encoding="utf-8")
        entry = {
            "destination": "configuration/bundle.yaml",
            "mode": "100644",
            "sha256": core_artifact.hashlib.sha256(source.read_bytes()).hexdigest(),
            "type": "file",
        }
        basis = {
            "compatibility": {"configuration_schema": 1},
            "configuration": {"authored_revision": "oracle-authored-v1:sha256:" + "3" * 64, "root": "configuration"},
            "core": {"commit": self._git("rev-parse", "HEAD"), "git_tree": self._git("rev-parse", "HEAD^{tree}")},
            "deployment_metadata": {"purpose": "test"},
            "entries": [entry],
            "generated_configuration_inputs": {"canonical_bundle": "configuration"},
            "household_id": "test_household",
            "ingress": {"posture": "host-local"},
            "installation_profiles": ["minimal-brain"],
            "logical_secret_requirements": [],
            "migrations": [],
            "template": {"manifest_git_blob": "4" * 40, "manifest_sha256": "5" * 64, "template_id": "minimal-v1"},
        }
        ledger = self.root / "ledger.json"
        ledger.write_text(
            oracle_admin.json.dumps(
                {"deployment_revision": core_artifact._household_revision(basis), "revision_basis": basis}
            ),
            encoding="utf-8",
        )
        core_artifact.build_household(ledger, payload, self.household)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def _supported_platform(self) -> dict[str, object]:
        return {
            "support_status": "supported",
            "os": {"id": "debian", "version_id": "13", "major": "13", "pretty_name": "Debian GNU/Linux 13"},
            "architecture": "amd64",
            "kernel": "test",
            "machine": "x86_64",
            "service_manager": {"command": "/usr/bin/systemctl", "running": True},
            "package_tooling": {"apt_get": "/usr/bin/apt-get", "dpkg": "/usr/bin/dpkg"},
            "commands": {},
            "python": {"executable": "/usr/bin/python3", "implementation": "CPython", "version": "3.13.5", "abi": "", "venv_module": True, "ensurepip_module": True},
            "operator": {"effective_uid": 1000, "is_root": False, "elevation_available": True},
            "oracle_identities": {
                "service_account": {"name": "oracle", "existing": None},
                "groups": {"oracle": None, "oracle-admin": None},
            },
            "installation_root": {
                "path": "/srv/oracle",
                "exists": False,
                "storage": {"available": True},
                "layout_state": {"exists": False, "entries": [], "blockers": []},
            },
            "findings": [],
            "blockers": [],
        }

    def test_valid_pair_produces_stable_non_mutating_plan(self) -> None:
        with mock.patch.object(oracle_admin, "platform_preflight", return_value=self._supported_platform()):
            first = oracle_admin.build_install_preflight(self.core, self.household)
            second = oracle_admin.build_install_preflight(self.core, self.household)
        self.assertEqual(first["status"], "ready")
        self.assertFalse(first["mutation_performed"])
        self.assertEqual(first["plan"]["identity"], second["plan"]["identity"])
        self.assertEqual(first["format"], "oracle-admin-output-v1")
        self.assertEqual(first["artifacts"]["pair"]["core_commit"], self._git("rev-parse", "HEAD"))

    def test_mismatched_pair_fails_before_any_mutation(self) -> None:
        other = self.root / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.name", "Oracle Test"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "oracle-test@example.invalid"], cwd=other, check=True)
        (other / "LICENSE").write_text("Apache-2.0\n", encoding="utf-8")
        (other / "README.md").write_text("Other\n", encoding="utf-8")
        (other / "scripts").mkdir()
        (other / "scripts" / "core_artifact.py").write_text("# other\n", encoding="utf-8")
        (other / "scripts" / "oracle-admin.py").write_text("# other\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=other, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "other"], cwd=other, check=True)
        wrong_core = self.root / "wrong-core.tar"
        core_artifact.build(other, "HEAD", wrong_core)
        with mock.patch.object(oracle_admin, "platform_preflight", return_value=self._supported_platform()):
            result = oracle_admin.build_install_preflight(wrong_core, self.household)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["mutation_performed"])
        self.assertIn("artifact_pair_invalid", {item["code"] for item in result["blockers"]})

    def test_missing_ensurepip_is_acquirable_when_apt_exists(self) -> None:
        with (
            mock.patch.object(oracle_admin, "_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}),
            mock.patch.object(oracle_admin, "_debian_architecture", return_value="amd64"),
            mock.patch.object(oracle_admin, "_python_capabilities", return_value={"executable": "/usr/bin/python3", "implementation": "CPython", "version": "3.13.5", "abi": "", "venv_module": True, "ensurepip_module": False}),
            mock.patch.object(oracle_admin.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
            mock.patch.object(oracle_admin.sys, "version_info", (3, 13, 5)),
        ):
            result = oracle_admin.platform_preflight(self.root / "oracle")
        self.assertEqual(result["blockers"], [])
        self.assertIn("dependency_acquisition_required", {item["code"] for item in result["findings"]})

    def test_platform_preflight_uses_discovered_host_python_not_cli_interpreter(self) -> None:
        selected = Path("/usr/bin/python3")
        facts = {
            "executable": str(selected),
            "implementation": "CPython",
            "version": "3.13.5",
            "abi": "cpython-313-x86_64-linux-gnu",
            "venv_module": True,
            "ensurepip_module": True,
        }
        with (
            mock.patch.object(oracle_admin, "_host_python_candidate", return_value=selected),
            mock.patch.object(oracle_admin, "_python_capabilities", return_value=facts) as capabilities,
            mock.patch.object(oracle_admin, "_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}),
            mock.patch.object(oracle_admin, "_debian_architecture", return_value="amd64"),
            mock.patch.object(oracle_admin.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"),
        ):
            result = oracle_admin.platform_preflight(self.root / "oracle")
        capabilities.assert_called_once_with(selected)
        self.assertEqual(result["python"]["executable"], "/usr/bin/python3")

    def test_bootstrap_commands_start_without_site_packages(self) -> None:
        missing_core = self.root / "missing-core.tar"
        missing_household = self.root / "missing-household.tar"
        for command in ("preflight", "stage-plan", "stage"):
            argv = [
                os.fspath(Path(os.sys.executable)),
                "-S",
                os.fspath(ROOT / "scripts" / "oracle-admin.py"),
                "--json",
                command,
                "--core-artifact",
                os.fspath(missing_core),
                "--household-artifact",
                os.fspath(missing_household),
            ]
            if command == "stage":
                argv.extend(["--approved-plan", "oracle-operation-plan-v1:sha256:" + "0" * 64])
            if command in {"stage-plan", "stage"}:
                argv.extend(["--operator-account", self.operator_account])
            completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotIn("ModuleNotFoundError", completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["command"], command)

    def test_managed_cli_imports_do_not_create_application_bytecode(self) -> None:
        application = self.root / "managed-application"
        scripts = application / "scripts"
        package = application / "server" / "oracle_app"
        scripts.mkdir(parents=True)
        package.mkdir(parents=True)
        for name in ("core_artifact.py", "installation_staging.py", "oracle-admin.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        for name in ("__init__.py", "installation_identity.py", "installation_profiles.py"):
            shutil.copy2(ROOT / "server" / "oracle_app" / name, package / name)
        environment = dict(os.environ)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment.pop("PYTHONPYCACHEPREFIX", None)
        subprocess.run(
            [os.fspath(Path(os.sys.executable)), os.fspath(scripts / "oracle-admin.py"), "--help"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        self.assertEqual(list(application.rglob("__pycache__")), [])
        self.assertEqual(list(application.rglob("*.pyc")), [])

    def test_machine_readable_output_isolates_inherited_child_stdout(self) -> None:
        program = """
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("oracle_admin_output_test", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module._machine_readable_output(True):
    subprocess.run([sys.executable, "-c", "print('dependency output')"], check=True)
print(json.dumps({"status": "ready"}))
"""
        completed = subprocess.run(
            [os.fspath(Path(os.sys.executable)), "-c", program, os.fspath(ROOT / "scripts" / "oracle-admin.py")],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(json.loads(completed.stdout), {"status": "ready"})
        self.assertIn("dependency output", completed.stderr)
        self.assertNotIn("dependency output", completed.stdout)

    def test_post_staging_assembly_reexecutes_through_exact_environment(self) -> None:
        identity = "oracle-python-environment-v1:sha256:" + "6" * 64
        root = self.root / "managed"
        environment = root / "environments" / ("environment-" + "6" * 64)
        interpreter = environment / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("", encoding="utf-8")
        commit = "7" * 40
        script = root / "revisions" / ("core-" + commit) / "scripts" / "oracle-admin.py"
        script.parent.mkdir(parents=True)
        script.write_text("# managed fixture\n", encoding="utf-8")
        args = SimpleNamespace(command="assemble-plan", environment_identity=identity, core_artifact=self.core)
        argv = ["assemble-plan", "--core-artifact", str(self.core), "--environment-identity", identity]
        with (
            mock.patch.object(oracle_admin.sys, "prefix", str(self.root / "bootstrap")),
            mock.patch.object(oracle_admin, "verify", return_value={"core_commit": commit}),
            mock.patch.object(oracle_admin.os, "execv") as execute,
        ):
            oracle_admin._reexecute_post_staging_command(args, argv, root=root)
        execute.assert_called_once_with(
            str(interpreter),
            [str(interpreter), "-B", str(script), *argv],
        )

    def test_standard_layout_plan_has_only_ratified_lifecycle_roots(self) -> None:
        root = self.root / "oracle"
        plan = oracle_admin.standard_layout_plan(root)
        relative = {
            Path(item["path"]).relative_to(root).as_posix()
            for item in plan["directories"]
        }
        self.assertEqual(
            relative,
            {
                "revisions", "environments", "deployments", "configuration", "secrets",
                "activations", "selection", "state", "state/installation", "state/control", "data", "cache", "tmp",
            },
        )
        by_path = {
            Path(item["path"]).relative_to(root).as_posix(): item
            for item in plan["directories"]
        }
        self.assertEqual(by_path["revisions"]["owner"], "root")
        self.assertEqual(by_path["environments"]["authority"], "elevated_maintenance")
        self.assertEqual(by_path["configuration"]["owner"], "oracle")
        self.assertEqual(by_path["secrets"]["mode"], "0700")
        self.assertEqual(by_path["data"]["authority"], "oracle_runtime")
        self.assertEqual(plan["identities"]["online_operator_group"], "oracle-admin")
        self.assertFalse(plan["identities"]["implicit_operator_membership"])

    def test_staging_plan_is_distinct_and_excludes_activation_and_service_mutation(self) -> None:
        with mock.patch.object(oracle_admin, "platform_preflight", return_value=self._supported_platform()):
            result = oracle_admin.build_staging_preflight(
                self.core,
                self.household,
                self.operator_account,
                root=self.root / "oracle",
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["plan"]["operation"], "stage-installation-foundation")
        self.assertIn("activation creation", result["plan"]["excluded"])
        self.assertIn("systemd installation", result["plan"]["excluded"])
        self.assertEqual(
            result["plan"]["operator_enrollment"]["name"],
            self.operator_account,
        )
        self.assertFalse(result["mutation_performed"])

    def test_staging_plan_rejects_profiles_outside_bounded_minimal_brain_slice(self) -> None:
        base = {
            "format": "oracle-admin-output-v1",
            "command": "preflight",
            "status": "ready",
            "mutation_performed": False,
            "platform": self._supported_platform(),
            "artifacts": {"archives": {}, "pair": {"installation_profiles": ["minimal-brain", "fast-whisper"]}},
            "plan": {},
            "blockers": [],
        }
        with mock.patch.object(oracle_admin, "build_install_preflight", return_value=base):
            result = oracle_admin.build_staging_preflight(
                self.core,
                self.household,
                self.operator_account,
                root=self.root / "oracle",
            )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("unsupported_staging_profile_set", {item["code"] for item in result["blockers"]})

    def test_initial_assembly_plan_requires_exact_staged_components_and_changes_no_selection(self) -> None:
        installation = self.root / "oracle"
        pair = oracle_admin.artifact_preflight(self.core, self.household)["pair"]
        assert pair is not None
        application_identity = "core-" + pair["core_commit"]
        deployment_identity = pair["deployment_revision"]
        environment_identity = "oracle-python-environment-v1:sha256:" + "7" * 64
        (installation / "revisions" / application_identity).mkdir(parents=True)
        deployment = installation / "deployments" / deployment_identity
        (deployment / "configuration").mkdir(parents=True)
        environment = installation / "environments" / ("environment-" + "7" * 64)
        environment.mkdir(parents=True)
        (environment / "oracle-environment.json").write_text(
            json.dumps({"environment_identity": environment_identity}), encoding="utf-8"
        )
        (installation / "selection").mkdir(parents=True)
        eligible = SimpleNamespace(report=SimpleNamespace(activation_eligible=True))
        snapshot = SimpleNamespace(authored_revision=pair["configuration"]["authored_revision"])
        with (
            mock.patch.object(oracle_admin, "inspect_candidate", return_value=eligible),
            mock.patch.object(oracle_admin, "initial_safety_acknowledgements", return_value=frozenset()),
            mock.patch.object(oracle_admin, "snapshot_candidate", return_value=snapshot),
            mock.patch.object(
                oracle_admin,
                "_payload_inventory",
                side_effect=lambda path: (
                    oracle_admin.verify(self.core)["inventory"]
                    if path == installation / "revisions" / application_identity
                    else oracle_admin.verify(self.household)["inventory"]
                ),
            ),
            mock.patch.object(oracle_admin, "_tree_identity", return_value=pair["core_git_tree"]),
            mock.patch.object(oracle_admin, "validate_python_environment"),
        ):
            result = oracle_admin.build_initial_assembly_plan(
                self.core,
                self.household,
                environment_identity,
                root=installation,
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["plan"]["operation"], "assemble-initial-activation")
        self.assertEqual(result["plan"]["target"]["required_safety_acknowledgements"], [])
        self.assertIn("active selection", result["plan"]["excluded"])
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(list((installation / "selection").iterdir()), [])

    def test_update_assembly_plan_carries_forward_selected_secret_identity(self) -> None:
        installation = self.root / "oracle"
        artifacts = oracle_admin.artifact_preflight(self.core, self.household)
        pair = dict(artifacts["pair"])
        pair["logical_secret_requirements"] = ["API_TOKEN"]
        application_identity = "core-" + pair["core_commit"]
        deployment_identity = pair["deployment_revision"]
        environment_identity = "oracle-python-environment-v1:sha256:" + "7" * 64
        (installation / "revisions" / application_identity).mkdir(parents=True)
        deployment = installation / "deployments" / deployment_identity
        (deployment / "configuration").mkdir(parents=True)
        environment = installation / "environments" / ("environment-" + "7" * 64)
        environment.mkdir(parents=True)
        (installation / "selection").mkdir(parents=True)
        secrets = SecretSnapshot({"API_TOKEN": "private-value"})
        selected = SimpleNamespace(
            config=SimpleNamespace(config_revision="oracle-config-v1:sha256:" + "8" * 64),
            secrets=SimpleNamespace(snapshot=secrets),
        )
        eligible = SimpleNamespace(
            report=SimpleNamespace(activation_eligible=True),
            normalized=SimpleNamespace(config_revision=selected.config.config_revision),
        )
        snapshot = SimpleNamespace(authored_revision=pair["configuration"]["authored_revision"])
        installed = SimpleNamespace(activation_id="oracle-installation-activation-v1:sha256:" + "9" * 64)
        generation_store = mock.Mock()
        generation_store.load_selected.return_value = selected
        with (
            mock.patch.object(
                oracle_admin,
                "artifact_preflight",
                return_value={**artifacts, "pair": pair},
            ),
            mock.patch.object(oracle_admin, "inspect_candidate", return_value=eligible),
            mock.patch.object(oracle_admin, "snapshot_candidate", return_value=snapshot),
            mock.patch.object(
                oracle_admin,
                "_payload_inventory",
                side_effect=lambda path: (
                    oracle_admin.verify(self.core)["inventory"]
                    if path == installation / "revisions" / application_identity
                    else oracle_admin.verify(self.household)["inventory"]
                ),
            ),
            mock.patch.object(oracle_admin, "_tree_identity", return_value=pair["core_git_tree"]),
            mock.patch.object(oracle_admin, "validate_python_environment"),
            mock.patch.object(oracle_admin, "load_selected_activation", return_value=installed),
            mock.patch(
                "oracle_app.configuration.generations.GenerationStore",
                return_value=generation_store,
            ),
        ):
            result = oracle_admin.build_update_assembly_plan(
                self.core,
                self.household,
                environment_identity,
                root=installation,
            )
        expected_identity = "oracle-secret-companion-v1:sha256:" + oracle_admin.hashlib.sha256(
            secrets._companion_bytes()
        ).hexdigest()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["plan"]["target"]["secret_companion_identity"], expected_identity)
        self.assertNotIn("private-value", json.dumps(result))

    def test_initial_publication_drops_to_service_authority_and_restores_elevation(self) -> None:
        account = SimpleNamespace(pw_uid=901)
        groups = {
            "oracle": SimpleNamespace(gr_gid=902),
            "oracle-admin": SimpleNamespace(gr_gid=903),
        }
        calls: list[tuple[str, object]] = []
        with (
            mock.patch.object(oracle_admin.pwd, "getpwnam", return_value=account),
            mock.patch.object(oracle_admin.grp, "getgrnam", side_effect=lambda name: groups[name]),
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin.os, "getegid", return_value=0),
            mock.patch.object(oracle_admin.os, "getgroups", return_value=[0]),
            mock.patch.object(oracle_admin.os, "setgroups", side_effect=lambda value: calls.append(("groups", value))),
            mock.patch.object(oracle_admin.os, "setegid", side_effect=lambda value: calls.append(("gid", value))),
            mock.patch.object(oracle_admin.os, "seteuid", side_effect=lambda value: calls.append(("uid", value))),
        ):
            with oracle_admin._service_authority():
                calls.append(("body", 1))
        self.assertEqual(
            calls,
            [
                ("groups", [902, 903]),
                ("gid", 902),
                ("uid", 901),
                ("body", 1),
                ("uid", 0),
                ("gid", 0),
                ("groups", [0]),
            ],
        )

    def test_initial_assembly_rejects_missing_or_extra_plan_acknowledgements_before_mutation(self) -> None:
        plan = {
            "identity": "oracle-operation-plan-v1:sha256:" + "1" * 64,
            "target": {"required_safety_acknowledgements": ["access_expansion"]},
        }
        preflight = {"status": "ready", "plan": plan}
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_initial_assembly_plan", return_value=preflight),
            mock.patch.object(oracle_admin, "_maintenance_lock") as maintenance_lock,
        ):
            for provided in (frozenset(), frozenset({"access_expansion", "public_health_enablement"})):
                with self.assertRaisesRegex(RuntimeError, "exact safety acknowledgements"):
                    oracle_admin.execute_initial_assembly(
                        self.core,
                        self.household,
                        "oracle-python-environment-v1:sha256:" + "2" * 64,
                        plan["identity"],
                        root=self.root / "oracle",
                        acknowledgements=provided,
                    )
        maintenance_lock.assert_not_called()

    def test_service_plan_keeps_unit_disabled_and_binds_current_systemd_state(self) -> None:
        exact = SimpleNamespace(
            identity="oracle-systemd-install-plan-v1:sha256:" + "1" * 64,
            source=Path("/srv/oracle/selection/staged/application/scripts/oracle-brain-standard.service"),
            destination=Path("/etc/systemd/system/oracle-brain.service"),
            service_definition_identity="systemd-unit-" + "2" * 64,
            disposition="install",
        )
        with (
            mock.patch.object(oracle_admin, "_systemctl_property", side_effect=["inactive", "disabled"]),
            mock.patch.object(oracle_admin, "build_systemd_install_plan", return_value=exact),
        ):
            result = oracle_admin.build_service_install_preflight(root=self.root / "oracle")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["plan"]["current_systemd_state"], {"active": "inactive", "enabled": "disabled"})
        self.assertIn("systemd enable", result["plan"]["excluded"])

    def test_service_plan_blocks_an_already_active_or_enabled_unit(self) -> None:
        exact = SimpleNamespace(
            identity="oracle-systemd-install-plan-v1:sha256:" + "1" * 64,
            source=Path("/source"),
            destination=Path("/destination"),
            service_definition_identity="systemd-unit-" + "2" * 64,
            disposition="reuse",
        )
        with (
            mock.patch.object(oracle_admin, "_systemctl_property", side_effect=["active", "enabled"]),
            mock.patch.object(oracle_admin, "build_systemd_install_plan", return_value=exact),
        ):
            result = oracle_admin.build_service_install_preflight(root=self.root / "oracle")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            {item["code"] for item in result["blockers"]},
            {"standard_service_already_active", "standard_service_already_enabled"},
        )

    def test_initial_runtime_verification_requires_all_distinct_gates(self) -> None:
        config_activation = "activation_" + "5" * 32
        responses = {
            "http://127.0.0.1:8011/health": {"status": "ok", "service": "oracle-brain"},
            "http://127.0.0.1:8011/api/admin/health/config": {
                "ok": True,
                "configuration": {
                    "mode": "canonical",
                    "applied_generation": {"activation_generation_id": config_activation},
                },
            },
        }
        command = {
            "reply_text": "It is 3 PM.",
            "source_id": "canonical-installation-verifier",
            "session_id": "stage4-install-verifier",
            "status": "executed",
            "failure_code": None,
            "trace_id": "trace-installation-verification",
            "effects": {},
        }
        with (
            mock.patch.object(oracle_admin, "_systemctl_property", return_value="active"),
            mock.patch.object(
                oracle_admin,
                "_http_json",
                side_effect=lambda url, payload=None: command if payload is not None else responses[url],
            ),
            mock.patch.object(oracle_admin, "_http_text", return_value="<!doctype html>"),
        ):
            result = oracle_admin.verify_initial_runtime(config_activation, timeout_seconds=0)
        self.assertTrue(result["passed"])
        self.assertTrue(all(result.values()))

    def test_initial_activation_orchestration_finalizes_only_after_runtime_verification(self) -> None:
        plan = {"identity": "oracle-initial-activation-plan-v1:sha256:" + "1" * 64}
        verification = {
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
        transaction = {"transaction_id": "initial_activation_" + "2" * 32}
        active = SimpleNamespace(record={"configuration_activation_identity": "activation_" + "3" * 32})
        final = {"transaction_id": transaction["transaction_id"], "candidate_activation_id": "candidate-1"}
        preflight = {"status": "ready", "plan": plan}
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_activation_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "_maintenance_lock", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "_service_authority", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "prepare_initial_activation", return_value=transaction),
            mock.patch.object(oracle_admin, "mark_initial_service_started") as started,
            mock.patch.object(oracle_admin, "verify_initial_runtime", return_value=verification) as verify,
            mock.patch.object(oracle_admin, "mark_initial_verification_passed") as marked,
            mock.patch.object(oracle_admin, "finalize_initial_activation", return_value=final) as finalized,
            mock.patch.object(oracle_admin, "load_selected_activation", return_value=active),
            mock.patch.object(oracle_admin, "_write_operation_evidence", return_value=Path("/evidence")),
            mock.patch.object(oracle_admin.subprocess, "run") as run,
        ):
            result = oracle_admin.execute_initial_activation(plan["identity"], root=self.root / "oracle")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["systemctl", "enable", "oracle-brain.service"], ["systemctl", "start", "oracle-brain.service"]],
        )
        started.assert_called_once()
        verify.assert_called_once_with("activation_" + "3" * 32)
        marked.assert_called_once_with(mock.ANY, verification)
        finalized.assert_called_once()

    def test_failed_first_activation_uses_elevated_transaction_cleanup_without_known_good(self) -> None:
        plan = {"identity": "oracle-initial-activation-plan-v1:sha256:" + "1" * 64}
        transaction = {
            "transaction_id": "initial_activation_" + "2" * 32,
            "candidate_activation_id": "candidate-1",
        }
        failed = {
            **transaction,
            "verification": {"passed": False, "reason": "CalledProcessError"},
        }
        preflight = {"status": "ready", "plan": plan}

        def systemctl(command, **_kwargs):
            if command == ["systemctl", "start", "oracle-brain.service"]:
                raise subprocess.CalledProcessError(1, command)
            return mock.DEFAULT

        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_activation_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "_maintenance_lock", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "_service_authority", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "prepare_initial_activation", return_value=transaction),
            mock.patch.object(oracle_admin, "fail_initial_activation", return_value=failed) as cleanup,
            mock.patch.object(oracle_admin, "_write_operation_evidence", return_value=Path("/evidence")),
            mock.patch.object(oracle_admin.subprocess, "run", side_effect=systemctl) as run,
        ):
            result = oracle_admin.execute_initial_activation(plan["identity"], root=self.root / "oracle")

        self.assertEqual(result["status"], "recovered_failed")
        self.assertFalse(result["approved"])
        self.assertFalse(result["previous_known_good"])
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["systemctl", "enable", "oracle-brain.service"],
                ["systemctl", "start", "oracle-brain.service"],
                ["systemctl", "stop", "oracle-brain.service"],
                ["systemctl", "disable", "oracle-brain.service"],
            ],
        )
        cleanup.assert_called_once_with(mock.ANY, reason="CalledProcessError")

    def test_managed_update_stops_selects_starts_and_finalizes_only_after_verification(self) -> None:
        plan = {
            "identity": "oracle-update-activation-plan-v1:sha256:" + "1" * 64,
            "operation": "update",
        }
        preflight = {"status": "ready", "plan": plan}
        transaction = {"transaction_id": "managed_activation_" + "2" * 32}
        active = SimpleNamespace(record={"configuration_activation_identity": "activation_" + "3" * 32})
        verification = {
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
        final = {
            "transaction_id": transaction["transaction_id"],
            "previous_activation_id": "previous",
            "target_activation_id": "candidate",
            "outcome": "verified",
        }
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_managed_activation_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "_maintenance_lock", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "_service_authority", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "prepare_managed_activation", return_value=transaction),
            mock.patch.object(oracle_admin, "select_managed_activation_target") as selected,
            mock.patch.object(oracle_admin, "mark_managed_service_started") as started,
            mock.patch.object(oracle_admin, "verify_initial_runtime", return_value=verification) as verify,
            mock.patch.object(oracle_admin, "mark_managed_verification_passed") as marked,
            mock.patch.object(oracle_admin, "finalize_managed_activation", return_value=final),
            mock.patch.object(oracle_admin, "load_selected_activation", return_value=active),
            mock.patch.object(
                oracle_admin,
                "_write_operation_evidence",
                return_value=Path("/evidence"),
            ) as evidence,
            mock.patch.object(oracle_admin.subprocess, "run") as run,
        ):
            result = oracle_admin.execute_managed_activation(
                plan["identity"],
                operation="update",
                root=self.root / "oracle",
            )
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["systemctl", "stop", "oracle-brain.service"], ["systemctl", "start", "oracle-brain.service"]],
        )
        selected.assert_called_once()
        started.assert_called_once()
        verify.assert_called_once_with("activation_" + "3" * 32)
        marked.assert_called_once_with(mock.ANY, verification)
        evidence.assert_called_once_with(
            self.root / "oracle",
            "update-results",
            transaction["transaction_id"],
            mock.ANY,
        )

    def test_failed_managed_update_restores_and_verifies_previous_complete_activation(self) -> None:
        plan = {
            "identity": "oracle-update-activation-plan-v1:sha256:" + "1" * 64,
            "operation": "update",
        }
        preflight = {"status": "ready", "plan": plan}
        transaction = {"transaction_id": "managed_activation_" + "2" * 32}
        candidate = SimpleNamespace(record={"configuration_activation_identity": "activation_" + "3" * 32})
        previous = SimpleNamespace(record={"configuration_activation_identity": "activation_" + "4" * 32})
        recovered = {
            "transaction_id": transaction["transaction_id"],
            "previous_activation_id": "previous",
            "target_activation_id": "candidate",
            "verification": {"passed": False, "reason": "RuntimeError"},
        }
        recovery_verification = {
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
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_managed_activation_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "_maintenance_lock", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "_service_authority", return_value=nullcontext()),
            mock.patch.object(oracle_admin, "prepare_managed_activation", return_value=transaction),
            mock.patch.object(oracle_admin, "select_managed_activation_target"),
            mock.patch.object(oracle_admin, "mark_managed_service_started"),
            mock.patch.object(
                oracle_admin,
                "verify_initial_runtime",
                side_effect=[RuntimeError("candidate unhealthy"), recovery_verification],
            ),
            mock.patch.object(oracle_admin, "recover_managed_activation", return_value=recovered) as recover,
            mock.patch.object(oracle_admin, "load_selected_activation", side_effect=[candidate, previous]),
            mock.patch.object(
                oracle_admin,
                "_write_operation_evidence",
                return_value=Path("/evidence"),
            ) as evidence,
            mock.patch.object(oracle_admin.subprocess, "run") as run,
        ):
            result = oracle_admin.execute_managed_activation(
                plan["identity"],
                operation="update",
                root=self.root / "oracle",
            )
        self.assertEqual(result["status"], "recovered_failed")
        self.assertTrue(result["automatic_recovery"])
        self.assertTrue(result["recovery_verification"]["passed"])
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["systemctl", "stop", "oracle-brain.service"],
                ["systemctl", "start", "oracle-brain.service"],
                ["systemctl", "stop", "oracle-brain.service"],
                ["systemctl", "start", "oracle-brain.service"],
            ],
        )
        recover.assert_called_once_with(mock.ANY, reason="RuntimeError")
        evidence.assert_called_once_with(
            self.root / "oracle",
            "update-results",
            transaction["transaction_id"],
            mock.ANY,
        )

    def test_existing_interactive_or_privileged_oracle_identity_blocks_reuse(self) -> None:
        identities = {
            "service_account": {
                "name": "oracle",
                "existing": {
                    "uid": 1500,
                    "primary_gid": 200,
                    "home": "/home/oracle",
                    "shell": "/bin/bash",
                    "supplementary_groups": ["sudo"],
                },
            },
            "groups": {"oracle": {"gid": 200, "members": []}, "oracle-admin": {"gid": 201, "members": []}},
        }
        with mock.patch.object(oracle_admin, "_system_uid_limit", return_value=1000):
            codes = {item["code"] for item in oracle_admin.identity_blockers(identities)}
        self.assertIn("oracle_identity_not_system_account", codes)
        self.assertIn("oracle_identity_login_shell_conflict", codes)
        self.assertIn("oracle_identity_broad_privilege_conflict", codes)

    def test_identity_creation_uses_conventional_system_non_login_account_without_operator_membership(self) -> None:
        absent = {
            "service_account": {"name": "oracle", "existing": None},
            "groups": {"oracle": None, "oracle-admin": None},
        }
        existing = {
            "service_account": {
                "name": "oracle",
                "existing": {
                    "uid": 900,
                    "primary_gid": 901,
                    "home": "/nonexistent",
                    "shell": "/usr/sbin/nologin",
                    "supplementary_groups": [],
                },
            },
            "groups": {"oracle": {"gid": 901, "members": []}, "oracle-admin": {"gid": 902, "members": []}},
        }
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "_identity_probe", side_effect=[absent, existing]),
            mock.patch.object(oracle_admin, "_system_uid_limit", return_value=1000),
            mock.patch.object(oracle_admin, "_password_status", side_effect=["L", "L"]),
            mock.patch.object(oracle_admin.subprocess, "run") as run,
        ):
            result = oracle_admin.ensure_standard_identities()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["groupadd", "--system", "oracle"], commands)
        self.assertIn(["groupadd", "--system", "oracle-admin"], commands)
        useradd = next(command for command in commands if command[0] == "useradd")
        self.assertIn("--no-create-home", useradd)
        self.assertIn("/usr/sbin/nologin", useradd)
        self.assertNotIn("oracle-admin", useradd)
        self.assertEqual(len(result["created"]), 3)
        self.assertTrue(result["password_locked"])

    def test_layout_creation_applies_lifecycle_owners_and_modes_without_extra_roots(self) -> None:
        root = self.root / "managed-oracle"
        account = SimpleNamespace(pw_uid=os.getuid())
        group = SimpleNamespace(gr_gid=os.getgid())
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin.pwd, "getpwnam", return_value=account),
            mock.patch.object(oracle_admin.grp, "getgrnam", return_value=group),
            mock.patch.object(oracle_admin.os, "chown") as chown,
        ):
            result = oracle_admin.ensure_standard_layout(root)
        self.assertEqual({path.name for path in root.iterdir()}, {
            "revisions", "environments", "deployments", "configuration", "secrets", "activations",
            "selection", "state", "data", "cache", "tmp",
        })
        self.assertEqual((root / "secrets").stat().st_mode & 0o777, 0o700)
        self.assertEqual((root / "revisions").stat().st_mode & 0o777, 0o750)
        self.assertEqual((root / "configuration").stat().st_mode & 0o7777, 0o2750)
        self.assertEqual((root / "activations").stat().st_mode & 0o7777, 0o2750)
        self.assertEqual((root / "state" / "control").stat().st_mode & 0o7777, 0o2750)
        self.assertTrue((root / "state" / "installation").is_dir())
        self.assertGreaterEqual(chown.call_count, 14)
        self.assertIn(str(root), result["created"])

    def test_layout_probe_rejects_undeclared_root_content_before_reconciliation(self) -> None:
        root = self.root / "managed-oracle"
        root.mkdir()
        (root / "unmanaged.txt").write_text("do not overwrite\n", encoding="utf-8")
        result = oracle_admin._layout_probe(root)
        self.assertIn("installation_root_undeclared_entry", {item["code"] for item in result["blockers"]})

    def test_stage_rejects_stale_plan_before_any_mutation(self) -> None:
        preflight = {
            "status": "ready",
            "plan": {"identity": "oracle-operation-plan-v1:sha256:" + "1" * 64},
            "platform": self._supported_platform(),
        }
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_staging_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "ensure_standard_identities") as identities,
        ):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                oracle_admin.execute_staging(
                    self.core,
                    self.household,
                    self.operator_account,
                    "wrong",
                    root=self.root / "oracle",
                )
        identities.assert_not_called()

    def test_operator_enrollment_is_explicit_bounded_and_verified(self) -> None:
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        primary = SimpleNamespace(gr_name="users", gr_gid=1000, gr_mem=[])
        operator_before = SimpleNamespace(gr_name="oracle-admin", gr_gid=988, gr_mem=[])
        operator_after = SimpleNamespace(gr_name="oracle-admin", gr_gid=988, gr_mem=["lukas"])
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin.pwd, "getpwnam", return_value=account),
            mock.patch.object(
                oracle_admin.grp,
                "getgrall",
                side_effect=[[primary, operator_before], [primary, operator_after]],
            ),
            mock.patch.object(oracle_admin.grp, "getgrnam", return_value=operator_before),
            mock.patch.object(oracle_admin.subprocess, "run") as run,
        ):
            result = oracle_admin.ensure_operator_membership("lukas")

        run.assert_called_once_with(
            ["usermod", "--append", "--groups", "oracle-admin", "lukas"],
            check=True,
        )
        self.assertEqual(result["disposition"], "added")
        self.assertEqual(result["account"], "lukas")

    def test_staging_plan_rejects_missing_or_privileged_operator_account(self) -> None:
        base = {
            "format": "oracle-admin-output-v1",
            "command": "preflight",
            "status": "ready",
            "mutation_performed": False,
            "platform": self._supported_platform(),
            "artifacts": {"archives": {}, "pair": {"installation_profiles": ["minimal-brain"]}},
            "plan": {},
            "blockers": [],
        }
        for account, probe, expected in (
            ("missing", {"name": "missing", "existing": None}, "operator_account_unavailable"),
            (
                "root",
                {"name": "root", "existing": {"uid": 0, "primary_gid": 0, "groups": ["root"]}},
                "operator_account_unsafe",
            ),
        ):
            with (
                self.subTest(account=account),
                mock.patch.object(oracle_admin, "build_install_preflight", return_value=base),
                mock.patch.object(oracle_admin, "_operator_account_probe", return_value=probe),
            ):
                result = oracle_admin.build_staging_preflight(
                    self.core,
                    self.household,
                    account,
                    root=self.root / "oracle",
                )
            self.assertIn(expected, {item["code"] for item in result["blockers"]})

    def test_maintenance_lock_rejects_concurrent_mutation(self) -> None:
        lock = self.root / "oracle-installation.lock"
        with oracle_admin._maintenance_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "another Oracle"):
                with oracle_admin._maintenance_lock(lock):
                    self.fail("concurrent lock unexpectedly acquired")

    def test_exact_approved_stage_wires_bounded_components_without_activation(self) -> None:
        identity = "oracle-operation-plan-v1:sha256:" + "1" * 64
        platform_result = self._supported_platform()
        preflight = {"status": "ready", "plan": {"identity": identity}, "platform": platform_result}
        components = {
            "application_revision_identity": "core-" + "2" * 40,
            "application_path": str(self.root / "oracle" / "revisions" / ("core-" + "2" * 40)),
            "household_deployment_revision": "oracle-household-deployment-v1:sha256:" + "3" * 64,
        }
        environment = {
            "environment_identity": "oracle-python-environment-v1:sha256:" + "4" * 64,
            "path": str(self.root / "oracle" / "environments" / ("environment-" + "4" * 64)),
        }
        with (
            mock.patch.object(oracle_admin.os, "geteuid", return_value=0),
            mock.patch.object(oracle_admin, "build_staging_preflight", return_value=preflight),
            mock.patch.object(oracle_admin, "_ensure_python_environment_support", return_value={"disposition": "reused", "package": None, "version": None}),
            mock.patch.object(
                oracle_admin,
                "ensure_standard_identities",
                return_value={
                    "created": [],
                    "identities": {
                        "groups": {
                            "oracle": {"gid": 901},
                            "oracle-admin": {"gid": 902},
                        }
                    },
                },
            ),
            mock.patch.object(oracle_admin, "ensure_standard_layout", return_value={"created": [], "layout": {}}),
            mock.patch.object(
                oracle_admin,
                "ensure_operator_membership",
                return_value={
                    "account": self.operator_account,
                    "uid": os.getuid(),
                    "group": "oracle-admin",
                    "disposition": "added",
                },
            ) as enroll_operator,
            mock.patch.object(oracle_admin, "stage_artifact_pair", return_value=components) as stage_pair,
            mock.patch.object(oracle_admin, "build_python_environment", return_value=environment) as build_environment,
            mock.patch.object(oracle_admin, "_write_staging_evidence", return_value=self.root / "evidence.json"),
        ):
            result = oracle_admin.execute_staging(
                self.core,
                self.household,
                self.operator_account,
                identity,
                root=self.root / "oracle",
                lock_path=self.root / "oracle-installation.lock",
            )
        self.assertEqual(result["status"], "staged")
        self.assertFalse(result["activation_created"])
        self.assertFalse(result["selection_changed"])
        self.assertFalse(result["service_modified"])
        self.assertEqual(stage_pair.call_count, 1)
        enroll_operator.assert_called_once_with(self.operator_account)
        self.assertEqual(stage_pair.call_args.kwargs["owner_uid"], 0)
        self.assertEqual(stage_pair.call_args.kwargs["read_gid"], 902)
        build_environment.assert_called_once()
        self.assertEqual(build_environment.call_args.kwargs["owner_uid"], 0)
        self.assertEqual(build_environment.call_args.kwargs["read_gid"], 902)

    def test_preflight_reports_existing_or_absent_oracle_identities(self) -> None:
        with (
            mock.patch.object(oracle_admin.pwd, "getpwnam", side_effect=KeyError),
            mock.patch.object(oracle_admin.grp, "getgrnam", side_effect=KeyError),
        ):
            identities = oracle_admin._identity_probe()
        self.assertIsNone(identities["service_account"]["existing"])
        self.assertIsNone(identities["groups"]["oracle"])
        self.assertIsNone(identities["groups"]["oracle-admin"])

    def test_operator_read_reconciliation_excludes_secret_and_runtime_surfaces(self) -> None:
        root = self.root / "oracle"
        for name in (
            "revisions", "environments", "deployments", "configuration", "secrets",
            "activations", "selection", "state/installation", "state/control", "data", "cache", "tmp",
        ):
            (root / name).mkdir(parents=True, exist_ok=True)
        immutable = root / "revisions" / "core-example"
        immutable.mkdir()
        executable = immutable / "oracle-admin.py"
        executable.write_text("#!/usr/bin/python3\n", encoding="utf-8")
        executable.chmod(0o700)
        secret = root / "secrets" / "secrets.env"
        secret.write_text("TOKEN=secret\n", encoding="utf-8")
        secret.chmod(0o600)
        data = root / "data" / "brain.db"
        data.write_bytes(b"private")
        data.chmod(0o600)

        with mock.patch.object(oracle_admin.os, "chown") as chown:
            oracle_admin._reconcile_operator_read_access(
                root,
                service_uid=os.getuid(),
                operator_gid=os.getgid(),
            )

        self.assertEqual(executable.stat().st_mode & 0o777, 0o550)
        self.assertFalse(executable.stat().st_mode & 0o020)
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertEqual(data.stat().st_mode & 0o777, 0o600)
        touched = {Path(call.args[0]) for call in chown.call_args_list}
        self.assertNotIn(secret, touched)
        self.assertNotIn(data, touched)

    def test_diagnostic_export_omits_secret_and_runtime_trees(self) -> None:
        root = self.root / "oracle"
        installation = root / "state" / "installation"
        control = root / "state" / "control"
        secrets = root / "secrets"
        data = root / "data"
        for directory in (installation, control, secrets, data):
            directory.mkdir(parents=True)
        (installation / "install.json").write_text('{"status":"ok"}\n', encoding="utf-8")
        (control / "result.json").write_text('{"status":"ok"}\n', encoding="utf-8")
        (secrets / "secrets.env").write_text("TOKEN=do-not-export\n", encoding="utf-8")
        (data / "brain.db").write_text("private-runtime-state\n", encoding="utf-8")
        output = self.root / "diagnostics.json"
        with mock.patch.object(
            oracle_admin,
            "build_managed_status",
            return_value={"format": oracle_admin.OUTPUT_FORMAT, "status": "healthy"},
        ):
            result = oracle_admin.export_managed_diagnostics(output, root=root)
        payload = output.read_text(encoding="utf-8")
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["secret_material_included"])
        self.assertNotIn("do-not-export", payload)
        self.assertNotIn("private-runtime-state", payload)
        self.assertNotIn("secrets/secrets.env", payload)
        self.assertNotIn("data/brain.db", payload)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_status_reports_exact_active_non_secret_identities_without_mutation(self) -> None:
        root = self.root / "oracle"
        application = root / "revisions" / "core-example"
        environment = root / "environments" / "environment-example"
        deployment = root / "deployments" / "deployment-example"
        configuration = root / "configuration" / "activations" / "configuration-example"
        activation_directory = root / "activations" / "activation-example"
        for directory in (application, environment, deployment, configuration, activation_directory):
            directory.mkdir(parents=True)
        (application / "README.md").write_text("Oracle\n", encoding="utf-8")
        (deployment / "deployment.json").write_text("{}\n", encoding="utf-8")
        (configuration / "metadata.json").write_text("{}\n", encoding="utf-8")
        configuration.chmod(0o750)
        (configuration / "metadata.json").chmod(0o640)
        for link, target in (
            ("application", application),
            ("environment", environment),
            ("deployment", deployment),
            ("configuration", configuration),
        ):
            (activation_directory / link).symlink_to(os.path.relpath(target, activation_directory))
        core_tree = "1" * 40
        activation_id = "oracle-installation-activation-v1:sha256:" + "2" * 64
        record = {
            "core": {"commit": "3" * 40, "git_tree": core_tree},
            "application_revision_identity": application.name,
            "python_environment_identity": "oracle-python-environment-v1:sha256:" + "4" * 64,
            "household_deployment_revision": deployment.name,
            "configuration_activation_identity": configuration.name,
            "service_definition_identity": "systemd-unit-" + "5" * 64,
            "persistent_state_checkpoint": None,
        }
        selected = SimpleNamespace(
            activation_id=activation_id,
            directory=activation_directory,
            record=record,
        )
        evidence = {
            "components": {
                "application_inventory_sha256": oracle_admin.inventory_sha256(
                    oracle_admin._payload_inventory(application)
                ),
                "deployment_inventory_sha256": oracle_admin.inventory_sha256(
                    oracle_admin._payload_inventory(deployment)
                ),
            },
            "environment": {"profile": "full-production-brain"},
        }

        def selection(_layout, name="active"):
            if name == "active":
                return selected
            raise ValueError("selection absent")

        group = SimpleNamespace(gr_gid=os.getgid())
        account = SimpleNamespace(pw_uid=os.getuid())
        health = {"status": "ok", "service": "oracle-brain"}
        config_health = {"ok": True}
        with (
            mock.patch.object(oracle_admin, "load_selected_activation", side_effect=selection),
            mock.patch.object(oracle_admin.grp, "getgrnam", return_value=group),
            mock.patch.object(oracle_admin.pwd, "getpwnam", return_value=account),
            mock.patch.object(oracle_admin, "require_immutable_permissions"),
            mock.patch.object(
                oracle_admin,
                "validate_python_environment",
                return_value={},
            ) as validate_environment,
            mock.patch.object(oracle_admin, "_tree_identity", return_value=core_tree),
            mock.patch.object(oracle_admin, "_latest_staging_evidence", return_value=evidence),
            mock.patch.object(
                oracle_admin,
                "service_definition_identity",
                return_value=record["service_definition_identity"],
            ),
            mock.patch.object(oracle_admin, "_systemctl_property", side_effect=["active", "enabled"]),
            mock.patch.object(oracle_admin, "_http_json", side_effect=[health, config_health]),
            mock.patch.object(
                oracle_admin,
                "configuration_control_status",
                return_value={"selected_activation_generation_id": configuration.name},
            ),
            mock.patch.object(oracle_admin, "platform_preflight", return_value={"support_status": "supported"}),
        ):
            result = oracle_admin.build_managed_status(root=root)
        self.assertEqual(result["status"], "healthy")
        self.assertFalse(result["mutation_performed"])
        self.assertEqual(result["selections"]["active"]["activation_id"], activation_id)
        self.assertEqual(result["selections"]["active"]["core_git_tree"], core_tree)
        self.assertEqual(result["findings"], [])
        validate_environment.assert_called_once_with(
            application,
            environment,
            profile="full-production-brain",
        )

    def test_mutating_lifecycle_remains_elevation_gated(self) -> None:
        with mock.patch.object(oracle_admin.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(RuntimeError, "explicitly elevated"):
                oracle_admin.execute_staging(
                    self.core,
                    self.household,
                    self.operator_account,
                    "oracle-operation-plan-v1:sha256:" + "1" * 64,
                    root=self.root / "oracle",
                )
            with self.assertRaisesRegex(RuntimeError, "explicitly elevated"):
                oracle_admin.execute_service_install("plan", root=self.root / "oracle")
            with self.assertRaisesRegex(RuntimeError, "explicitly elevated"):
                oracle_admin.execute_managed_activation("plan", operation="update", root=self.root / "oracle")


if __name__ == "__main__":
    unittest.main()
