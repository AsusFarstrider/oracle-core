from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re

from .generation_storage import GenerationIntegrityError, _atomic_replace, _read_json
from .generations import GenerationStore
from .normalization import canonicalize_json
from .projections import SatelliteRuntimeCompatibility


@dataclass(frozen=True)
class AcceptedSatelliteRuntimeCompatibility:
    satellite_id: str
    accepted_at: str
    report: SatelliteRuntimeCompatibility


class SatelliteRuntimeCompatibilityStore:
    """Own accepted satellite compatibility reports beside installed generations."""

    FORMAT = "oracle-satellite-runtime-compatibility-v1"
    _SATELLITE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")

    def __init__(self, store: GenerationStore) -> None:
        store.validate_initialized()
        self.store = store
        self.directory = Path(store.root) / "runtime-compatibility"

    def accept(
        self,
        satellite_id: str,
        report: SatelliteRuntimeCompatibility,
        *,
        accepted_at: str | None = None,
    ) -> AcceptedSatelliteRuntimeCompatibility:
        path = self._path(satellite_id)
        self.directory.mkdir(mode=self.store.configuration_directory_mode, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationIntegrityError("Runtime compatibility state escapes the installed store.")
        timestamp = accepted_at or datetime.now(UTC).isoformat()
        payload = {
            "format": self.FORMAT,
            "satellite_id": satellite_id,
            "accepted_at": timestamp,
            "report": report.model_dump(mode="json"),
        }
        _atomic_replace(path, canonicalize_json(payload), mode=self.store.configuration_file_mode)
        return AcceptedSatelliteRuntimeCompatibility(satellite_id, timestamp, report)

    def load(self, satellite_id: str) -> AcceptedSatelliteRuntimeCompatibility | None:
        path = self._path(satellite_id)
        if not path.exists() and not path.is_symlink():
            return None
        payload = _read_json(path)
        if not isinstance(payload, dict) or set(payload) != {"format", "satellite_id", "accepted_at", "report"}:
            raise GenerationIntegrityError("Runtime compatibility report has an invalid envelope.")
        if payload["format"] != self.FORMAT or payload["satellite_id"] != satellite_id:
            raise GenerationIntegrityError("Runtime compatibility report identity is invalid.")
        if not isinstance(payload["accepted_at"], str) or not payload["accepted_at"]:
            raise GenerationIntegrityError("Runtime compatibility acceptance time is invalid.")
        try:
            report = SatelliteRuntimeCompatibility.model_validate(payload["report"])
        except Exception as exc:
            raise GenerationIntegrityError("Runtime compatibility report is invalid.") from exc
        return AcceptedSatelliteRuntimeCompatibility(satellite_id, payload["accepted_at"], report)

    def _path(self, satellite_id: str) -> Path:
        if self._SATELLITE_ID.fullmatch(satellite_id) is None:
            raise ValueError("Satellite compatibility identity is invalid.")
        return self.directory / f"{satellite_id}.json"
