"""Explicit cross-version configuration and installation transition capsule."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Mapping

from .configuration import GenerationStore, inspect_candidate, snapshot_candidate
from .installation import InstallationLayout, InstalledActivation, load_selected_activation


SCHEMA_TRANSITION_PLAN_FORMAT = "oracle-schema-transition-plan-v1"
SCHEMA_TRANSITION_CAPSULE_FORMAT = "oracle-schema-transition-capsule-v1"
SCHEMA_TRANSITION_PATH = "schema-transition-capsule.json"


class SchemaTransitionError(RuntimeError):
    """An explicit cross-version transition is absent, stale, or unsafe."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _path(layout: InstallationLayout) -> Path:
    return layout.control_state / SCHEMA_TRANSITION_PATH


def schema_transition_pending(layout: InstallationLayout) -> bool:
    path = _path(layout)
    return path.exists() or path.is_symlink()


def _seal(value: Mapping[str, object]) -> dict[str, object]:
    basis = {key: item for key, item in value.items() if key != "content_sha256"}
    return {**basis, "content_sha256": hashlib.sha256(_json_bytes(basis)).hexdigest()}


def _write(
    layout: InstallationLayout,
    value: dict[str, object],
    *,
    create: bool = False,
) -> dict[str, object]:
    path = _path(layout)
    sealed = _seal(value)
    content = _json_bytes(sealed)
    if create:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o640)
        _fsync_directory(path.parent)
        return sealed
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
    return sealed


def _selection_snapshot(selected) -> dict[str, object]:
    return {
        "activation_generation_id": selected.activation.generation_id,
        "config_generation_id": selected.config.generation_id,
        "config_revision": selected.config.config_revision,
        "secret_generation_id": selected.secrets.generation_id,
        "selection_operation_id": selected.selection_operation_id,
        "selection_revision": selected.selection_revision,
        "satellite_projection_activation_ids": dict(selected.satellite_projection_activation_ids),
    }


def _candidate_deployment(layout: InstallationLayout, candidate: Path) -> tuple[Path, str]:
    if candidate.is_symlink() or not candidate.is_dir():
        raise SchemaTransitionError("Schema-transition candidate is absent or unsafe.")
    try:
        resolved = candidate.resolve(strict=True)
        deployments = layout.deployments.resolve(strict=True)
    except OSError as exc:
        raise SchemaTransitionError("Schema-transition candidate cannot be resolved safely.") from exc
    if resolved.name != "configuration" or resolved.parent.parent != deployments:
        raise SchemaTransitionError("Schema-transition candidate is not one staged deployment configuration.")
    return resolved, resolved.parent.name


def build_schema_transition_plan(
    layout: InstallationLayout,
    candidate: Path,
    *,
    target_core_commit: str,
    target_core_git_tree: str,
    target_python_environment_identity: str,
) -> dict[str, object]:
    """Plan one explicit schema transition before canonical selection changes."""

    if _path(layout).exists() or _path(layout).is_symlink():
        raise SchemaTransitionError("A schema-transition capsule already requires completion or recovery.")
    managed_transaction = layout.control_state / "managed-activation-transaction.json"
    if managed_transaction.exists() or managed_transaction.is_symlink():
        raise SchemaTransitionError("A managed activation transaction already requires recovery.")
    staged = layout.selection / "staged"
    if staged.exists() or staged.is_symlink():
        raise SchemaTransitionError("Schema transition requires an empty staged installation selection.")
    active = load_selected_activation(layout)
    known_good = load_selected_activation(layout, "previous-known-good")
    approved = load_selected_activation(layout, "approved")
    if active.activation_id != known_good.activation_id:
        raise SchemaTransitionError("Schema transition requires the active installation to be known-good.")
    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    store.validate_initialized()
    selected = store.load_selected()
    if active.record.get("configuration_activation_identity") != selected.activation.generation_id:
        raise SchemaTransitionError("Schema transition must begin from coherent installation and configuration selections.")
    candidate, deployment_revision = _candidate_deployment(layout, candidate)
    inspection = inspect_candidate(candidate, secret_snapshot=selected.secrets.snapshot)
    if not inspection.report.activation_eligible or inspection.normalized is None:
        raise SchemaTransitionError("Schema-transition candidate is not activation eligible.")
    if inspection.normalized.config_revision == selected.config.config_revision:
        raise SchemaTransitionError("Schema-transition capsule is forbidden for an effective configuration no-op.")
    basis = {
        "format": SCHEMA_TRANSITION_PLAN_FORMAT,
        "installation_root": str(layout.root),
        "previous_installation_activation_id": active.activation_id,
        "approved_before_activation_id": approved.activation_id,
        "previous_configuration_selection": _selection_snapshot(selected),
        "target": {
            "core_commit": target_core_commit,
            "core_git_tree": target_core_git_tree,
            "python_environment_identity": target_python_environment_identity,
            "household_deployment_revision": deployment_revision,
            "authored_revision": snapshot_candidate(candidate).authored_revision,
            "config_revision": inspection.normalized.config_revision,
            "secret_generation_id": selected.secrets.generation_id,
        },
        "target_configuration_activation_id": None,
        "sequence": [
            "persist this exact recovery capsule before configuration mutation",
            "select the target configuration through the canonical transaction engine",
            "assemble only the pinned target installation against that selection",
            "activate and verify the complete target",
            "on failure restore the captured configuration and projection selection before the previous installation",
            "verify the recovered runtime before reporting recovery success",
        ],
        "normal_lifecycle_selector_divergence_allowed": False,
    }
    return {
        **basis,
        "identity": f"{SCHEMA_TRANSITION_PLAN_FORMAT}:sha256:{hashlib.sha256(_json_bytes(basis)).hexdigest()}",
    }


