from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from oracle_runtime_config import (
    KNOWN_SATELLITE_ENV_NAMES,
    SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES,
)

from .audio import open_input_stream, resolve_audio_input_config, resolve_audio_output_config
from .wake import FRAME_LENGTH, SAMPLE_RATE

_SATELLITE_DEPRECATED_ENV_NAMES = {
    "ORACLE_URL": "Use ORACLE_BRAIN_URL instead.",
    "ORACLE_SATELLITE_SOURCE": "Use ORACLE_SOURCE instead.",
    "ORACLE_MUSIC_CONTROL_URL": "A later naming phase should replace this with a control-service-oriented name.",
    "ORACLE_MUSIC_API_KEY": "A later naming phase should replace this with a control-service-oriented name.",
}

def build_satellite_runtime_report(
    args,
    *,
    probe_audio_input: bool = False,
    logger: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    oracle_url = str(getattr(args, "oracle_url", "") or "").strip()
    parsed_url = urlsplit(oracle_url)
    if not oracle_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "oracle_url",
                "severity": "error",
                "status": "missing_required_config",
                "effective_source": "argv",
                "message": "Pi satellite requires a valid http(s) brain URL.",
            }
        )

    source = str(getattr(args, "source", "") or "").strip()
    if not source:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "source",
                "severity": "error",
                "status": "missing_required_config",
                "effective_source": "argv",
                "message": "Pi satellite source must not be empty.",
            }
        )

    wake_capture_sync_transport = str(os.environ.get("ORACLE_WAKE_CAPTURE_SYNC_TRANSPORT", "auto")).strip().lower()
    if wake_capture_sync_transport not in {"auto", "rsync", "scp"}:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "wake_capture_sync_transport",
                "severity": "error",
                "status": "invalid_required_config",
                "effective_source": "environment",
                "message": "Wake capture sync transport must be auto, rsync, or scp.",
            }
        )

    model_path = Path(str(getattr(args, "model_path", "") or "").strip())
    if not model_path.exists():
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "model_path",
                "severity": "error",
                "status": "missing_required_config",
                "effective_source": "argv",
                "message": f"Wake model file not found: {model_path}",
            }
        )

    for setting_name, message in (
        ("wake_threshold", "Wake threshold must be between 0.0 and 1.0."),
        ("wake_log_threshold", "Wake log threshold must be between 0.0 and 1.0."),
        ("wake_playback_threshold", "Playback wake threshold must be between 0.0 and 1.0."),
        ("wake_playback_log_threshold", "Playback wake log threshold must be between 0.0 and 1.0."),
        ("music_duck_trigger_threshold", "Music duck trigger threshold must be between 0.0 and 1.0."),
        ("wake_capture_near_threshold_fraction", "Wake capture near-threshold fraction must be between 0.0 and 1.0."),
    ):
        try:
            numeric = float(getattr(args, setting_name))
        except (TypeError, ValueError, AttributeError):
            numeric = -1.0
        if numeric < 0.0 or numeric > 1.0:
            findings.append(
                {
                    "subsystem": "pi_satellite",
                    "setting": setting_name,
                    "severity": "error",
                    "status": "invalid_required_config",
                    "effective_source": "argv",
                    "message": message,
                }
            )

    for setting_name in (
        "wake_cooldown_seconds",
        "wake_retry_cooldown_seconds",
        "input_gain",
        "playback_gain",
        "conversation_timeout_seconds",
        "alerts_poll_seconds",
        "silence_seconds",
        "post_playback_block_seconds",
        "max_record_seconds",
        "min_speech_seconds",
        "followup_silence_seconds",
        "followup_max_record_seconds",
        "followup_speech_start_timeout_seconds",
        "wake_playback_poll_seconds",
        "wake_playback_hold_seconds",
        "music_duck_max_seconds",
        "playback_interrupt_settle_seconds",
        "wake_capture_event_cooldown_seconds",
    ):
        try:
            numeric = float(getattr(args, setting_name))
        except (TypeError, ValueError, AttributeError):
            numeric = -1.0
        if numeric < 0.0:
            findings.append(
                {
                    "subsystem": "pi_satellite",
                    "setting": setting_name,
                    "severity": "error",
                    "status": "invalid_required_config",
                    "effective_source": "argv",
                    "message": f"{setting_name} must be non-negative.",
                }
            )

    try:
        consecutive_frames = int(getattr(args, "wake_playback_consecutive_frames"))
    except (TypeError, ValueError, AttributeError):
        consecutive_frames = 0
    if consecutive_frames < 1:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "wake_playback_consecutive_frames",
                "severity": "error",
                "status": "invalid_required_config",
                "effective_source": "argv",
                "message": "wake_playback_consecutive_frames must be at least 1.",
            }
        )

    for setting_name in (
        "music_duck_volume",
        "music_duck_stage_one_volume",
        "music_duck_stage_two_volume",
        "music_duck_stage_three_volume",
        "wake_capture_pre_roll_ms",
        "wake_capture_post_roll_ms",
    ):
        try:
            numeric = int(getattr(args, setting_name))
        except (TypeError, ValueError, AttributeError):
            numeric = -1
        if setting_name.startswith("wake_capture_"):
            if numeric < 0:
                findings.append(
                    {
                        "subsystem": "pi_satellite",
                        "setting": setting_name,
                        "severity": "error",
                        "status": "invalid_required_config",
                        "effective_source": "argv",
                        "message": f"{setting_name} must be non-negative.",
                    }
                )
            continue
        if numeric < 0 or numeric > 100:
            findings.append(
                {
                    "subsystem": "pi_satellite",
                    "setting": setting_name,
                    "severity": "error",
                    "status": "invalid_required_config",
                    "effective_source": "argv",
                    "message": f"{setting_name} must be between 0 and 100.",
                }
            )

    input_config = None
    try:
        input_config = resolve_audio_input_config(args)
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "audio_input",
                "severity": "info",
                "status": "resolved_input",
                "effective_source": input_config.backend,
                "message": f"Resolved audio input {input_config.label}",
            }
        )
    except Exception as exc:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "audio_input",
                "severity": "error",
                "status": "audio_input_resolution_failed",
                "effective_source": "argv",
                "message": str(exc),
            }
        )

    try:
        output_config = resolve_audio_output_config(args)
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "audio_output",
                "severity": "info",
                "status": "resolved_output",
                "effective_source": output_config.backend,
                "message": f"Resolved audio output {output_config.label}",
            }
        )
    except Exception as exc:
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": "audio_output",
                "severity": "error",
                "status": "audio_output_resolution_failed",
                "effective_source": "argv",
                "message": str(exc),
            }
        )

    if probe_audio_input and input_config is not None:
        try:
            with open_input_stream(
                sample_rate=SAMPLE_RATE,
                frame_length=FRAME_LENGTH,
                callback=lambda *_args, **_kwargs: None,
                args=args,
                logger=logger,
            ):
                pass
        except Exception as exc:
            findings.append(
                {
                    "subsystem": "pi_satellite",
                    "setting": "audio_input",
                    "severity": "error",
                    "status": "audio_input_open_failed",
                    "effective_source": input_config.backend,
                    "message": str(exc),
                }
            )

    for env_name, guidance in _SATELLITE_DEPRECATED_ENV_NAMES.items():
        env_value = os.getenv(env_name)
        if env_value in (None, ""):
            continue
        findings.append(
            {
                "subsystem": "pi_satellite",
                "setting": env_name,
                "severity": "warning",
                "status": "deprecated_env",
                "effective_source": "env",
                "deprecated": True,
                "message": guidance,
            }
        )

    for env_name in sorted(name for name in os.environ if name.startswith("ORACLE_")):
        if env_name not in (
            KNOWN_SATELLITE_ENV_NAMES | SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES
        ):
            findings.append(
                {
                    "subsystem": "pi_satellite",
                    "setting": env_name,
                    "severity": "warning",
                    "status": "unknown_env",
                    "effective_source": "env",
                    "message": f"Unknown Oracle environment variable for Pi satellite runtime: {env_name}",
                }
            )

    return findings
