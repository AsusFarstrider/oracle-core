from __future__ import annotations

import os
from pathlib import Path

from .models import WakeCaptureConfig


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_wake_capture_config(args) -> WakeCaptureConfig:
    return WakeCaptureConfig(
        enabled=bool(getattr(args, "wake_capture_enabled", False)),
        source_id=str(getattr(args, "source", "unknown-source")),
        capture_activation=bool(getattr(args, "wake_capture_activation", True)),
        capture_near_threshold=bool(getattr(args, "wake_capture_near_threshold", True)),
        pre_roll_ms=int(getattr(args, "wake_capture_pre_roll_ms", 2500)),
        post_roll_ms=int(getattr(args, "wake_capture_post_roll_ms", 1500)),
        near_threshold_fraction=float(getattr(args, "wake_capture_near_threshold_fraction", 0.85)),
        event_cooldown_seconds=float(getattr(args, "wake_capture_event_cooldown_seconds", 3.0)),
        local_storage_path=Path(str(getattr(args, "wake_capture_local_storage_path", "/tmp/oracle-wake-capture"))),
        sync_enabled=_env_bool("ORACLE_WAKE_CAPTURE_SYNC_ENABLED", False),
        sync_interval_seconds=int(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_INTERVAL_SECONDS", "86400")),
        server_sync_path=str(os.environ.get("ORACLE_WAKE_CAPTURE_SERVER_SYNC_PATH", "/oracle-data/wake-capture")),
        delete_local_after_sync=_env_bool("ORACLE_WAKE_CAPTURE_DELETE_LOCAL_AFTER_SYNC", True),
        sync_host=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_HOST", "")),
        sync_user=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_USER", "")),
        sync_ssh_key_path=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_SSH_KEY_PATH", "")),
        sync_transport=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_TRANSPORT", "auto")).strip().lower(),
        synced_local_retention_days=int(os.environ.get("ORACLE_WAKE_CAPTURE_SYNCED_LOCAL_RETENTION_DAYS", "7")),
        input_gain=float(getattr(args, "input_gain", 1.0)),
    )


def load_wake_capture_config_from_env(*, source_id: str | None = None) -> WakeCaptureConfig:
    env_source_id = source_id or os.environ.get("ORACLE_SOURCE") or os.environ.get("ORACLE_SATELLITE_SOURCE") or "unknown-source"
    return WakeCaptureConfig(
        enabled=_env_bool("ORACLE_WAKE_CAPTURE_ENABLED", False),
        source_id=str(env_source_id),
        capture_activation=_env_bool("ORACLE_WAKE_CAPTURE_ACTIVATION", True),
        capture_near_threshold=_env_bool("ORACLE_WAKE_CAPTURE_NEAR_THRESHOLD", True),
        pre_roll_ms=int(os.environ.get("ORACLE_WAKE_CAPTURE_PRE_ROLL_MS", "2500")),
        post_roll_ms=int(os.environ.get("ORACLE_WAKE_CAPTURE_POST_ROLL_MS", "1500")),
        near_threshold_fraction=float(os.environ.get("ORACLE_WAKE_CAPTURE_NEAR_THRESHOLD_FRACTION", "0.85")),
        event_cooldown_seconds=float(os.environ.get("ORACLE_WAKE_CAPTURE_EVENT_COOLDOWN_SECONDS", "3.0")),
        local_storage_path=Path(os.environ.get("ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH", "/tmp/oracle-wake-capture")),
        sync_enabled=_env_bool("ORACLE_WAKE_CAPTURE_SYNC_ENABLED", False),
        sync_interval_seconds=int(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_INTERVAL_SECONDS", "86400")),
        server_sync_path=str(os.environ.get("ORACLE_WAKE_CAPTURE_SERVER_SYNC_PATH", "/oracle-data/wake-capture")),
        delete_local_after_sync=_env_bool("ORACLE_WAKE_CAPTURE_DELETE_LOCAL_AFTER_SYNC", True),
        sync_host=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_HOST", "")),
        sync_user=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_USER", "")),
        sync_ssh_key_path=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_SSH_KEY_PATH", "")),
        sync_transport=str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_TRANSPORT", "auto")).strip().lower(),
        synced_local_retention_days=int(os.environ.get("ORACLE_WAKE_CAPTURE_SYNCED_LOCAL_RETENTION_DAYS", "7")),
        input_gain=float(os.environ.get("ORACLE_INPUT_GAIN", "1.0")),
    )