def prepare_schema_transition(
    layout: InstallationLayout,
    candidate: Path,
    *,
    target_core_commit: str,
    target_core_git_tree: str,
    target_python_environment_identity: str,
    approved_plan: str,
) -> dict[str, object]:
    plan = build_schema_transition_plan(
        layout,
        candidate,
        target_core_commit=target_core_commit,
        target_core_git_tree=target_core_git_tree,
        target_python_environment_identity=target_python_environment_identity,
    )
    if plan["identity"] != approved_plan:
        raise SchemaTransitionError("Schema-transition plan is stale or unapproved.")
    basis = {
        "format": SCHEMA_TRANSITION_CAPSULE_FORMAT,
        "transaction_id": f"schema_transition_{secrets.token_hex(16)}",
        "plan_identity": approved_plan,
        "state": "prepared",
        "previous_installation_activation_id": plan["previous_installation_activation_id"],
        "approved_before_activation_id": plan["approved_before_activation_id"],
        "previous_configuration_selection": plan["previous_configuration_selection"],
        "target": plan["target"],
        "target_installation_activation_id": None,
        "target_configuration_activation_id": None,
    }
    capsule = {
        **basis,
        "identity": (
            f"{SCHEMA_TRANSITION_CAPSULE_FORMAT}:sha256:"
            + hashlib.sha256(
                _json_bytes(
                    {
                        "plan_identity": approved_plan,
                        "transaction_id": basis["transaction_id"],
                    }
                )
            ).hexdigest()
        ),
    }
    return _write(layout, capsule, create=True)


