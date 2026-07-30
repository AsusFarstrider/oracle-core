from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from oracle_app.configuration import (
    ConfigurationService,
    GenerationStore,
    SecretAlreadyExists,
    SecretCompanionDrift,
    SecretGenerationConflict,
    SecretGenerationPrunedError,
    SecretGenerationRevokedError,
    SecretNotFound,
    SecretRemovalBlocked,
    SecretMutationUnavailable,
    load_secret_companion,
    snapshot_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationSecretMutationTests(unittest.TestCase):
    def test_create_is_write_only_and_activates_only_a_new_secret_pair(self) -> None:
        with self._environment() as (bundle, store, service):
            before = self._activate(bundle, service)
            raw_value = 'quoted $value=with\\slashes'

            result = service.mutate_secret(
                bundle,
                operation="create_secret",
                logical_id="EXAMPLE_TOKEN",
                value=raw_value,
                expected_secret_generation_id=before.selected.secrets.generation_id,
                actor="system_mode",
            )

            self.assertEqual(result.selected.config.generation_id, before.selected.config.generation_id)
            self.assertEqual(result.selected.config.config_revision, before.selected.config.config_revision)
            self.assertNotEqual(result.secret_generation_id, before.selected.secrets.generation_id)
            self.assertNotEqual(result.selected.activation.generation_id, before.selected.activation.generation_id)
            self.assertEqual(load_secret_companion(bundle).resolve("EXAMPLE_TOKEN"), raw_value)
            self.assertEqual(store.secret_generation_status(before.selected.secrets.generation_id)["state"], "revoked")
            self.assertEqual(store.secret_generation_status(result.secret_generation_id)["state"], "available")
            audit = (store.root / "audit" / f"{result.audit_event_id}.json").read_bytes()
            self.assertNotIn(raw_value.encode(), audit)
            self.assertIn(b'"secret_logical_id":"EXAMPLE_TOKEN"', audit)
            self.assertNotIn(raw_value, repr(result))

    def test_same_value_replace_still_creates_a_new_generation(self) -> None:
        with self._environment() as (bundle, _store, service):
            initial = self._activate(bundle, service)
            created = self._mutate(bundle, service, initial, "create_secret", "TOKEN", "same-value")
            replaced = self._mutate(bundle, service, created, "replace_secret", "TOKEN", "same-value")

            self.assertNotEqual(created.secret_generation_id, replaced.secret_generation_id)
            self.assertNotEqual(created.selected.activation.generation_id, replaced.selected.activation.generation_id)

    def test_rotation_revokes_old_generation_and_prevents_reactivation(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            created = self._mutate(bundle, service, initial, "create_secret", "TOKEN", "first")
            rotated = self._mutate(bundle, service, created, "rotate_secret", "TOKEN", "second")

            old = store.load_secrets(created.secret_generation_id)
            self.assertEqual(old.state, "revoked")
            with self.assertRaises(SecretGenerationRevokedError):
                store.create_activation(rotated.selected.config.generation_id, created.secret_generation_id)

    def test_remove_unreferenced_secret_creates_generation_and_removes_companion_entry(self) -> None:
        with self._environment() as (bundle, _store, service):
            initial = self._activate(bundle, service)
            created = self._mutate(bundle, service, initial, "create_secret", "TOKEN", "value")
            removed = self._mutate(bundle, service, created, "remove_secret", "TOKEN", None)

            self.assertNotIn("TOKEN", removed.selected.secrets.snapshot.present_ids)
            self.assertNotIn("TOKEN", load_secret_companion(bundle).present_ids)

    def test_required_secret_removal_is_blocked_before_any_write(self) -> None:
        with self._environment(required_secret=True) as (bundle, store, service):
            initial = self._activate(bundle, service)
            pointer_before = (store.root / "selected.json").read_bytes()
            companion_before = (bundle / "secrets.env").read_bytes()
            generation_count = len(tuple((store.root / "secret-generations").iterdir()))

            with self.assertRaises(SecretRemovalBlocked):
                service.mutate_secret(
                    bundle,
                    operation="remove_secret",
                    logical_id="RESIDENT_PHONE_TOKEN",
                    value=None,
                    expected_secret_generation_id=initial.selected.secrets.generation_id,
                    actor="host_local_cli",
                )

            self.assertEqual((store.root / "selected.json").read_bytes(), pointer_before)
            self.assertEqual((bundle / "secrets.env").read_bytes(), companion_before)
            self.assertEqual(len(tuple((store.root / "secret-generations").iterdir())), generation_count)

    def test_operation_preconditions_are_explicit(self) -> None:
        with self._environment() as (bundle, _store, service):
            initial = self._activate(bundle, service)
            created = self._mutate(bundle, service, initial, "create_secret", "TOKEN", "value")
            with self.assertRaises(SecretAlreadyExists):
                self._mutate(bundle, service, created, "create_secret", "TOKEN", "new")
            with self.assertRaises(SecretNotFound):
                self._mutate(bundle, service, created, "replace_secret", "MISSING", "new")
            with self.assertRaises(ValueError):
                self._mutate(bundle, service, created, "replace_secret", "TOKEN", "two\nlines")
            with self.assertRaises(ValueError):
                self._mutate(bundle, service, created, "remove_secret", "TOKEN", "must-not-be-present")

    def test_stale_secret_revision_and_companion_drift_fail_before_mutation(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            with self.assertRaises(SecretGenerationConflict):
                service.mutate_secret(
                    bundle,
                    operation="create_secret",
                    logical_id="TOKEN",
                    value="value",
                    expected_secret_generation_id="secret_00000000000000000000000000000000",
                    actor="system_mode",
                )
            (bundle / "secrets.env").write_text("MANUAL=value\n", encoding="utf-8")
            with self.assertRaises(SecretCompanionDrift):
                service.mutate_secret(
                    bundle,
                    operation="create_secret",
                    logical_id="TOKEN",
                    value="value",
                    expected_secret_generation_id=initial.selected.secrets.generation_id,
                    actor="system_mode",
                )
            self.assertEqual(store.load_selected().activation, initial.selected.activation)

    def test_failed_companion_commit_discards_staged_raw_values(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            raw_value = "must-disappear-after-failure"

            with patch.object(
                service._secret_transactions,
                "commit_companion",
                side_effect=SecretMutationUnavailable("injected failure"),
            ):
                with self.assertRaises(SecretMutationUnavailable):
                    service.mutate_secret(
                        bundle,
                        operation="create_secret",
                        logical_id="TOKEN",
                        value=raw_value,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="system_mode",
                    )

            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(load_secret_companion(bundle).present_ids, frozenset())
            self.assertEqual(len(tuple((store.root / "secret-generations").iterdir())), 1)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())
            persisted = b"".join(path.read_bytes() for path in store.root.rglob("*") if path.is_file())
            self.assertNotIn(raw_value.encode(), persisted)

    def test_retention_keeps_current_and_one_revoked_transition_only(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            created = self._mutate(bundle, service, initial, "create_secret", "TOKEN", "one")
            replaced = self._mutate(bundle, service, created, "replace_secret", "TOKEN", "two")

            self.assertFalse(store.secret_generation_status(initial.selected.secrets.generation_id)["raw_present"])
            self.assertTrue(store.secret_generation_status(created.secret_generation_id)["raw_present"])
            self.assertTrue(store.secret_generation_status(replaced.secret_generation_id)["raw_present"])
            with self.assertRaises(SecretGenerationPrunedError):
                store.load_secrets(initial.selected.secrets.generation_id)

    def test_recovery_rolls_back_companion_and_discards_unselected_raw_generation(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            candidate = initial.selected.secrets.snapshot._with_value("TOKEN", "staged-raw")
            transaction = service._secret_transactions.prepare(
                root=bundle,
                operation="create_secret",
                actor="service",
                logical_id="TOKEN",
                previous=initial.selected,
                candidate=candidate,
            )
            new_secret = store.install_secrets(candidate)
            activation = store.create_activation(initial.selected.config.generation_id, new_secret.generation_id)
            transaction.update(
                new_secret_generation_id=new_secret.generation_id,
                new_activation_generation_id=activation.generation_id,
            )
            service._secret_transactions.write(transaction)
            service._secret_transactions.commit_companion(transaction)
            transaction["companion_committed"] = True
            service._secret_transactions.write(transaction)

            recovered = service.recover_secret_transactions(bundle)

            self.assertEqual(recovered, (transaction["transaction_id"],))
            self.assertEqual(store.load_selected().activation, initial.selected.activation)
            self.assertEqual(load_secret_companion(bundle).present_ids, frozenset())
            self.assertFalse((store.root / "secret-generations" / new_secret.generation_id).exists())
            self.assertNotIn(b"staged-raw", b"".join(path.read_bytes() for path in store.root.rglob("*") if path.is_file()))

    def test_recovery_finishes_revocation_after_pointer_commit(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            candidate = initial.selected.secrets.snapshot._with_value("TOKEN", "new-value")
            transaction = service._secret_transactions.prepare(
                root=bundle,
                operation="create_secret",
                actor="service",
                logical_id="TOKEN",
                previous=initial.selected,
                candidate=candidate,
            )
            new_secret = store.install_secrets(candidate)
            activation = store.create_activation(initial.selected.config.generation_id, new_secret.generation_id)
            transaction.update(
                new_secret_generation_id=new_secret.generation_id,
                new_activation_generation_id=activation.generation_id,
            )
            service._secret_transactions.write(transaction)
            service._secret_transactions.commit_companion(transaction)
            transaction["companion_committed"] = True
            service._secret_transactions.write(transaction)
            store._replace_selected_pointer(
                activation.generation_id,
                operation_id="selection_op_" + "f" * 32,
                selection_revision=initial.selected.selection_revision + 1,
                satellite_projection_activation_ids=initial.selected.satellite_projection_activation_ids,
            )

            service.recover_secret_transactions(bundle)

            self.assertEqual(store.load_selected().secrets.generation_id, new_secret.generation_id)
            self.assertEqual(store.secret_generation_status(initial.selected.secrets.generation_id)["state"], "revoked")
            self.assertEqual(load_secret_companion(bundle).resolve("TOKEN"), "new-value")
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def test_recovery_is_bound_to_the_selected_authoring_root(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            candidate = initial.selected.secrets.snapshot._with_value("TOKEN", "value")
            transaction = service._secret_transactions.prepare(
                root=bundle,
                operation="create_secret",
                actor="service",
                logical_id="TOKEN",
                previous=initial.selected,
                candidate=candidate,
            )
            other_root = bundle.parent / "other-config"
            other_root.mkdir()

            with self.assertRaises(ValueError):
                service.recover_secret_transactions(other_root)

            self.assertTrue((store.root / "transactions" / transaction["transaction_id"]).is_dir())
            service.recover_secret_transactions(bundle)
            self.assertEqual(tuple((store.root / "transactions").iterdir()), ())

    def _mutate(self, bundle, service, previous, operation, logical_id, value):
        selected = previous.selected
        return service.mutate_secret(
            bundle,
            operation=operation,
            logical_id=logical_id,
            value=value,
            expected_secret_generation_id=selected.secrets.generation_id,
            actor="host_local_cli",
        )

    def _activate(self, bundle: Path, service: ConfigurationService):
        acknowledgements = (
            frozenset({"access_expansion"})
            if "source_authentication:" in (bundle / "access.yaml").read_text(encoding="utf-8")
            else frozenset()
        )
        return service.activate_candidate(
            bundle,
            expected_authored_revision=snapshot_candidate(bundle).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
            acknowledgements=acknowledgements,
        )

    def _environment(self, *, required_secret: bool = False):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        bundle = root / "config"
        shutil.copytree(EXAMPLE_ROOT, bundle)
        if required_secret:
            self._add_required_source(bundle)
            (bundle / "secrets.env").write_text("RESIDENT_PHONE_TOKEN=initial\n", encoding="utf-8")
        store = GenerationStore(root / "store")
        store.initialize("example-home")
        service = ConfigurationService(store)

        class EnvironmentContext:
            def __enter__(self_nonlocal):
                return bundle, store, service

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return EnvironmentContext()

    @staticmethod
    def _add_required_source(bundle: Path) -> None:
        household = bundle / "household.yaml"
        household.write_text(
            household.read_text(encoding="utf-8").replace(
                "sources: []",
                "sources:\n"
                "  - id: resident_phone\n"
                "    enabled: true\n"
                "    type: mobile_app\n"
                "    fixed: false",
            ),
            encoding="utf-8",
        )
        access = bundle / "access.yaml"
        access.write_text(
            access.read_text(encoding="utf-8")
            + "source_authentication:\n"
            + "  credential_bindings:\n"
            + "    - source_id: resident_phone\n"
            + "      credential_secret: RESIDENT_PHONE_TOKEN\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
