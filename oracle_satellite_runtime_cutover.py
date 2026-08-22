from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Literal, Mapping, TextIO

import rfc8785

from oracle_satellite_projection import (
    SatelliteProjectionIntegrityError,
    SatelliteProjectionLocalStore,
)
from oracle_satellite_runtime_config import (
    ControlServiceEffectiveConfig,
    InteractionRuntimeEffectiveConfig,
    build_control_service_effective_config,
    build_interaction_runtime_effective_config,
    load_control_service_effective_config,
    load_interaction_runtime_effective_config,
    load_runtime_compatibility_file,
)


CUTOVER_FORMAT = "oracle-satellite-runtime-cutover-v1"
CUTOVER_PATH = "canonical-runtime-required.json"
LEGACY_CONFIGURATION_ENV_NAME = "ORACLE_ALLOW_LEGACY_SATELLITE_CONFIGURATION"
_ACTIVATION_ID = re.compile(r"^sat_activation_[0-9a-f]{32}$")
_PROJECTION_REVISION = re.compile(r"^oracle-projection-v1:sha256:[0-9a-f]{64}$")


class SatelliteRuntimeCutoverError(SatelliteProjectionIntegrityError):
    pass


@dataclass(frozen=True)
class SatelliteRuntimeCutoverMarker:
    format: Literal["oracle-satellite-runtime-cutover-v1"]
    satellite_id: str
    activation_id: str
    projection_revision: str


@dataclass(frozen=True)
class SatelliteComponentStartup:
    mode: Literal["legacy_migration", "canonical"]
    effective_config: InteractionRuntimeEffectiveConfig | ControlServiceEffectiveConfig | None


def satellite_runtime_cutover_required(store: SatelliteProjectionLocalStore) -> bool:
    path = store.root / CUTOVER_PATH
    if not path.exists() and not path.is_symlink():
        return False
    load_satellite_runtime_cutover_marker(store)
    return True


def load_satellite_runtime_cutover_marker(
    store: SatelliteProjectionLocalStore,
) -> SatelliteRuntimeCutoverMarker:
    path = store.root / CUTOVER_PATH
    if path.is_symlink() or not path.is_file():
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover marker is invalid.")
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover marker is invalid.") from exc
    fields = set(SatelliteRuntimeCutoverMarker.__dataclass_fields__)
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or rfc8785.dumps(payload) != encoded
    ):
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover marker is invalid.")
    try:
        marker = SatelliteRuntimeCutoverMarker(**payload)
    except TypeError as exc:
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover marker is invalid.") from exc
    if (
        marker.format != CUTOVER_FORMAT
        or marker.satellite_id != store.satellite_id
        or _ACTIVATION_ID.fullmatch(marker.activation_id) is None
        or _PROJECTION_REVISION.fullmatch(marker.projection_revision) is None
    ):
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover marker is invalid.")
    return marker


def arm_satellite_runtime_cutover(
    store: SatelliteProjectionLocalStore,
    *,
    acknowledge_one_way: bool,
    interaction_runtime_installed: bool,
    control_service_installed: bool,
) -> tuple[SatelliteRuntimeCutoverMarker, bool]:
    if not acknowledge_one_way:
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover requires one-way acknowledgement.")
    if not interaction_runtime_installed and not control_service_installed:
        raise SatelliteRuntimeCutoverError("Satellite runtime cutover requires an installed runtime component.")
    path = store.root / CUTOVER_PATH
    if path.exists() or path.is_symlink():
        return load_satellite_runtime_cutover_marker(store), False

    selected = store.load_selected()
    if selected is None:  # pragma: no cover - required by the optional return type
        raise SatelliteRuntimeCutoverError("No local satellite activation is selected.")
    if interaction_runtime_installed:
        build_interaction_runtime_effective_config(selected)
    if control_service_installed:
        build_control_service_effective_config(selected)
    current = store.load_selected()
    if current is None or (
        current.activation_id != selected.activation_id
        or current.selection_operation_id != selected.selection_operation_id
        or current.selection_revision != selected.selection_revision
    ):
        raise SatelliteRuntimeCutoverError("Local satellite selection changed during cutover validation.")

    marker = SatelliteRuntimeCutoverMarker(
        format=CUTOVER_FORMAT,
        satellite_id=store.satellite_id,
        activation_id=selected.activation_id,
        projection_revision=selected.activation.projection_revision,
    )
    try:
        _write_new(path, rfc8785.dumps(asdict(marker)))
    except FileExistsError:
        return load_satellite_runtime_cutover_marker(store), False
    _fsync_directory(store.root)
    return marker, True


