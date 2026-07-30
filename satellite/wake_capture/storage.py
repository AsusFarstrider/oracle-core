from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import wave

import numpy as np

from .models import CaptureEvent, PendingClip


def _utc_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _score_slug(score: float) -> str:
    return f"{score:.3f}".rstrip("0").rstrip(".")


def _base_name(event: CaptureEvent) -> str:
    ts = _utc_datetime(event.timestamp).strftime("%Y%m%dT%H%M%S.%fZ")
    playback = 1 if event.playback_active else 0
    return f"{ts}_score-{_score_slug(event.score)}_playback-{playback}"


def pending_root(local_storage_path: Path) -> Path:
    return local_storage_path / "pending"


def synced_root(local_storage_path: Path) -> Path:
    return local_storage_path / "synced"


def event_relative_dir(event: CaptureEvent) -> Path:
    day = _utc_datetime(event.timestamp).strftime("%Y-%m-%d")
    return Path(event.source_id) / day / event.event_type


def write_pending_clip(*, local_storage_path: Path, clip: PendingClip, sample_rate: int) -> tuple[Path, Path]:
    event = clip.event
    base_dir = pending_root(local_storage_path) / event_relative_dir(event)
    base_dir.mkdir(parents=True, exist_ok=True)
    base_name = _base_name(event)
    wav_path = base_dir / f"{base_name}.wav"
    json_path = base_dir / f"{base_name}.json"

    all_frames = clip.pre_frames + clip.post_frames
    pcm = np.concatenate(all_frames).astype(np.int16).tobytes() if all_frames else b""
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)

    metadata = asdict(event)
    metadata["timestamp_iso"] = _utc_datetime(event.timestamp).isoformat()
    metadata["sample_rate"] = sample_rate
    metadata["channels"] = 1
    metadata["sample_width_bytes"] = 2
    metadata["format"] = "wav_pcm_s16le"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return wav_path, json_path


def iter_pending_files(local_storage_path: Path) -> list[Path]:
    root = pending_root(local_storage_path)
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def relative_pending_path(local_storage_path: Path, path: Path) -> Path:
    return path.relative_to(pending_root(local_storage_path))


def relative_synced_path(local_storage_path: Path, path: Path) -> Path:
    return path.relative_to(synced_root(local_storage_path))
