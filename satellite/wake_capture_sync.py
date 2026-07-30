from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import time
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from oracle_runtime_config import KNOWN_SATELLITE_ENV_NAMES
from oracle_satellite_projection import SatelliteProjectionLocalStore
from oracle_satellite_runtime_config import (
    InteractionRuntimeEffectiveConfig,
    load_runtime_compatibility_file,
)
from oracle_satellite_runtime_cutover import resolve_satellite_component_startup
from satellite.wake_capture import (
    WakeCaptureConfig,
    WakeCaptureUploadConfig,
    sync_pending_captures,
    sync_pending_captures_http,
)


SATELLITE_ID_ENV = "ORACLE_SATELLITE_ID"
PROJECTION_STORE_ROOT_ENV = "ORACLE_SATELLITE_PROJECTION_STORE_ROOT"
RUNTIME_COMPATIBILITY_PATH_ENV = "ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH"
_SELECTOR_ENV_NAMES = frozenset(
    {SATELLITE_ID_ENV, PROJECTION_STORE_ROOT_ENV, RUNTIME_COMPATIBILITY_PATH_ENV}
)
_HOST_BOOTSTRAP_ENV_NAMES = frozenset({"ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH"})


class WakeCaptureSyncStartupError(ValueError):
    pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Oracle wake-capture sync utility")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--once", action="store_true", help="Run a single sync pass and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("oracle-wake-capture-sync")
    mode, config = resolve_wake_capture_sync_config()
    if not config.enabled:
        logger.info("Wake capture sync disabled.")
        return 0
    if mode == "legacy_migration" and not config.sync_enabled:
        logger.info("Wake capture sync is not enabled.")
        return 0

    if args.once:
        _sync_once(mode, config, logger)
        return 0

    while True:
        _sync_once(mode, config, logger)
        time.sleep(max(1, int(config.sync_interval_seconds)))


def resolve_wake_capture_sync_config(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, WakeCaptureConfig | WakeCaptureUploadConfig]:
    values = os.environ if environment is None else environment
    store = _load_optional_store(values)
    startup = resolve_satellite_component_startup(
        store,
        "interaction_runtime",
        values,
    )
    if startup.mode != "canonical":
        raise WakeCaptureSyncStartupError(
            "Standard wake-capture sync requires canonical configuration."
        )
    effective = startup.effective_config
    if not isinstance(effective, InteractionRuntimeEffectiveConfig):
        raise WakeCaptureSyncStartupError("Canonical wake-capture configuration is unavailable.")
    _reject_canonical_legacy_environment(values)
    return startup.mode, _canonical_upload_config(effective, values)


def _load_optional_store(
    environment: Mapping[str, str],
) -> SatelliteProjectionLocalStore | None:
    supplied = {name: str(environment.get(name) or "").strip() for name in _SELECTOR_ENV_NAMES}
    present = {name for name, value in supplied.items() if value}
    if not present:
        return None
    if present != _SELECTOR_ENV_NAMES:
        raise WakeCaptureSyncStartupError("Canonical satellite startup selectors must be supplied together.")
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
        raise WakeCaptureSyncStartupError(f"Canonical {label} path must be absolute.")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise WakeCaptureSyncStartupError(f"Canonical {label} path is unavailable.") from exc


def _reject_canonical_legacy_environment(environment: Mapping[str, str]) -> None:
    allowed = _SELECTOR_ENV_NAMES | _HOST_BOOTSTRAP_ENV_NAMES
    rejected = sorted(
        name
        for name in KNOWN_SATELLITE_ENV_NAMES - allowed
        if str(environment.get(name) or "").strip()
    )
    if rejected:
        raise WakeCaptureSyncStartupError(
            "Canonical wake-capture sync rejects legacy behavior environment inputs."
        )


def _canonical_upload_config(
    effective: InteractionRuntimeEffectiveConfig,
    environment: Mapping[str, str],
) -> WakeCaptureUploadConfig:
    interaction = _mapping(effective.configuration, "interaction runtime")
    wake = _mapping(interaction.get("wake"), "wake configuration")
    capture = _mapping(wake.get("capture"), "wake-capture configuration")
    sync = _mapping(capture.get("sync"), "wake-capture sync configuration")
    storage_value = capture.get("local_storage_path")
    if storage_value is None:
        storage_value = environment.get(
            "ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH",
            str(Path(os.getenv("TMPDIR", "/tmp")) / "oracle-wake-capture"),
        )
    return WakeCaptureUploadConfig(
        enabled=_boolean(capture.get("enabled"), "wake capture")
        and _boolean(sync.get("enabled"), "wake-capture sync"),
        satellite_id=_text(effective.satellite_id, "satellite identity"),
        source_id=_text(effective.source_id, "source identity"),
        local_storage_path=Path(_text(storage_value, "wake-capture storage path")),
        brain_base_url=_text(effective.brain_base_url, "Brain endpoint"),
        brain_credential=_text(effective.brain_credential, "Brain credential"),
        sync_interval_seconds=_positive_number(sync.get("interval_seconds"), "sync interval"),
        delete_local_after_sync=_boolean(
            sync.get("delete_local_after_sync"),
            "delete-after-sync policy",
        ),
        synced_local_retention_days=_non_negative_integer(
            sync.get("synced_local_retention_days"),
            "synced-local retention",
        ),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise WakeCaptureSyncStartupError(f"Canonical {label} is invalid.")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WakeCaptureSyncStartupError(f"Canonical {label} is invalid.")
    return value.strip()


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise WakeCaptureSyncStartupError(f"Canonical {label} is invalid.")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise WakeCaptureSyncStartupError(f"Canonical {label} is invalid.")
    return float(value)


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WakeCaptureSyncStartupError(f"Canonical {label} is invalid.")
    return value


def _sync_once(
    mode: str,
    config: WakeCaptureConfig | WakeCaptureUploadConfig,
    logger: logging.Logger,
) -> None:
    if mode == "canonical":
        if not isinstance(config, WakeCaptureUploadConfig):
            raise WakeCaptureSyncStartupError("Canonical wake-capture configuration is invalid.")
        sync_pending_captures_http(config=config, logger=logger)
        return
    if not isinstance(config, WakeCaptureConfig):
        raise WakeCaptureSyncStartupError("Legacy wake-capture configuration is invalid.")
    sync_pending_captures(config=config, logger=logger)


if __name__ == "__main__":
    raise SystemExit(main())
