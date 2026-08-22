from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence

from oracle_runtime_config import KNOWN_CONTROL_SERVICE_ENV_NAMES
from oracle_satellite_projection import SatelliteProjectionLocalStore
from oracle_satellite_runtime_config import (
    ControlServiceEffectiveConfig,
    load_runtime_compatibility_file,
)
from oracle_satellite_runtime_cutover import resolve_satellite_component_startup

from .settings import ControlServiceHostBootstrap, ControlServiceSettings


SATELLITE_ID_ENV = "ORACLE_SATELLITE_ID"
PROJECTION_STORE_ROOT_ENV = "ORACLE_SATELLITE_PROJECTION_STORE_ROOT"
RUNTIME_COMPATIBILITY_PATH_ENV = "ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH"

_CANONICAL_SELECTOR_ENV_NAMES = frozenset(
    {
        SATELLITE_ID_ENV,
        PROJECTION_STORE_ROOT_ENV,
        RUNTIME_COMPATIBILITY_PATH_ENV,
    }
)
_CANONICAL_HOST_BOOTSTRAP_ENV_NAMES = frozenset(
    {
        "ORACLE_SATELLITE_CONTROL_BIND_HOST",
        "ORACLE_SATELLITE_CONTROL_BIND_PORT",
        "ORACLE_SATELLITE_CONTROL_LOG_LEVEL",
        "ORACLE_REPLY_AUDIO_STATE_PATH",
        "ORACLE_REPLY_AUDIO_STOP_PATH",
    }
)
LONGFORM_PLAYER = Path(__file__).resolve().parents[1] / "longform_player.py"


class ControlServiceStartupError(ValueError):
    pass


def resolve_control_service_settings(
    *,
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> ControlServiceSettings:
    values = os.environ if environment is None else environment
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    store = _load_optional_store(values)
    startup = resolve_satellite_component_startup(store, "control_service", values)
    if startup.mode != "canonical":
        raise ControlServiceStartupError(
            "Standard control-service runtime requires canonical configuration."
        )
    effective = startup.effective_config
    if not isinstance(effective, ControlServiceEffectiveConfig):
        raise ControlServiceStartupError("Canonical control-service configuration is unavailable.")
    if arguments:
        raise ControlServiceStartupError(
            "Canonical control-service startup rejects legacy behavior arguments."
        )
    _reject_canonical_legacy_environment(values)
    return ControlServiceSettings.from_canonical(effective, _host_bootstrap(values))


def _load_optional_store(
    environment: Mapping[str, str],
) -> SatelliteProjectionLocalStore | None:
    supplied = {
        name: str(environment.get(name) or "").strip()
        for name in _CANONICAL_SELECTOR_ENV_NAMES
    }
    present = {name for name, value in supplied.items() if value}
    if not present:
        return None
    if present != _CANONICAL_SELECTOR_ENV_NAMES:
        raise ControlServiceStartupError(
            "Canonical satellite startup selectors must be supplied together."
        )
    store_root = _existing_path(supplied[PROJECTION_STORE_ROOT_ENV], "projection store")
    compatibility_path = _existing_path(
        supplied[RUNTIME_COMPATIBILITY_PATH_ENV],
        "runtime compatibility",
    )
    return SatelliteProjectionLocalStore(
        store_root,
        satellite_id=supplied[SATELLITE_ID_ENV],
        runtime_compatibility=load_runtime_compatibility_file(compatibility_path),
    )


def _existing_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ControlServiceStartupError(f"Canonical {label} path must be absolute.")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ControlServiceStartupError(f"Canonical {label} path is unavailable.") from exc


def _reject_canonical_legacy_environment(environment: Mapping[str, str]) -> None:
    allowed = _CANONICAL_SELECTOR_ENV_NAMES | _CANONICAL_HOST_BOOTSTRAP_ENV_NAMES
    rejected = sorted(
        name
        for name in KNOWN_CONTROL_SERVICE_ENV_NAMES - allowed
        if str(environment.get(name) or "").strip()
    )
    if rejected:
        raise ControlServiceStartupError(
            "Canonical control-service startup rejects legacy behavior environment inputs."
        )


def _host_bootstrap(environment: Mapping[str, str]) -> ControlServiceHostBootstrap:
    temporary = Path(tempfile.gettempdir())
    commands = _longform_commands()
    return ControlServiceHostBootstrap(
        bind_host=_environment_text(
            environment,
            "ORACLE_SATELLITE_CONTROL_BIND_HOST",
            "0.0.0.0",
        ),
        bind_port=_environment_port(
            environment,
            "ORACLE_SATELLITE_CONTROL_BIND_PORT",
            8021,
        ),
        oracle_native_music_player_bin="auto",
        **commands,
        reply_audio_state_path=_environment_text(
            environment,
            "ORACLE_REPLY_AUDIO_STATE_PATH",
            str(temporary / "oracle-reply-audio-state.json"),
        ),
        reply_audio_stop_path=_environment_text(
            environment,
            "ORACLE_REPLY_AUDIO_STOP_PATH",
            str(temporary / "oracle-reply-audio-stop.flag"),
        ),
        log_level=_environment_text(
            environment,
            "ORACLE_SATELLITE_CONTROL_LOG_LEVEL",
            "INFO",
        ),
    )


def _longform_commands() -> dict[str, str]:
    try:
        script = LONGFORM_PLAYER.resolve(strict=True)
    except OSError as exc:
        raise ControlServiceStartupError(
            "Canonical packaged long-form player is unavailable."
        ) from exc
    if not script.is_file():
        raise ControlServiceStartupError(
            "Canonical packaged long-form player is unavailable."
        )
    prefix = [sys.executable, str(script)]
    return {
        "play_longform_audio_cmd": _shell_command(
            [*prefix, "play", "--manifest", "{manifest_path}", "--player-bin", "auto"]
        ),
        "pause_longform_audio_cmd": _shell_command([*prefix, "pause"]),
        "resume_longform_audio_cmd": _shell_command(
            [*prefix, "resume", "--player-bin", "auto"]
        ),
        "stop_longform_audio_cmd": _shell_command([*prefix, "stop"]),
        "seek_longform_audio_cmd": _shell_command(
            [*prefix, "seek", "--position-seconds", "{position_seconds}", "--player-bin", "auto"]
        ),
        "longform_state_cmd": _shell_command([*prefix, "state"]),
    }


def _shell_command(arguments: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def _environment_text(
    environment: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    value = str(environment.get(name) or "").strip()
    return value or default


def _environment_port(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = str(environment.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ControlServiceStartupError("Canonical listener port is invalid.") from exc
    if value < 1 or value > 65535:
        raise ControlServiceStartupError("Canonical listener port is invalid.")
    return value
