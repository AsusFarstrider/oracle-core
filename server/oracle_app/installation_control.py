"""Recoverable complete-activation coordination for standard installations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import secrets
from typing import Callable, Literal, Mapping

from .configuration.generations import _atomic_replace, _fsync_directory, _json_bytes, _read_json
from .configuration.secret_transactions import SecretMutationOperation, SecretMutationResult
from .configuration.service import ConfigurationService
from .installation import (
    ActivationRequest,
    InstalledActivation,
    InstallationLayout,
    InstallationLayoutError,
    load_selected_activation,
    publish_activation,
    select_activation,
)


CONTROL_TRANSACTION_FORMAT = "oracle-complete-activation-transaction-v1"
CONTROL_TRANSACTION_PATH = "activation-transaction.json"
ONLINE_AUTHORIZATION_AUDIT_PATH = "online-authorization-audit.jsonl"


class CompleteActivationCoordinationError(RuntimeError):
    pass


class CompleteActivationLock(AbstractContextManager["CompleteActivationLock"]):
    def __init__(self, layout: InstallationLayout) -> None:
        self.path = layout.control_state / "maintenance.lock"
        self._stream = None

    def __enter__(self) -> CompleteActivationLock:
        self.path.parent.mkdir(mode=0o2750, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o640)
        self._stream = os.fdopen(descriptor, "r+b")
        import fcntl

        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._stream.close()
            self._stream = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise CompleteActivationCoordinationError("Oracle maintenance is already in progress.") from exc
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        if self._stream is None:
            return
        import fcntl

        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None


@dataclass(frozen=True)
class StagedSecretActivation:
    transaction_id: str
    previous_activation: InstalledActivation
    candidate_activation: InstalledActivation
    mutation: SecretMutationResult


def _request_from_active(active: InstalledActivation, configuration_activation_id: str) -> ActivationRequest:
    record = active.record
    core = record.get("core")
    if not isinstance(core, Mapping):
        raise InstallationLayoutError("Active installation record lacks core identity.")
    return ActivationRequest(
        core_commit=core.get("commit"),  # type: ignore[arg-type]
        core_git_tree=core.get("git_tree"),  # type: ignore[arg-type]
        application_revision_identity=record.get("application_revision_identity"),  # type: ignore[arg-type]
        python_environment_identity=record.get("python_environment_identity"),  # type: ignore[arg-type]
        household_deployment_revision=record.get("household_deployment_revision"),  # type: ignore[arg-type]
        configuration_activation_identity=configuration_activation_id,
        service_definition_identity=record.get("service_definition_identity"),  # type: ignore[arg-type]
        persistent_state_checkpoint=record.get("persistent_state_checkpoint"),  # type: ignore[arg-type]
    )


class StandardActivationCoordinator:
    def __init__(
        self,
        layout: InstallationLayout,
        service: ConfigurationService,
        *,
        secret_companion_root: Path,
    ) -> None:
        self.layout = layout
        self.service = service
        self.secret_companion_root = Path(secret_companion_root)
        self.journal_path = layout.control_state / CONTROL_TRANSACTION_PATH

    def stage_secret_mutation(
        self,
        *,
        operation: SecretMutationOperation,
        logical_id: str,
        value: str | None,
        expected_secret_generation_id: str,
        actor: Literal["service", "host_local_cli", "system_mode"] = "host_local_cli",
    ) -> StagedSecretActivation:
        with CompleteActivationLock(self.layout):
            if self.journal_path.exists() or self.journal_path.is_symlink():
                raise CompleteActivationCoordinationError("An activation transaction already requires recovery.")
            active = load_selected_activation(self.layout)
            previous = self.service.store.load_selected()
            if active.record["configuration_activation_identity"] != previous.activation.generation_id:
                raise CompleteActivationCoordinationError(
                    "Complete and configuration activation selections disagree before mutation."
                )
            transaction_id = f"complete_activation_tx_{secrets.token_hex(16)}"
            journal: dict[str, object] = {
                "format": CONTROL_TRANSACTION_FORMAT,
                "transaction_id": transaction_id,
                "state": "prepared",
                "previous_complete_activation_id": active.activation_id,
                "candidate_complete_activation_id": None,
                "previous_configuration_activation_id": previous.activation.generation_id,
                "candidate_configuration_activation_id": None,
                "previous_secret_generation_id": previous.secrets.generation_id,
                "candidate_secret_generation_id": None,
                "previous_satellite_projection_activation_ids": dict(
                    previous.satellite_projection_activation_ids
                ),
            }
            select_activation(self.layout, "previous-known-good", active)
            self._write_journal(journal, create=True)
            try:
                mutation = self.service.mutate_secret(
                    self.secret_companion_root,
                    operation=operation,
                    logical_id=logical_id,
                    value=value,
                    expected_secret_generation_id=expected_secret_generation_id,
                    actor=actor,
                    retirement="pending",
                )
                journal.update(
                    state="configuration_selected",
                    candidate_configuration_activation_id=mutation.selected.activation.generation_id,
                    candidate_secret_generation_id=mutation.secret_generation_id,
                )
                self._write_journal(journal)
                candidate = publish_activation(
                    self.layout,
                    _request_from_active(active, mutation.selected.activation.generation_id),
                )
                journal.update(candidate_complete_activation_id=candidate.activation_id)
                self._write_journal(journal)
                select_activation(self.layout, "previous-known-good", active)
                select_activation(self.layout, "staged", candidate)
                select_activation(self.layout, "active", candidate)
                journal.update(state="awaiting_verification")
                self._write_journal(journal)
            except BaseException:
                self._recover_locked(journal)
                raise
            return StagedSecretActivation(transaction_id, active, candidate, mutation)

    def finalize_verified(self) -> InstalledActivation:
        with CompleteActivationLock(self.layout):
            journal = self._load_journal()
            if journal["state"] != "awaiting_verification":
                raise CompleteActivationCoordinationError("Activation is not awaiting verification.")
            journal["state"] = "verification_passed"
            self._write_journal(journal)
            return self._finalize_locked(journal)

    def pending_candidate_activation_id(self) -> str | None:
        if not self.journal_path.exists() and not self.journal_path.is_symlink():
            return None
        journal = self._load_journal()
        candidate = journal.get("candidate_complete_activation_id")
        return candidate if isinstance(candidate, str) else None

    def _finalize_locked(self, journal: dict[str, object]) -> InstalledActivation:
        if journal["state"] != "verification_passed":
            raise CompleteActivationCoordinationError("Activation verification has not passed.")
        active = load_selected_activation(self.layout)
        if active.activation_id != journal["candidate_complete_activation_id"]:
            raise CompleteActivationCoordinationError("Active activation changed before verification finalized.")
        self.service.finalize_pending_secret_mutation(
            previous_secret_generation_id=str(journal["previous_secret_generation_id"]),
            selected_secret_generation_id=str(journal["candidate_secret_generation_id"]),
        )
        select_activation(self.layout, "approved", active)
        select_activation(self.layout, "previous-known-good", active)
        self._remove_selection("staged")
        self._finish_journal(journal, "verified")
        return active

    def recover_failed(self) -> InstalledActivation:
        with CompleteActivationLock(self.layout):
            journal = self._load_journal()
            if journal["state"] == "verification_passed":
                raise CompleteActivationCoordinationError(
                    "Verified activation must finish finalization rather than roll back."
                )
            return self._recover_locked(journal)

    def recover_failed_process(
        self,
        process_activation_id: str | None,
    ) -> InstalledActivation | None:
        """Recover only when the stopped process was the staged candidate.

        A mismatched marker belongs to the old process deliberately exiting
        after selection changed and must not roll the candidate back before its
        first start. An absent marker means the candidate failed before its
        entrypoint could record startup and therefore does require recovery.
        """

        with CompleteActivationLock(self.layout):
            journal = self._load_journal()
            candidate = journal.get("candidate_complete_activation_id")
            if not isinstance(candidate, str):
                raise CompleteActivationCoordinationError(
                    "Pending activation transaction has no complete candidate."
                )
            if process_activation_id is not None and process_activation_id != candidate:
                return None
            if journal["state"] == "verification_passed":
                return self._finalize_locked(journal)
            return self._recover_locked(journal)

    def recover(self) -> InstalledActivation:
        """Resume the only safe terminal outcome after an interrupted operation."""

        with CompleteActivationLock(self.layout):
            journal = self._load_journal()
            if journal["state"] == "verification_passed":
                return self._finalize_locked(journal)
            return self._recover_locked(journal)

    def _recover_locked(self, journal: dict[str, object]) -> InstalledActivation:
        previous = load_selected_activation(self.layout, "previous-known-good")
        candidate_secret = journal.get("candidate_secret_generation_id")
        if isinstance(candidate_secret, str):
            self.service.restore_pending_secret_mutation(
                self.secret_companion_root,
                previous_activation_generation_id=str(journal["previous_configuration_activation_id"]),
                previous_secret_generation_id=str(journal["previous_secret_generation_id"]),
                failed_secret_generation_id=candidate_secret,
                previous_satellite_projection_activation_ids=journal[
                    "previous_satellite_projection_activation_ids"
                ],  # type: ignore[arg-type]
            )
        select_activation(self.layout, "active", previous)
        select_activation(self.layout, "previous-known-good", previous)
        self._remove_selection("staged")
        self._finish_journal(journal, "recovered_previous")
        return previous

    def _write_journal(self, journal: dict[str, object], *, create: bool = False) -> None:
        self.journal_path.parent.mkdir(mode=0o2750, parents=True, exist_ok=True)
        if create:
            descriptor = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_json_bytes(journal))
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_directory(self.journal_path.parent)
        else:
            _atomic_replace(self.journal_path, _json_bytes(journal), mode=0o640)

    def _load_journal(self) -> dict[str, object]:
        value = _read_json(self.journal_path)
        fields = {
            "format",
            "transaction_id",
            "state",
            "previous_complete_activation_id",
            "candidate_complete_activation_id",
            "previous_configuration_activation_id",
            "candidate_configuration_activation_id",
            "previous_secret_generation_id",
            "candidate_secret_generation_id",
            "previous_satellite_projection_activation_ids",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value.get("format") != CONTROL_TRANSACTION_FORMAT
            or not isinstance(value.get("transaction_id"), str)
            or re.fullmatch(r"complete_activation_tx_[0-9a-f]{32}", value["transaction_id"]) is None
            or value.get("state")
            not in {"prepared", "configuration_selected", "awaiting_verification", "verification_passed"}
            or not isinstance(value.get("previous_complete_activation_id"), str)
            or not str(value["previous_complete_activation_id"]).startswith(
                "oracle-installation-activation-v1:sha256:"
            )
            or not isinstance(value.get("previous_configuration_activation_id"), str)
            or re.fullmatch(r"activation_[0-9a-f]{32}", value["previous_configuration_activation_id"])
            is None
            or not isinstance(value.get("previous_secret_generation_id"), str)
            or re.fullmatch(r"secret_[0-9a-f]{32}", value["previous_secret_generation_id"]) is None
            or not isinstance(value.get("previous_satellite_projection_activation_ids"), dict)
        ):
            raise CompleteActivationCoordinationError("Complete activation transaction journal is invalid.")
        for field, pattern in (
            ("candidate_complete_activation_id", r"oracle-installation-activation-v1:sha256:[0-9a-f]{64}"),
            ("candidate_configuration_activation_id", r"activation_[0-9a-f]{32}"),
            ("candidate_secret_generation_id", r"secret_[0-9a-f]{32}"),
        ):
            item = value[field]
            if item is not None and (not isinstance(item, str) or re.fullmatch(pattern, item) is None):
                raise CompleteActivationCoordinationError(
                    "Complete activation transaction journal is invalid."
                )
        if not all(
            isinstance(satellite_id, str)
            and isinstance(activation_id, str)
            and re.fullmatch(r"sat_activation_[0-9a-f]{32}", activation_id) is not None
            for satellite_id, activation_id in value[
                "previous_satellite_projection_activation_ids"
            ].items()
        ):
            raise CompleteActivationCoordinationError("Complete activation transaction journal is invalid.")
        return value

    def _remove_selection(self, name: str) -> None:
        path = self.layout.selection / name
        if path.is_symlink():
            path.unlink()
            _fsync_directory(path.parent)
        elif path.exists():
            raise CompleteActivationCoordinationError(
                f"Selection entry {name!r} is not a managed symbolic link."
            )

    def _finish_journal(self, journal: dict[str, object], outcome: str) -> None:
        result = {**journal, "state": "complete", "outcome": outcome}
        result_path = self.layout.control_state / f"{journal['transaction_id']}.json"
        result_bytes = _json_bytes(result)
        try:
            descriptor = os.open(result_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        except FileExistsError:
            if result_path.read_bytes() != result_bytes:
                raise CompleteActivationCoordinationError(
                    "Activation result identity conflicts with an existing record."
                )
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(result_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            self.journal_path.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(self.journal_path.parent)


def standard_online_authorization_audit(
    layout: InstallationLayout,
) -> Callable[[dict[str, object]], None]:
    """Return a redacted durable audit sink for the standard local socket."""

    path = layout.control_state / ONLINE_AUTHORIZATION_AUDIT_PATH

    def record(event: dict[str, object]) -> None:
        allowed = {
            "operation",
            "result",
            "peer_uid",
            "peer_pid",
            "peer_gid",
            "peer_account",
            "peer_category",
        }
        if not set(event).issubset(allowed):
            raise CompleteActivationCoordinationError(
                "Online authorization audit contains an unsupported field."
            )
        envelope = {
            "format": "oracle-online-authorization-audit-v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        path.parent.mkdir(mode=0o2750, parents=True, exist_ok=True)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o640,
        )
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(_json_bytes(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(path.parent)

    return record


def create_standard_host_local_control_server(
    layout: InstallationLayout,
    service: ConfigurationService,
    *,
    socket_path: Path,
    operator_group_gid: int,
    restart_request: Callable[[], None],
):
    """Assemble the ratified standard online control boundary.

    The runtime directory and its setgid operator-group ownership are host
    integration prerequisites. This factory does not create accounts or alter
    host permissions.
    """

    from .configuration.host_local import (
        HostLocalConfigurationServer,
        StandardUnixPeerAuthorizer,
    )

    coordinator = StandardActivationCoordinator(
        layout,
        service,
        secret_companion_root=layout.secrets,
    )
    return HostLocalConfigurationServer(
        socket_path,
        service,
        peer_authorizer=StandardUnixPeerAuthorizer(),
        expected_socket_group_gid=operator_group_gid,
        activation_coordinator=coordinator,
        authorization_audit=standard_online_authorization_audit(layout),
        restart_request=restart_request,
    )
