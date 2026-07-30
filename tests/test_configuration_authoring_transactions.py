from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from oracle_app.configuration import (
    AuthoredRevisionConflict,
    AuthoringModeError,
    AuthoringMutationUnavailable,
    CandidateActivationBlocked,
    ConfigurationService,
    GenerationStore,
    SecretCompanionDrift,
    inspect_candidate,
    snapshot_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationAuthoringTransactionTests(unittest.TestCase):
    def test_complete_staged_candidate_replaces_workspace_and_activates(self) -> None:
        with self._environment() as (authoring, store, service):
            (authoring / "secrets.env").write_text("UNUSED=value\n", encoding="utf-8")
            initial = self._activate(authoring, service)
            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: INFO", "level: DEBUG")

            result = service.replace_authored_candidate(
                staging,
                expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                expected_secret_generation_id=initial.selected.secrets.generation_id,
                actor="host_local_cli",
            )

            self.assertIn("level: DEBUG", (authoring / "brain.yaml").read_text(encoding="utf-8"))
            self.assertEqual((authoring / "secrets.env").read_text(encoding="utf-8"), "UNUSED=value\n")
            self.assertEqual(result.authored_revision, snapshot_candidate(authoring).authored_revision)
            self.assertEqual(result.selected.activation, store.load_selected().activation)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())
            audit = json.loads((store.root / "audit" / f"{result.audit_event_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["operation"], "replace_authored_candidate")
            self.assertEqual(audit["outcome"], "activated")

    def test_external_read_only_mode_rejects_mutation_before_writes(self) -> None:
        with self._environment(mode="external_read_only") as (authoring, store, service):
            before = self._role_bytes(authoring)
            staging = self._staging_copy(authoring)

            with self.assertRaises(AuthoringModeError):
                service.replace_authored_candidate(
                    staging,
                    expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                    expected_secret_generation_id="secret_00000000000000000000000000000000",
                    actor="system_mode",
                )

            self.assertEqual(self._role_bytes(authoring), before)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_comment_only_edit_updates_authored_tree_without_runtime_generation(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            staging = self._staging_copy(authoring)
            brain = staging / "brain.yaml"
            brain.write_text("# operator note\n" + brain.read_text(encoding="utf-8"), encoding="utf-8")
            counts_before = self._generation_counts(store)

            result = service.replace_authored_candidate(
                staging,
                expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                expected_secret_generation_id=initial.selected.secrets.generation_id,
                actor="system_mode",
            )

            self.assertEqual(result.outcome, "authored_no_op")
            self.assertTrue((authoring / "brain.yaml").read_text(encoding="utf-8").startswith("# operator note\n"))
            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(self._generation_counts(store), counts_before)

    def test_stale_workspace_revision_and_invalid_candidate_fail_before_staging(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            staging = self._staging_copy(authoring)
            expected = snapshot_candidate(authoring).authored_revision
            self._replace(authoring / "brain.yaml", "level: INFO", "level: DEBUG")
            transactions_before = tuple((store.root / "transactions").iterdir())

            with self.assertRaises(AuthoredRevisionConflict):
                service.replace_authored_candidate(
                    staging,
                    expected_authored_revision=expected,
                    expected_secret_generation_id=initial.selected.secrets.generation_id,
                    actor="system_mode",
                )
            self.assertEqual(tuple((store.root / "transactions").iterdir()), transactions_before)

            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: DEBUG", "level: INVALID")
            before = self._role_bytes(authoring)
            with self.assertRaises(CandidateActivationBlocked):
                service.replace_authored_candidate(
                    staging,
                    expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                    expected_secret_generation_id=initial.selected.secrets.generation_id,
                    actor="system_mode",
                )
            self.assertEqual(self._role_bytes(authoring), before)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_workspace_is_rechecked_after_candidate_validation(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            staging = self._staging_copy(authoring)
            expected = snapshot_candidate(authoring).authored_revision

            def inspect_with_concurrent_edit(root, *, secret_snapshot):
                self._replace(authoring / "brain.yaml", "level: INFO", "level: DEBUG")
                return inspect_candidate(root, secret_snapshot=secret_snapshot)

            with patch(
                "oracle_app.configuration.service.inspect_candidate",
                side_effect=inspect_with_concurrent_edit,
            ):
                with self.assertRaises(AuthoredRevisionConflict):
                    service.replace_authored_candidate(
                        staging,
                        expected_authored_revision=expected,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="system_mode",
                    )

            self.assertIn("level: DEBUG", (authoring / "brain.yaml").read_text(encoding="utf-8"))
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_non_secret_staging_rejects_companion_and_unowned_files(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            for relative_path in ("secrets.env", "notes.txt"):
                with self.subTest(path=relative_path):
                    staging = self._staging_copy(authoring)
                    (staging / relative_path).write_text("raw-value-must-not-stage\n", encoding="utf-8")
                    with self.assertRaises(AuthoringMutationUnavailable):
                        service.replace_authored_candidate(
                            staging,
                            expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                            expected_secret_generation_id=initial.selected.secrets.generation_id,
                            actor="system_mode",
                        )
                    persisted = b"".join(
                        path.read_bytes() for path in store.root.rglob("*") if path.is_file()
                    )
                    self.assertNotIn(b"raw-value-must-not-stage", persisted)

    def test_selected_secret_generation_must_match_preserved_companion(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            staging = self._staging_copy(authoring)
            (authoring / "secrets.env").write_text("MANUAL=drift\n", encoding="utf-8")
            before = self._role_bytes(authoring)

            with self.assertRaises(SecretCompanionDrift):
                service.replace_authored_candidate(
                    staging,
                    expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                    expected_secret_generation_id=initial.selected.secrets.generation_id,
                    actor="host_local_cli",
                )

            self.assertEqual(self._role_bytes(authoring), before)
            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_failure_after_authored_commit_restores_every_role(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            before = self._role_bytes(authoring)
            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: INFO", "level: DEBUG")
            (staging / "domains" / "music.yaml").unlink()

            with patch.object(store, "install_config_candidate", side_effect=RuntimeError("injected failure")):
                with self.assertRaises(RuntimeError):
                    service.replace_authored_candidate(
                        staging,
                        expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="service",
                    )

            self.assertEqual(self._role_bytes(authoring), before)
            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())
            outcomes = [
                json.loads(path.read_text(encoding="utf-8"))["outcome"]
                for path in (store.root / "audit").glob("*.json")
            ]
            self.assertIn("recovered_rollback", outcomes)

    def test_partial_per_file_commit_is_restored_before_error_returns(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            before = self._role_bytes(authoring)
            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: INFO", "level: DEBUG")

            def partial_commit(transaction):
                candidate_path = store.root / "transactions" / transaction["transaction_id"] / "candidate" / "brain.yaml"
                service._authoring_transactions._replace_logical(
                    authoring,
                    authoring / "brain.yaml",
                    candidate_path.read_bytes(),
                )
                raise OSError("injected interruption")

            with patch.object(service._authoring_transactions, "commit_candidate", side_effect=partial_commit):
                with self.assertRaises(OSError):
                    service.replace_authored_candidate(
                        staging,
                        expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="service",
                    )

            self.assertEqual(self._role_bytes(authoring), before)
            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_recovery_finishes_workspace_after_selected_pointer_commit(self) -> None:
        with self._environment() as (authoring, store, service):
            initial = self._activate(authoring, service)
            previous = snapshot_candidate(authoring)
            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: INFO", "level: DEBUG")
            candidate = snapshot_candidate(staging)
            inspection = inspect_candidate(staging, secret_snapshot=initial.selected.secrets.snapshot)
            transaction = service._authoring_transactions.prepare(
                root=authoring,
                actor="service",
                previous=previous,
                candidate=candidate,
                runtime_change_required=True,
            )
            service._authoring_transactions.commit_candidate(transaction)
            transaction["authoring_committed"] = True
            config = store.install_config_candidate(inspection)
            activation = store.create_activation(config.generation_id, initial.selected.secrets.generation_id)
            transaction["new_config_generation_id"] = config.generation_id
            transaction["new_activation_generation_id"] = activation.generation_id
            service._authoring_transactions.write(transaction)
            store._replace_selected_pointer(
                activation.generation_id,
                operation_id="selection_op_" + "f" * 32,
                selection_revision=initial.selected.selection_revision + 1,
                satellite_projection_activation_ids=initial.selected.satellite_projection_activation_ids,
            )
            self._replace(authoring / "brain.yaml", "level: DEBUG", "level: INFO")

            recovered = service.recover_authoring_transactions()

            self.assertEqual(recovered, (transaction["transaction_id"],))
            self.assertEqual(snapshot_candidate(authoring).authored_revision, candidate.authored_revision)
            self.assertIn("level: DEBUG", (authoring / "brain.yaml").read_text(encoding="utf-8"))
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_optional_role_removal_and_existing_role_symlink_are_preserved(self) -> None:
        with self._environment() as (authoring, _store, service):
            initial = self._activate(authoring, service)
            brain = authoring / "brain.yaml"
            target = authoring / "brain-owned.yaml"
            brain.rename(target)
            brain.symlink_to(target.name)
            music = authoring / "domains" / "music.yaml"
            music_target = authoring / "domains" / "music-owned.yaml"
            music.rename(music_target)
            music.symlink_to(music_target.name)
            staging = self._staging_copy(authoring)
            self._replace(staging / "brain.yaml", "level: INFO", "level: DEBUG")
            (staging / "domains" / "music.yaml").unlink()

            with patch.object(_store, "install_config_candidate", side_effect=RuntimeError("injected failure")):
                with self.assertRaises(RuntimeError):
                    service.replace_authored_candidate(
                        staging,
                        expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="host_local_cli",
                    )
            self.assertTrue(brain.is_symlink())
            self.assertTrue(music.is_symlink())
            self.assertTrue(target.exists())
            self.assertTrue(music_target.exists())

            service.replace_authored_candidate(
                staging,
                expected_authored_revision=snapshot_candidate(authoring).authored_revision,
                expected_secret_generation_id=initial.selected.secrets.generation_id,
                actor="host_local_cli",
            )

            self.assertTrue(brain.is_symlink())
            self.assertEqual(brain.readlink(), Path("brain-owned.yaml"))
            self.assertIn("level: DEBUG", target.read_text(encoding="utf-8"))
            self.assertFalse((authoring / "domains" / "music.yaml").exists())
            self.assertFalse(music_target.exists())

    def _environment(self, *, mode: str = "managed_writable"):
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        authoring = base / "config"
        shutil.copytree(EXAMPLE_ROOT, authoring)
        (authoring / "secrets.env.example").unlink()
        store = GenerationStore(base / "installed")
        store.initialize("example-home")
        service = ConfigurationService(
            store,
            authoring_mode=mode,
            authoring_root=authoring,
        )

        class Environment:
            def __enter__(self):
                return authoring, store, service

            def __exit__(self, *_args):
                temporary.cleanup()

        return Environment()

    @staticmethod
    def _activate(authoring: Path, service: ConfigurationService):
        return service.activate_candidate(
            authoring,
            expected_authored_revision=snapshot_candidate(authoring).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
        )

    @staticmethod
    def _staging_copy(root: Path) -> Path:
        staging = root.parent / f"staging-{next(tempfile._get_candidate_names())}"
        shutil.copytree(root, staging, symlinks=False)
        companion = staging / "secrets.env"
        if companion.exists():
            companion.unlink()
        for path in list(staging.rglob("*")):
            relative = path.relative_to(staging).as_posix()
            if path.is_file() and relative not in snapshot_candidate(staging).authored_bytes:
                path.unlink()
        return staging

    @staticmethod
    def _replace(path: Path, before: str, after: str) -> None:
        text = path.read_text(encoding="utf-8")
        if before not in text:
            raise AssertionError(f"Expected fixture text not found: {before}")
        path.write_text(text.replace(before, after), encoding="utf-8")

    @staticmethod
    def _role_bytes(root: Path) -> dict[str, bytes]:
        return dict(snapshot_candidate(root).authored_bytes)

    @staticmethod
    def _generation_counts(store: GenerationStore) -> tuple[int, int, int]:
        return tuple(
            len(tuple((store.root / name).iterdir()))
            for name in ("config-generations", "secret-generations", "activations")
        )


if __name__ == "__main__":
    unittest.main()
