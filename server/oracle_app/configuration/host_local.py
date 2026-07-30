from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict
import errno
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import stat
import tempfile
from typing import Any

from .authoring_transactions import AuthoringModeError, AuthoringMutationError
from .generations import GenerationStoreError, _fsync_directory
from .loader import AuthoredRevisionConflict, snapshot_candidate
from .projections import SatelliteRuntimeCompatibility
from .roles import KNOWN_ROLE_PATHS, SECRET_COMPANION_PATH
from .secret_transactions import SecretMutationError
from .service import (
    CandidateActivationBlocked,
    ConfigurationService,
    SafetyAcknowledgementRequired,
    SecretGenerationConflict,
    TransitionActivationBlocked,
)
from .selection_transactions import SelectionCommittedAuditPending


HOST_LOCAL_PROTOCOL_FORMAT = "oracle-config-host-local-v1"
MAX_REQUEST_BYTES = 4 * 1024 * 1024


class HostLocalProtocolError(ValueError):
    pass


class HostLocalServiceAlreadyRunning(RuntimeError):
    pass


class ServicePresenceLock(AbstractContextManager["ServicePresenceLock"]):
    def __init__(self, store_root: Path) -> None:
        self.path = Path(store_root) / ".service.lock"
        self._stream = None

    def __enter__(self) -> ServicePresenceLock:
        if os.name == "nt":
            raise OSError("Unix host-local configuration transport is unavailable on Windows.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        self._stream = os.fdopen(descriptor, "r+b")
        import fcntl

        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._stream.close()
            self._stream = None
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise HostLocalServiceAlreadyRunning("Configuration service presence lock is already held.") from exc
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


class HostLocalDispatcher:
    def __init__(self, service: ConfigurationService) -> None:
        self.service = service

    def dispatch(self, request: object) -> dict[str, object]:
        try:
            payload = self._request(request)
            operation = self._required_string(payload, "operation")
            self._validate_shape(payload, operation)
            if operation == "status":
                result: object = asdict(self.service.status())
            elif operation == "review_candidate":
                result = self._with_candidate(payload, self._review)
            elif operation == "activate_candidate":
                result = self._with_candidate(payload, self._activate)
            elif operation == "replace_authored_candidate":
                result = self._with_candidate(payload, self._replace_authored)
            elif operation == "rollback":
                result = self._rollback(payload)
            elif operation == "mutate_secret":
                result = self._mutate_secret(payload)
            elif operation == "recover":
                result = self._recover()
            elif operation == "require_canonical_runtime":
                result = self._runtime_cutover(payload)
            elif operation == "accept_satellite_runtime_compatibility":
                result = self._accept_satellite_runtime_compatibility(payload)
            else:
                raise HostLocalProtocolError("Unsupported host-local operation.")
            return {"format": HOST_LOCAL_PROTOCOL_FORMAT, "ok": True, "result": result}
        except Exception as exc:
            return {"format": HOST_LOCAL_PROTOCOL_FORMAT, "ok": False, "error": self._error(exc)}

    def _with_candidate(self, payload: dict[str, object], callback) -> object:
        roles = payload.get("roles")
        root = self._materialize_roles(roles)
        try:
            expected = self._required_string(payload, "candidate_authored_revision")
            snapshot = snapshot_candidate(root)
            if snapshot.authored_revision != expected:
                raise AuthoredRevisionConflict(expected, snapshot.authored_revision)
            return callback(payload, root)
        finally:
            shutil.rmtree(root)

    def _review(self, _payload: dict[str, object], root: Path) -> dict[str, object]:
        review = self.service.review_candidate(root, actor="host_local_cli")
        return self._review_result(review)

    def _review_result(self, review: object) -> dict[str, object]:
        inspection = review.inspection
        return {
            "candidate_id": inspection.candidate_id,
            "authored_revision": inspection.authored_revision,
            "normalized_candidate_revision": inspection.normalized_candidate_revision,
            "activation_eligible": inspection.report.activation_eligible,
            "validation_findings": [asdict(item) for item in inspection.report.validation_findings],
            "activation_blockers": [asdict(item) for item in inspection.report.activation_blockers],
            "transition_blockers": [asdict(item) for item in inspection.report.transition_blockers],
            "transition_validation_context": (
                None
                if inspection.transition_validation is None
                else asdict(inspection.transition_validation.context)
            ),
            "readiness_findings": [asdict(item) for item in inspection.report.readiness_findings],
            "semantic_changes": [self.service._change_summary(item) for item in review.semantic_changes],
            "required_safety_acknowledgements": sorted(review.required_safety_acknowledgements),
            "audit_event_id": review.audit_event_id,
            "validation_version": review.validation_version,
        }

    def _activate(self, payload: dict[str, object], root: Path) -> dict[str, object]:
        result = self.service.activate_candidate(
            root,
            expected_authored_revision=self._required_string(payload, "candidate_authored_revision"),
            expected_secret_generation_id=self._optional_string(payload, "expected_secret_generation_id"),
            actor="host_local_cli",
            acknowledgements=self._acknowledgements(payload),
        )
        return self._transaction_result(result)

    def _replace_authored(self, payload: dict[str, object], root: Path) -> dict[str, object]:
        result = self.service.replace_authored_candidate(
            root,
            expected_authored_revision=self._required_string(payload, "expected_authored_revision"),
            expected_secret_generation_id=self._required_string(payload, "expected_secret_generation_id"),
            actor="host_local_cli",
            acknowledgements=self._acknowledgements(payload),
        )
        return self._authoring_result(result)

    @staticmethod
    def _authoring_result(result: object) -> dict[str, object]:
        output = HostLocalDispatcher._selected(result.selected)  # type: ignore[attr-defined]
        output.update(
            operation=result.operation,
            outcome=result.outcome,
            audit_event_id=result.audit_event_id,
            candidate_id=result.candidate_id,
            previous_authored_revision=result.previous_authored_revision,
            authored_revision=result.authored_revision,
        )
        return output

    def _rollback(self, payload: dict[str, object]) -> dict[str, object]:
        result = self.service.rollback(
            self._required_string(payload, "config_generation_id"),
            expected_secret_generation_id=self._required_string(payload, "expected_secret_generation_id"),
            actor="host_local_cli",
            acknowledgements=self._acknowledgements(payload),
        )
        return self._transaction_result(result)

    def _mutate_secret(self, payload: dict[str, object]) -> dict[str, object]:
        if self.service.authoring_root is None:
            raise AuthoringModeError("Secret mutation requires a bootstrap authoring root.")
        operation = self._required_string(payload, "secret_operation")
        value = payload.get("value")
        if value is not None and not isinstance(value, str):
            raise HostLocalProtocolError("Secret value must be text or null.")
        result = self.service.mutate_secret(
            self.service.authoring_root,
            operation=operation,  # type: ignore[arg-type]
            logical_id=self._required_string(payload, "logical_id"),
            value=value,
            expected_secret_generation_id=self._required_string(payload, "expected_secret_generation_id"),
            actor="host_local_cli",
        )
        return self._secret_result(result)

    @staticmethod
    def _secret_result(result: object) -> dict[str, object]:
        output = HostLocalDispatcher._selected(result.selected)  # type: ignore[attr-defined]
        output.update(
            operation=result.operation,
            audit_event_id=result.audit_event_id,
            logical_id=result.logical_id,
            previous_secret_generation_id=result.previous_secret_generation_id,
            revoked_secret_generation_id=result.revoked_secret_generation_id,
            pruned_secret_generation_ids=list(result.pruned_secret_generation_ids),
        )
        return output

    def _recover(self) -> dict[str, object]:
        selections = self.service.recover_selection_transactions()
        authoring: tuple[str, ...] = ()
        secrets: tuple[str, ...] = ()
        if self.service.authoring_mode == "managed_writable":
            authoring = self.service.recover_authoring_transactions(actor="host_local_cli")
        if self.service.authoring_root is not None:
            secrets = self.service.recover_secret_transactions(
                self.service.authoring_root,
                actor="host_local_cli",
            )
        return {
            "selection_operation_ids": list(selections),
            "authoring_transaction_ids": list(authoring),
            "secret_transaction_ids": list(secrets),
        }

    def _runtime_cutover(self, payload: dict[str, object]) -> dict[str, object]:
        result = self.service.require_canonical_runtime(
            actor="host_local_cli",
            acknowledge_one_way=payload.get("acknowledge_one_way") is True,
        )
        return self._runtime_cutover_result(result)

    def _accept_satellite_runtime_compatibility(
        self,
        payload: dict[str, object],
    ) -> dict[str, object]:
        report = SatelliteRuntimeCompatibility.model_validate(
            payload.get("compatibility_report")
        )
        result = self.service.accept_satellite_runtime_compatibility(
            self._required_string(payload, "satellite_id"),
            report,
            actor="host_local_cli",
        )
        return self._runtime_compatibility_result(result)

    @staticmethod
    def _runtime_compatibility_result(result: object) -> dict[str, object]:
        report = result.report  # type: ignore[attr-defined]
        return {
            "operation": "accept_satellite_runtime_compatibility",
            "satellite_id": result.satellite_id,  # type: ignore[attr-defined]
            "accepted_at": result.accepted_at,  # type: ignore[attr-defined]
            "platform": report.platform,
            "projection_schema_versions": list(report.projection_schema_versions),
            "interaction_runtime_version": report.interaction_runtime.runtime_version,
            "control_service_runtime_version": report.control_service.runtime_version,
            "audit_event_id": result.audit_event_id,  # type: ignore[attr-defined]
        }

    @staticmethod
    def _runtime_cutover_result(result: object) -> dict[str, object]:
        marker = result.marker  # type: ignore[attr-defined]
        return {
            "operation": result.operation,  # type: ignore[attr-defined]
            "outcome": result.outcome,  # type: ignore[attr-defined]
            "audit_event_id": result.audit_event_id,  # type: ignore[attr-defined]
            "bundle_id": marker.bundle_id,
            "activation_generation_id": marker.activation_generation_id,
            "config_revision": marker.config_revision,
            "selection_revision": marker.selection_revision,
            "canonical_runtime_required": True,
        }

    def _materialize_roles(self, value: object) -> Path:
        if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
            raise HostLocalProtocolError("Candidate roles must be a string-to-string map.")
        if not set(value).issubset(KNOWN_ROLE_PATHS):
            raise HostLocalProtocolError("Candidate contains an unknown role path.")
        encoded = {key: item.encode("utf-8") for key, item in value.items()}
        if sum(len(item) for item in encoded.values()) > MAX_REQUEST_BYTES:
            raise HostLocalProtocolError("Candidate role content exceeds the host-local request limit.")
        candidates = self.service.store.root / "candidates"
        candidates.mkdir(mode=0o700, exist_ok=True)
        if candidates.is_symlink() or not candidates.resolve(strict=True).is_relative_to(self.service.store.root):
            raise HostLocalProtocolError("Host-local candidate staging escapes the installed store.")
        root = Path(tempfile.mkdtemp(prefix="host-local-", dir=candidates))
        for role_path, data in encoded.items():
            path = root / role_path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o600)
        _fsync_directory(candidates)
        return root

    @staticmethod
    def _request(value: object) -> dict[str, object]:
        if not isinstance(value, dict) or value.get("format") != HOST_LOCAL_PROTOCOL_FORMAT:
            raise HostLocalProtocolError("Host-local request format is invalid.")
        return value

    @staticmethod
    def _validate_shape(payload: dict[str, object], operation: str) -> None:
        common = {"format", "operation"}
        allowed = {
            "status": common,
            "review_candidate": common | {"roles", "candidate_authored_revision"},
            "activate_candidate": common
            | {"roles", "candidate_authored_revision", "expected_secret_generation_id", "acknowledgements"},
            "replace_authored_candidate": common
            | {
                "roles",
                "candidate_authored_revision",
                "expected_authored_revision",
                "expected_secret_generation_id",
                "acknowledgements",
            },
            "rollback": common | {"config_generation_id", "expected_secret_generation_id", "acknowledgements"},
            "mutate_secret": common
            | {"secret_operation", "logical_id", "value", "expected_secret_generation_id"},
            "recover": common,
            "require_canonical_runtime": common | {"acknowledge_one_way"},
            "accept_satellite_runtime_compatibility": common
            | {"satellite_id", "compatibility_report"},
        }.get(operation)
        if allowed is None:
            raise HostLocalProtocolError("Unsupported host-local operation.")
        if not set(payload).issubset(allowed):
            raise HostLocalProtocolError("Host-local request contains fields not admitted for this operation.")

    @staticmethod
    def _required_string(payload: dict[str, object], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise HostLocalProtocolError(f"Host-local request requires {field}.")
        return value

    @staticmethod
    def _optional_string(payload: dict[str, object], field: str) -> str | None:
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or not value):
            raise HostLocalProtocolError(f"Host-local request {field} must be text or null.")
        return value

    @staticmethod
    def _acknowledgements(payload: dict[str, object]) -> frozenset[str]:
        value = payload.get("acknowledgements", [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise HostLocalProtocolError("Safety acknowledgements must be a string list.")
        return frozenset(value)

    @staticmethod
    def _transaction_result(result: object) -> dict[str, object]:
        selected = getattr(result, "selected")
        output = HostLocalDispatcher._selected(selected)
        output.update(
            operation=getattr(result, "operation"),
            outcome=getattr(result, "outcome"),
            audit_event_id=getattr(result, "audit_event_id"),
            candidate_id=getattr(result, "candidate_id"),
            authored_revision=getattr(result, "authored_revision"),
        )
        return output

    @staticmethod
    def _selected(selected: object) -> dict[str, object]:
        return {
            "activation_generation_id": selected.activation.generation_id,  # type: ignore[attr-defined]
            "config_generation_id": selected.config.generation_id,  # type: ignore[attr-defined]
            "config_revision": selected.config.config_revision,  # type: ignore[attr-defined]
            "secret_generation_id": selected.secrets.generation_id,  # type: ignore[attr-defined]
            "selection_operation_id": selected.selection_operation_id,  # type: ignore[attr-defined]
            "selection_revision": selected.selection_revision,  # type: ignore[attr-defined]
        }

    @staticmethod
    def _error(exc: BaseException) -> dict[str, object]:
        if isinstance(exc, SelectionCommittedAuditPending):
            return {
                "code": "selection_committed_audit_pending",
                "message": str(exc),
                "operation_id": exc.operation_id,
                "selection_revision": exc.selection_revision,
            }
        if isinstance(exc, SafetyAcknowledgementRequired):
            return {
                "code": "safety_acknowledgement_required",
                "message": str(exc),
                "required": sorted(exc.required),
                "provided": sorted(exc.provided),
            }
        if isinstance(exc, AuthoredRevisionConflict):
            return {"code": "authored_revision_conflict", "message": str(exc), "expected": exc.expected, "actual": exc.actual}
        if isinstance(exc, SecretGenerationConflict):
            return {"code": "secret_generation_conflict", "message": str(exc), "expected": exc.expected, "actual": exc.actual}
        if isinstance(exc, CandidateActivationBlocked):
            report = exc.inspection.report
            return {
                "code": "candidate_activation_blocked",
                "message": str(exc),
                "candidate_id": exc.inspection.candidate_id,
                "validation_findings": [asdict(item) for item in report.validation_findings],
                "activation_blockers": [asdict(item) for item in report.activation_blockers],
                "transition_blockers": [asdict(item) for item in report.transition_blockers],
                "transition_validation_context": (
                    None
                    if exc.inspection.transition_validation is None
                    else asdict(exc.inspection.transition_validation.context)
                ),
            }
        if isinstance(exc, TransitionActivationBlocked):
            return {
                "code": "transition_activation_blocked",
                "message": str(exc),
                "transition_blockers": [asdict(item) for item in exc.findings],
            }
        if isinstance(exc, HostLocalServiceAlreadyRunning):
            return {"code": "configuration_service_running", "message": str(exc)}
        if isinstance(exc, (HostLocalProtocolError, AuthoringMutationError, SecretMutationError, GenerationStoreError, ValueError)):
            return {"code": "request_rejected", "message": str(exc)}
        return {"code": "internal_error", "message": "Host-local configuration operation failed."}


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        data = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES or not data.endswith(b"\n"):
            response = {
                "format": HOST_LOCAL_PROTOCOL_FORMAT,
                "ok": False,
                "error": {"code": "request_rejected", "message": "Host-local request is too large or incomplete."},
            }
        else:
            try:
                request = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError):
                request = None
            response = self.server.dispatcher.dispatch(request)  # type: ignore[attr-defined]
        self.wfile.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")


class HostLocalConfigurationServer(socketserver.UnixStreamServer):
    def __init__(self, socket_path: Path, service: ConfigurationService) -> None:
        if not hasattr(socket, "AF_UNIX"):
            raise OSError("Unix-domain sockets are unavailable on this platform.")
        requested = Path(socket_path)
        requested.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = requested.parent.resolve(strict=True)
        parent_stat = parent.stat()
        if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o022:
            raise HostLocalProtocolError(
                "Host-local socket parent must be owned by the service user and not writable by group or others."
            )
        self.socket_path = parent / requested.name
        self.dispatcher = HostLocalDispatcher(service)
        self._presence = ServicePresenceLock(service.store.root)
        self._presence.__enter__()
        try:
            self._prepare_socket_path()
            previous_umask = os.umask(0o177)
            try:
                super().__init__(str(self.socket_path), _RequestHandler)
                self.socket_path.chmod(0o600)
            finally:
                os.umask(previous_umask)
        except BaseException:
            self._presence.__exit__(None, None, None)
            raise

    def server_close(self) -> None:
        try:
            super().server_close()
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass
        finally:
            self._presence.__exit__(None, None, None)

    def _prepare_socket_path(self) -> None:
        try:
            mode = self.socket_path.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise HostLocalProtocolError("Host-local socket path already exists and is not a socket.")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.1)
            probe.connect(str(self.socket_path))
        except OSError:
            self.socket_path.unlink()
        else:
            raise HostLocalServiceAlreadyRunning("Host-local configuration socket is already accepting connections.")
        finally:
            probe.close()