def resolve_satellite_component_startup(
    store: SatelliteProjectionLocalStore | None,
    component: Literal["interaction_runtime", "control_service"],
    environment: Mapping[str, str] | None = None,
) -> SatelliteComponentStartup:
    if component not in {"interaction_runtime", "control_service"}:
        raise SatelliteRuntimeCutoverError("Satellite runtime component is unsupported.")
    values = os.environ if environment is None else environment
    legacy_value = values.get(LEGACY_CONFIGURATION_ENV_NAME)
    if legacy_value is not None and legacy_value != "1":
        raise SatelliteRuntimeCutoverError(
            f"{LEGACY_CONFIGURATION_ENV_NAME} must be exactly 1 when supplied."
        )
    legacy_allowed = legacy_value == "1"
    if store is None:
        if legacy_allowed:
            return SatelliteComponentStartup("legacy_migration", None)
        raise SatelliteRuntimeCutoverError(
            "Satellite startup has neither canonical bootstrap nor explicit legacy migration permission."
        )

    canonical_required = satellite_runtime_cutover_required(store)
    if canonical_required:
        if legacy_allowed:
            raise SatelliteRuntimeCutoverError(
                "Canonical satellite runtime is permanently required; remove the stale legacy migration permission."
            )
        if component == "interaction_runtime":
            effective = load_interaction_runtime_effective_config(store)
        else:
            effective = load_control_service_effective_config(store)
        return SatelliteComponentStartup("canonical", effective)
    if not legacy_allowed:
        raise SatelliteRuntimeCutoverError(
            "Canonical satellite runtime has not been armed and explicit legacy migration permission is absent."
        )
    return SatelliteComponentStartup("legacy_migration", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Permanently require canonical configuration for one satellite installation."
    )
    parser.add_argument("--satellite-id", required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--runtime-compatibility", type=Path, required=True)
    parser.add_argument("--interaction-runtime-installed", action="store_true")
    parser.add_argument("--control-service-installed", action="store_true")
    parser.add_argument("--acknowledge-one-way", action="store_true")
    args = parser.parse_args(argv)
    try:
        store_root = args.store_root.resolve(strict=True)
        metadata_path = store_root / "store.json"
        if not store_root.is_dir() or metadata_path.is_symlink() or not metadata_path.is_file():
            raise SatelliteRuntimeCutoverError(
                "Satellite runtime cutover requires an existing local projection store."
            )
        store = SatelliteProjectionLocalStore(
            store_root,
            satellite_id=args.satellite_id,
            runtime_compatibility=load_runtime_compatibility_file(args.runtime_compatibility),
        )
        marker, created = arm_satellite_runtime_cutover(
            store,
            acknowledge_one_way=args.acknowledge_one_way,
            interaction_runtime_installed=args.interaction_runtime_installed,
            control_service_installed=args.control_service_installed,
        )
    except (SatelliteProjectionIntegrityError, OSError, ValueError):
        _write_json(
            sys.stderr,
            {
                "ok": False,
                "code": "satellite_runtime_cutover_failed",
                "message": "Satellite runtime cutover validation failed.",
            },
        )
        return 1
    _write_json(
        sys.stdout,
        {
            "ok": True,
            "status": "armed" if created else "already_required",
            **asdict(marker),
        },
    )
    return 0


def _write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_json(stream: TextIO, payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
