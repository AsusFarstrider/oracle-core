from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import errno
import os
from pathlib import Path
import re
import secrets
import time
from types import MappingProxyType
from typing import Literal, Mapping

from .authoring_transactions import (
    AuthoringModeError,
    AuthoringTransactionJournal,
)
from .access_safety import intrinsic_access_acknowledgements
from .diff import SemanticChange, semantic_diff
from .generations import (
    ConfigGeneration,
    GenerationStore,
    GenerationStoreError,
    SelectedActivation,
    _fsync_directory,
    _json_bytes,
    _read_json,
    _write_new,
)
from .loader import AuthoredRevisionConflict, LoadedBundle, assert_authored_revision, snapshot_candidate
from .model_base import ConfigurationModel
from .models import validate_role
from .normalization import CONFIG_FORMAT, NormalizedBundle, canonicalize_json
from .reporting import CandidateInspection, inspect_candidate
from .projection_generations import SatelliteProjectionGenerationStore
from .projections import (
    ProjectionGenerationError,
    SatelliteRuntimeCompatibility,
    SatelliteRuntimeCompatibilityStore,
    generate_satellite_projection,
)
from .runtime_cutover import RuntimeCutoverMarker, arm_runtime_cutover, runtime_cutover_required
from .secret_transactions import (
    SecretAlreadyExists,
    SecretCompanionDrift,
    SecretMutationError,
    SecretMutationOperation,
    SecretMutationResult,
    SecretNotFound,
    SecretRemovalBlocked,
    SecretTransactionJournal,
)
from .secrets import load_secret_companion
from .selection_transactions import (
    SelectionCommittedAuditPending,
    SelectionRecoveryAmbiguous,
    SELECTION_TRANSACTION_FORMAT,
    SelectionTransactionEnvelope,
    SelectionTransactionJournal,
)
from .transition_validation import (
    TransitionValidationContext,
    validate_configuration_transition,
)
from .validation import ConfigurationFinding


REPORT_FORMAT = "oracle-candidate-report-v1"
AUDIT_FORMAT = "oracle-configuration-audit-v1"
VALIDATION_VERSION = "oracle-configuration-validation-v1"
Actor = Literal["service", "host_local_cli", "system_mode"]
AuthoringMode = Literal["managed_writable", "external_read_only"]
_LOGICAL_SECRET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")
_KNOWN_ACKNOWLEDGEMENTS = frozenset(
    {
        "access_expansion",
        "credential_role_change",
        "identity_removal",
        "mutating_control_enablement",
        "public_health_enablement",
    }
)


class StoreLockTimeout(GenerationStoreError):
    pass


class SecretGenerationConflict(GenerationStoreError):
    def __init__(self, expected: str | None, actual: str | None) -> None:
        super().__init__("Selected secret generation changed before the transaction acquired the store lock.")
        self.expected = expected
        self.actual = actual


class CandidateActivationBlocked(GenerationStoreError):
    def __init__(self, inspection: CandidateInspection) -> None:
        super().__init__("Candidate is not eligible for activation.")
        self.inspection = inspection


class TransitionActivationBlocked(GenerationStoreError):
    def __init__(self, findings: tuple[ConfigurationFinding, ...]) -> None:
        super().__init__("Configuration transition is not eligible for activation.")
        self.findings = findings


class SelectedGenerationChanged(GenerationStoreError):
    pass


class SafetyAcknowledgementRequired(GenerationStoreError):
    def __init__(self, required: frozenset[str], provided: frozenset[str]) -> None:
        super().__init__("Transaction is missing required safety acknowledgements.")
        self.required = required
        self.provided = provided


@dataclass(frozen=True)
class ConfigurationTransactionResult:
    operation: Literal["activate", "rollback"]
    outcome: Literal["activated", "no_op"]
    selected: SelectedActivation
    audit_event_id: str
    candidate_id: str | None = None
    authored_revision: str | None = None


@dataclass(frozen=True)
class AuthoringMutationResult:
    operation: Literal["replace_authored_candidate"]
    outcome: Literal["activated", "authored_no_op"]
    selected: SelectedActivation
    audit_event_id: str
    candidate_id: str
    previous_authored_revision: str
    authored_revision: str


@dataclass(frozen=True)
class CandidateReview:
    inspection: CandidateInspection
    semantic_changes: tuple[SemanticChange, ...]
    required_safety_acknowledgements: frozenset[str]
    audit_event_id: str
    validation_version: str


@dataclass(frozen=True)
class ConfigurationStatus:
    authoring_mode: AuthoringMode
    authoring_root_configured: bool
    authored_revision: str | None
    selected_activation_generation_id: str | None
    config_generation_id: str | None
    config_revision: str | None
    secret_generation_id: str | None
    selection_operation_id: str | None
    selection_revision: int
    canonical_runtime_required: bool


@dataclass(frozen=True)
class RuntimeCutoverResult:
    operation: Literal["require_canonical_runtime"]
    outcome: Literal["armed", "already_required"]
    marker: RuntimeCutoverMarker
    audit_event_id: str


@dataclass(frozen=True)
class SatelliteRuntimeCompatibilityAcceptanceResult:
    satellite_id: str
    accepted_at: str
    report: SatelliteRuntimeCompatibility
    audit_event_id: str


