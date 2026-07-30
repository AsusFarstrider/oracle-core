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


if __name__ == "__main__":
    unittest.main()