class HostLocalConfigurationClient:
    def __init__(self, socket_path: Path, *, timeout_seconds: float = 10.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_seconds = timeout_seconds

    def request(self, payload: dict[str, object]) -> dict[str, object]:
        request = dict(payload)
        request["format"] = HOST_LOCAL_PROTOCOL_FORMAT
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_REQUEST_BYTES:
            raise HostLocalProtocolError("Host-local request exceeds the size limit.")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout_seconds)
            connection.connect(str(self.socket_path))
            connection.sendall(encoded)
            with connection.makefile("rb") as stream:
                response_data = stream.readline(MAX_REQUEST_BYTES + 1)
        finally:
            connection.close()
        if len(response_data) > MAX_REQUEST_BYTES or not response_data.endswith(b"\n"):
            raise HostLocalProtocolError("Host-local response is too large or incomplete.")
        try:
            response = json.loads(response_data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HostLocalProtocolError("Host-local response is invalid JSON.") from exc
        if not isinstance(response, dict) or response.get("format") != HOST_LOCAL_PROTOCOL_FORMAT:
            raise HostLocalProtocolError("Host-local response format is invalid.")
        return response


def candidate_role_text(root: Path) -> tuple[dict[str, str], str]:
    snapshot = snapshot_candidate(root)
    if snapshot.snapshot_findings or snapshot.non_authoritative_paths:
        raise HostLocalProtocolError("Candidate directory must contain only a valid fixed-role tree.")
    if (snapshot.root / SECRET_COMPANION_PATH).exists() or (snapshot.root / SECRET_COMPANION_PATH).is_symlink():
        raise HostLocalProtocolError("Non-secret host-local candidates cannot contain secrets.env.")
    try:
        roles = {path: data.decode("utf-8") for path, data in snapshot.authored_bytes.items()}
    except UnicodeDecodeError as exc:
        raise HostLocalProtocolError("Candidate roles must be UTF-8 text.") from exc
    return roles, snapshot.authored_revision
