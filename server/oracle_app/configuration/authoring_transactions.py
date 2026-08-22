from __future__ import annotations

import os
from pathlib import Path
import re
import secrets

from .generations import (
    GenerationStore,
    GenerationStoreError,
    _atomic_replace,
    _fsync_directory,
    _json_bytes,
    _read_json,
    _write_new,
)
from .loader import AuthoredCandidateSnapshot, snapshot_candidate
from .roles import KNOWN_ROLE_PATHS, SECRET_COMPANION_PATH


AUTHORING_TRANSACTION_FORMAT = "oracle-authoring-transaction-v1"


class AuthoringMutationError(GenerationStoreError):
    pass


class AuthoringMutationUnavailable(AuthoringMutationError):
    pass


class AuthoringModeError(AuthoringMutationError):
    pass


class AuthoringTransactionJournal:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store

    def prepare(
        self,
        *,
        root: Path,
        actor: str,
        previous: AuthoredCandidateSnapshot,
        candidate: AuthoredCandidateSnapshot,
        runtime_change_required: bool,
    ) -> dict[str, object]:
        resolved_root = Path(root).resolve(strict=True)
        if previous.root != resolved_root:
            raise AuthoringMutationUnavailable("Previous snapshot belongs to another authoring root.")
        self._validate_candidate_tree(candidate)
        transaction_id = f"authoring_tx_{secrets.token_hex(16)}"
        directory = self.store.root / "transactions" / transaction_id
        directory.mkdir(mode=0o700)
        entries = self._previous_entries(resolved_root, previous)
        transaction: dict[str, object] = {
            "format": AUTHORING_TRANSACTION_FORMAT,
            "transaction_id": transaction_id,
            "actor": actor,
            "authoring_root": str(resolved_root),
            "previous_authored_revision": previous.authored_revision,
            "candidate_authored_revision": candidate.authored_revision,
            "previous_role_paths": sorted(previous.authored_bytes),
            "candidate_role_paths": sorted(candidate.authored_bytes),
            "previous_entries": entries,
            "runtime_change_required": runtime_change_required,
            "new_config_generation_id": None,
            "new_activation_generation_id": None,
            "authoring_committed": False,
        }
        try:
            self._write_snapshot(directory / "previous", previous)
            self._write_snapshot(directory / "candidate", candidate)
            self.write(transaction)
        except BaseException as exc:
            self.cleanup(transaction)
            raise AuthoringMutationUnavailable("Authored candidate cannot be staged durably.") from exc
        return transaction

    def write(self, transaction: dict[str, object]) -> None:
        directory = self._directory(transaction)
        journal = directory / "journal.json"
        if journal.exists():
            _atomic_replace(journal, _json_bytes(transaction))
        else:
            _write_new(journal, _json_bytes(transaction), mode=0o600)
            _fsync_directory(directory)

    def commit_candidate(self, transaction: dict[str, object]) -> None:
        self._apply_tree(transaction, tree="candidate")

    def ensure_candidate(self, transaction: dict[str, object]) -> None:
        self._apply_tree(transaction, tree="candidate")

    def restore_previous(self, transaction: dict[str, object]) -> None:
        self._apply_tree(transaction, tree="previous")

    def pending(self, *, root: Path) -> tuple[dict[str, object], ...]:
        resolved_root = Path(root).resolve(strict=True)
        pending: list[dict[str, object]] = []
        transactions = self.store.root / "transactions"
        for directory in sorted(transactions.glob("authoring_tx_*")):
            if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(self.store.root):
                raise GenerationStoreError("Authoring transaction directory escapes the installed store.")
            journal_path = directory / "journal.json"
            if not journal_path.exists():
                self._remove_tree(directory)
                continue
            journal = _read_json(journal_path)
            if not isinstance(journal, dict):
                raise GenerationStoreError("Authoring transaction journal is invalid.")
            self.validate(journal, expected_root=resolved_root)
            pending.append(journal)
        return tuple(pending)

    def cleanup(self, transaction: dict[str, object]) -> None:
        directory = self._directory(transaction)
        if directory.exists():
            self._remove_tree(directory)

    def validate(self, transaction: dict[str, object], *, expected_root: Path) -> None:
        required = {
            "format",
            "transaction_id",
            "actor",
            "authoring_root",
            "previous_authored_revision",
            "candidate_authored_revision",
            "previous_role_paths",
            "candidate_role_paths",
            "previous_entries",
            "runtime_change_required",
            "new_config_generation_id",
            "new_activation_generation_id",
            "authoring_committed",
        }
        if set(transaction) != required or transaction["format"] != AUTHORING_TRANSACTION_FORMAT:
            raise GenerationStoreError("Authoring transaction journal shape is invalid.")
        transaction_id = transaction["transaction_id"]
        if not isinstance(transaction_id, str) or re.fullmatch(r"authoring_tx_[0-9a-f]{32}", transaction_id) is None:
            raise GenerationStoreError("Authoring transaction identifier is invalid.")
        if transaction["actor"] not in {"service", "host_local_cli", "system_mode"}:
            raise GenerationStoreError("Authoring transaction actor is invalid.")
        if not isinstance(transaction["authoring_committed"], bool) or not isinstance(
            transaction["runtime_change_required"], bool
        ):
            raise GenerationStoreError("Authoring transaction state flag is invalid.")
        root = Path(str(transaction["authoring_root"])).resolve(strict=True)
        if root != expected_root:
            raise GenerationStoreError("Authoring transaction belongs to a different authoring root.")
        previous_paths = self._validate_role_paths(transaction["previous_role_paths"])
        candidate_paths = self._validate_role_paths(transaction["candidate_role_paths"])
        entries = transaction["previous_entries"]
        if not isinstance(entries, dict) or set(entries) != previous_paths:
            raise GenerationStoreError("Authoring transaction previous-entry map is invalid.")
        for role_path, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("kind") not in {"file", "symlink"}:
                raise GenerationStoreError("Authoring transaction entry type is invalid.")
            if entry["kind"] == "file" and set(entry) != {"kind"}:
                raise GenerationStoreError("Authoring regular-file entry is invalid.")
            if entry["kind"] == "symlink" and (
                set(entry) != {"kind", "link_target", "target_path"}
                or not isinstance(entry["link_target"], str)
                or not isinstance(entry["target_path"], str)
                or not self._safe_relative_path(entry["target_path"])
            ):
                raise GenerationStoreError("Authoring symlink entry is invalid.")
        for field, pattern in (
            ("new_config_generation_id", r"config_[0-9a-f]{32}"),
            ("new_activation_generation_id", r"activation_[0-9a-f]{32}"),
        ):
            value = transaction[field]
            if value is not None and (not isinstance(value, str) or re.fullmatch(pattern, value) is None):
                raise GenerationStoreError("Authoring transaction generation identity is invalid.")
        if transaction["new_activation_generation_id"] is not None and transaction["new_config_generation_id"] is None:
            raise GenerationStoreError("Authoring activation lacks its config generation.")
        directory = self._directory(transaction)
        for tree, paths in (("previous", previous_paths), ("candidate", candidate_paths)):
            tree_root = directory / tree
            for role_path in paths:
                artifact = tree_root / role_path
                if artifact.is_symlink() or not artifact.is_file() or not artifact.resolve(strict=True).is_relative_to(directory):
                    raise GenerationStoreError("Authoring transaction staged role is invalid.")

    @staticmethod
    def _validate_candidate_tree(candidate: AuthoredCandidateSnapshot) -> None:
        if candidate.snapshot_findings:
            raise AuthoringMutationUnavailable("Authored staging tree has invalid role structure.")
        if candidate.non_authoritative_paths:
            raise AuthoringMutationUnavailable("Authored staging tree must contain only fixed YAML roles.")
        if (candidate.root / SECRET_COMPANION_PATH).exists() or (candidate.root / SECRET_COMPANION_PATH).is_symlink():
            raise AuthoringMutationUnavailable("Non-secret authoring staging cannot contain secrets.env.")

    @staticmethod
    def _previous_entries(root: Path, snapshot: AuthoredCandidateSnapshot) -> dict[str, dict[str, str]]:
        entries: dict[str, dict[str, str]] = {}
        targets: set[Path] = set()
        for role_path in snapshot.authored_bytes:
            logical = root / role_path
            target = logical.resolve(strict=True)
            if target in targets:
                raise AuthoringMutationUnavailable("Multiple authored roles cannot share one final file target.")
            targets.add(target)
            if logical.is_symlink():
                entries[role_path] = {
                    "kind": "symlink",
                    "link_target": os.readlink(logical),
                    "target_path": target.relative_to(root).as_posix(),
                }
            else:
                entries[role_path] = {"kind": "file"}
        return entries

    def _apply_tree(self, transaction: dict[str, object], *, tree: str) -> None:
        root = Path(str(transaction["authoring_root"])).resolve(strict=True)
        directory = self._directory(transaction)
        desired_paths = set(self._validate_role_paths(transaction[f"{tree}_role_paths"]))
        previous_entries = transaction["previous_entries"]
        if not isinstance(previous_entries, dict):
            raise GenerationStoreError("Authoring transaction previous-entry map is invalid.")
        for role_path in sorted(KNOWN_ROLE_PATHS):
            logical = root / role_path
            if role_path in desired_paths:
                data = (directory / tree / role_path).read_bytes()
                if tree == "previous":
                    entry = previous_entries[role_path]
                    self._restore_entry(root, logical, entry, data)
                else:
                    self._replace_logical(root, logical, data)
            else:
                entry = previous_entries.get(role_path)
                if tree == "candidate" and isinstance(entry, dict) and entry.get("kind") == "symlink":
                    self._remove_previous_symlink(root, logical, entry)
                else:
                    self._remove_logical(root, logical)
        _fsync_directory(root)

    @staticmethod
    def _replace_logical(root: Path, logical: Path, data: bytes) -> None:
        logical.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = logical.parent.resolve(strict=True)
        if not parent.is_relative_to(root):
            raise AuthoringMutationUnavailable("Authored role parent escapes the authoring root.")
        if logical.is_symlink():
            target = logical.resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise AuthoringMutationUnavailable("Authored role symlink target is invalid.")
            _atomic_replace(target, data)
        else:
            if logical.exists() and not logical.is_file():
                raise AuthoringMutationUnavailable("Authored role target is not a regular file.")
            _atomic_replace(logical, data)

    @staticmethod
    def _restore_entry(root: Path, logical: Path, entry: object, data: bytes) -> None:
        if not isinstance(entry, dict):
            raise GenerationStoreError("Authoring transaction previous entry is invalid.")
        if entry["kind"] == "file":
            if logical.is_symlink():
                logical.unlink()
            AuthoringTransactionJournal._replace_logical(root, logical, data)
            return
        link_target = str(entry["link_target"])
        if not logical.is_symlink() or os.readlink(logical) != link_target:
            AuthoringTransactionJournal._remove_logical(root, logical)
            logical.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            logical.symlink_to(link_target)
        try:
            target = logical.resolve(strict=True)
        except FileNotFoundError:
            target = (logical.parent / link_target).resolve(strict=False)
            if not target.is_relative_to(root):
                raise AuthoringMutationUnavailable("Restored role symlink target escapes the authoring root.")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _atomic_replace(target, data)
        else:
            if not target.is_relative_to(root):
                raise AuthoringMutationUnavailable("Restored role symlink target escapes the authoring root.")
            _atomic_replace(target, data)

    @staticmethod
    def _remove_logical(root: Path, logical: Path) -> None:
        if logical.is_symlink() or logical.exists():
            if not logical.is_symlink() and not logical.is_file():
                raise AuthoringMutationUnavailable("Authored role target is not removable as a regular file.")
            logical.unlink()
            _fsync_directory(logical.parent.resolve(strict=True))
        if not logical.parent.resolve(strict=True).is_relative_to(root):
            raise AuthoringMutationUnavailable("Authored role parent escapes the authoring root.")

    @staticmethod
    def _remove_previous_symlink(root: Path, logical: Path, entry: dict[str, object]) -> None:
        target = root / str(entry["target_path"])
        AuthoringTransactionJournal._remove_logical(root, logical)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or not target.resolve(strict=True).is_relative_to(root):
                raise AuthoringMutationUnavailable("Authored role symlink target is not safely removable.")
            target.unlink()
            _fsync_directory(target.parent.resolve(strict=True))

    @staticmethod
    def _safe_relative_path(value: str) -> bool:
        path = Path(value)
        return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and "." not in path.parts

    @staticmethod
    def _validate_role_paths(value: object) -> set[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise GenerationStoreError("Authoring transaction role list is invalid.")
        paths = set(value)
        if len(paths) != len(value) or not paths.issubset(KNOWN_ROLE_PATHS):
            raise GenerationStoreError("Authoring transaction role list is invalid.")
        return paths

    @staticmethod
    def _write_snapshot(root: Path, snapshot: AuthoredCandidateSnapshot) -> None:
        root.mkdir(mode=0o700)
        for role_path, data in snapshot.authored_bytes.items():
            path = root / role_path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_new(path, data, mode=0o600)
        for directory in sorted({(root / role).parent for role in snapshot.authored_bytes}, reverse=True):
            _fsync_directory(directory)
        _fsync_directory(root)

    def _directory(self, transaction: dict[str, object]) -> Path:
        return self.store.root / "transactions" / str(transaction["transaction_id"])

    @staticmethod
    def _remove_tree(root: Path) -> None:
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        root.rmdir()
        _fsync_directory(root.parent)
