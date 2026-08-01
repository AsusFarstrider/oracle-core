from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("household_deployment", ROOT / "scripts" / "household_deployment.py")
assert SPEC is not None and SPEC.loader is not None
household_deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(household_deployment)


class HouseholdDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Oracle Test")
        self._git("config", "user.email", "oracle-test@example.invalid")
        self._write("examples/deployment/minimal/template.json", {"format_version": 1, "template_id": "oracle-minimal-household-v1"})
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "template")
        self.template_commit = self._git("rev-parse", "HEAD")
        self.template_blob = self._git("rev-parse", "HEAD:examples/deployment/minimal/template.json")
        self.template_sha256 = self._sha256("examples/deployment/minimal/template.json")
        self._build_household()
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "household")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

    def _write(self, relative: str, value: dict[str, object] | str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, indent=2, sort_keys=True) + "\n" if isinstance(value, dict) else value
        path.write_text(text, encoding="utf-8")

    def _sha256(self, relative: str) -> str:
        import hashlib
        return hashlib.sha256((self.repo / relative).read_bytes()).hexdigest()

    def _build_household(self) -> None:
        self._write("ops/households/authority.json", {
            "format_version": 1,
            "households": [{"household_id": "test_home", "root": "ops/households/test-home"}],
        })
        self._write("ops/households/test-home/configuration/bundle.yaml", "kind: oracle_configuration_bundle\n")
        content = (self.repo / "ops/households/test-home/configuration/bundle.yaml").read_bytes()
        import hashlib
        digest = hashlib.sha256()
        digest.update(b"oracle-authored-v1\0")
        role = b"bundle.yaml"
        digest.update(len(role).to_bytes(4, "big"))
        digest.update(role)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        definition = {
            "authority_files": ["deployment.json"],
            "compatibility": {"configuration_schema": 1},
            "configuration": {"authored_revision": f"oracle-authored-v1:sha256:{digest.hexdigest()}", "root": "configuration"},
            "core": {"commit": "1" * 40, "git_tree": "2" * 40},
            "deployment_metadata": {"purpose": "test-minimal-household"},
            "format_version": 1,
            "generated_configuration_inputs": {"canonical_bundle": "configuration"},
            "household_id": "test_home",
            "ingress": {"posture": "host-local"},
            "installation_profiles": ["minimal-brain"],
            "logical_secret_requirements": [],
            "migrations": [],
            "payload": [{"destination": "configuration/bundle.yaml", "source": "configuration/bundle.yaml"}],
            "template": {
                "manifest_git_blob": self.template_blob,
                "manifest_path": "examples/deployment/minimal/template.json",
                "manifest_sha256": self.template_sha256,
                "source_commit": self.template_commit,
                "template_id": "oracle-minimal-household-v1",
            },
        }
        self._write("ops/households/test-home/deployment.json", definition)

    def _resolve(self) -> dict[str, object]:
        return household_deployment.resolve(self.repo, "HEAD", "ops/households/authority.json", "test_home")

    def test_resolves_and_materializes_one_exact_household(self) -> None:
        ledger = self._resolve()
        self.assertTrue(str(ledger["deployment_revision"]).startswith(household_deployment.REVISION_PREFIX))
        self.assertEqual(ledger["core"], {"commit": "1" * 40, "git_tree": "2" * 40})
        self.assertEqual(len(ledger["entries"]), 1)
        serialized_basis = json.dumps(ledger["revision_basis"], sort_keys=True)
        self.assertNotIn("ops/households", serialized_basis)
        self.assertNotIn("source_commit", serialized_basis)
        self.assertNotIn("object_id", serialized_basis)
        self.assertIn("deployment_metadata", ledger["revision_basis"])
        self.assertIn("generated_configuration_inputs", ledger["revision_basis"])
        output = Path(self.temporary.name) / "output"
        household_deployment.materialize(self.repo, ledger, output)
        self.assertEqual((output / "configuration/bundle.yaml").read_text(), "kind: oracle_configuration_bundle\n")

    def test_new_tracked_household_path_fails_closed(self) -> None:
        self._write("ops/households/test-home/forgotten.txt", "private material\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "unclassified")
        with self.assertRaisesRegex(household_deployment.DeploymentError, "unclassified"):
            self._resolve()

    def test_cross_household_source_is_rejected(self) -> None:
        authority = json.loads((self.repo / "ops/households/authority.json").read_text())
        authority["households"].append({"household_id": "other_home", "root": "ops/households/other-home"})
        self._write("ops/households/authority.json", authority)
        self._write("ops/households/other-home/deployment.json", "{}\n")
        definition_path = self.repo / "ops/households/test-home/deployment.json"
        definition = json.loads(definition_path.read_text())
        definition["payload"][0]["source"] = "../other-home/deployment.json"
        self._write("ops/households/test-home/deployment.json", definition)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "cross reference")
        with self.assertRaisesRegex(household_deployment.DeploymentError, "unsafe payload source"):
            self._resolve()

    def test_duplicate_destination_is_rejected(self) -> None:
        definition_path = self.repo / "ops/households/test-home/deployment.json"
        definition = json.loads(definition_path.read_text())
        definition["payload"].append(dict(definition["payload"][0]))
        self._write("ops/households/test-home/deployment.json", definition)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "duplicate")
        with self.assertRaisesRegex(household_deployment.DeploymentError, "duplicate payload source"):
            self._resolve()

    def test_overlapping_household_roots_are_rejected(self) -> None:
        authority = json.loads((self.repo / "ops/households/authority.json").read_text())
        authority["households"].append({"household_id": "nested_home", "root": "ops/households/test-home/nested"})
        self._write("ops/households/authority.json", authority)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "overlap")
        with self.assertRaisesRegex(household_deployment.DeploymentError, "roots overlap"):
            self._resolve()

    def test_unrelated_gitlink_does_not_invalidate_selected_household(self) -> None:
        external = Path(self.temporary.name) / "external"
        external.mkdir()
        subprocess.run(["git", "-C", str(external), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(external), "config", "user.name", "External"], check=True)
        subprocess.run(["git", "-C", str(external), "config", "user.email", "external@example.invalid"], check=True)
        (external / "README.md").write_text("external\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(external), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(external), "commit", "-q", "-m", "external"], check=True)
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "-C", str(self.repo), "submodule", "add", "-q", str(external), "external/source"],
            check=True,
        )
        self._git("commit", "-q", "-am", "unrelated gitlink")

        ledger = self._resolve()

        self.assertEqual(len(ledger["entries"]), 1)


if __name__ == "__main__":
    unittest.main()
