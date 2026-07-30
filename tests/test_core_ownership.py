from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "core_ownership.py"
SPEC = importlib.util.spec_from_file_location("core_ownership", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
core_ownership = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core_ownership)


class CoreOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary_directory.name)
        self.output_root = Path(self.output_directory.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Oracle Test")
        self._git("config", "user.email", "oracle-test@example.invalid")
        self.gitlink_target: str | None = None

        (self.repo / "regular.txt").write_text("regular\n", encoding="utf-8")
        executable = self.repo / "run.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        (self.repo / "link").symlink_to("regular.txt")
        self._commit("initial files")

        self.gitlink_target = self._git("rev-parse", "HEAD")
        self._write_manifest(self._base_entries())
        self._commit("add ownership manifest and external source gitlink")

    def tearDown(self) -> None:
        self.output_directory.cleanup()
        self.temporary_directory.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        if self.gitlink_target is not None:
            self._git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{self.gitlink_target},vendor/upstream",
            )
        self._git("commit", "-q", "-m", message)

    def _base_entries(self) -> list[dict[str, object]]:
        return [
            {
                "path": "link",
                "classification": "core_direct",
                "destination": "app/link",
            },
            {
                "path": "ownership.json",
                "classification": "private_development",
            },
            {
                "path": "regular.txt",
                "classification": "core_direct",
                "destination": "app/regular.txt",
            },
            {
                "path": "run.sh",
                "classification": "core_direct",
                "destination": "app/run.sh",
            },
            {
                "path": "vendor/upstream",
                "classification": "external_dependency",
            },
        ]

    def _write_manifest(self, entries: list[dict[str, object]]) -> None:
        content = {"format_version": 1, "entries": entries}
        (self.repo / "ownership.json").write_text(
            json.dumps(content, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_inventory_reads_committed_objects_and_preserves_entry_types(self) -> None:
        source_commit = self._git("rev-parse", "HEAD")
        committed_regular = self._git("rev-parse", "HEAD:regular.txt")
        (self.repo / "regular.txt").write_text("dirty\n", encoding="utf-8")
        (self.repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        result = core_ownership.inventory(self.repo, source_commit)

        entries = {entry["path"]: entry for entry in result["entries"]}
        self.assertEqual(result["source_commit"], source_commit)
        self.assertEqual(entries["regular.txt"]["object_id"], committed_regular)
        self.assertEqual(entries["run.sh"]["mode"], "100755")
        self.assertEqual(entries["link"]["type"], "symlink")
        self.assertEqual(entries["vendor/upstream"]["type"], "gitlink")
        self.assertNotIn("untracked.txt", entries)

    def test_resolve_produces_complete_deterministic_ledger_from_committed_manifest(self) -> None:
        source_commit = self._git("rev-parse", "HEAD")
        self._write_manifest([])

        first = core_ownership.resolve_ledger(self.repo, source_commit, "ownership.json")
        second = core_ownership.resolve_ledger(self.repo, source_commit, "ownership.json")

        self.assertEqual(first, second)
        self.assertEqual(first["source_commit"], source_commit)
        self.assertRegex(first["ledger_sha256"], r"^[0-9a-f]{64}$")
        mapping = {
            entry["path"]: entry.get("destination")
            for entry in first["entries"]
            if entry["classification"] in core_ownership.PROMOTED_CLASSES
        }
        self.assertEqual(
            mapping,
            {
                "link": "app/link",
                "regular.txt": "app/regular.txt",
                "run.sh": "app/run.sh",
            },
        )

    def test_resolve_fails_on_unclassified_path_and_promoted_gitlink(self) -> None:
        entries = [entry for entry in self._base_entries() if entry["path"] != "run.sh"]
        self._write_manifest(entries)
        self._commit("make manifest incomplete")
        with self.assertRaisesRegex(core_ownership.OwnershipError, "run.sh"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

        entries.append(
            {
                "path": "run.sh",
                "classification": "core_direct",
                "destination": "app/run.sh",
            }
        )
        for entry in entries:
            if entry["path"] == "vendor/upstream":
                entry.update(
                    classification="core_direct",
                    destination="vendor/upstream",
                )
        self._write_manifest(entries)
        self._commit("attempt to promote gitlink")
        with self.assertRaisesRegex(core_ownership.OwnershipError, "Gitlink cannot be promoted"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

    def test_curated_derivative_staleness_is_checked_against_selected_commit(self) -> None:
        (self.repo / "private-roadmap.md").write_text("private source\n", encoding="utf-8")
        (self.repo / "distribution-roadmap.md").write_text("safe derivative\n", encoding="utf-8")
        self._commit("add documentation source and derivative")
        reviewed_blob = self._git("rev-parse", "HEAD:private-roadmap.md")
        entries = self._base_entries() + [
            {
                "path": "private-roadmap.md",
                "classification": "private_development",
            },
            {
                "path": "distribution-roadmap.md",
                "classification": "core_curated_derivative",
                "destination": "docs/roadmap.md",
                "review": {
                    "status": "approved",
                    "sources": [
                        {
                            "path": "private-roadmap.md",
                            "git_blob": reviewed_blob,
                        }
                    ],
                },
            },
        ]
        self._write_manifest(entries)
        self._commit("approve curated derivative")
        core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

        (self.repo / "private-roadmap.md").write_text("changed private source\n", encoding="utf-8")
        self._commit("change private source")
        with self.assertRaisesRegex(core_ownership.OwnershipError, "is stale"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

    def test_promoted_symlink_must_remain_confined_and_resolvable(self) -> None:
        os.unlink(self.repo / "link")
        (self.repo / "link").symlink_to("../../outside")
        self._commit("make symlink escape")

        with self.assertRaisesRegex(core_ownership.OwnershipError, "escapes the core tree"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

    def test_destinations_cannot_overlap_as_file_and_descendant(self) -> None:
        entries = self._base_entries()
        for entry in entries:
            if entry["path"] == "run.sh":
                entry["destination"] = "app/regular.txt/child"
        self._write_manifest(entries)
        self._commit("add overlapping destination")

        with self.assertRaisesRegex(core_ownership.OwnershipError, "file and descendant"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

    def test_promoted_symlink_cannot_resolve_to_an_ancestor(self) -> None:
        os.unlink(self.repo / "link")
        (self.repo / "link").symlink_to(".")
        self._commit("make symlink point to exported ancestor")

        with self.assertRaisesRegex(core_ownership.OwnershipError, "itself or an ancestor"):
            core_ownership.resolve_ledger(self.repo, "HEAD", "ownership.json")

    def test_materialize_builds_and_verifies_exact_empty_tree_candidate(self) -> None:
        source_commit = self._git("rev-parse", "HEAD")
        first_root = self.output_root / "candidate-one"
        first_ledger = self.output_root / "candidate-one.ledger.json"
        second_root = self.output_root / "candidate-two"
        second_ledger = self.output_root / "candidate-two.ledger.json"

        first = core_ownership.materialize_candidate(
            self.repo,
            source_commit,
            "ownership.json",
            first_root,
            first_ledger,
        )
        second = core_ownership.materialize_candidate(
            self.repo,
            source_commit,
            "ownership.json",
            second_root,
            second_ledger,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["candidate_identity"], second["candidate_identity"])
        self.assertEqual(first["counts"], {
            "exported_paths": 3,
            "regular_files": 2,
            "executable_files": 1,
            "symlinks": 1,
        })
        self.assertEqual(first["top_level"], ["app"])
        self.assertEqual((first_root / "app/regular.txt").read_text(), "regular\n")
        self.assertTrue((first_root / "app/run.sh").stat().st_mode & 0o111)
        self.assertTrue((first_root / "app/link").is_symlink())
        self.assertEqual(os.readlink(first_root / "app/link"), "regular.txt")
        self.assertFalse((first_root / ".git").exists())
        self.assertEqual(json.loads(first_ledger.read_text()), first)
        self.assertEqual(
            sorted(path.relative_to(first_root).as_posix() for path in first_root.rglob("*") if not path.is_dir()),
            ["app/link", "app/regular.txt", "app/run.sh"],
        )

    def test_materialize_refuses_existing_output_and_candidate_inside_private_repo(self) -> None:
        existing = self.output_root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(core_ownership.OwnershipError, "already exists"):
            core_ownership.materialize_candidate(
                self.repo,
                "HEAD",
                "ownership.json",
                existing,
                self.output_root / "existing.ledger.json",
            )

        with self.assertRaisesRegex(core_ownership.OwnershipError, "outside the private repository"):
            core_ownership.materialize_candidate(
                self.repo,
                "HEAD",
                "ownership.json",
                self.repo / "candidate",
                self.output_root / "inside.ledger.json",
            )

    def test_materialize_empty_tree_reconstruction_does_not_retain_deleted_destination(self) -> None:
        (self.repo / "removed.txt").write_text("remove me\n", encoding="utf-8")
        self._commit("add future deletion")
        entries = self._base_entries() + [
            {
                "path": "removed.txt",
                "classification": "core_direct",
                "destination": "app/removed.txt",
            }
        ]
        self._write_manifest(entries)
        self._commit("promote future deletion")
        before_root = self.output_root / "before-deletion"
        core_ownership.materialize_candidate(
            self.repo,
            "HEAD",
            "ownership.json",
            before_root,
            self.output_root / "before-deletion.ledger.json",
        )
        self.assertTrue((before_root / "app/removed.txt").is_file())

        (self.repo / "removed.txt").unlink()
        self._write_manifest(self._base_entries())
        self._commit("remove promoted destination")
        after_root = self.output_root / "after-deletion"
        core_ownership.materialize_candidate(
            self.repo,
            "HEAD",
            "ownership.json",
            after_root,
            self.output_root / "after-deletion.ledger.json",
        )

        self.assertFalse((after_root / "app/removed.txt").exists())
        self.assertEqual(
            sorted(path.relative_to(after_root).as_posix() for path in after_root.rglob("*") if not path.is_dir()),
            ["app/link", "app/regular.txt", "app/run.sh"],
        )


if __name__ == "__main__":
    unittest.main()
