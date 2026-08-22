from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re
import secrets
from typing import Mapping

from .generations import GenerationIntegrityError, GenerationStore, _fsync_directory, _json_bytes, _read_json, _write_new


SELECTION_TRANSACTION_FORMAT = "oracle-selection-transaction-v1"
SELECTION_OPERATION_ID_PATTERN = re.compile(r"^selection_op_[0-9a-f]{32}$")
_AUDIT_EVENT_ID_PATTERN = re.compile(r"^audit_[0-9a-f]{32}$")
_ACTIVATION_ID_PATTERN = re.compile(r"^activation_[0-9a-f]{32}$")
_CONFIG_ID_PATTERN = re.compile(r"^config_[0-9a-f]{32}$")
_SECRET_ID_PATTERN = re.compile(r"^secret_[0-9a-f]{32}$")
_CANDIDATE_ID_PATTERN = re.compile(r"^candidate_[0-9a-f]{32}$")
_LOGICAL_SECRET_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SATELLITE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
_SATELLITE_ACTIVATION_ID_PATTERN = re.compile(r"^sat_activation_[0-9a-f]{32}$")
_OPERATIONS = frozenset({"activate", "rollback", "replace_authored_candidate", "secret_mutation"})
_AUDIT_OPERATIONS = frozenset(
    {"activate", "rollback", "replace_authored_candidate", "create_secret", "replace_secret", "rotate_secret", "remove_secret"}
)
_ACTORS = frozenset({"service", "host_local_cli", "system_mode"})
_ACKNOWLEDGEMENTS = frozenset(
    {"access_expansion", "credential_role_change", "identity_removal", "mutating_control_enablement", "public_health_enablement"}
)


class SelectionRecoveryAmbiguous(GenerationIntegrityError):
    pass


class SelectionCommittedAuditPending(RuntimeError):
    def __init__(self, operation_id: str, selection_revision: int) -> None:
        super().__init__("Configuration selection committed, but its audit record is still pending recovery.")
        self.operation_id = operation_id
        self.selection_revision = selection_revision