def load_schema_transition(layout: InstallationLayout) -> dict[str, object]:
    path = _path(layout)
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaTransitionError("Schema-transition capsule is absent or invalid.") from exc
    required = {
        "format", "identity", "transaction_id", "plan_identity", "state",
        "previous_installation_activation_id", "approved_before_activation_id",
        "previous_configuration_selection", "target", "target_installation_activation_id",
        "target_configuration_activation_id", "content_sha256",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("format") != SCHEMA_TRANSITION_CAPSULE_FORMAT:
        raise SchemaTransitionError("Schema-transition capsule has an invalid shape.")
    expected = (
        f"{SCHEMA_TRANSITION_CAPSULE_FORMAT}:sha256:"
        + hashlib.sha256(
            _json_bytes(
                {
                    "plan_identity": value.get("plan_identity"),
                    "transaction_id": value.get("transaction_id"),
                }
            )
        ).hexdigest()
    )
    if value.get("identity") != expected:
        raise SchemaTransitionError("Schema-transition capsule identity is invalid.")
    if value.get("content_sha256") != _seal(value).get("content_sha256"):
        raise SchemaTransitionError("Schema-transition capsule content integrity is invalid.")
    if value.get("state") not in {"prepared", "activation_staged", "configuration_recovered"}:
        raise SchemaTransitionError("Schema-transition capsule state is invalid.")
    return value


def validate_schema_transition_assembly(
    layout: InstallationLayout,
    active: InstalledActivation,
    candidate: Path,
    *,
    target_core_commit: str,
    target_core_git_tree: str,
    target_python_environment_identity: str,
) -> dict[str, object]:
    capsule = load_schema_transition(layout)
    if capsule["state"] != "prepared":
        raise SchemaTransitionError("Schema-transition capsule is not ready for activation assembly.")
    previous = capsule["previous_configuration_selection"]
    target = capsule["target"]
    if not isinstance(previous, dict) or not isinstance(target, dict):
        raise SchemaTransitionError("Schema-transition capsule selection data is invalid.")
    candidate, deployment_revision = _candidate_deployment(layout, candidate)
    if (
        active.activation_id != capsule["previous_installation_activation_id"]
        or active.record.get("configuration_activation_identity") != previous.get("activation_generation_id")
        or target.get("core_commit") != target_core_commit
        or target.get("core_git_tree") != target_core_git_tree
        or target.get("python_environment_identity") != target_python_environment_identity
        or target.get("household_deployment_revision") != deployment_revision
        or target.get("authored_revision") != snapshot_candidate(candidate).authored_revision
    ):
        raise SchemaTransitionError("Schema-transition capsule does not match the exact installation pair.")
    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    selected = store.load_selected()
    inspection = inspect_candidate(candidate, secret_snapshot=selected.secrets.snapshot)
    if (
        not inspection.report.activation_eligible
        or inspection.normalized is None
        or selected.activation.generation_id == previous.get("activation_generation_id")
        or selected.config.config_revision != target.get("config_revision")
        or inspection.normalized.config_revision != target.get("config_revision")
        or selected.secrets.generation_id != target.get("secret_generation_id")
        or selected.secrets.generation_id != previous.get("secret_generation_id")
        or selected.selection_revision != previous.get("selection_revision", 0) + 1
    ):
        raise SchemaTransitionError("Canonical configuration does not match the explicit schema-transition target.")
    return capsule


def mark_schema_transition_activation_staged(
    layout: InstallationLayout,
    capsule_identity: str,
    activation: InstalledActivation,
) -> dict[str, object]:
    capsule = load_schema_transition(layout)
    if capsule["identity"] != capsule_identity or capsule["state"] != "prepared":
        raise SchemaTransitionError("Schema-transition capsule changed before activation publication.")
    target = capsule["target"]
    if not isinstance(target, dict) or (
        activation.record.get("core", {}).get("commit") != target.get("core_commit")
        or activation.record.get("core", {}).get("git_tree") != target.get("core_git_tree")
        or activation.record.get("python_environment_identity") != target.get("python_environment_identity")
        or activation.record.get("household_deployment_revision") != target.get("household_deployment_revision")
    ):
        raise SchemaTransitionError("Published activation differs from the schema-transition target.")
    selected = GenerationStore(layout.configuration, secret_root=layout.secrets).load_selected()
    if activation.record.get("configuration_activation_identity") != selected.activation.generation_id:
        raise SchemaTransitionError("Published activation does not bind the selected target configuration.")
    basis = {
        **{key: value for key, value in capsule.items() if key != "identity"},
        "state": "activation_staged",
        "target_installation_activation_id": activation.activation_id,
        "target_configuration_activation_id": selected.activation.generation_id,
    }
    updated = {
        **basis,
        "identity": capsule["identity"],
    }
    return _write(layout, updated)


def validate_managed_schema_transition(
    layout: InstallationLayout,
    active: InstalledActivation,
    target_activation: InstalledActivation,
) -> dict[str, object]:
    capsule = load_schema_transition(layout)
    previous = capsule.get("previous_configuration_selection")
    approved = load_selected_activation(layout, "approved")
    if not isinstance(previous, dict) or (
        capsule.get("state") != "activation_staged"
        or capsule.get("previous_installation_activation_id") != active.activation_id
        or capsule.get("target_installation_activation_id") != target_activation.activation_id
        or capsule.get("approved_before_activation_id") != approved.activation_id
        or previous.get("activation_generation_id") != active.record.get("configuration_activation_identity")
    ):
        raise SchemaTransitionError("Managed activation does not match the explicit schema-transition capsule.")
    selected = GenerationStore(layout.configuration, secret_root=layout.secrets).load_selected()
    if target_activation.record.get("configuration_activation_identity") != selected.activation.generation_id:
        raise SchemaTransitionError("Schema-transition target does not bind the selected configuration.")
    return capsule


def restore_schema_transition_configuration(
    layout: InstallationLayout,
    capsule_identity: str,
) -> dict[str, object]:
    capsule = load_schema_transition(layout)
    if capsule["identity"] != capsule_identity or capsule["state"] not in {
        "prepared", "activation_staged", "configuration_recovered",
    }:
        raise SchemaTransitionError("Schema-transition capsule is not recoverable from this state.")
    previous = capsule["previous_configuration_selection"]
    if not isinstance(previous, dict) or not isinstance(previous.get("satellite_projection_activation_ids"), dict):
        raise SchemaTransitionError("Schema-transition recovery selection is invalid.")
    store = GenerationStore(layout.configuration, secret_root=layout.secrets)
    current = store.load_selected()
    target = capsule["target"]
    already_previous = current.activation.generation_id == previous.get("activation_generation_id")
    target_activation_id = capsule.get("target_configuration_activation_id")
    if not isinstance(target, dict) or (not already_previous and (
        current.config.config_revision != target.get("config_revision")
        or current.secrets.generation_id != target.get("secret_generation_id")
        or current.selection_revision != int(previous["selection_revision"]) + 1
        or (
            target_activation_id is not None
            and current.activation.generation_id != target_activation_id
        )
    )):
        raise SchemaTransitionError("Schema-transition target changed before recovery.")
    selected = store.restore_schema_transition_selection(
        str(previous["activation_generation_id"]),
        expected_current_activation_generation_id=current.activation.generation_id,
        previous_selection_revision=int(previous["selection_revision"]),
        satellite_projection_activation_ids=previous["satellite_projection_activation_ids"],
    )
    if (
        selected.activation.generation_id != previous.get("activation_generation_id")
        or selected.config.generation_id != previous.get("config_generation_id")
        or selected.config.config_revision != previous.get("config_revision")
        or selected.secrets.generation_id != previous.get("secret_generation_id")
        or dict(selected.satellite_projection_activation_ids)
        != dict(previous["satellite_projection_activation_ids"])
    ):
        raise SchemaTransitionError("Schema-transition recovery did not restore the complete captured selection.")
    basis = {
        **{key: value for key, value in capsule.items() if key != "identity"},
        "state": "configuration_recovered",
    }
    updated = {
        **basis,
        "identity": capsule["identity"],
    }
    updated = _write(layout, updated)
    return {**updated, "recovered_selection": _selection_snapshot(selected)}


def finish_schema_transition(
    layout: InstallationLayout,
    capsule_identity: str,
    *,
    outcome: str,
) -> dict[str, object]:
    if schema_transition_pending(layout):
        capsule = load_schema_transition(layout)
    else:
        destination = layout.control_state / f"schema-transition-result-{capsule_identity.split(':sha256:')[-1]}.json"
        try:
            result = json.loads(destination.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SchemaTransitionError("Schema-transition capsule is absent or invalid.") from exc
        if (
            not isinstance(result, dict)
            or result.get("identity") != capsule_identity
            or result.get("state") != "complete"
            or result.get("outcome") != outcome
            or result.get("content_sha256") != _seal(result).get("content_sha256")
        ):
            raise SchemaTransitionError("Schema-transition result conflicts with durable evidence.")
        return result
    if capsule["identity"] != capsule_identity:
        raise SchemaTransitionError("Schema-transition capsule changed before finalization.")
    if outcome == "verified" and capsule["state"] != "activation_staged":
        raise SchemaTransitionError("Schema-transition target was not staged before verification.")
    if outcome == "recovered_previous" and capsule["state"] != "configuration_recovered":
        raise SchemaTransitionError("Schema-transition configuration was not recovered before finalization.")
    result = _seal({**capsule, "state": "complete", "outcome": outcome})
    destination = layout.control_state / f"schema-transition-result-{str(capsule['identity']).split(':sha256:')[-1]}.json"
    content = _json_bytes(result)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise SchemaTransitionError("Schema-transition result conflicts with durable evidence.")
    else:
        with destination.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        destination.chmod(0o440)
    _path(layout).unlink()
    _fsync_directory(layout.control_state)
    return result


def recover_schema_transition_before_managed_activation(
    layout: InstallationLayout,
) -> dict[str, object]:
    """Restore a prepared transition before a managed activation was begun."""

    capsule = load_schema_transition(layout)
    active = load_selected_activation(layout)
    if active.activation_id != capsule["previous_installation_activation_id"]:
        raise SchemaTransitionError("Pre-activation schema recovery requires the captured previous installation.")
    if capsule["state"] == "activation_staged":
        staged = load_selected_activation(layout, "staged")
        if staged.activation_id != capsule["target_installation_activation_id"]:
            raise SchemaTransitionError("Staged activation changed before schema-transition recovery.")
        (layout.selection / "staged").unlink()
        _fsync_directory(layout.selection)
    recovered = restore_schema_transition_configuration(layout, str(capsule["identity"]))
    return {
        "capsule_identity": capsule["identity"],
        "previous_installation_activation_id": active.activation_id,
        "configuration_activation_identity": recovered["recovered_selection"]["activation_generation_id"],
    }
