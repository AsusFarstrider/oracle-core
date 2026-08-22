from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


EVENT_TYPE_ACTIVATION = "activation"
EVENT_TYPE_NEAR_THRESHOLD = "near_threshold"


@dataclass(frozen=True)
class WakeCaptureConfig:
    enabled: bool = False
    source_id: str = "unknown-source"
    capture_activation: bool = True
    capture_near_threshold: bool = True
    pre_roll_ms: int = 2500
    post_roll_ms: int = 1500
    near_threshold_fraction: float = 0.85
    event_cooldown_seconds: float = 3.0
    local_storage_path: Path = Path("/tmp/oracle-wake-capture")
    sync_enabled: bool = False
    sync_interval_seconds: int = 86400
    server_sync_path: str = "/oracle-data/wake-capture"
    delete_local_after_sync: bool = True
    sync_host: str = ""
    sync_user: str = ""
    sync_ssh_key_path: str = ""
    sync_transport: str = "auto"
    synced_local_retention_days: int = 7
    input_gain: float = 1.0


@dataclass
class CaptureEvent:
    event_type: str
    timestamp: float
    source_id: str
    score: float
    playback_active: bool
    ducking_triggered: Optional[bool] = None


@dataclass
class PendingClip:
    event: CaptureEvent
    pre_frames: list[np.ndarray]
    remaining_post_frames: int
    post_frames: list[np.ndarray] = field(default_factory=list)


@dataclass
class NearThresholdPeak:
    timestamp: float
    score: float
    playback_active: bool
    pre_frames: list[np.ndarray]
    ducking_triggered: Optional[bool] = None


@dataclass
class SyncResult:
    synced_files: int = 0
    deleted_local_files: int = 0
    retained_local_files: int = 0


@dataclass(frozen=True)
class WakeCaptureUploadConfig:
    enabled: bool
    satellite_id: str
    source_id: str
    local_storage_path: Path
    brain_base_url: str
    brain_credential: str = field(repr=False)
    sync_interval_seconds: float
    delete_local_after_sync: bool
    synced_local_retention_days: int