@dataclass(frozen=True)
class SelectionTransactionEnvelope:
    format: str
    operation_id: str
    audit_event_id: str
    recorded_at: str
    operation: str
    audit_operation: str
    actor: str
    previous_activation_generation_id: str | None
    previous_config_generation_id: str | None
    previous_secret_generation_id: str | None
    previous_selection_operation_id: str | None
    previous_selection_revision: int
    previous_satellite_projection_activation_ids: Mapping[str, str]
    target_activation_generation_id: str
    target_config_generation_id: str
    target_secret_generation_id: str
    selection_revision: int
    target_satellite_projection_activation_ids: Mapping[str, str]
    report_candidate_id: str | None
    acknowledgements: tuple[str, ...]
    secret_logical_id: str | None
    revoked_secret_generation_id: str | None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["acknowledgements"] = list(self.acknowledgements)
        payload["previous_satellite_projection_activation_ids"] = dict(
            self.previous_satellite_projection_activation_ids
        )
        payload["target_satellite_projection_activation_ids"] = dict(
            self.target_satellite_projection_activation_ids
        )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> SelectionTransactionEnvelope:
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != fields:
            raise SelectionRecoveryAmbiguous("Selection transaction shape is invalid.")
        acknowledgements = value["acknowledgements"]
        if not isinstance(acknowledgements, list) or not all(isinstance(item, str) for item in acknowledgements):
            raise SelectionRecoveryAmbiguous("Selection transaction acknowledgements are invalid.")
        try:
            envelope = cls(
                **{key: item for key, item in value.items() if key != "acknowledgements"},
                acknowledgements=tuple(acknowledgements),
            )
        except TypeError as exc:
            raise SelectionRecoveryAmbiguous("Selection transaction field types are invalid.") from exc
        envelope.validate()
        return envelope

    def validate(self) -> None:
        if self.format != SELECTION_TRANSACTION_FORMAT:
            raise SelectionRecoveryAmbiguous("Selection transaction format is invalid.")
        if SELECTION_OPERATION_ID_PATTERN.fullmatch(self.operation_id) is None:
            raise SelectionRecoveryAmbiguous("Selection operation identity is invalid.")
        if _AUDIT_EVENT_ID_PATTERN.fullmatch(self.audit_event_id) is None:
            raise SelectionRecoveryAmbiguous("Selection audit event identity is invalid.")
        try:
            datetime.fromisoformat(self.recorded_at)
        except (TypeError, ValueError) as exc:
            raise SelectionRecoveryAmbiguous("Selection audit timestamp is invalid.") from exc
        if self.operation not in _OPERATIONS or self.audit_operation not in _AUDIT_OPERATIONS:
            raise SelectionRecoveryAmbiguous("Selection operation type is invalid.")
        expected_audit_operations = {
            "activate": {"activate"},
            "rollback": {"rollback"},
            "replace_authored_candidate": {"replace_authored_candidate"},
            "secret_mutation": {"create_secret", "replace_secret", "rotate_secret", "remove_secret"},
        }
        if self.audit_operation not in expected_audit_operations[self.operation] or self.actor not in _ACTORS:
            raise SelectionRecoveryAmbiguous("Selection audit operation or actor is invalid.")
        if self.previous_selection_revision < 0 or self.selection_revision != self.previous_selection_revision + 1:
            raise SelectionRecoveryAmbiguous("Selection transaction revision is not consecutive.")
        self._optional_id(self.previous_activation_generation_id, _ACTIVATION_ID_PATTERN, "previous activation")
        self._optional_id(self.previous_config_generation_id, _CONFIG_ID_PATTERN, "previous config")
        self._optional_id(self.previous_secret_generation_id, _SECRET_ID_PATTERN, "previous secret")
        self._required_id(self.target_activation_generation_id, _ACTIVATION_ID_PATTERN, "target activation")
        self._required_id(self.target_config_generation_id, _CONFIG_ID_PATTERN, "target config")
        self._required_id(self.target_secret_generation_id, _SECRET_ID_PATTERN, "target secret")
        previous_ids = (
            self.previous_activation_generation_id,
            self.previous_config_generation_id,
            self.previous_secret_generation_id,
        )
        if (self.previous_selection_revision == 0) != all(item is None for item in previous_ids):
            raise SelectionRecoveryAmbiguous("Selection previous-generation identity is incomplete.")
        if self.previous_selection_revision > 0 and any(item is None for item in previous_ids):
            raise SelectionRecoveryAmbiguous("Selection previous-generation identity is incomplete.")
        if self.previous_selection_revision == 0:
            if self.previous_selection_operation_id is not None:
                raise SelectionRecoveryAmbiguous("Initial selection cannot name a previous operation.")
        elif (
            not isinstance(self.previous_selection_operation_id, str)
            or SELECTION_OPERATION_ID_PATTERN.fullmatch(self.previous_selection_operation_id) is None
        ):
            raise SelectionRecoveryAmbiguous("Previous selection operation identity is invalid.")
        if tuple(sorted(set(self.acknowledgements))) != self.acknowledgements or not set(self.acknowledgements).issubset(_ACKNOWLEDGEMENTS):
            raise SelectionRecoveryAmbiguous("Selection acknowledgements are invalid.")
        self._projection_map(self.previous_satellite_projection_activation_ids, "previous")
        self._projection_map(self.target_satellite_projection_activation_ids, "target")
        if self.previous_selection_revision == 0 and self.previous_satellite_projection_activation_ids:
            raise SelectionRecoveryAmbiguous("Initial selection cannot name previous satellite activations.")
        if self.operation in {"activate", "replace_authored_candidate"}:
            self._required_id(self.report_candidate_id, _CANDIDATE_ID_PATTERN, "candidate report")
        elif self.report_candidate_id is not None:
            raise SelectionRecoveryAmbiguous("Selection operation cannot reference a candidate report.")
        if self.operation == "secret_mutation":
            self._required_id(self.secret_logical_id, _LOGICAL_SECRET_ID_PATTERN, "secret logical")
            self._required_id(self.revoked_secret_generation_id, _SECRET_ID_PATTERN, "revoked secret")
        elif self.secret_logical_id is not None or self.revoked_secret_generation_id is not None:
            raise SelectionRecoveryAmbiguous("Non-secret selection contains secret-mutation metadata.")

    @staticmethod
    def _required_id(value: object, pattern: re.Pattern[str], label: str) -> None:
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise SelectionRecoveryAmbiguous(f"Selection {label} identity is invalid.")

    @classmethod
    def _optional_id(cls, value: object, pattern: re.Pattern[str], label: str) -> None:
        if value is not None:
            cls._required_id(value, pattern, label)

    @staticmethod
    def _projection_map(value: object, label: str) -> None:
        if not isinstance(value, dict) or not all(
            isinstance(satellite_id, str)
            and _SATELLITE_ID_PATTERN.fullmatch(satellite_id) is not None
            and isinstance(activation_id, str)
            and _SATELLITE_ACTIVATION_ID_PATTERN.fullmatch(activation_id) is not None
            for satellite_id, activation_id in value.items()
        ):
            raise SelectionRecoveryAmbiguous(f"Selection {label} satellite activation map is invalid.")


class SelectionTransactionJournal:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store

    @staticmethod
    def new_operation_id() -> str:
        return f"selection_op_{secrets.token_hex(16)}"

    def prepare(self, envelope: SelectionTransactionEnvelope) -> SelectionTransactionEnvelope:
        envelope.validate()
        directory = self._directory(envelope.operation_id)
        directory.mkdir(mode=0o700)
        try:
            _write_new(directory / "journal.json", _json_bytes(envelope.to_dict()), mode=0o600)
            _fsync_directory(directory)
            _fsync_directory(directory.parent)
        except BaseException:
            self.cleanup(envelope)
            raise
        return envelope

    def pending(self) -> tuple[SelectionTransactionEnvelope, ...]:
        transactions = self.store.root / "transactions"
        pending: list[SelectionTransactionEnvelope] = []
        for directory in sorted(transactions.glob("selection_op_*")):
            if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(self.store.root):
                raise SelectionRecoveryAmbiguous("Selection transaction directory escapes the installed store.")
            journal_path = directory / "journal.json"
            if not journal_path.is_file() or journal_path.is_symlink():
                raise SelectionRecoveryAmbiguous("Selection transaction journal is missing or invalid.")
            envelope = SelectionTransactionEnvelope.from_dict(_read_json(journal_path))
            if directory.name != envelope.operation_id:
                raise SelectionRecoveryAmbiguous("Selection transaction identity is inconsistent.")
            pending.append(envelope)
        if len(pending) > 1:
            raise SelectionRecoveryAmbiguous("Multiple pending selection transactions are ambiguous.")
        return tuple(pending)

    def cleanup(self, envelope: SelectionTransactionEnvelope) -> None:
        directory = self._directory(envelope.operation_id)
        if not directory.exists():
            return
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        _fsync_directory(directory.parent)

    def _directory(self, operation_id: str) -> Path:
        return self.store.root / "transactions" / operation_id
