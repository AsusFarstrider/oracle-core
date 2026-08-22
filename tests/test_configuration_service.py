from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from oracle_app.configuration import (
    CandidateActivationBlocked,
    ConfigurationService,
    ExclusiveStoreLock,
    GenerationStore,
    SecretGenerationConflict,
    SafetyAcknowledgementRequired,
    ControlServiceCompatibility,
    InteractionRuntimeCompatibility,
    SatelliteRuntimeCompatibility,
    SatelliteRuntimeCompatibilityStore,
    SelectionCommittedAuditPending,
    SelectionRecoveryAmbiguous,
    SelectionTransactionEnvelope,
    SelectedGenerationChanged,
    StoreLockTimeout,
    TransitionActivationBlocked,
    snapshot_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "examples" / "config"


class ConfigurationServiceTests(unittest.TestCase):
    def test_runtime_compatibility_acceptance_is_typed_replaceable_and_audited(self) -> None:
        with self._environment() as (_bundle, store, service):
            first_report = self._compatibility(runtime_version="stage3-first")
            first = service.accept_satellite_runtime_compatibility(
                "test_satellite_alpha",
                first_report,
                actor="host_local_cli",
            )
            replacement_report = self._compatibility(runtime_version="stage3-replacement")
            replacement = service.accept_satellite_runtime_compatibility(
                "test_satellite_alpha",
                replacement_report,
                actor="system_mode",
            )

            accepted = SatelliteRuntimeCompatibilityStore(store).load(
                "test_satellite_alpha"
            )
            self.assertIsNotNone(accepted)
            self.assertEqual(
                accepted.report.interaction_runtime.runtime_version,
                "stage3-replacement",
            )
            for result, actor in (
                (first, "host_local_cli"),
                (replacement, "system_mode"),
            ):
                audit = json.loads(
                    (
                        store.root / "audit" / f"{result.audit_event_id}.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    audit["operation"],
                    "accept_satellite_runtime_compatibility",
                )
                self.assertEqual(audit["outcome"], "acceptance_requested")
                self.assertEqual(audit["satellite_id"], "test_satellite_alpha")
                self.assertEqual(audit["actor"], actor)

    def test_review_persists_complete_report_and_distinct_actor_audits(self) -> None:
        with self._environment() as (bundle, store, service):
            selected = self._activate(bundle, service)

            host_review = service.review_candidate(bundle, actor="host_local_cli")
            system_review = service.review_candidate(bundle, actor="system_mode")

            self.assertNotEqual(host_review.inspection.candidate_id, system_review.inspection.candidate_id)
            for review, actor in ((host_review, "host_local_cli"), (system_review, "system_mode")):
                report = json.loads(
                    (store.root / "reports" / f"{review.inspection.candidate_id}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(report["validation_version"], review.validation_version)
                self.assertEqual(report["authored_revision"], review.inspection.authored_revision)
                self.assertEqual(report["normalized_candidate_revision"], review.inspection.normalized_candidate_revision)
                self.assertEqual(
                    report["selected_baseline"]["config_revision"],
                    selected.selected.config.config_revision,
                )
                audit = json.loads(
                    (store.root / "audit" / f"{review.audit_event_id}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(audit["operation"], "review_candidate")
                self.assertEqual(audit["outcome"], "reviewed")
                self.assertEqual(audit["actor"], actor)
            self.assertNotEqual(host_review.audit_event_id, system_review.audit_event_id)

    def test_enabled_identity_requires_selected_disabled_generation_before_removal(self) -> None:
        with self._environment() as (bundle, store, service):
            household = bundle / "household.yaml"
            enabled_block = (
                "  - id: temporary_resident\n"
                "    enabled: true\n"
                "    display_name: Temporary Resident\n"
                "    aliases: []\n"
                "    capabilities: {}\n"
            )
            household.write_text(
                household.read_text(encoding="utf-8").replace("defaults:\n", enabled_block + "defaults:\n"),
                encoding="utf-8",
            )
            enabled = self._activate(bundle, service)
            household.write_text(household.read_text(encoding="utf-8").replace(enabled_block, ""), encoding="utf-8")

            review = service.review_candidate(bundle, actor="host_local_cli")
            self.assertEqual(
                review.inspection.transition_validation.context.config_revision,
                enabled.selected.config.config_revision,
            )
            self.assertEqual(
                review.inspection.report.transition_blockers[0].code,
                "config.transition.enabled_identity_removed",
            )
            review_audit = json.loads(
                (store.root / "audit" / f"{review.audit_event_id}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review_audit["outcome"], "review_blocked")
            self.assertEqual(review_audit["actor"], "host_local_cli")
            with self.assertRaises(CandidateActivationBlocked):
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                    expected_secret_generation_id=enabled.selected.secrets.generation_id,
                    actor="host_local_cli",
                    acknowledgements=frozenset({"identity_removal"}),
                )
            reports = [json.loads(path.read_text(encoding="utf-8")) for path in (store.root / "reports").glob("*.json")]
            blocked_report = next(item for item in reports if item["transition_blockers"])
            self.assertEqual(blocked_report["transition_blockers"][0]["category"], "activation")
            self.assertEqual(
                blocked_report["transition_validation_context"]["config_revision"],
                enabled.selected.config.config_revision,
            )

            disabled_block = enabled_block.replace("enabled: true", "enabled: false")
            household.write_text(
                household.read_text(encoding="utf-8").replace("defaults:\n", disabled_block + "defaults:\n"),
                encoding="utf-8",
            )
            disabled = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=enabled.selected.secrets.generation_id,
                actor="host_local_cli",
            )
            household.write_text(household.read_text(encoding="utf-8").replace(disabled_block, ""), encoding="utf-8")
            removed = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=disabled.selected.secrets.generation_id,
                actor="host_local_cli",
                acknowledgements=frozenset({"identity_removal"}),
            )
            self.assertEqual(removed.outcome, "activated")

    def test_rollback_cannot_remove_identity_enabled_in_current_selection(self) -> None:
        with self._environment() as (bundle, _store, service):
            initial = self._activate(bundle, service)
            household = bundle / "household.yaml"
            household.write_text(
                household.read_text(encoding="utf-8").replace(
                    "defaults:\n",
                    "  - id: rollback_resident\n"
                    "    enabled: true\n"
                    "    display_name: Rollback Resident\n"
                    "    aliases: []\n"
                    "    capabilities: {}\n"
                    "defaults:\n",
                ),
                encoding="utf-8",
            )
            current = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=initial.selected.secrets.generation_id,
                actor="host_local_cli",
            )

            with self.assertRaises(TransitionActivationBlocked):
                service.rollback(
                    initial.selected.config.generation_id,
                    expected_secret_generation_id=current.selected.secrets.generation_id,
                    actor="host_local_cli",
                    acknowledgements=frozenset({"identity_removal"}),
                )

    def test_activation_rejects_selection_change_before_transition_revalidation(self) -> None:
        with self._environment() as (bundle, store, service):
            initial = self._activate(bundle, service)
            brain = bundle / "brain.yaml"
            brain.write_text(
                brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
                encoding="utf-8",
            )
            intervening = store.create_activation(
                initial.selected.config.generation_id,
                initial.selected.secrets.generation_id,
            )

            def change_selection(*_args, **_kwargs) -> None:
                store._replace_selected_pointer(
                    intervening.generation_id,
                    operation_id="selection_op_" + "e" * 32,
                    selection_revision=initial.selected.selection_revision + 1,
                    satellite_projection_activation_ids=initial.selected.satellite_projection_activation_ids,
                )

            with patch.object(service, "_persist_report", side_effect=change_selection):
                with self.assertRaises(SelectedGenerationChanged):
                    service.activate_candidate(
                        bundle,
                        expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                        expected_secret_generation_id=initial.selected.secrets.generation_id,
                        actor="host_local_cli",
                    )

    def test_initial_activation_persists_report_audit_and_selected_generation(self) -> None:
        with self._environment() as (bundle, store, service):
            result = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=None,
                actor="host_local_cli",
            )

            self.assertEqual(result.outcome, "activated")
            self.assertEqual(store.load_selected().activation, result.selected.activation)
            report = json.loads((store.root / "reports" / f"{result.candidate_id}.json").read_text(encoding="utf-8"))
            audit = json.loads((store.root / "audit" / f"{result.audit_event_id}.json").read_text(encoding="utf-8"))
            self.assertTrue(report["activation_eligible"])
            self.assertEqual(report["normalized_candidate_revision"], result.selected.config.config_revision)
            self.assertEqual(audit["outcome"], "activated")
            self.assertEqual(audit["actor"], "host_local_cli")

    def test_committed_selection_with_pending_audit_recovers_once_by_operation_id(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            brain = bundle / "brain.yaml"
            brain.write_text(
                brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
                encoding="utf-8",
            )

            with patch.object(service, "_persist_audit_payload", side_effect=OSError("audit unavailable")):
                with self.assertRaises(SelectionCommittedAuditPending) as caught:
                    service.activate_candidate(
                        bundle,
                        expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                        expected_secret_generation_id=first.selected.secrets.generation_id,
                        actor="host_local_cli",
                    )

            operation_id = caught.exception.operation_id
            selected = store.load_selected()
            self.assertEqual(selected.selection_operation_id, operation_id)
            self.assertEqual(selected.selection_revision, first.selected.selection_revision + 1)
            journal_path = store.root / "transactions" / operation_id / "journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertNotIn("audit_payload", journal)
            self.assertEqual(journal["target_activation_generation_id"], selected.activation.generation_id)
            self.assertEqual(journal["target_config_generation_id"], selected.config.generation_id)
            self.assertEqual(journal["target_secret_generation_id"], selected.secrets.generation_id)
            self.assertEqual(
                journal["target_satellite_projection_activation_ids"],
                dict(selected.satellite_projection_activation_ids),
            )
            self.assertTrue((store.root / "reports" / f"{journal['report_candidate_id']}.json").is_file())

            self.assertEqual(service.recover_selection_transactions(), (operation_id,))
            self.assertEqual(service.recover_selection_transactions(), ())
            self.assertFalse((store.root / "transactions" / operation_id).exists())
            audit_index = json.loads(
                (store.root / "audit-operations" / f"{operation_id}.json").read_text(encoding="utf-8")
            )
            audit = json.loads(
                (store.root / "audit" / f"{audit_index['event_id']}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(audit["operation_id"], operation_id)
            self.assertEqual(audit["outcome"], "activated")
            self.assertEqual(
                audit["satellite_projection_activation_ids"],
                dict(selected.satellite_projection_activation_ids),
            )

    def test_selection_recovery_fails_closed_when_pointer_and_journal_are_ambiguous(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            brain = bundle / "brain.yaml"
            brain.write_text(
                brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
                encoding="utf-8",
            )
            with patch.object(service, "_persist_audit_payload", side_effect=OSError("audit unavailable")):
                with self.assertRaises(SelectionCommittedAuditPending) as caught:
                    service.activate_candidate(
                        bundle,
                        expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                        expected_secret_generation_id=first.selected.secrets.generation_id,
                        actor="host_local_cli",
                    )

            pointer_path = store.root / "selected.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["operation_id"] = "selection_op_" + "0" * 32
            pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
            with self.assertRaises(SelectionRecoveryAmbiguous):
                service.recover_selection_transactions()
            self.assertTrue((store.root / "transactions" / caught.exception.operation_id).exists())

    def test_selection_envelope_rejects_arbitrary_payload_and_generation_drift(self) -> None:
        with self.assertRaises(SelectionRecoveryAmbiguous):
            SelectionTransactionEnvelope.from_dict({"audit_payload": {"raw_secret": "must-not-be-accepted"}})

        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            brain = bundle / "brain.yaml"
            brain.write_text(
                brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"),
                encoding="utf-8",
            )
            with patch.object(service, "_persist_audit_payload", side_effect=OSError("audit unavailable")):
                with self.assertRaises(SelectionCommittedAuditPending) as caught:
                    service.activate_candidate(
                        bundle,
                        expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                        expected_secret_generation_id=first.selected.secrets.generation_id,
                        actor="host_local_cli",
                    )
            journal_path = store.root / "transactions" / caught.exception.operation_id / "journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["target_config_generation_id"] = "config_" + "0" * 32
            journal_path.write_text(json.dumps(journal), encoding="utf-8")

            with self.assertRaises(SelectionRecoveryAmbiguous):
                service.recover_selection_transactions()
            self.assertTrue(journal_path.exists())
            self.assertFalse((store.root / "audit-operations" / f"{caught.exception.operation_id}.json").exists())

    def test_semantic_no_op_writes_report_and_audit_but_no_generations(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            counts_before = self._generation_counts(store)

            second = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=first.selected.secrets.generation_id,
                actor="system_mode",
            )

            self.assertEqual(second.outcome, "no_op")
            self.assertEqual(second.selected.activation, first.selected.activation)
            self.assertEqual(self._generation_counts(store), counts_before)
            self.assertTrue((store.root / "reports" / f"{second.candidate_id}.json").is_file())
            audit = json.loads((store.root / "audit" / f"{second.audit_event_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["outcome"], "no_op")

    def test_stale_secret_generation_fails_before_persistence(self) -> None:
        with self._environment() as (bundle, store, service):
            self._activate(bundle, service)
            counts_before = self._generation_counts(store)

            with self.assertRaises(SecretGenerationConflict):
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                    expected_secret_generation_id="secret_00000000000000000000000000000000",
                    actor="system_mode",
                )

            self.assertEqual(self._generation_counts(store), counts_before)

    def test_blocked_candidate_cannot_replace_selected_generation(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            self._add_required_source_without_secret(bundle)

            with self.assertRaises(CandidateActivationBlocked) as caught:
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                    expected_secret_generation_id=first.selected.secrets.generation_id,
                    actor="system_mode",
                )

            self.assertEqual(store.load_selected().activation, first.selected.activation)
            self.assertEqual(caught.exception.inspection.report.activation_blockers[0].code, "config.secret.required_missing")
            outcomes = {
                json.loads(path.read_text(encoding="utf-8"))["outcome"]
                for path in (store.root / "audit").glob("*.json")
            }
            self.assertIn("blocked", outcomes)

    def test_rollback_creates_new_activation_with_current_secrets(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            brain = bundle / "brain.yaml"
            brain.write_text(brain.read_text(encoding="utf-8").replace("level: INFO", "level: DEBUG"), encoding="utf-8")
            second = service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=first.selected.secrets.generation_id,
                actor="host_local_cli",
            )

            rollback = service.rollback(
                first.selected.config.generation_id,
                expected_secret_generation_id=second.selected.secrets.generation_id,
                actor="host_local_cli",
            )

            self.assertEqual(rollback.outcome, "activated")
            self.assertEqual(rollback.selected.config.config_revision, first.selected.config.config_revision)
            self.assertEqual(rollback.selected.secrets.generation_id, second.selected.secrets.generation_id)
            self.assertNotEqual(rollback.selected.activation.generation_id, first.selected.activation.generation_id)
            self.assertNotEqual(rollback.selected.activation.generation_id, second.selected.activation.generation_id)

    def test_rollback_to_same_revision_is_no_op(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            count_before = self._generation_counts(store)

            rollback = service.rollback(
                first.selected.config.generation_id,
                expected_secret_generation_id=first.selected.secrets.generation_id,
                actor="service",
            )

            self.assertEqual(rollback.outcome, "no_op")
            self.assertEqual(self._generation_counts(store), count_before)

    def test_safety_classification_requires_explicit_acknowledgement(self) -> None:
        with self._environment() as (bundle, store, service):
            first = self._activate(bundle, service)
            access = bundle / "access.yaml"
            access.write_text(
                access.read_text(encoding="utf-8").replace("public_health:\n  enabled: false", "public_health:\n  enabled: true"),
                encoding="utf-8",
            )
            authored_revision = snapshot_candidate(bundle).authored_revision

            with self.assertRaises(SafetyAcknowledgementRequired) as caught:
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=authored_revision,
                    expected_secret_generation_id=first.selected.secrets.generation_id,
                    actor="system_mode",
                )

            self.assertEqual(caught.exception.required, frozenset({"public_health_enablement"}))
            self.assertEqual(store.load_selected().activation, first.selected.activation)
            activated = service.activate_candidate(
                bundle,
                expected_authored_revision=authored_revision,
                expected_secret_generation_id=first.selected.secrets.generation_id,
                actor="system_mode",
                acknowledgements=frozenset({"public_health_enablement"}),
            )
            audit = json.loads((store.root / "audit" / f"{activated.audit_event_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["safety_acknowledgements"], ["public_health_enablement"])
            self.assertEqual(audit["semantic_changes"][0]["path"], "roles.access.yaml.public_health.enabled")

    def test_initial_unsafe_enablement_also_requires_acknowledgement(self) -> None:
        with self._environment() as (bundle, _store, service):
            access = bundle / "access.yaml"
            access.write_text(
                access.read_text(encoding="utf-8").replace("public_health:\n  enabled: false", "public_health:\n  enabled: true"),
                encoding="utf-8",
            )
            authored_revision = snapshot_candidate(bundle).authored_revision

            with self.assertRaises(SafetyAcknowledgementRequired):
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=authored_revision,
                    expected_secret_generation_id=None,
                    actor="host_local_cli",
                )

            result = service.activate_candidate(
                bundle,
                expected_authored_revision=authored_revision,
                expected_secret_generation_id=None,
                actor="host_local_cli",
                acknowledgements=frozenset({"public_health_enablement"}),
            )
            self.assertEqual(result.outcome, "activated")

    def test_unknown_acknowledgement_is_rejected(self) -> None:
        with self._environment() as (bundle, _store, service):
            with self.assertRaises(ValueError):
                service.activate_candidate(
                    bundle,
                    expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                    expected_secret_generation_id=None,
                    actor="host_local_cli",
                    acknowledgements=frozenset({"approve_everything"}),
                )

    def test_exclusive_lock_serializes_mutations(self) -> None:
        with self._environment(lock_timeout_seconds=0.0) as (bundle, _store, service):
            with ExclusiveStoreLock(service.store.root):
                with self.assertRaises(StoreLockTimeout):
                    self._activate(bundle, service)

    def test_audit_and_report_never_contain_raw_secret_values(self) -> None:
        with self._environment() as (bundle, store, service):
            self._add_required_source_without_secret(bundle)
            raw_secret = "raw-secret-must-not-escape"
            (bundle / "secrets.env").write_text(f"RESIDENT_PHONE_TOKEN={raw_secret}\n", encoding="utf-8")

            service.review_candidate(bundle, actor="host_local_cli")
            service.activate_candidate(
                bundle,
                expected_authored_revision=snapshot_candidate(bundle).authored_revision,
                expected_secret_generation_id=None,
                actor="service",
                acknowledgements=frozenset({"access_expansion"}),
            )

            report_and_audit = b"".join(path.read_bytes() for name in ("reports", "audit") for path in (store.root / name).glob("*.json"))
            self.assertNotIn(raw_secret.encode(), report_and_audit)

    def _activate(self, bundle: Path, service: ConfigurationService):
        return service.activate_candidate(
            bundle,
            expected_authored_revision=snapshot_candidate(bundle).authored_revision,
            expected_secret_generation_id=None,
            actor="service",
        )

    @staticmethod
    def _compatibility(*, runtime_version: str) -> SatelliteRuntimeCompatibility:
        return SatelliteRuntimeCompatibility(
            platform="linux",
            projection_schema_versions=[1],
            interaction_runtime=InteractionRuntimeCompatibility(
                runtime_version=runtime_version,
                voice_capture=True,
                brain_interaction=True,
                conversational_audio=True,
                wake_processing=True,
                cues=True,
                audio_input_types=["alsa_arecord"],
                interaction_output_types=["system_default"],
                wake_model_formats=["onnx"],
            ),
            control_service=ControlServiceCompatibility(
                runtime_version=runtime_version,
                playback_authority_schema_versions=[1],
                oracle_native_music=True,
                oracle_audiobook=True,
                volume_control_types=["alsa"],
            ),
        )

    @staticmethod
    def _generation_counts(store: GenerationStore) -> tuple[int, int, int]:
        return tuple(
            len(tuple((store.root / name).iterdir()))
            for name in ("config-generations", "secret-generations", "activations")
        )

    @staticmethod
    def _add_required_source_without_secret(bundle: Path) -> None:
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

    def _environment(self, *, lock_timeout_seconds: float = 5.0):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        bundle = root / "config"
        shutil.copytree(EXAMPLE_ROOT, bundle)
        store = GenerationStore(root / "store")
        store.initialize("example-home")
        service = ConfigurationService(store, lock_timeout_seconds=lock_timeout_seconds)

        class EnvironmentContext:
            def __enter__(self_nonlocal):
                return bundle, store, service

            def __exit__(self_nonlocal, *_args):
                temporary.cleanup()

        return EnvironmentContext()


if __name__ == "__main__":
    unittest.main()
