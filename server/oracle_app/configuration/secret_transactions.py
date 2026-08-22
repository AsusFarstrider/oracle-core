from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
from typing import Literal

from .generations import (
    GenerationStore,
    GenerationStoreError,
    SelectedActivation,
    _atomic_replace,
    _fsync_directory,
    _json_bytes,
    _read_json,
    _write_new,
)
from .secrets import SecretCompanionError, SecretSnapshot, load_secret_companion


SECRET_TRANSACTION_FORMAT = "oracle-secret-transaction-v1"
SecretMutationOperation = Literal["create_secret", "replace_secret", "rotate_secret", "remove_secret"]
_LOGICAL_SECRET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretMutationError(GenerationStoreError):
    pass


class SecretMutationUnavailable(SecretMutationError):
    pass


class SecretAlreadyExists(SecretMutationError):
    pass


class SecretNotFound(SecretMutationError):
    pass


class SecretRemovalBlocked(SecretMutationError):
    pass


class SecretCompanionDrift(SecretMutationError):
    pass


@dataclass(frozen=True)
class SecretMutationResult:
    operation: SecretMutationOperation
    selected: SelectedActivation
    previous_secret_generation_id: str
    secret_generation_id: str
    revoked_secret_generation_id: str
    pruned_secret_generation_ids: tuple[str, ...]
    audit_event_id: str
    logical_id: str
    retirement_pending: bool = False


