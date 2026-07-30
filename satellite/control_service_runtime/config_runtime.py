from __future__ import annotations

import os
from argparse import Namespace
from typing import Any
from urllib.parse import urlsplit

from oracle_runtime_config import (
    CONTROL_SERVICE_HOST_BOOTSTRAP_ENV_NAMES,
    KNOWN_CONTROL_SERVICE_ENV_NAMES,
    SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES,
)
from satellite.control_service_runtime.system_volume import windows_default_endpoint_support_status


def build_control_service_runtime_report(args: Namespace) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    api_key = str(getattr(args, "api_key", "") or "").strip()
    if not api_key:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "api_key",
                "severity": "error",
                "status": "missing_required_config",
                "effective_source": "argv",
                "message": "Satellite control service API key is required.",
            }
        )

    bind_host = str(getattr(args, "bind_host", "") or "").strip()
    if not bind_host:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "bind_host",
                "severity": "error",
                "status": "invalid_bind_host",
                "effective_source": "argv",
                "message": "Satellite control service bind host must not be empty.",
            }
        )

    try:
        bind_port = int(getattr(args, "bind_port"))
    except (TypeError, ValueError):
        bind_port = -1
    if bind_port < 1 or bind_port > 65535:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "bind_port",
                "severity": "error",
                "status": "invalid_bind_port",
                "effective_source": "argv",
                "message": "Satellite control service bind port must be between 1 and 65535.",
            }
        )

    adapter = str(getattr(args, "adapter", "") or "").strip()
    if adapter not in {"local_playback", "plexamp_http", "shell"}:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "adapter",
                "severity": "error",
                "status": "invalid_adapter",
                "effective_source": "argv",
                "message": "Satellite control service adapter must be local_playback, plexamp_http, or shell.",
            }
        )

    if adapter in {"local_playback", "plexamp_http"}:
        plexamp_url = str(getattr(args, "plexamp_url", "") or "").strip()
        parsed = urlsplit(plexamp_url)
        if not plexamp_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            findings.append(
                {
                    "subsystem": "satellite_control_service",
                    "setting": "plexamp_url",
                    "severity": "error",
                    "status": "invalid_required_config",
                    "effective_source": "argv",
                    "message": "local_playback adapter requires a valid http(s) plexamp_url.",
                }
            )
    output_volume_backend = str(getattr(args, "output_volume_backend", "") or "").strip().lower()
    output_volume_card = str(getattr(args, "output_volume_card", "") or "").strip()
    output_volume_control = str(getattr(args, "output_volume_control", "") or "").strip()
    if output_volume_backend and output_volume_backend not in {"alsa", "windows_default_endpoint"}:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "output_volume_backend",
                "severity": "error",
                "status": "invalid_output_volume_backend",
                "effective_source": "argv",
                "message": (
                    "Satellite control service output volume backend must be alsa or "
                    "windows_default_endpoint when configured."
                ),
            }
        )
    if output_volume_backend == "alsa" and (not output_volume_card or not output_volume_control):
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "output_volume_backend",
                "severity": "error",
                "status": "invalid_output_volume_config",
                "effective_source": "argv",
                "message": "ALSA output volume control requires both output_volume_card and output_volume_control.",
            }
        )
    if output_volume_backend == "windows_default_endpoint":
        available, message = windows_default_endpoint_support_status()
        if not available:
            findings.append(
                {
                    "subsystem": "satellite_control_service",
                    "setting": "output_volume_backend",
                    "severity": "error",
                    "status": "windows_default_endpoint_unavailable",
                    "effective_source": "runtime",
                    "message": message,
                }
            )

    longform_commands = {
        "play_longform_audio_cmd": str(getattr(args, "play_longform_audio_cmd", "") or "").strip(),
        "pause_longform_audio_cmd": str(getattr(args, "pause_longform_audio_cmd", "") or "").strip(),
        "resume_longform_audio_cmd": str(getattr(args, "resume_longform_audio_cmd", "") or "").strip(),
        "stop_longform_audio_cmd": str(getattr(args, "stop_longform_audio_cmd", "") or "").strip(),
        "seek_longform_audio_cmd": str(getattr(args, "seek_longform_audio_cmd", "") or "").strip(),
        "longform_state_cmd": str(getattr(args, "longform_state_cmd", "") or "").strip(),
    }
    missing_longform = sorted(name for name, value in longform_commands.items() if not value)
    if missing_longform:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "longform_commands",
                "severity": "warning",
                "status": "optional_longform_config_missing",
                "effective_source": "argv",
                "message": (
                    "Long-form commands are incomplete; startup continues and long-form actions will fail until configured: "
                    + ", ".join(missing_longform)
                ),
            }
        )

    env_api_key = os.getenv("ORACLE_SATELLITE_CONTROL_API_KEY")
    if env_api_key in (None, "") and not api_key:
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "ORACLE_SATELLITE_CONTROL_API_KEY",
                "severity": "warning",
                "status": "missing_required_env",
                "effective_source": "env",
                "message": "Satellite control service API key is not configured in the environment.",
            }
        )

    for env_name in sorted(name for name in os.environ if name.startswith("ORACLE_")):
        if env_name not in (
            KNOWN_CONTROL_SERVICE_ENV_NAMES
            | SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES
            | CONTROL_SERVICE_HOST_BOOTSTRAP_ENV_NAMES
        ):
            findings.append(
                {
                    "subsystem": "satellite_control_service",
                    "setting": env_name,
                    "severity": "warning",
                    "status": "unknown_env",
                    "effective_source": "env",
                    "message": f"Unknown Oracle environment variable for satellite control service: {env_name}",
                }
            )

    return findings
