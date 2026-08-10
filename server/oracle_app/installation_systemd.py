"""Fixed systemd-unit installation and initial activation planning."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

from .installation import (
    InstallationLayout,
    activation_directory_name,
    load_activation,
    load_selected_activation,
    select_activation,
)
from .installation_assembly import service_definition_identity


STANDARD_UNIT_NAME = "oracle-brain.service"
STANDARD_UNIT_PATH = Path("/etc/systemd/system") / STANDARD_UNIT_NAME
SERVICE_PLAN_FORMAT = "oracle-systemd-install-plan-v1"
ACTIVATION_PLAN_FORMAT = "oracle-initial-activation-plan-v1"
INITIAL_TRANSACTION_FORMAT = "oracle-initial-activation-transaction-v1"
INITIAL_TRANSACTION_PATH = "initial-activation-transaction.json"
UPDATE_ACTIVATION_PLAN_FORMAT = "oracle-update-activation-plan-v1"
ROLLBACK_ACTIVATION_PLAN_FORMAT = "oracle-rollback-activation-plan-v1"
MANAGED_TRANSACTION_FORMAT = "oracle-managed-activation-transaction-v1"
MANAGED_TRANSACTION_PATH = "managed-activation-transaction.json"


class StandardSystemdError(RuntimeError):
    """The standard unit or initial activation cannot be handled safely."""


@dataclass(frozen=True)
class SystemdInstallPlan:
    identity: str
    source: Path
    destination: Path
    service_definition_identity: str
    disposition: str


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_systemd_install_plan(
    layout: InstallationLayout,
    *,
    unit_path: Path = STANDARD_UNIT_PATH,
) -> SystemdInstallPlan:
    staged = load_selected_activation(layout, "staged")
    application = Path(staged.directory / "application").resolve(strict=True)
    source = application / "scripts" / "oracle-brain-standard.service"
    identity = service_definition_identity(source)
    if staged.record.get("service_definition_identity") != identity:
        raise StandardSystemdError("Staged activation and service definition identities disagree.")
    if unit_path.is_symlink() or (unit_path.exists() and not unit_path.is_file()):
        raise StandardSystemdError("Standard systemd unit destination is unsafe.")
    disposition = "reuse" if unit_path.is_file() and unit_path.read_bytes() == source.read_bytes() else "install"
    basis = {
        "format": SERVICE_PLAN_FORMAT,
        "unit_name": STANDARD_UNIT_NAME,
        "source_activation_id": staged.activation_id,
        "source": str(source),
        "destination": str(unit_path),
        "service_definition_identity": identity,
        "disposition": disposition,
        "daemon_reload": True,
        "enable": False,
        "start": False,
    }
    plan_id = f"{SERVICE_PLAN_FORMAT}:sha256:{hashlib.sha256(_json_bytes(basis)).hexdigest()}"
    return SystemdInstallPlan(plan_id, source, unit_path, identity, disposition)


def install_systemd_unit(
    plan: SystemdInstallPlan,
    *,
    systemd_analyze: str = "systemd-analyze",
    systemctl: str = "systemctl",
) -> dict[str, object]:
    """Publish the exact fixed unit, reload systemd, and deliberately do not enable it."""

    if os.geteuid() != 0:
        raise StandardSystemdError("Systemd unit installation requires elevated maintenance authority.")
    current = build_systemd_install_plan(
        InstallationLayout(plan.source.parents[3]),
        unit_path=plan.destination,
    )
    if current != plan:
        raise StandardSystemdError("Approved systemd installation plan is stale.")
    subprocess.run([systemd_analyze, "verify", str(plan.source)], check=True)
    changed = plan.disposition == "install"
    if changed:
        plan.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = plan.destination.parent / f".{plan.destination.name}.tmp-{secrets.token_hex(8)}"
        try:
            with plan.source.open("rb") as source, temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            temporary.chmod(0o644)
            os.chown(temporary, 0, 0)
            os.replace(temporary, plan.destination)
            _fsync_directory(plan.destination.parent)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    if service_definition_identity(plan.destination) != plan.service_definition_identity:
        raise StandardSystemdError("Installed systemd unit identity differs from the staged activation.")
    subprocess.run([systemctl, "daemon-reload"], check=True)
    return {
        "plan_identity": plan.identity,
        "unit": str(plan.destination),
        "service_definition_identity": plan.service_definition_identity,
        "disposition": "installed" if changed else "reused",
        "daemon_reloaded": True,
        "enabled": False,
        "started": False,
    }


def build_initial_activation_plan(
    layout: InstallationLayout,
    *,
    unit_path: Path = STANDARD_UNIT_PATH,
) -> dict[str, object]:
    """Plan the first active selection without changing selectors or systemd."""

    staged = load_selected_activation(layout, "staged")
    for selection in ("active", "approved", "previous-known-good"):
        path = layout.selection / selection
        if path.exists() or path.is_symlink():
            raise StandardSystemdError("Initial activation requires no established active lifecycle selection.")
    installed_identity = service_definition_identity(unit_path)
    if installed_identity != staged.record.get("service_definition_identity"):
        raise StandardSystemdError("Installed service definition does not match the staged activation.")
    basis = {
        "format": ACTIVATION_PLAN_FORMAT,
        "installation_root": str(layout.root),
        "candidate_activation_id": staged.activation_id,
        "service_definition_identity": installed_identity,
        "unit": str(unit_path),
        "sequence": [
            "revalidate the complete staged activation",
            "atomically select the staged activation as active",
            "enable the fixed systemd unit",
            "start Oracle through systemd",
            "verify process state, readiness, health, configuration identity, deterministic interaction, and web surfaces",
            "mark the candidate approved and previous-known-good only after verification",
        ],
        "failure_posture": "stop_service_remove_unverified_active_retain_staged_for_diagnostics",
        "rollback_available": False,
        "reason": "initial installation has no prior known-good activation",
    }
    return {
        **basis,
        "identity": f"{ACTIVATION_PLAN_FORMAT}:sha256:{hashlib.sha256(_json_bytes(basis)).hexdigest()}",
    }


def _installed_unit_identity(unit_path: Path) -> str:
    try:
        return service_definition_identity(unit_path)
    except (OSError, ValueError) as exc:
        raise StandardSystemdError("Installed service definition is absent or invalid.") from exc


def _managed_activation_plan(
    layout: InstallationLayout,
    *,
    operation: str,
    target,
    unit_path: Path,
) -> dict[str, object]:
    active = load_selected_activation(layout)
    approved = load_selected_activation(layout, "approved")
    known_good = load_selected_activation(layout, "previous-known-good")
    if active.activation_id != known_good.activation_id:
        raise StandardSystemdError("Managed activation requires the active selection to be known-good.")
    if target.activation_id == active.activation_id:
        raise StandardSystemdError("Managed activation target must differ from the active activation.")
    installed_identity = _installed_unit_identity(unit_path)
    if active.record.get("service_definition_identity") != installed_identity:
        raise StandardSystemdError("Active activation and installed service definition disagree.")
    if target.record.get("service_definition_identity") != installed_identity:
        raise StandardSystemdError(
            "Stage 4 managed activation requires an unchanged validated service-launch contract."
        )
    if target.record.get("configuration_activation_identity") != active.record.get(
        "configuration_activation_identity"
    ):
        raise StandardSystemdError(
            "Application update or rollback cannot implicitly change canonical configuration."
        )
    if target.record.get("persistent_state_checkpoint") != active.record.get("persistent_state_checkpoint"):
        raise StandardSystemdError("Managed activation persistent-state checkpoints are incompatible.")
    plan_format = (
        UPDATE_ACTIVATION_PLAN_FORMAT if operation == "update" else ROLLBACK_ACTIVATION_PLAN_FORMAT
    )
    basis = {
        "format": plan_format,
        "operation": operation,
        "installation_root": str(layout.root),
        "previous_activation_id": active.activation_id,
        "target_activation_id": target.activation_id,
        "approved_before_activation_id": approved.activation_id,
        "service_definition_identity": installed_identity,
        "unit": str(unit_path),
        "sequence": [
            "revalidate the complete target and current known-good activation",
            "durably record the managed activation transaction",
            "stop Oracle through systemd",
            "atomically select the complete target activation",
            "start Oracle through systemd",
            "verify process state, readiness, health, configuration identity, deterministic interaction, and web surfaces",
            "record the target known-good only after complete verification",
        ],
        "failure_posture": "restore_and_verify_previous_complete_known_good_activation",
        "configuration_change": False,
        "persistent_state_migration": False,
    }
    return {
        **basis,
        "identity": f"{plan_format}:sha256:{hashlib.sha256(_json_bytes(basis)).hexdigest()}",
    }


def build_update_activation_plan(
    layout: InstallationLayout,
    *,
    unit_path: Path = STANDARD_UNIT_PATH,
) -> dict[str, object]:
    staged = load_selected_activation(layout, "staged")
    return _managed_activation_plan(
        layout,
        operation="update",
        target=staged,
        unit_path=unit_path,
    )


def build_rollback_activation_plan(
    layout: InstallationLayout,
    target_activation_id: str,
    *,
    unit_path: Path = STANDARD_UNIT_PATH,
) -> dict[str, object]:
    target = load_activation(
        layout,
        layout.activations / activation_directory_name(target_activation_id),
    )
    return _managed_activation_plan(
        layout,
        operation="rollback",
        target=target,
        unit_path=unit_path,
    )


def _transaction_path(layout: InstallationLayout) -> Path:
    return layout.control_state / INITIAL_TRANSACTION_PATH


def _read_transaction(layout: InstallationLayout) -> dict[str, object]:
    path = _transaction_path(layout)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StandardSystemdError("Initial activation transaction is absent or invalid.") from exc
    required = {"format", "transaction_id", "plan_identity", "candidate_activation_id", "state", "verification"}
    if not isinstance(value, dict) or set(value) != required or value.get("format") != INITIAL_TRANSACTION_FORMAT:
        raise StandardSystemdError("Initial activation transaction has an invalid shape.")
    return value


def load_initial_activation_transaction(layout: InstallationLayout) -> dict[str, object]:
    """Return redacted lifecycle state for offline recovery decisions."""

    return dict(_read_transaction(layout))


def _write_transaction(layout: InstallationLayout, value: dict[str, object], *, create: bool = False) -> None:
    path = _transaction_path(layout)
    content = _json_bytes(value)
    if create:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(8)}"
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o640)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    path.chmod(0o640)
    _fsync_directory(path.parent)


def prepare_initial_activation(layout: InstallationLayout, plan: dict[str, object]) -> dict[str, object]:
    """Durably record and select one unverified initial candidate."""

    current = build_initial_activation_plan(layout, unit_path=Path(str(plan.get("unit"))))
    if current != plan:
        raise StandardSystemdError("Approved initial activation plan is stale.")
    transaction_path = _transaction_path(layout)
    if transaction_path.exists() or transaction_path.is_symlink():
        raise StandardSystemdError("An initial activation transaction already requires recovery.")
    staged = load_selected_activation(layout, "staged")
    transaction: dict[str, object] = {
        "format": INITIAL_TRANSACTION_FORMAT,
        "transaction_id": f"initial_activation_{secrets.token_hex(16)}",
        "plan_identity": plan["identity"],
        "candidate_activation_id": staged.activation_id,
        "state": "prepared",
        "verification": None,
    }
    _write_transaction(layout, transaction, create=True)
    select_activation(layout, "active", staged)
    transaction["state"] = "active_selected"
    _write_transaction(layout, transaction)
    return transaction


def mark_initial_service_started(layout: InstallationLayout) -> dict[str, object]:
    transaction = _read_transaction(layout)
    if transaction["state"] != "active_selected":
        raise StandardSystemdError("Initial activation is not ready to record service startup.")
    active = load_selected_activation(layout)
    if active.activation_id != transaction["candidate_activation_id"]:
        raise StandardSystemdError("Active selection changed during initial activation.")
    transaction["state"] = "service_started"
    _write_transaction(layout, transaction)
    return transaction


def mark_initial_verification_passed(
    layout: InstallationLayout,
    verification: dict[str, object],
) -> dict[str, object]:
    transaction = _read_transaction(layout)
    if transaction["state"] != "service_started":
        raise StandardSystemdError("Initial activation is not awaiting verification.")
    if verification.get("passed") is not True or set(verification) != {
        "passed", "systemd_active", "readiness", "health", "configuration_identity",
        "deterministic_interaction", "house_ui", "system_ui", "satellite_ui",
    }:
        raise StandardSystemdError("Initial activation verification evidence is incomplete.")
    transaction["verification"] = dict(verification)
    transaction["state"] = "verification_passed"
    _write_transaction(layout, transaction)
    return transaction


def finalize_initial_activation(layout: InstallationLayout) -> dict[str, object]:
    transaction = _read_transaction(layout)
    if transaction["state"] != "verification_passed":
        raise StandardSystemdError("Initial activation has not passed verification.")
    active = load_selected_activation(layout)
    if active.activation_id != transaction["candidate_activation_id"]:
        raise StandardSystemdError("Verified initial activation is no longer active.")
    select_activation(layout, "approved", active)
    select_activation(layout, "previous-known-good", active)
    _remove_selection(layout, "staged")
    return _finish_initial_transaction(layout, transaction, outcome="verified")


def fail_initial_activation(layout: InstallationLayout, *, reason: str) -> dict[str, object]:
    transaction = _read_transaction(layout)
    if transaction["state"] == "verification_passed":
        return finalize_initial_activation(layout)
    try:
        active = load_selected_activation(layout)
    except ValueError:
        active = None
    if active is not None and active.activation_id == transaction["candidate_activation_id"]:
        _remove_selection(layout, "active")
    transaction["verification"] = {"passed": False, "reason": reason}
    return _finish_initial_transaction(layout, transaction, outcome="failed")


def _remove_selection(layout: InstallationLayout, name: str) -> None:
    path = layout.selection / name
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _finish_initial_transaction(
    layout: InstallationLayout,
    transaction: dict[str, object],
    *,
    outcome: str,
) -> dict[str, object]:
    result = {**transaction, "state": outcome}
    destination = layout.control_state / f"initial-activation-result-{transaction['transaction_id']}.json"
    content = _json_bytes(result)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise StandardSystemdError("Initial activation result conflicts with durable recovery evidence.")
    else:
        with destination.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        destination.chmod(0o440)
    try:
        _transaction_path(layout).unlink()
    except FileNotFoundError:
        pass
    _fsync_directory(layout.control_state)
    return result


def _managed_transaction_path(layout: InstallationLayout) -> Path:
    return layout.control_state / MANAGED_TRANSACTION_PATH


def _read_managed_transaction(layout: InstallationLayout) -> dict[str, object]:
    try:
        value = json.loads(_managed_transaction_path(layout).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise StandardSystemdError("Managed activation transaction is absent or invalid.") from exc
    fields = {
        "format",
        "transaction_id",
        "plan_identity",
        "operation",
        "previous_activation_id",
        "target_activation_id",
        "approved_before_activation_id",
        "state",
        "verification",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("format") != MANAGED_TRANSACTION_FORMAT
        or value.get("operation") not in {"update", "rollback"}
        or value.get("state")
        not in {"prepared", "target_selected", "service_started", "verification_passed"}
        or not all(
            isinstance(value.get(field), str)
            for field in (
                "transaction_id",
                "plan_identity",
                "previous_activation_id",
                "target_activation_id",
                "approved_before_activation_id",
            )
        )
    ):
        raise StandardSystemdError("Managed activation transaction has an invalid shape.")
    return value


def load_managed_activation_transaction(layout: InstallationLayout) -> dict[str, object]:
    return dict(_read_managed_transaction(layout))


def _write_managed_transaction(
    layout: InstallationLayout,
    value: dict[str, object],
    *,
    create: bool = False,
) -> None:
    path = _managed_transaction_path(layout)
    content = _json_bytes(value)
    if create:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o640)
        _fsync_directory(path.parent)
        return
    temporary = path.parent / f".{path.name}.tmp-{secrets.token_hex(8)}"
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o640)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _activation_by_id(layout: InstallationLayout, activation_id: str):
    return load_activation(
        layout,
        layout.activations / activation_directory_name(activation_id),
    )


def prepare_managed_activation(layout: InstallationLayout, plan: dict[str, object]) -> dict[str, object]:
    operation = plan.get("operation")
    target_id = plan.get("target_activation_id")
    if operation == "update":
        current = build_update_activation_plan(layout, unit_path=Path(str(plan.get("unit"))))
    elif operation == "rollback" and isinstance(target_id, str):
        current = build_rollback_activation_plan(
            layout,
            target_id,
            unit_path=Path(str(plan.get("unit"))),
        )
    else:
        raise StandardSystemdError("Managed activation plan operation is invalid.")
    if current != plan:
        raise StandardSystemdError("Approved managed activation plan is stale.")
    path = _managed_transaction_path(layout)
    if path.exists() or path.is_symlink():
        raise StandardSystemdError("A managed activation transaction already requires recovery.")
    transaction: dict[str, object] = {
        "format": MANAGED_TRANSACTION_FORMAT,
        "transaction_id": f"managed_activation_{secrets.token_hex(16)}",
        "plan_identity": plan["identity"],
        "operation": operation,
        "previous_activation_id": plan["previous_activation_id"],
        "target_activation_id": plan["target_activation_id"],
        "approved_before_activation_id": plan["approved_before_activation_id"],
        "state": "prepared",
        "verification": None,
    }
    select_activation(
        layout,
        "previous-known-good",
        _activation_by_id(layout, str(transaction["previous_activation_id"])),
    )
    _write_managed_transaction(layout, transaction, create=True)
    return transaction


def select_managed_activation_target(layout: InstallationLayout) -> dict[str, object]:
    transaction = _read_managed_transaction(layout)
    if transaction["state"] != "prepared":
        raise StandardSystemdError("Managed activation is not ready to select its target.")
    active = load_selected_activation(layout)
    if active.activation_id != transaction["previous_activation_id"]:
        raise StandardSystemdError("Active selection changed after managed activation planning.")
    target = _activation_by_id(layout, str(transaction["target_activation_id"]))
    select_activation(layout, "active", target)
    transaction["state"] = "target_selected"
    _write_managed_transaction(layout, transaction)
    return transaction


def mark_managed_service_started(layout: InstallationLayout) -> dict[str, object]:
    transaction = _read_managed_transaction(layout)
    if transaction["state"] != "target_selected":
        raise StandardSystemdError("Managed activation target is not selected for service startup.")
    if load_selected_activation(layout).activation_id != transaction["target_activation_id"]:
        raise StandardSystemdError("Managed activation target changed before service startup.")
    transaction["state"] = "service_started"
    _write_managed_transaction(layout, transaction)
    return transaction


def mark_managed_verification_passed(
    layout: InstallationLayout,
    verification: dict[str, object],
) -> dict[str, object]:
    transaction = _read_managed_transaction(layout)
    if transaction["state"] != "service_started":
        raise StandardSystemdError("Managed activation is not awaiting verification.")
    if verification.get("passed") is not True or set(verification) != {
        "passed", "systemd_active", "readiness", "health", "configuration_identity",
        "deterministic_interaction", "house_ui", "system_ui", "satellite_ui",
    }:
        raise StandardSystemdError("Managed activation verification evidence is incomplete.")
    transaction["verification"] = dict(verification)
    transaction["state"] = "verification_passed"
    _write_managed_transaction(layout, transaction)
    return transaction


def finalize_managed_activation(layout: InstallationLayout) -> dict[str, object]:
    transaction = _read_managed_transaction(layout)
    if transaction["state"] != "verification_passed":
        raise StandardSystemdError("Managed activation has not passed verification.")
    target = _activation_by_id(layout, str(transaction["target_activation_id"]))
    if load_selected_activation(layout).activation_id != target.activation_id:
        raise StandardSystemdError("Verified managed activation is no longer active.")
    if transaction["operation"] == "update":
        select_activation(layout, "approved", target)
        staged_path = layout.selection / "staged"
        if staged_path.is_symlink():
            if load_selected_activation(layout, "staged").activation_id != target.activation_id:
                raise StandardSystemdError("Staged update selection changed before finalization.")
            _remove_selection(layout, "staged")
        elif staged_path.exists():
            raise StandardSystemdError("Staged update selection is not a managed symbolic link.")
    else:
        approved = _activation_by_id(layout, str(transaction["approved_before_activation_id"]))
        select_activation(layout, "approved", approved)
    select_activation(layout, "previous-known-good", target)
    return _finish_managed_transaction(layout, transaction, outcome="verified")


def recover_managed_activation(layout: InstallationLayout, *, reason: str) -> dict[str, object]:
    transaction = _read_managed_transaction(layout)
    if transaction["state"] == "verification_passed":
        return finalize_managed_activation(layout)
    previous = _activation_by_id(layout, str(transaction["previous_activation_id"]))
    approved = _activation_by_id(layout, str(transaction["approved_before_activation_id"]))
    select_activation(layout, "active", previous)
    select_activation(layout, "previous-known-good", previous)
    select_activation(layout, "approved", approved)
    transaction["verification"] = {"passed": False, "reason": reason}
    return _finish_managed_transaction(layout, transaction, outcome="recovered_previous")


def _finish_managed_transaction(
    layout: InstallationLayout,
    transaction: dict[str, object],
    *,
    outcome: str,
) -> dict[str, object]:
    result = {**transaction, "state": "complete", "outcome": outcome}
    destination = layout.control_state / f"managed-activation-result-{transaction['transaction_id']}.json"
    content = _json_bytes(result)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise StandardSystemdError("Managed activation result conflicts with durable recovery evidence.")
    else:
        with destination.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        destination.chmod(0o440)
    try:
        _managed_transaction_path(layout).unlink()
    except FileNotFoundError:
        pass
    _fsync_directory(layout.control_state)
    return result