class SecretTransactionJournal:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store

    def prepare(
        self,
        *,
        root: Path,
        operation: SecretMutationOperation,
        actor: str,
        logical_id: str,
        previous: SelectedActivation,
        candidate: SecretSnapshot,
    ) -> dict[str, object]:
        resolved_root, target, previous_exists = self._companion_target(root)
        transaction_id = f"secret_tx_{secrets.token_hex(16)}"
        transaction_directory = self.store.secret_transactions_root / transaction_id
        transaction_directory.mkdir(mode=0o700)
        staged_path = target.parent / f".secrets-{secrets.token_hex(16)}.tmp"
        transaction: dict[str, object] = {
            "format": SECRET_TRANSACTION_FORMAT,
            "transaction_id": transaction_id,
            "operation": operation,
            "actor": actor,
            "logical_id": logical_id,
            "bundle_root": str(resolved_root),
            "target_path": str(target),
            "staged_path": str(staged_path),
            "previous_exists": previous_exists,
            "previous_activation_generation_id": previous.activation.generation_id,
            "old_secret_generation_id": previous.secrets.generation_id,
            "new_secret_generation_id": None,
            "new_activation_generation_id": None,
            "companion_committed": False,
        }
        try:
            self.write(transaction)
            if previous_exists:
                _write_new(transaction_directory / "previous.env", target.read_bytes(), mode=0o600)
            _write_new(staged_path, candidate._companion_bytes(), mode=0o600)
            _fsync_directory(staged_path.parent)
        except BaseException as exc:
            self.cleanup(transaction)
            raise SecretMutationUnavailable("Secret companion cannot be staged for atomic replacement.") from exc
        return transaction

    def write(self, transaction: dict[str, object]) -> None:
        transaction_directory = self.store.secret_transactions_root / str(transaction["transaction_id"])
        journal = transaction_directory / "journal.json"
        if journal.exists():
            _atomic_replace(journal, _json_bytes(transaction))
        else:
            _write_new(journal, _json_bytes(transaction), mode=0o600)
            _fsync_directory(transaction_directory)

    @staticmethod
    def commit_companion(transaction: dict[str, object]) -> None:
        staged = Path(str(transaction["staged_path"]))
        target = Path(str(transaction["target_path"]))
        try:
            os.replace(staged, target)
            _fsync_directory(target.parent)
        except OSError as exc:
            raise SecretMutationUnavailable("Secret companion cannot be atomically replaced.") from exc

    def pending(self, *, root: Path) -> tuple[dict[str, object], ...]:
        pending: list[dict[str, object]] = []
        transactions = self.store.secret_transactions_root
        for directory in sorted(transactions.glob("secret_tx_*")):
            if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(self.store.secret_root):
                raise GenerationStoreError("Secret transaction directory escapes the installed store.")
            if not (directory / "journal.json").exists():
                for child in directory.iterdir():
                    child.unlink()
                directory.rmdir()
                _fsync_directory(directory.parent)
                continue
            journal = _read_json(directory / "journal.json")
            if not isinstance(journal, dict):
                raise GenerationStoreError("Secret transaction journal is invalid.")
            self.validate(journal, expected_root=root)
            pending.append(journal)
        return tuple(pending)

    def ensure_committed_companion(
        self,
        transaction: dict[str, object],
        snapshot: SecretSnapshot,
    ) -> None:
        target = Path(str(transaction["target_path"]))
        bundle_root = Path(str(transaction["bundle_root"]))
        installed_companion = load_secret_companion(bundle_root)
        if not installed_companion._matches(snapshot):
            _atomic_replace(target, snapshot._companion_bytes())

    def restore_companion(self, transaction: dict[str, object]) -> None:
        target = Path(str(transaction["target_path"]))
        journal_directory = self.store.secret_transactions_root / str(transaction["transaction_id"])
        if bool(transaction["previous_exists"]):
            backup = journal_directory / "previous.env"
            if backup.exists():
                _atomic_replace(target, backup.read_bytes())
            elif transaction["new_secret_generation_id"] is not None or transaction["companion_committed"]:
                raise GenerationStoreError("Secret transaction recovery backup is missing.")
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            _fsync_directory(target.parent)

    def cleanup(self, transaction: dict[str, object]) -> None:
        staged = Path(str(transaction["staged_path"]))
        try:
            staged.unlink()
        except FileNotFoundError:
            pass
        directory = self.store.secret_transactions_root / str(transaction["transaction_id"])
        if directory.exists():
            for child in directory.iterdir():
                child.unlink()
            directory.rmdir()
            _fsync_directory(directory.parent)

    @staticmethod
    def validate(transaction: dict[str, object], *, expected_root: Path) -> None:
        required = {
            "format",
            "transaction_id",
            "operation",
            "actor",
            "logical_id",
            "bundle_root",
            "target_path",
            "staged_path",
            "previous_exists",
            "previous_activation_generation_id",
            "old_secret_generation_id",
            "new_secret_generation_id",
            "new_activation_generation_id",
            "companion_committed",
        }
        if set(transaction) != required or transaction["format"] != SECRET_TRANSACTION_FORMAT:
            raise GenerationStoreError("Secret transaction journal shape is invalid.")
        transaction_id = transaction["transaction_id"]
        if not isinstance(transaction_id, str) or re.fullmatch(r"secret_tx_[0-9a-f]{32}", transaction_id) is None:
            raise GenerationStoreError("Secret transaction identifier is invalid.")
        if transaction["operation"] not in {"create_secret", "replace_secret", "rotate_secret", "remove_secret"}:
            raise GenerationStoreError("Secret transaction operation is invalid.")
        if transaction["actor"] not in {"service", "host_local_cli", "system_mode"}:
            raise GenerationStoreError("Secret transaction actor is invalid.")
        logical_id = transaction["logical_id"]
        if not isinstance(logical_id, str) or _LOGICAL_SECRET_ID.fullmatch(logical_id) is None:
            raise GenerationStoreError("Secret transaction logical ID is invalid.")
        if not isinstance(transaction["previous_exists"], bool) or not isinstance(transaction["companion_committed"], bool):
            raise GenerationStoreError("Secret transaction state flags are invalid.")
        required_ids = {
            "previous_activation_generation_id": r"activation_[0-9a-f]{32}",
            "old_secret_generation_id": r"secret_[0-9a-f]{32}",
        }
        for field, pattern in required_ids.items():
            value = transaction[field]
            if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
                raise GenerationStoreError("Secret transaction generation identity is invalid.")
        optional_ids = {
            "new_secret_generation_id": r"secret_[0-9a-f]{32}",
            "new_activation_generation_id": r"activation_[0-9a-f]{32}",
        }
        for field, pattern in optional_ids.items():
            value = transaction[field]
            if value is not None and (not isinstance(value, str) or re.fullmatch(pattern, value) is None):
                raise GenerationStoreError("Secret transaction staged generation identity is invalid.")
        if transaction["new_activation_generation_id"] is not None and transaction["new_secret_generation_id"] is None:
            raise GenerationStoreError("Secret transaction activation lacks its secret generation.")
        if transaction["companion_committed"] and transaction["new_activation_generation_id"] is None:
            raise GenerationStoreError("Committed secret companion lacks an activation generation.")
        root = Path(str(transaction["bundle_root"])).resolve(strict=True)
        if root != expected_root:
            raise GenerationStoreError("Secret transaction belongs to a different authoring root.")
        target = Path(str(transaction["target_path"]))
        staged = Path(str(transaction["staged_path"]))
        if (
            not target.is_relative_to(root)
            or staged.parent.resolve(strict=True) != target.parent.resolve(strict=True)
            or not staged.parent.resolve(strict=True).is_relative_to(root)
        ):
            raise GenerationStoreError("Secret transaction paths escape the authored bundle root.")

    @staticmethod
    def _companion_target(root: Path) -> tuple[Path, Path, bool]:
        resolved_root = Path(root).resolve(strict=True)
        logical_path = resolved_root / "secrets.env"
        previous_exists = logical_path.exists() or logical_path.is_symlink()
        if not previous_exists:
            return resolved_root, logical_path, False
        try:
            target = logical_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SecretCompanionError("config.secret.target", "Secret companion target cannot be resolved.") from exc
        if not target.is_relative_to(resolved_root):
            raise SecretCompanionError("config.secret.path_escape", "Secret companion escapes the resolved bundle root.")
        if not target.is_file():
            raise SecretCompanionError("config.secret.target", "Secret companion target must be a regular file.")
        return resolved_root, target, True