class ExclusiveStoreLock(AbstractContextManager["ExclusiveStoreLock"]):
    def __init__(self, root: Path, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("Lock timeout cannot be negative.")
        self.path = Path(root) / ".lock"
        self.timeout_seconds = timeout_seconds
        self._stream = None

    def __enter__(self) -> ExclusiveStoreLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        self._stream = os.fdopen(descriptor, "r+b")
        if os.name == "nt":
            self._stream.seek(0)
            if self._stream.read(1) == b"":
                self._stream.write(b"\0")
                self._stream.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._acquire_nonblocking()
                return self
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    self._stream.close()
                    self._stream = None
                    raise
                if time.monotonic() >= deadline:
                    self._stream.close()
                    self._stream = None
                    raise StoreLockTimeout("Timed out waiting for the exclusive configuration-store lock.") from exc
                time.sleep(0.01)

    def __exit__(self, *_args: object) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def _acquire_nonblocking(self) -> None:
        if self._stream is None:
            raise RuntimeError("Store lock is not open.")
        if os.name == "nt":
            import msvcrt

            self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class ConfigurationService:
    def __init__(
        self,
        store: GenerationStore,
        *,
        lock_timeout_seconds: float = 5.0,
        authoring_mode: AuthoringMode = "external_read_only",
        authoring_root: Path | None = None,
    ) -> None:
        if authoring_mode not in {"managed_writable", "external_read_only"}:
            raise ValueError("Configuration authoring mode is unsupported.")
        if authoring_mode == "managed_writable" and authoring_root is None:
            raise ValueError("managed_writable authoring requires one bootstrap authoring root.")
        self.store = store
        self.lock_timeout_seconds = lock_timeout_seconds
        self.authoring_mode = authoring_mode
        self.authoring_root = None if authoring_root is None else Path(authoring_root).resolve(strict=True)
        self._secret_transactions = SecretTransactionJournal(store)
        self._authoring_transactions = AuthoringTransactionJournal(store)
        self._selection_transactions = SelectionTransactionJournal(store)
        self._projection_generations = SatelliteProjectionGenerationStore(store)
        self._runtime_compatibility = SatelliteRuntimeCompatibilityStore(store)

    def status(self) -> ConfigurationStatus:
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor="service")
            selected = self._selected_or_none()
            authored_revision = None
            if self.authoring_root is not None:
                authored_revision = snapshot_candidate(self.authoring_root).authored_revision
            return ConfigurationStatus(
                authoring_mode=self.authoring_mode,
                authoring_root_configured=self.authoring_root is not None,
                authored_revision=authored_revision,
                selected_activation_generation_id=(
                    None if selected is None else selected.activation.generation_id
                ),
                config_generation_id=None if selected is None else selected.config.generation_id,
                config_revision=None if selected is None else selected.config.config_revision,
                secret_generation_id=None if selected is None else selected.secrets.generation_id,
                selection_operation_id=None if selected is None else selected.selection_operation_id,
                selection_revision=0 if selected is None else selected.selection_revision,
                canonical_runtime_required=runtime_cutover_required(self.store),
            )

    def require_canonical_runtime(self, *, actor: Actor, acknowledge_one_way: bool) -> RuntimeCutoverResult:
        self._validate_actor(actor)
        if acknowledge_one_way is not True:
            raise SafetyAcknowledgementRequired(frozenset({"canonical_runtime_cutover"}), frozenset())
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor="service")
            selected = self._selected_or_none()
            if selected is None:
                raise GenerationStoreError("Canonical runtime cutover requires one selected activation.")
            if runtime_cutover_required(self.store):
                marker, _created = arm_runtime_cutover(self.store, selected, actor=actor)
                event_id = self._persist_audit(
                    operation="require_canonical_runtime",
                    outcome="already_required",
                    actor=actor,
                    previous=selected,
                    selected=selected,
                )
                return RuntimeCutoverResult("require_canonical_runtime", "already_required", marker, event_id)

            event_id = f"audit_{secrets.token_hex(16)}"
            recorded_at = datetime.now(UTC).isoformat()
            payload = self._audit_payload(
                operation="require_canonical_runtime",
                outcome="cutover_requested",
                actor=actor,
                previous=selected,
                selected=selected,
                event_id=event_id,
                recorded_at=recorded_at,
                acknowledgements=frozenset({"canonical_runtime_cutover"}),
            )
            self._persist_audit_payload(payload)
            marker, _created = arm_runtime_cutover(
                self.store,
                selected,
                actor=actor,
                audit_event_id=event_id,
                committed_at=recorded_at,
            )
            return RuntimeCutoverResult("require_canonical_runtime", "armed", marker, event_id)

    def accept_satellite_runtime_compatibility(
        self,
        satellite_id: str,
        report: SatelliteRuntimeCompatibility,
        *,
        actor: Actor,
    ) -> SatelliteRuntimeCompatibilityAcceptanceResult:
        """Accept finite operational evidence without making it configuration authority."""
        self._validate_actor(actor)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor="service")
            self._runtime_compatibility.load(satellite_id)
            selected = self._selected_or_none()
            event_id = self._persist_audit(
                operation="accept_satellite_runtime_compatibility",
                outcome="acceptance_requested",
                actor=actor,
                previous=selected,
                selected=selected,
                satellite_id=satellite_id,
            )
            accepted = self._runtime_compatibility.accept(satellite_id, report)
            return SatelliteRuntimeCompatibilityAcceptanceResult(
                satellite_id=accepted.satellite_id,
                accepted_at=accepted.accepted_at,
                report=accepted.report,
                audit_event_id=event_id,
            )

    def review_candidate(self, root: Path, *, actor: Actor) -> CandidateReview:
        self._validate_actor(actor)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor="service")
            current = self._selected_or_none()
            inspection = (
                inspect_candidate(root)
                if current is None
                else inspect_candidate(root, secret_snapshot=current.secrets.snapshot)
            )
            inspection = self._with_transition_validation(current, inspection)
            changes = self._candidate_changes(current, inspection)
            required = self._required_acknowledgements(changes) | self._intrinsic_acknowledgements(
                inspection if current is None else None
            )
            self._persist_report(inspection, changes, required, selected_baseline=current)
            event_id = self._persist_audit(
                operation="review_candidate",
                outcome="reviewed" if inspection.report.activation_eligible else "review_blocked",
                actor=actor,
                previous=current,
                selected=current,
                inspection=inspection,
                changes=changes,
            )
            return CandidateReview(inspection, changes, required, event_id, VALIDATION_VERSION)

    def replace_authored_candidate(
        self,
        staging_root: Path,
        *,
        expected_authored_revision: str,
        expected_secret_generation_id: str,
        actor: Actor,
        acknowledgements: frozenset[str] = frozenset(),
    ) -> AuthoringMutationResult:
        self._validate_actor(actor)
        self._validate_acknowledgements(acknowledgements)
        root = self._managed_authoring_root()
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_authoring_transactions_locked(root=root, actor=actor)
            self._recover_secret_transactions_locked(root=root, actor=actor)
            current = self.store.load_selected()
            self._assert_secret_generation(expected_secret_generation_id, current.secrets.generation_id)
            companion = load_secret_companion(root)
            if not companion._matches(current.secrets.snapshot):
                raise SecretCompanionDrift(
                    "Secret companion differs from the selected secret generation; resolve it before authored mutation."
                )
            previous = snapshot_candidate(root)
            assert_authored_revision(previous, expected_authored_revision)
            candidate = snapshot_candidate(staging_root)
            inspection = inspect_candidate(staging_root, secret_snapshot=current.secrets.snapshot)
            inspection = self._with_transition_validation(current, inspection)
            if inspection.authored_revision != candidate.authored_revision:
                raise AuthoredRevisionConflict(candidate.authored_revision, inspection.authored_revision)
            changes = self._candidate_changes(current, inspection)
            required_acknowledgements = self._required_acknowledgements(changes)
            self._persist_report(inspection, changes, required_acknowledgements, selected_baseline=current)
            if not inspection.report.activation_eligible:
                self._persist_audit(
                    operation="replace_authored_candidate",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                raise CandidateActivationBlocked(inspection)
            if not required_acknowledgements.issubset(acknowledgements):
                self._persist_audit(
                    operation="replace_authored_candidate",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                raise SafetyAcknowledgementRequired(required_acknowledgements, acknowledgements)

            inspection = self._revalidate_exact_transition(current, inspection)

            latest = snapshot_candidate(root)
            assert_authored_revision(latest, previous.authored_revision)
            transaction = self._authoring_transactions.prepare(
                root=root,
                actor=actor,
                previous=latest,
                candidate=candidate,
                runtime_change_required=(
                    inspection.normalized is None
                    or inspection.normalized.config_revision != current.config.config_revision
                ),
            )
            try:
                self._authoring_transactions.commit_candidate(transaction)
                transaction["authoring_committed"] = True
                self._authoring_transactions.write(transaction)
                committed = snapshot_candidate(root)
                if committed.authored_revision != candidate.authored_revision:
                    raise AuthoredRevisionConflict(candidate.authored_revision, committed.authored_revision)
                if not bool(transaction["runtime_change_required"]):
                    selected = current
                    event_id = self._persist_audit(
                        operation="replace_authored_candidate",
                        outcome="no_op",
                        actor=actor,
                        previous=current,
                        selected=current,
                        inspection=inspection,
                        changes=changes,
                        acknowledgements=acknowledgements,
                    )
                    self._authoring_transactions.cleanup(transaction)
                    return AuthoringMutationResult(
                        operation="replace_authored_candidate",
                        outcome="authored_no_op",
                        selected=selected,
                        audit_event_id=event_id,
                        candidate_id=inspection.candidate_id,
                        previous_authored_revision=previous.authored_revision,
                        authored_revision=candidate.authored_revision,
                    )
                config = self.store.install_config_candidate(inspection)
                transaction["new_config_generation_id"] = config.generation_id
                self._authoring_transactions.write(transaction)
                activation = self.store.create_activation(config.generation_id, current.secrets.generation_id)
                transaction["new_activation_generation_id"] = activation.generation_id
                self._authoring_transactions.write(transaction)
                satellite_activations = self._prepare_satellite_projection_activations(
                    self._require_inspection_bundle(inspection),
                    config_revision=config.config_revision,
                    secrets=current.secrets.snapshot,
                    previous=current,
                )
                selected, event_id = self._commit_selection(
                    operation="replace_authored_candidate",
                    actor=actor,
                    previous=current,
                    activation_generation_id=activation.generation_id,
                    satellite_projection_activation_ids=satellite_activations,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                self._authoring_transactions.cleanup(transaction)
            except SelectionCommittedAuditPending:
                raise
            except BaseException:
                self._recover_authoring_transaction(transaction, actor=actor, record_audit=True)
                raise
            return AuthoringMutationResult(
                operation="replace_authored_candidate",
                outcome="activated",
                selected=selected,
                audit_event_id=event_id,
                candidate_id=inspection.candidate_id,
                previous_authored_revision=previous.authored_revision,
                authored_revision=candidate.authored_revision,
            )

    def recover_authoring_transactions(
        self,
        *,
        actor: Actor = "service",
    ) -> tuple[str, ...]:
        self._validate_actor(actor)
        root = self._managed_authoring_root()
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_selection_transactions_locked()
            return self._recover_authoring_transactions_locked(root=root, actor=actor)

    def activate_candidate(
        self,
        root: Path,
        *,
        expected_authored_revision: str,
        expected_secret_generation_id: str | None,
        actor: Actor,
        acknowledgements: frozenset[str] = frozenset(),
    ) -> ConfigurationTransactionResult:
        self._validate_actor(actor)
        self._validate_acknowledgements(acknowledgements)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor=actor)
            current = self._selected_or_none()
            actual_secret_id = None if current is None else current.secrets.generation_id
            self._assert_secret_generation(expected_secret_generation_id, actual_secret_id)

            snapshot = snapshot_candidate(root)
            assert_authored_revision(snapshot, expected_authored_revision)
            companion_path = snapshot.root / "secrets.env"
            if current is not None and (companion_path.exists() or companion_path.is_symlink()):
                companion = load_secret_companion(snapshot.root)
                if not companion._matches(current.secrets.snapshot):
                    raise SecretCompanionDrift(
                        "Secret companion differs from the selected secret generation; use an explicit secret transaction."
                    )
            inspection = (
                inspect_candidate(root)
                if current is None
                else inspect_candidate(root, secret_snapshot=current.secrets.snapshot)
            )
            inspection = self._with_transition_validation(current, inspection)
            if inspection.authored_revision != snapshot.authored_revision:
                raise AuthoredRevisionConflict(snapshot.authored_revision, inspection.authored_revision)
            changes = self._candidate_changes(current, inspection)
            required_acknowledgements = self._required_acknowledgements(changes) | self._intrinsic_acknowledgements(
                inspection if current is None else None
            )
            self._persist_report(inspection, changes, required_acknowledgements, selected_baseline=current)
            if not inspection.report.activation_eligible or inspection.normalized is None:
                event_id = self._persist_audit(
                    operation="activate",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                raise CandidateActivationBlocked(inspection)
            if not required_acknowledgements.issubset(acknowledgements):
                self._persist_audit(
                    operation="activate",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                raise SafetyAcknowledgementRequired(required_acknowledgements, acknowledgements)

            if current is not None and current.config.config_revision == inspection.normalized.config_revision:
                event_id = self._persist_audit(
                    operation="activate",
                    outcome="no_op",
                    actor=actor,
                    previous=current,
                    selected=current,
                    inspection=inspection,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                return ConfigurationTransactionResult(
                    operation="activate",
                    outcome="no_op",
                    selected=current,
                    audit_event_id=event_id,
                    candidate_id=inspection.candidate_id,
                    authored_revision=inspection.authored_revision,
                )

            inspection = self._revalidate_exact_transition(current, inspection)
            config = self.store.install_config_candidate(inspection)
            secret_generation = (
                self.store.install_secrets(inspection.secrets)
                if current is None and inspection.secrets is not None
                else current.secrets if current is not None else None
            )
            if secret_generation is None:
                raise GenerationStoreError("Candidate has no validated secret generation.")
            activation = self.store.create_activation(config.generation_id, secret_generation.generation_id)
            satellite_activations = self._prepare_satellite_projection_activations(
                self._require_inspection_bundle(inspection),
                config_revision=config.config_revision,
                secrets=secret_generation.snapshot,
                previous=current,
            )
            selected, event_id = self._commit_selection(
                operation="activate",
                actor=actor,
                previous=current,
                activation_generation_id=activation.generation_id,
                satellite_projection_activation_ids=satellite_activations,
                inspection=inspection,
                changes=changes,
                acknowledgements=acknowledgements,
            )
            return ConfigurationTransactionResult(
                operation="activate",
                outcome="activated",
                selected=selected,
                audit_event_id=event_id,
                candidate_id=inspection.candidate_id,
                authored_revision=inspection.authored_revision,
            )

    def rollback(
        self,
        config_generation_id: str,
        *,
        expected_secret_generation_id: str,
        actor: Actor,
        acknowledgements: frozenset[str] = frozenset(),
    ) -> ConfigurationTransactionResult:
        self._validate_actor(actor)
        self._validate_acknowledgements(acknowledgements)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_configured_authoring_locked(actor=actor)
            current = self.store.load_selected()
            self._assert_secret_generation(expected_secret_generation_id, current.secrets.generation_id)
            target = self.store.load_config(config_generation_id)
            current_normalized = self._normalized(current.config)
            target_normalized = self._normalized(target)
            changes = semantic_diff(current_normalized, target_normalized)
            if current.selection_operation_id is None:
                raise GenerationIntegrityError("Selected generation lacks transition-validation identity.")
            transition = validate_configuration_transition(
                current_normalized,
                target_normalized,
                context=TransitionValidationContext(
                    activation_generation_id=current.activation.generation_id,
                    config_generation_id=current.config.generation_id,
                    config_revision=current.config.config_revision,
                    selection_operation_id=current.selection_operation_id,
                    selection_revision=current.selection_revision,
                ),
            )
            if transition.blockers:
                self._persist_audit(
                    operation="rollback",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    changes=changes,
                    acknowledgements=acknowledgements,
                    transition_blockers=transition.blockers,
                )
                raise TransitionActivationBlocked(transition.blockers)
            required_acknowledgements = self._required_acknowledgements(changes)
            if not required_acknowledgements.issubset(acknowledgements):
                self._persist_audit(
                    operation="rollback",
                    outcome="blocked",
                    actor=actor,
                    previous=current,
                    selected=current,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                raise SafetyAcknowledgementRequired(required_acknowledgements, acknowledgements)
            if target.config_revision == current.config.config_revision:
                event_id = self._persist_audit(
                    operation="rollback",
                    outcome="no_op",
                    actor=actor,
                    previous=current,
                    selected=current,
                    changes=changes,
                    acknowledgements=acknowledgements,
                )
                return ConfigurationTransactionResult("rollback", "no_op", current, event_id)

            latest = self._assert_exact_selected(current)
            if latest is None:
                raise SelectedGenerationChanged("Selected generation changed before rollback.")
            repeated_transition = validate_configuration_transition(
                self._normalized(latest.config),
                target_normalized,
                context=transition.context,
            )
            if repeated_transition.blockers:
                raise TransitionActivationBlocked(repeated_transition.blockers)
            activation = self.store.create_activation(target.generation_id, current.secrets.generation_id)
            satellite_activations = self._prepare_satellite_projection_activations(
                self._bundle_from_config(target),
                config_revision=target.config_revision,
                secrets=current.secrets.snapshot,
                previous=current,
            )
            selected, event_id = self._commit_selection(
                operation="rollback",
                actor=actor,
                previous=current,
                activation_generation_id=activation.generation_id,
                satellite_projection_activation_ids=satellite_activations,
                changes=changes,
                acknowledgements=acknowledgements,
            )
            return ConfigurationTransactionResult("rollback", "activated", selected, event_id)

    def mutate_secret(
        self,
        root: Path,
        *,
        operation: SecretMutationOperation,
        logical_id: str,
        value: str | None,
        expected_secret_generation_id: str,
        actor: Actor,
    ) -> SecretMutationResult:
        self._validate_actor(actor)
        self._validate_secret_mutation(operation, logical_id, value)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            resolved_root = Path(root).resolve(strict=True)
            self._recover_configured_authoring_locked(actor=actor)
            self._recover_secret_transactions_locked(root=resolved_root)
            current = self.store.load_selected()
            self._assert_secret_generation(expected_secret_generation_id, current.secrets.generation_id)
            companion = load_secret_companion(root)
            if not companion._matches(current.secrets.snapshot):
                raise SecretCompanionDrift(
                    "Secret companion differs from the selected secret generation; activate or restore it before mutation."
                )

            present = logical_id in current.secrets.snapshot.present_ids
            if operation == "create_secret" and present:
                raise SecretAlreadyExists("Logical secret already exists; use replace or rotate explicitly.")
            if operation != "create_secret" and not present:
                raise SecretNotFound("Logical secret does not exist in the selected generation.")
            if operation == "remove_secret":
                if logical_id in current.config.required_secret_ids:
                    raise SecretRemovalBlocked("Secret is required by enabled selected configuration.")
                candidate = current.secrets.snapshot._without_value(logical_id)
            else:
                if value is None:
                    raise SecretMutationError("Secret value is required for this operation.")
                candidate = current.secrets.snapshot._with_value(logical_id, value)

            transaction = self._secret_transactions.prepare(
                root=root,
                operation=operation,
                actor=actor,
                logical_id=logical_id,
                previous=current,
                candidate=candidate,
            )
            try:
                new_secret = self.store.install_secrets(candidate)
                activation = self.store.create_activation(current.config.generation_id, new_secret.generation_id)
                satellite_activations = self._prepare_satellite_projection_activations(
                    self._bundle_from_config(current.config),
                    config_revision=current.config.config_revision,
                    secrets=new_secret.snapshot,
                    previous=current,
                )
                transaction.update(
                    new_secret_generation_id=new_secret.generation_id,
                    new_activation_generation_id=activation.generation_id,
                )
                self._secret_transactions.write(transaction)
                self._secret_transactions.commit_companion(transaction)
                transaction["companion_committed"] = True
                self._secret_transactions.write(transaction)
                selected, event_id = self._commit_selection(
                    operation="secret_mutation",
                    audit_operation=operation,
                    actor=actor,
                    previous=current,
                    activation_generation_id=activation.generation_id,
                    satellite_projection_activation_ids=satellite_activations,
                    secret_logical_id=logical_id,
                    revoked_secret_generation_id=current.secrets.generation_id,
                )
                self.store.revoke_secret_generation(
                    current.secrets.generation_id,
                    replaced_by=new_secret.generation_id,
                )
                pruned = self.store.prune_revoked_secret_values(retain=1)
                self._secret_transactions.cleanup(transaction)
            except SelectionCommittedAuditPending:
                raise
            except BaseException:
                self._recover_secret_transaction(transaction, actor=actor, record_audit=True)
                raise
            return SecretMutationResult(
                operation=operation,
                selected=selected,
                previous_secret_generation_id=current.secrets.generation_id,
                secret_generation_id=selected.secrets.generation_id,
                revoked_secret_generation_id=current.secrets.generation_id,
                pruned_secret_generation_ids=pruned,
                audit_event_id=event_id,
                logical_id=logical_id,
            )

    def recover_secret_transactions(self, root: Path, *, actor: Actor = "service") -> tuple[str, ...]:
        self._validate_actor(actor)
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            self._recover_selection_transactions_locked()
            return self._recover_secret_transactions_locked(root=Path(root).resolve(strict=True), actor=actor)

    def _recover_authoring_transactions_locked(
        self,
        *,
        root: Path,
        actor: Actor,
    ) -> tuple[str, ...]:
        recovered: list[str] = []
        for journal in self._authoring_transactions.pending(root=root):
            self._recover_authoring_transaction(journal, actor=actor, record_audit=True)
            recovered.append(str(journal["transaction_id"]))
        return tuple(recovered)

    def _recover_authoring_transaction(
        self,
        transaction: dict[str, object],
        *,
        actor: Actor,
        record_audit: bool,
    ) -> None:
        new_activation_id = transaction["new_activation_generation_id"]
        selected = self._selected_or_none()
        committed = (
            isinstance(new_activation_id, str)
            and selected is not None
            and selected.activation.generation_id == new_activation_id
        )
        if not bool(transaction["runtime_change_required"]) and bool(transaction["authoring_committed"]):
            committed = True
        if committed:
            self._authoring_transactions.ensure_candidate(transaction)
            outcome = "recovered_commit"
        else:
            self._authoring_transactions.restore_previous(transaction)
            if isinstance(new_activation_id, str):
                activation_path = self.store.root / "activations" / new_activation_id
                if activation_path.exists():
                    self.store.discard_activation(new_activation_id)
            outcome = "recovered_rollback"
        if record_audit:
            self._persist_audit(
                operation="recover_authoring_transaction",
                outcome=outcome,
                actor=actor,
                previous=None,
                selected=self._selected_or_none(),
            )
        self._authoring_transactions.cleanup(transaction)

    @staticmethod
    def _validate_secret_mutation(
        operation: str,
        logical_id: str,
        value: str | None,
    ) -> None:
        if operation not in {"create_secret", "replace_secret", "rotate_secret", "remove_secret"}:
            raise ValueError("Secret mutation operation is unsupported.")
        if not isinstance(logical_id, str) or _LOGICAL_SECRET_ID.fullmatch(logical_id) is None:
            raise ValueError("Secret mutation requires a canonical logical secret ID.")
        if operation == "remove_secret":
            if value is not None:
                raise ValueError("Secret removal cannot include a raw value.")
            return
        if not isinstance(value, str) or value == "" or "\n" in value or "\r" in value:
            raise ValueError("Secret value must be one nonempty physical line.")

    def _recover_secret_transactions_locked(
        self,
        *,
        root: Path,
        actor: Actor = "service",
    ) -> tuple[str, ...]:
        recovered: list[str] = []
        for journal in self._secret_transactions.pending(root=root):
            self._recover_secret_transaction(journal, actor=actor, record_audit=True)
            recovered.append(str(journal["transaction_id"]))
        return tuple(recovered)

    def _recover_secret_transaction(
        self,
        transaction: dict[str, object],
        *,
        actor: Actor = "service",
        record_audit: bool,
    ) -> None:
        new_activation_id = transaction["new_activation_generation_id"]
        selected = self._selected_or_none()
        committed = (
            isinstance(new_activation_id, str)
            and selected is not None
            and selected.activation.generation_id == new_activation_id
        )
        if committed:
            self._secret_transactions.ensure_committed_companion(transaction, selected.secrets.snapshot)
            old_secret_id = transaction["old_secret_generation_id"]
            if isinstance(old_secret_id, str):
                self.store.revoke_secret_generation(old_secret_id, replaced_by=selected.secrets.generation_id)
            self.store.prune_revoked_secret_values(retain=1)
            outcome = "recovered_commit"
        else:
            self._secret_transactions.restore_companion(transaction)
            if isinstance(new_activation_id, str):
                activation_path = self.store.root / "activations" / new_activation_id
                if activation_path.exists():
                    self.store.discard_activation(new_activation_id)
            new_secret_id = transaction["new_secret_generation_id"]
            if isinstance(new_secret_id, str):
                secret_path = self.store.root / "secret-generations" / new_secret_id
                if secret_path.exists():
                    self.store.discard_secret_generation(new_secret_id)
            outcome = "recovered_rollback"
        if record_audit:
            self._persist_audit(
                operation="recover_secret_transaction",
                outcome=outcome,
                actor=actor,
                previous=None,
                selected=self._selected_or_none(),
                secret_logical_id=str(transaction["logical_id"]),
            )
        self._secret_transactions.cleanup(transaction)

    def _selected_or_none(self) -> SelectedActivation | None:
        if not (self.store.root / "selected.json").exists():
            return None
        return self.store.load_selected()

    def _managed_authoring_root(self) -> Path:
        if self.authoring_mode != "managed_writable" or self.authoring_root is None:
            raise AuthoringModeError("Non-secret authored mutation requires managed_writable bootstrap mode.")
        return self.authoring_root

    def _recover_configured_authoring_locked(self, *, actor: Actor) -> tuple[str, ...]:
        self._recover_selection_transactions_locked()
        if self.authoring_mode != "managed_writable" or self.authoring_root is None:
            return ()
        return self._recover_authoring_transactions_locked(root=self.authoring_root, actor=actor)

    def recover_selection_transactions(self) -> tuple[str, ...]:
        with ExclusiveStoreLock(self.store.root, timeout_seconds=self.lock_timeout_seconds):
            return self._recover_selection_transactions_locked()

    def _commit_selection(
        self,
        *,
        operation: Literal["activate", "rollback", "replace_authored_candidate", "secret_mutation"],
        audit_operation: str | None = None,
        actor: Actor,
        previous: SelectedActivation | None,
        activation_generation_id: str,
        satellite_projection_activation_ids: Mapping[str, str],
        inspection: CandidateInspection | None = None,
        changes: tuple[SemanticChange, ...] = (),
        acknowledgements: frozenset[str] = frozenset(),
        secret_logical_id: str | None = None,
        revoked_secret_generation_id: str | None = None,
    ) -> tuple[SelectedActivation, str]:
        self._recover_selection_transactions_locked()
        operation_id = self._selection_transactions.new_operation_id()
        previous_revision = 0 if previous is None else previous.selection_revision
        selection_revision = previous_revision + 1
        target = self.store._resolve_activation(activation_generation_id)
        validated_satellite_activations = self.store._validate_satellite_projection_activation_ids(
            target.config, satellite_projection_activation_ids
        )
        selected_preview = SelectedActivation(
            activation=target.activation,
            config=target.config,
            secrets=target.secrets,
            selection_operation_id=operation_id,
            selection_revision=selection_revision,
            satellite_projection_activation_ids=MappingProxyType(dict(validated_satellite_activations)),
        )
        event_id = f"audit_{secrets.token_hex(16)}"
        recorded_at = datetime.now(UTC).isoformat()
        audit_payload = self._audit_payload(
            operation=audit_operation or operation,
            outcome="activated",
            actor=actor,
            previous=previous,
            selected=selected_preview,
            inspection=inspection,
            changes=changes,
            acknowledgements=acknowledgements,
            secret_logical_id=secret_logical_id,
            revoked_secret_generation_id=revoked_secret_generation_id,
            operation_id=operation_id,
            event_id=event_id,
            recorded_at=recorded_at,
        )
        transaction = self._selection_transactions.prepare(SelectionTransactionEnvelope(
            format=SELECTION_TRANSACTION_FORMAT,
            operation_id=operation_id,
            audit_event_id=event_id,
            recorded_at=recorded_at,
            operation=operation,
            audit_operation=audit_operation or operation,
            actor=actor,
            previous_activation_generation_id=None if previous is None else previous.activation.generation_id,
            previous_config_generation_id=None if previous is None else previous.config.generation_id,
            previous_secret_generation_id=None if previous is None else previous.secrets.generation_id,
            previous_selection_operation_id=None if previous is None else previous.selection_operation_id,
            previous_selection_revision=previous_revision,
            previous_satellite_projection_activation_ids=(
                {} if previous is None else dict(previous.satellite_projection_activation_ids)
            ),
            target_activation_generation_id=activation_generation_id,
            target_config_generation_id=target.config.generation_id,
            target_secret_generation_id=target.secrets.generation_id,
            selection_revision=selection_revision,
            target_satellite_projection_activation_ids=dict(validated_satellite_activations),
            report_candidate_id=None if inspection is None else inspection.candidate_id,
            acknowledgements=tuple(sorted(acknowledgements)),
            secret_logical_id=secret_logical_id,
            revoked_secret_generation_id=revoked_secret_generation_id,
        ))
        try:
            selected = self.store._replace_selected_pointer(
                activation_generation_id,
                operation_id=operation_id,
                selection_revision=selection_revision,
                satellite_projection_activation_ids=validated_satellite_activations,
            )
        except BaseException:
            self._recover_selection_transactions_locked()
            raise
        try:
            event_id = self._persist_audit_payload(audit_payload)
        except BaseException as exc:
            raise SelectionCommittedAuditPending(operation_id, selection_revision) from exc
        self._selection_transactions.cleanup(transaction)
        return selected, event_id

    def _recover_selection_transactions_locked(self) -> tuple[str, ...]:
        recovered: list[str] = []
        for transaction in self._selection_transactions.pending():
            operation_id = transaction.operation_id
            target = self.store._resolve_activation(transaction.target_activation_generation_id)
            if (
                target.config.generation_id != transaction.target_config_generation_id
                or target.secrets.generation_id != transaction.target_secret_generation_id
            ):
                raise SelectionRecoveryAmbiguous("Selection journal target generations are inconsistent.")
            self.store._validate_satellite_projection_activation_ids(
                target.config, transaction.target_satellite_projection_activation_ids
            )
            pointer_path = self.store.root / "selected.json"
            current = self.store.load_selected() if pointer_path.exists() else None
            committed = (
                current is not None
                and current.activation.generation_id == transaction.target_activation_generation_id
                and current.selection_operation_id == operation_id
                and current.selection_revision == transaction.selection_revision
                and dict(current.satellite_projection_activation_ids)
                == dict(transaction.target_satellite_projection_activation_ids)
            )
            uncommitted = (
                (
                    current is None
                    and transaction.previous_activation_generation_id is None
                    and transaction.previous_selection_revision == 0
                )
                or (
                    current is not None
                    and current.activation.generation_id == transaction.previous_activation_generation_id
                    and current.config.generation_id == transaction.previous_config_generation_id
                    and current.secrets.generation_id == transaction.previous_secret_generation_id
                    and current.selection_operation_id == transaction.previous_selection_operation_id
                    and current.selection_revision == transaction.previous_selection_revision
                    and dict(current.satellite_projection_activation_ids)
                    == dict(transaction.previous_satellite_projection_activation_ids)
                )
            )
            if not committed and not uncommitted:
                raise SelectionRecoveryAmbiguous("Selection journal and selected pointer cannot prove one outcome.")
            previous = self._selection_previous(transaction)
            changes = () if previous is None else semantic_diff(self._normalized(previous.config), self._normalized(target.config))
            candidate_identity = self._selection_report_identity(transaction, previous)
            payload = self._audit_payload(
                operation=transaction.audit_operation,
                outcome="activated" if committed else "recovered_rollback",
                actor=transaction.actor,  # type: ignore[arg-type]
                previous=previous,
                selected=current,
                changes=changes,
                acknowledgements=frozenset(transaction.acknowledgements),
                secret_logical_id=transaction.secret_logical_id,
                revoked_secret_generation_id=transaction.revoked_secret_generation_id,
                operation_id=operation_id,
                event_id=transaction.audit_event_id,
                recorded_at=transaction.recorded_at,
                candidate_identity=candidate_identity,
            )
            self._persist_audit_payload(payload)
            self._selection_transactions.cleanup(transaction)
            recovered.append(operation_id)
        return tuple(recovered)

    def _selection_previous(
        self,
        transaction: SelectionTransactionEnvelope,
    ) -> SelectedActivation | None:
        if transaction.previous_activation_generation_id is None:
            return None
        previous = self.store._resolve_activation(transaction.previous_activation_generation_id)
        if (
            previous.config.generation_id != transaction.previous_config_generation_id
            or previous.secrets.generation_id != transaction.previous_secret_generation_id
        ):
            raise SelectionRecoveryAmbiguous("Selection journal previous generations are inconsistent.")
        self.store._validate_satellite_projection_activation_ids(
            previous.config, transaction.previous_satellite_projection_activation_ids
        )
        return SelectedActivation(
            activation=previous.activation,
            config=previous.config,
            secrets=previous.secrets,
            selection_operation_id=transaction.previous_selection_operation_id,
            selection_revision=transaction.previous_selection_revision,
            satellite_projection_activation_ids=MappingProxyType(
                dict(transaction.previous_satellite_projection_activation_ids)
            ),
        )

    def _selection_report_identity(
        self,
        transaction: SelectionTransactionEnvelope,
        previous: SelectedActivation | None,
    ) -> dict[str, object] | None:
        if transaction.report_candidate_id is None:
            return None
        report = _read_json(self.store.root / "reports" / f"{transaction.report_candidate_id}.json")
        if (
            not isinstance(report, dict)
            or report.get("format") != REPORT_FORMAT
            or report.get("validation_version") != VALIDATION_VERSION
            or report.get("candidate_id") != transaction.report_candidate_id
            or report.get("selected_baseline") != self._selected_baseline(previous)
            or not isinstance(report.get("authored_revision"), str)
            or (
                report.get("normalized_candidate_revision") is not None
                and not isinstance(report.get("normalized_candidate_revision"), str)
            )
        ):
            raise SelectionRecoveryAmbiguous("Selection candidate report does not match its journal baseline.")
        return {
            "candidate_id": report["candidate_id"],
            "authored_revision": report["authored_revision"],
            "normalized_candidate_revision": report["normalized_candidate_revision"],
        }

    @staticmethod
    def _assert_secret_generation(expected: str | None, actual: str | None) -> None:
        if expected != actual:
            raise SecretGenerationConflict(expected, actual)

    @staticmethod
    def _validate_actor(actor: str) -> None:
        if actor not in {"service", "host_local_cli", "system_mode"}:
            raise ValueError("Configuration actor must be a supported sanitized actor class.")

    @staticmethod
    def _validate_acknowledgements(acknowledgements: frozenset[str]) -> None:
        if not isinstance(acknowledgements, frozenset) or not acknowledgements.issubset(_KNOWN_ACKNOWLEDGEMENTS):
            raise ValueError("Safety acknowledgements must be a set of supported acknowledgement IDs.")

    def _persist_report(
        self,
        inspection: CandidateInspection,
        changes: tuple[SemanticChange, ...],
        required_acknowledgements: frozenset[str],
        *,
        selected_baseline: SelectedActivation | None,
    ) -> None:
        directory = self._confined_output_directory("reports")
        path = directory / f"{inspection.candidate_id}.json"
        payload = {
            "format": REPORT_FORMAT,
            "validation_version": VALIDATION_VERSION,
            "candidate_id": inspection.candidate_id,
            "authored_revision": inspection.authored_revision,
            "normalized_candidate_revision": inspection.normalized_candidate_revision,
            "activation_eligible": inspection.report.activation_eligible,
            "validation_findings": [asdict(item) for item in inspection.report.validation_findings],
            "activation_blockers": [asdict(item) for item in inspection.report.activation_blockers],
            "transition_blockers": [asdict(item) for item in inspection.report.transition_blockers],
            "readiness_findings": [asdict(item) for item in inspection.report.readiness_findings],
            "transition_validation_context": (
                None
                if inspection.transition_validation is None
                else asdict(inspection.transition_validation.context)
            ),
            "selected_baseline": self._selected_baseline(selected_baseline),
            "semantic_changes": [self._change_summary(item) for item in changes],
            "required_safety_acknowledgements": sorted(required_acknowledgements),
        }
        _write_new(path, _json_bytes(payload), mode=0o600)
        _fsync_directory(directory)

    def _persist_audit(
        self,
        *,
        operation: Literal[
            "accept_satellite_runtime_compatibility",
            "activate",
            "review_candidate",
            "rollback",
            "replace_authored_candidate",
            "recover_authoring_transaction",
            "create_secret",
            "replace_secret",
            "rotate_secret",
            "remove_secret",
            "recover_secret_transaction",
            "require_canonical_runtime",
        ],
        outcome: Literal[
            "acceptance_requested",
            "activated",
            "no_op",
            "blocked",
            "reviewed",
            "review_blocked",
            "recovered_commit",
            "recovered_rollback",
            "cutover_requested",
            "already_required",
        ],
        actor: Actor,
        previous: SelectedActivation | None,
        selected: SelectedActivation | None,
        inspection: CandidateInspection | None = None,
        changes: tuple[SemanticChange, ...] = (),
        acknowledgements: frozenset[str] = frozenset(),
        secret_logical_id: str | None = None,
        revoked_secret_generation_id: str | None = None,
        pruned_secret_generation_ids: tuple[str, ...] = (),
        operation_id: str | None = None,
        transition_blockers: tuple[ConfigurationFinding, ...] = (),
        satellite_id: str | None = None,
    ) -> str:
        payload = self._audit_payload(
            operation=operation,
            outcome=outcome,
            actor=actor,
            previous=previous,
            selected=selected,
            inspection=inspection,
            changes=changes,
            acknowledgements=acknowledgements,
            secret_logical_id=secret_logical_id,
            revoked_secret_generation_id=revoked_secret_generation_id,
            pruned_secret_generation_ids=pruned_secret_generation_ids,
            operation_id=operation_id,
            transition_blockers=transition_blockers,
            satellite_id=satellite_id,
        )
        return self._persist_audit_payload(payload)

    def _audit_payload(
        self,
        *,
        operation: str,
        outcome: str,
        actor: Actor,
        previous: SelectedActivation | None,
        selected: SelectedActivation | None,
        inspection: CandidateInspection | None = None,
        changes: tuple[SemanticChange, ...] = (),
        acknowledgements: frozenset[str] = frozenset(),
        secret_logical_id: str | None = None,
        revoked_secret_generation_id: str | None = None,
        pruned_secret_generation_ids: tuple[str, ...] = (),
        operation_id: str | None = None,
        transition_blockers: tuple[ConfigurationFinding, ...] = (),
        satellite_id: str | None = None,
        event_id: str | None = None,
        recorded_at: str | None = None,
        candidate_identity: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if inspection is not None and candidate_identity is not None:
            raise ValueError("Audit candidate identity has two sources.")
        candidate_id = None if inspection is None else inspection.candidate_id
        authored_revision = None if inspection is None else inspection.authored_revision
        normalized_revision = None if inspection is None else inspection.normalized_candidate_revision
        if candidate_identity is not None:
            candidate_id = candidate_identity["candidate_id"]
            authored_revision = candidate_identity["authored_revision"]
            normalized_revision = candidate_identity["normalized_candidate_revision"]
        payload: dict[str, object] = {
            "format": AUDIT_FORMAT,
            "validation_version": VALIDATION_VERSION,
            "event_id": event_id or f"audit_{secrets.token_hex(16)}",
            "operation_id": operation_id,
            "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
            "operation": operation,
            "outcome": outcome,
            "actor": actor,
            "candidate_id": candidate_id,
            "authored_revision": authored_revision,
            "normalized_candidate_revision": normalized_revision,
            "previous_activation_generation_id": None if previous is None else previous.activation.generation_id,
            "selected_activation_generation_id": None if selected is None else selected.activation.generation_id,
            "selection_operation_id": None if selected is None else selected.selection_operation_id,
            "selection_revision": 0 if selected is None else selected.selection_revision,
            "config_generation_id": None if selected is None else selected.config.generation_id,
            "config_revision": None if selected is None else selected.config.config_revision,
            "secret_generation_id": None if selected is None else selected.secrets.generation_id,
            "satellite_projection_activation_ids": (
                {} if selected is None else dict(selected.satellite_projection_activation_ids)
            ),
            "secret_logical_id": secret_logical_id,
            "revoked_secret_generation_id": revoked_secret_generation_id,
            "pruned_secret_generation_ids": list(pruned_secret_generation_ids),
            "semantic_changes": [self._change_summary(item) for item in changes],
            "safety_acknowledgements": sorted(acknowledgements),
            "transition_blockers": [asdict(item) for item in transition_blockers],
        }
        if satellite_id is not None:
            payload["satellite_id"] = satellite_id
        return payload

    def _persist_audit_payload(self, payload: dict[str, object]) -> str:
        directory = self._confined_output_directory("audit")
        operation_id = payload.get("operation_id")
        event_id = str(payload["event_id"])
        path = directory / f"{event_id}.json"
        if path.exists():
            existing = _read_json(path)
            if existing != payload:
                raise SelectionRecoveryAmbiguous("Selection operation has conflicting audit records.")
        else:
            _write_new(path, _json_bytes(payload), mode=0o600)
            _fsync_directory(directory)
        if operation_id is not None:
            index = self._confined_output_directory("audit-operations")
            index_path = index / f"{operation_id}.json"
            identity = {"operation_id": operation_id, "event_id": event_id}
            if index_path.exists():
                if _read_json(index_path) != identity:
                    raise SelectionRecoveryAmbiguous("Selection operation audit identity is ambiguous.")
            else:
                _write_new(index_path, _json_bytes(identity), mode=0o600)
                _fsync_directory(index)
        return event_id

    def _confined_output_directory(self, name: Literal["reports", "audit", "audit-operations"]) -> Path:
        directory = self.store.root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationStoreError(f"Configuration {name} directory escapes the installed store.")
        return directory

    @staticmethod
    def _change_summary(change: SemanticChange) -> dict[str, object]:
        return {
            "path": change.path,
            "operation": change.operation,
            "restart_required": change.restart_required,
            "safety_acknowledgements": list(change.safety_acknowledgements),
        }

    @staticmethod
    def _selected_baseline(selected: SelectedActivation | None) -> dict[str, object] | None:
        if selected is None:
            return None
        return {
            "activation_generation_id": selected.activation.generation_id,
            "config_generation_id": selected.config.generation_id,
            "config_revision": selected.config.config_revision,
            "secret_generation_id": selected.secrets.generation_id,
            "satellite_projection_activation_ids": dict(selected.satellite_projection_activation_ids),
            "selection_operation_id": selected.selection_operation_id,
            "selection_revision": selected.selection_revision,
        }

    @staticmethod
    def _require_inspection_bundle(inspection: CandidateInspection) -> LoadedBundle:
        if inspection.bundle is None:
            raise GenerationStoreError("Validated candidate has no typed bundle for satellite projection generation.")
        return inspection.bundle

    def _prepare_satellite_projection_activations(
        self,
        bundle: LoadedBundle,
        *,
        config_revision: str,
        secrets: object,
        previous: SelectedActivation | None,
    ) -> dict[str, str]:
        from .secrets import SecretSnapshot

        if not isinstance(secrets, SecretSnapshot):
            raise GenerationStoreError("Satellite projections require one validated secret snapshot.")
        selected: dict[str, str] = {}
        previous_ids = {} if previous is None else previous.satellite_projection_activation_ids
        for satellite in bundle.satellites.satellites:
            if not satellite.enabled:
                continue
            accepted = self._runtime_compatibility.load(satellite.id)
            if accepted is None:
                raise ProjectionGenerationError(
                    f"Enabled satellite {satellite.id!r} has no accepted runtime compatibility report."
                )
            generated = generate_satellite_projection(
                bundle,
                source_config_revision=config_revision,
                satellite_id=satellite.id,
                runtime_compatibility=accepted.report,
                secrets=secrets,
            )
            previous_id = previous_ids.get(satellite.id)
            if previous_id is not None:
                installed = self._projection_generations.load_installed(satellite.id, previous_id)
                if (
                    installed.activation.source_config_revision == config_revision
                    and installed.projection.projection_revision == generated.projection.projection_revision
                    and installed.secrets.snapshot._matches(generated.secrets)
                ):
                    selected[satellite.id] = previous_id
                    continue
            installed = self._projection_generations.install(generated)
            selected[satellite.id] = installed.activation.generation_id
        return dict(sorted(selected.items()))

    def _bundle_from_config(self, config: ConfigGeneration) -> LoadedBundle:
        configuration = config.configuration
        raw_roles = configuration.get("roles")
        if not isinstance(raw_roles, Mapping):
            raise GenerationStoreError("Installed configuration roles are invalid.")

        def thaw(value: object) -> object:
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, tuple):
                return [thaw(item) for item in value]
            return value

        manifest = validate_role(
            "bundle.yaml",
            {
                "kind": configuration.get("kind"),
                "schema_version": configuration.get("schema_version"),
                "bundle_id": configuration.get("bundle_id"),
            },
        )
        roles: dict[str, ConfigurationModel] = {"bundle.yaml": manifest}
        for role_path, primitive in raw_roles.items():
            thawed = thaw(primitive)
            if not isinstance(role_path, str) or not isinstance(thawed, dict):
                raise GenerationStoreError("Installed configuration role payload is invalid.")
            roles[role_path] = validate_role(role_path, thawed)
        return LoadedBundle(
            candidate_id="candidate_00000000000000000000000000000000",
            root=self.store.root,
            authored_revision="installed-generation",
            authored_bytes=MappingProxyType({}),
            roles=MappingProxyType(roles),
            non_authoritative_paths=(),
        )

    @staticmethod
    def _required_acknowledgements(changes: tuple[SemanticChange, ...]) -> frozenset[str]:
        return frozenset(value for change in changes for value in change.safety_acknowledgements)

    @staticmethod
    def _intrinsic_acknowledgements(inspection: CandidateInspection | None) -> frozenset[str]:
        if inspection is None or inspection.normalized is None:
            return frozenset()
        roles = inspection.normalized.configuration["roles"]
        required: set[str] = set()
        required.update(intrinsic_access_acknowledgements(inspection.normalized.configuration))
        for role_path in (
            "domains/home-assistant.yaml",
            "domains/routines.yaml",
            "domains/network/inventory.yaml",
        ):
            role = roles.get(role_path)
            if role is not None and role["enabled"]:
                required.add("mutating_control_enablement")
        return frozenset(required)

    def _candidate_changes(
        self,
        current: SelectedActivation | None,
        inspection: CandidateInspection,
    ) -> tuple[SemanticChange, ...]:
        if current is None or inspection.normalized is None:
            return ()
        return semantic_diff(self._normalized(current.config), inspection.normalized)

    def _with_transition_validation(
        self,
        current: SelectedActivation | None,
        inspection: CandidateInspection,
    ) -> CandidateInspection:
        if current is None or inspection.normalized is None:
            return inspection
        if current.selection_operation_id is None or current.selection_revision < 1:
            raise GenerationIntegrityError("Selected generation lacks transition-validation identity.")
        result = validate_configuration_transition(
            self._normalized(current.config),
            inspection.normalized,
            context=TransitionValidationContext(
                activation_generation_id=current.activation.generation_id,
                config_generation_id=current.config.generation_id,
                config_revision=current.config.config_revision,
                selection_operation_id=current.selection_operation_id,
                selection_revision=current.selection_revision,
            ),
        )
        report = replace(inspection.report, transition_blockers=result.blockers)
        return replace(inspection, report=report, transition_validation=result)

    def _revalidate_exact_transition(
        self,
        current: SelectedActivation | None,
        inspection: CandidateInspection,
    ) -> CandidateInspection:
        latest = self._assert_exact_selected(current)
        if current is None:
            return inspection
        validated = self._with_transition_validation(latest, inspection)
        if validated.report.transition_blockers:
            raise CandidateActivationBlocked(validated)
        return validated

    def _assert_exact_selected(self, current: SelectedActivation | None) -> SelectedActivation | None:
        latest = self._selected_or_none()
        if current is None:
            if latest is not None:
                raise SelectedGenerationChanged("Selected generation changed before activation.")
            return None
        if latest is None or (
            latest.activation.generation_id != current.activation.generation_id
            or latest.config.generation_id != current.config.generation_id
            or latest.config.config_revision != current.config.config_revision
            or latest.selection_operation_id != current.selection_operation_id
            or latest.selection_revision != current.selection_revision
        ):
            raise SelectedGenerationChanged("Selected generation changed before activation.")
        return latest

    @staticmethod
    def _normalized(config: object) -> NormalizedBundle:
        configuration = getattr(config, "configuration")
        plain = ConfigurationService._plain(configuration)
        return NormalizedBundle(
            format=CONFIG_FORMAT,
            config_revision=getattr(config, "config_revision"),
            configuration=configuration,
            canonical_bytes=canonicalize_json(plain),
        )

    @staticmethod
    def _plain(value: object) -> object:
        from collections.abc import Mapping

        if isinstance(value, Mapping):
            return {key: ConfigurationService._plain(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [ConfigurationService._plain(item) for item in value]
        return value
