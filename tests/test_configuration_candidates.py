from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from oracle_app.configuration import (
    AUTHORED_REVISION_PREFIX,
    AuthoredRevisionConflict,
    assert_authored_revision,
    inspect_candidate,
    load_bundle_snapshot,
    normalize_bundle,
    snapshot_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationCandidateTests(unittest.TestCase):
    def test_candidate_ids_are_opaque_unique_and_authored_revision_is_deterministic(self) -> None:
        first = snapshot_candidate(EXAMPLE_ROOT)
        second = snapshot_candidate(EXAMPLE_ROOT)

        self.assertNotEqual(first.candidate_id, second.candidate_id)
        self.assertRegex(first.candidate_id, r"^candidate_[0-9a-f]{32}$")
        self.assertEqual(first.authored_revision, second.authored_revision)
        self.assertTrue(first.authored_revision.startswith(AUTHORED_REVISION_PREFIX))

    def test_authored_revision_changes_for_comments_but_config_revision_does_not(self) -> None:
        with self._bundle_copy() as root:
            before_snapshot = snapshot_candidate(root)
            before_config = normalize_bundle(load_bundle_snapshot(before_snapshot))
            path = root / "brain.yaml"
            path.write_text("# author note\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
            after_snapshot = snapshot_candidate(root)
            after_config = normalize_bundle(load_bundle_snapshot(after_snapshot))

            self.assertNotEqual(before_snapshot.authored_revision, after_snapshot.authored_revision)
            self.assertEqual(before_config.config_revision, after_config.config_revision)

    def test_secret_companion_does_not_change_authored_revision(self) -> None:
        with self._bundle_copy() as root:
            before = snapshot_candidate(root).authored_revision
            (root / "secrets.env").write_text("TOKEN=first\n", encoding="utf-8")
            after = snapshot_candidate(root).authored_revision

            self.assertEqual(before, after)

    def test_invalid_candidate_still_reports_candidate_and_authored_revisions(self) -> None:
        with self._bundle_copy() as root:
            (root / "brain.yaml").write_text("runtime: &value {}\nlogging: *value\n", encoding="utf-8")

            inspection = inspect_candidate(root)

            self.assertRegex(inspection.candidate_id, r"^candidate_[0-9a-f]{32}$")
            self.assertTrue(inspection.authored_revision.startswith(AUTHORED_REVISION_PREFIX))
            self.assertFalse(inspection.report.activation_eligible)
            self.assertIsNone(inspection.bundle)
            self.assertIsNone(inspection.normalized_candidate_revision)

    def test_normalized_candidate_revision_identifies_exact_validated_graph(self) -> None:
        inspection = inspect_candidate(EXAMPLE_ROOT)

        self.assertTrue(inspection.report.activation_eligible)
        self.assertEqual(inspection.normalized_candidate_revision, inspection.normalized.config_revision)

    def test_validation_consumes_snapshot_even_if_authored_file_changes(self) -> None:
        with self._bundle_copy() as root:
            snapshot = snapshot_candidate(root)
            original = snapshot.authored_bytes["bundle.yaml"]
            (root / "bundle.yaml").write_text("invalid: true\n", encoding="utf-8")

            loaded = load_bundle_snapshot(snapshot)

            self.assertEqual(loaded.authored_bytes["bundle.yaml"], original)
            self.assertEqual(loaded.candidate_id, snapshot.candidate_id)

    def test_optimistic_concurrency_rejects_stale_authored_revision(self) -> None:
        snapshot = snapshot_candidate(EXAMPLE_ROOT)
        assert_authored_revision(snapshot, snapshot.authored_revision)

        with self.assertRaises(AuthoredRevisionConflict) as caught:
            assert_authored_revision(snapshot, f"{AUTHORED_REVISION_PREFIX}{'0' * 64}")

        self.assertEqual(caught.exception.actual, snapshot.authored_revision)
        self.assertNotEqual(caught.exception.expected, caught.exception.actual)

    def test_explicit_candidate_id_must_use_opaque_path_safe_format(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_candidate(EXAMPLE_ROOT, candidate_id="example-home:revision")

    def _bundle_copy(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name) / "config"
        shutil.copytree(EXAMPLE_ROOT, root)

        class BundleContext:
            def __enter__(self_nonlocal):
                return root

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return BundleContext()


if __name__ == "__main__":
    unittest.main()
