from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("core_artifact", ROOT / "scripts" / "core_artifact.py")
assert SPEC is not None and SPEC.loader is not None
core_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core_artifact)


class CoreArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Oracle Test")
        self._git("config", "user.email", "oracle-test@example.invalid")
        (self.repo / "README.md").write_text("Oracle\n", encoding="utf-8")
        script = self.repo / "run.sh"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        script.chmod(0o755)
        (self.repo / "readme-link").symlink_to("README.md")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

    def test_build_and_verify_round_trip_exact_git_tree(self) -> None:
        output = Path(self.temporary.name) / "core.tar"
        built = core_artifact.build(self.repo, "HEAD", output)
        verified = core_artifact.verify(output)
        self.assertEqual(verified["core_commit"], self._git("rev-parse", "HEAD"))
        self.assertEqual(verified["core_git_tree"], self._git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(verified, built)
        self.assertEqual({item["mode"] for item in verified["inventory"]}, {"100644", "100755", "120000"})

    def test_extract_verified_leaves_the_exact_payload_without_transport_metadata(self) -> None:
        output = Path(self.temporary.name) / "core.tar"
        destination = Path(self.temporary.name) / "extracted"
        built = core_artifact.build(self.repo, "HEAD", output)
        extracted = core_artifact.extract_verified(output, destination)
        self.assertEqual(extracted, built)
        self.assertEqual(core_artifact._tree_identity(destination), built["core_git_tree"])
        self.assertFalse((destination / "manifest.json").exists())
        with self.assertRaisesRegex(core_artifact.ArtifactError, "already exists"):
            core_artifact.extract_verified(output, destination)

    def test_verify_rejects_path_escape_before_extraction(self) -> None:
        output = Path(self.temporary.name) / "unsafe.tar"
        manifest = {"format_version": 1, "artifact_kind": "oracle-core", "core_commit": "0" * 40, "core_git_tree": "0" * 40, "inventory": []}
        with tarfile.open(output, "w") as archive:
            raw = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
            bad = tarfile.TarInfo("payload/../escape")
            bad.size = 1
            archive.addfile(bad, io.BytesIO(b"x"))
        with self.assertRaisesRegex(core_artifact.ArtifactError, "unsafe relative path"):
            core_artifact.verify(output)

    def test_verify_rejects_escaping_symlink(self) -> None:
        with self.assertRaisesRegex(core_artifact.ArtifactError, "escapes payload"):
            core_artifact._safe_symlink(Path("link"), "../outside")

    def _household_fixture(self, *, core_commit: str | None = None, core_tree: str | None = None) -> tuple[Path, Path, dict[str, object]]:
        payload = Path(self.temporary.name) / "household-payload"
        payload.mkdir()
        configuration = payload / "configuration"
        configuration.mkdir()
        source = configuration / "bundle.yaml"
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
            "core": {
                "commit": core_commit or self._git("rev-parse", "HEAD"),
                "git_tree": core_tree or self._git("rev-parse", "HEAD^{tree}"),
            },
            "deployment_metadata": {"label": "Minimal household"},
            "entries": [entry],
            "generated_configuration_inputs": {"canonical_bundle": "configuration/bundle.yaml"},
            "household_id": "stage4-minimal",
            "ingress": {"posture": "host-local"},
            "installation_profiles": ["minimal-brain"],
            "logical_secret_requirements": [],
            "migrations": [],
            "template": {"manifest_git_blob": "4" * 40, "manifest_sha256": "5" * 64, "template_id": "minimal-v1"},
        }
        ledger = {
            "deployment_revision": core_artifact._household_revision(basis),
            "revision_basis": basis,
            "source_commit": "private-source-must-not-enter-artifact",
            "entries": [{**entry, "source": "ops/households/private/configuration/bundle.yaml", "object_id": "6" * 40}],
        }
        ledger_path = Path(self.temporary.name) / "household-ledger.json"
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        return ledger_path, payload, basis

    def test_household_build_verify_and_pair_round_trip(self) -> None:
        core_output = Path(self.temporary.name) / "core.tar"
        household_output = Path(self.temporary.name) / "household.tar"
        core_artifact.build(self.repo, "HEAD", core_output)
        ledger, payload, basis = self._household_fixture()
        built = core_artifact.build_household(ledger, payload, household_output)
        verified = core_artifact.verify(household_output)
        pair = core_artifact.verify_pair(core_output, household_output)
        self.assertEqual(verified, built)
        self.assertEqual(verified["deployment_revision"], core_artifact._household_revision(basis))
        self.assertEqual(pair["household"]["deployment_revision"], verified["deployment_revision"])
        serialized = json.dumps(verified, sort_keys=True)
        self.assertNotIn("ops/households", serialized)
        self.assertNotIn("private-source", serialized)
        self.assertNotIn("object_id", serialized)

    def test_household_build_rejects_extra_payload(self) -> None:
        ledger, payload, _basis = self._household_fixture()
        (payload / "undeclared.txt").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(core_artifact.ArtifactError, "does not match"):
            core_artifact.build_household(ledger, payload, Path(self.temporary.name) / "household.tar")

    def test_household_pair_rejects_mismatched_core_pin(self) -> None:
        core_output = Path(self.temporary.name) / "core.tar"
        household_output = Path(self.temporary.name) / "household.tar"
        core_artifact.build(self.repo, "HEAD", core_output)
        ledger, payload, _basis = self._household_fixture(core_commit="7" * 40, core_tree="8" * 40)
        core_artifact.build_household(ledger, payload, household_output)
        with self.assertRaisesRegex(core_artifact.ArtifactError, "core pin does not match"):
            core_artifact.verify_pair(core_output, household_output)


if __name__ == "__main__":
    unittest.main()
