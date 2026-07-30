from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import Mapping, Sequence

from oracle_runtime_config import KNOWN_SATELLITE_ENV_NAMES
from oracle_satellite_projection import SatelliteProjectionLocalStore
from oracle_satellite_runtime_config import (
    InteractionRuntimeEffectiveConfig,
    load_runtime_compatibility_file,
)
from oracle_satellite_runtime_cutover import resolve_satellite_component_startup

from .host_tools import (
    DEFAULT_ALARM_SOUND_PATH,
    DEFAULT_TIMER_SOUND_PATH,
    MODEL_DIR,
)
from .settings import InteractionRuntimeHostBootstrap, InteractionRuntimeSettings


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
        "ORACLE_SATELLITE_CONFIG_BIND_HOST",
        "ORACLE_SATELLITE_CONFIG_BIND_PORT",
        "ORACLE_REPLY_AUDIO_STATE_PATH",
        "ORACLE_REPLY_AUDIO_STOP_PATH",
        "ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH",
    }
)


class InteractionRuntimeStartupError(ValueError):
    pass


def resolve_interaction_runtime_settings(
    *,
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> InteractionRuntimeSettings:
    values = os.environ if environment is None else environment
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    store = _load_optional_store(values)
    startup = resolve_satellite_component_startup(
        store,
        "interaction_runtime",
        values,
    )
    if startup.mode != "canonical":
        raise InteractionRuntimeStartupError(
            "Standard interaction runtime requires canonical configuration."
        )
    effective = startup.effective_config
    if not isinstance(effective, InteractionRuntimeEffectiveConfig):
        raise InteractionRuntimeStartupError("Canonical interaction configuration is unavailable.")
    command_mode = _canonical_command_mode(arguments)
    _reject_canonical_legacy_environment(values)
    return InteractionRuntimeSettings.from_canonical(
        effective,
        _host_bootstrap(values, effective, list_devices=command_mode == "list_devices"),
    )


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
        raise InteractionRuntimeStartupError(
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
        raise InteractionRuntimeStartupError(f"Canonical {label} path must be absolute.")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise InteractionRuntimeStartupError(f"Canonical {label} path is unavailable.") from exc


def _canonical_command_mode(arguments: list[str]) -> str:
    if not arguments:
        return "run"
    if arguments == ["--list-devices"]:
        return "list_devices"
    raise InteractionRuntimeStartupError(
        "Canonical interaction startup rejects legacy behavior arguments."
    )


def _reject_canonical_legacy_environment(environment: Mapping[str, str]) -> None:
    allowed = _CANONICAL_SELECTOR_ENV_NAMES | _CANONICAL_HOST_BOOTSTRAP_ENV_NAMES
    rejected = sorted(
        name
        for name in KNOWN_SATELLITE_ENV_NAMES - allowed
        if str(environment.get(name) or "").strip()
    )
    if rejected:
        raise InteractionRuntimeStartupError(
            "Canonical interaction startup rejects legacy behavior environment inputs."
        )


def _host_bootstrap(
    environment: Mapping[str, str],
    effective: InteractionRuntimeEffectiveConfig,
    *,
    list_devices: bool,
) -> InteractionRuntimeHostBootstrap:
    temporary = Path(tempfile.gettempdir())
    capture_default = _environment_text(
        environment,
        "ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH",
        str(temporary / "oracle-wake-capture"),
    )
    return InteractionRuntimeHostBootstrap(
        config_bind_host=_environment_text(
            environment,
            "ORACLE_SATELLITE_CONFIG_BIND_HOST",
            "0.0.0.0",
        ),
        config_bind_port=_environment_port(
            environment,
            "ORACLE_SATELLITE_CONFIG_BIND_PORT",
            8022,
        ),
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
        packaged_asset_paths=_packaged_asset_paths(effective),
        wake_capture_default_storage_path=capture_default,
        list_devices=list_devices,
    )


def _packaged_asset_paths(
    effective: InteractionRuntimeEffectiveConfig,
) -> dict[str, str]:
    assets = {
        "alarm": _installed_asset(DEFAULT_ALARM_SOUND_PATH, "alarm"),
        "timer": _installed_asset(DEFAULT_TIMER_SOUND_PATH, "timer"),
    }
    wake = effective.configuration.get("wake")
    if isinstance(wake, Mapping):
        model = wake.get("model")
        if isinstance(model, Mapping) and model.get("asset_id") == "hey_oracle":
            model_format = str(model.get("format") or "").strip()
            if model_format in {"onnx", "tflite"}:
                assets["hey_oracle"] = _installed_asset(
                    MODEL_DIR / f"hey_oracle.{model_format}",
                    "wake model",
                )
    return assets


def _installed_asset(path: Path, label: str) -> str:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise InteractionRuntimeStartupError(
            f"Canonical packaged {label} asset is unavailable."
        ) from exc
    if not resolved.is_file():
        raise InteractionRuntimeStartupError(
            f"Canonical packaged {label} asset is unavailable."
        )
    return str(resolved)


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
        raise InteractionRuntimeStartupError("Canonical listener port is invalid.") from exc
    if value < 1 or value > 65535:
        raise InteractionRuntimeStartupError("Canonical listener port is invalid.")
    return value
