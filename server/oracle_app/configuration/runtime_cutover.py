from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import secrets
from typing import Literal

from .generations import (
    GenerationIntegrityError,
    GenerationStore,
    SelectedActivation,
    _fsync_directory,
    _json_bytes,
    _read_json,
    _write_new,
)


RUNTIME_CUTOVER_FORMAT = "oracle-runtime-cutover-v1"
RUNTIME_CUTOVER_PATH = "canonical-runtime-required.json"
_EVENT_ID = re.compile(r"^audit_[0-9a-f]{32}$")
_ACTIVATION_ID = re.compile(r"^activation_[0-9a-f]{32}$")
_CONFIG_ID = re.compile(r"^config_[0-9a-f]{32}$")
_SECRET_ID = re.compile(r"^secret_[0-9a-f]{32}$")
CutoverActor = Literal["service", "host_local_cli", "system_mode"]


@dataclass(frozen=True)
class RuntimeCutoverMarker:
    format: Literal["oracle-runtime-cutover-v1"]
    bundle_id: str
    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    config_revision: str
    selection_revision: int
    audit_event_id: str
    actor: CutoverActor
    committed_at: str


def runtime_cutover_required(store: GenerationStore) -> bool:
    path = store.root / RUNTIME_CUTOVER_PATH
    if not path.exists() and not path.is_symlink():
        return False
    load_runtime_cutover_marker(store)
    return True


def load_runtime_cutover_marker(store: GenerationStore) -> RuntimeCutoverMarker:
    path = store.root / RUNTIME_CUTOVER_PATH
    payload = _read_json(path)
    fields = set(RuntimeCutoverMarker.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != fields:
        raise GenerationIntegrityError("Canonical runtime cutover marker has an invalid shape.")
    try:
        marker = RuntimeCutoverMarker(**payload)
    except TypeError as exc:
        raise GenerationIntegrityError("Canonical runtime cutover marker is invalid.") from exc
    if marker.format != RUNTIME_CUTOVER_FORMAT:
        raise GenerationIntegrityError("Canonical runtime cutover marker format is unsupported.")
    if marker.bundle_id != store.validate_initialized():
        raise GenerationIntegrityError("Canonical runtime cutover marker has the wrong bundle lineage.")
    if (
        _ACTIVATION_ID.fullmatch(marker.activation_generation_id) is None
        or _CONFIG_ID.fullmatch(marker.config_generation_id) is None
        or _SECRET_ID.fullmatch(marker.secret_generation_id) is None
        or _EVENT_ID.fullmatch(marker.audit_event_id) is None
        or marker.actor not in {"service", "host_local_cli", "system_mode"}
        or not isinstance(marker.selection_revision, int)
        or marker.selection_revision < 1
        or not isinstance(marker.config_revision, str)
        or not marker.config_revision.startswith("oracle-config-v1:sha256:")
        or not isinstance(marker.committed_at, str)
        or not marker.committed_at
    ):
        raise GenerationIntegrityError("Canonical runtime cutover marker fields are invalid.")
    return marker


def arm_runtime_cutover(
    store: GenerationStore,
    selected: SelectedActivation,
    *,
    actor: CutoverActor,
    audit_event_id: str | None = None,
    committed_at: str | None = None,
) -> tuple[RuntimeCutoverMarker, bool]:
    """Durably require canonical startup. Returns marker and whether it was created."""

    path = store.root / RUNTIME_CUTOVER_PATH
    if path.exists() or path.is_symlink():
        return load_runtime_cutover_marker(store), False
    current = store.load_selected()
    if current.activation.generation_id != selected.activation.generation_id:
        raise GenerationIntegrityError("Selected activation changed before runtime cutover was armed.")
    marker = RuntimeCutoverMarker(
        format=RUNTIME_CUTOVER_FORMAT,
        bundle_id=current.config.bundle_id,
        activation_generation_id=current.activation.generation_id,
        config_generation_id=current.config.generation_id,
        secret_generation_id=current.secrets.generation_id,
        config_revision=current.config.config_revision,
        selection_revision=current.selection_revision,
        audit_event_id=audit_event_id or f"audit_{secrets.token_hex(16)}",
        actor=actor,
        committed_at=committed_at or datetime.now(UTC).isoformat(),
    )
    _write_new(path, _json_bytes(asdict(marker)), mode=0o600)
    _fsync_directory(Path(store.root))
    return marker, True
