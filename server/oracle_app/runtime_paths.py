"""Fixed writable-path bindings for development and standard installations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


STANDARD_INSTALLATION_ENV = "ORACLE_STANDARD_INSTALLATION"
STANDARD_INSTALLATION_ROOT = Path("/srv/oracle")
DEVELOPMENT_APPLICATION_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RuntimePathBindings:
    standard_installation: bool
    application_root: Path
    data: Path
    cache: Path
    tmp: Path

    @property
    def memory_database(self) -> Path:
        return self.data / "oracle-memory.sqlite3"

    @property
    def provisional_suggestions_database(self) -> Path:
        return self.data / "openclaw_suggestions.sqlite3"

    @property
    def alerts_state(self) -> Path:
        return self.data / "alerts-state.json"

    @property
    def home_assistant_cache(self) -> Path:
        return self.cache / "home-assistant-cache.json"

    @property
    def facts_cache(self) -> Path:
        return self.cache / "facts-cache.json"

    @property
    def tts_cache(self) -> Path:
        return self.cache / "tts-cache"

    @property
    def local_host_restart_state(self) -> Path:
        return self.data / "network-local-restart.json"

    @property
    def local_service_restart_state(self) -> Path:
        return self.data / "network-local-service-restart.json"

    @property
    def last_suggestions_packet(self) -> Path:
        return self.data / "last_openclaw_packet.json"

    @property
    def last_suggestions_response(self) -> Path:
        return self.data / "last_openclaw_response.json"


def resolve_runtime_paths(
    environment: Mapping[str, str] | None = None,
    *,
    standard_root: Path = STANDARD_INSTALLATION_ROOT,
    development_root: Path = DEVELOPMENT_APPLICATION_ROOT,
) -> RuntimePathBindings:
    """Resolve only Oracle's two ratified runtime postures.

    ``standard_root`` and ``development_root`` are injectable for tests; the
    installed process has no environment or CLI option for relocating either
    posture.
    """

    values = os.environ if environment is None else environment
    if values.get(STANDARD_INSTALLATION_ENV) == "1":
        root = Path(standard_root)
        return RuntimePathBindings(True, root, root / "data", root / "cache", root / "tmp")
    root = Path(development_root)
    # Preserve Phil's existing source-tree defaults exactly. Runtime caches
    # historically share the development data directory.
    return RuntimePathBindings(False, root, root / "data", root / "data", Path("/tmp"))


RUNTIME_PATHS = resolve_runtime_paths()


def validate_standard_storage_settings(
    memory_database_path: str,
    alerts_state_path: str,
) -> None:
    """Reject canonical storage settings that contradict the fixed layout."""
    expected = {
        "storage.memory.database_path": "data/oracle-memory.sqlite3",
        "storage.alerts.state_path": "data/alerts-state.json",
    }
    actual = {
        "storage.memory.database_path": str(memory_database_path),
        "storage.alerts.state_path": str(alerts_state_path),
    }
    conflicts = [
        f"{name} must be {expected[name]!r}, not {value!r}"
        for name, value in actual.items()
        if value != expected[name]
    ]
    if conflicts:
        raise ValueError(
            "Standard Oracle storage settings must use the managed /srv/oracle data paths: "
            + "; ".join(conflicts)
        )
