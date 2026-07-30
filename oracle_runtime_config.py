from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any


SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES = frozenset(
    {
        "ORACLE_ALLOW_LEGACY_SATELLITE_CONFIGURATION",
        "ORACLE_SATELLITE_ID",
        "ORACLE_SATELLITE_PROJECTION_STORE_ROOT",
        "ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH",
    }
)

CONTROL_SERVICE_HOST_BOOTSTRAP_ENV_NAMES = frozenset(
    {"ORACLE_SATELLITE_CONTROL_LOG_LEVEL"}
)

KNOWN_SATELLITE_ENV_NAMES = frozenset(
    {
        "ORACLE_BRAIN_URL",
        "ORACLE_URL",
        "ORACLE_SOURCE",
        "ORACLE_SATELLITE_SOURCE",
        "ORACLE_WAKE_MODEL_PATH",
        "ORACLE_WAKE_PLAYBACK_THRESHOLD",
        "ORACLE_WAKE_PLAYBACK_LOG_THRESHOLD",
        "ORACLE_WAKE_PLAYBACK_POLL_SECONDS",
        "ORACLE_WAKE_PLAYBACK_HOLD_SECONDS",
        "ORACLE_WAKE_PLAYBACK_CONSECUTIVE_FRAMES",
        "ORACLE_WAKE_ARBITRATION_TIMEOUT_SECONDS",
        "ORACLE_WAKE_ARBITRATION_LOSER_SUPPRESSION_MS",
        "ORACLE_REPLY_AUDIO_STATE_PATH",
        "ORACLE_REPLY_AUDIO_STOP_PATH",
        "ORACLE_INPUT_DEVICE_NAME",
        "ORACLE_INPUT_GAIN",
        "ORACLE_OUTPUT_DEVICE_NAME",
        "ORACLE_ALARM_SOUND_PATH",
        "ORACLE_TIMER_SOUND_PATH",
        "ORACLE_ERROR_TONE_ENABLED",
        "ORACLE_ERROR_TONE_COOLDOWN_SECONDS",
        "ORACLE_FOLLOWUP_SILENCE_SECONDS",
        "ORACLE_FOLLOWUP_MAX_RECORD_SECONDS",
        "ORACLE_FOLLOWUP_SPEECH_START_TIMEOUT_SECONDS",
        "ORACLE_FALSE_START_SILENCE_SECONDS",
        "ORACLE_SPEECH_START_TIMEOUT_SECONDS",
        "ORACLE_INTERIM_ACK_ENABLED",
        "ORACLE_INTERIM_ACK_POLL_INTERVAL_SECONDS",
        "ORACLE_INTERIM_ACK_REQUEST_TIMEOUT_SECONDS",
        "ORACLE_MUSIC_CONTROL_URL",
        "ORACLE_MUSIC_API_KEY",
        "ORACLE_MUSIC_DUCK_VOLUME",
        "ORACLE_MUSIC_DUCK_STAGE_ONE_VOLUME",
        "ORACLE_MUSIC_DUCK_STAGE_TWO_VOLUME",
        "ORACLE_MUSIC_DUCK_STAGE_THREE_VOLUME",
        "ORACLE_MUSIC_DUCK_TRIGGER_THRESHOLD",
        "ORACLE_MUSIC_DUCK_MAX_SECONDS",
        "ORACLE_PLAYBACK_INTERRUPT_SETTLE_SECONDS",
        "ORACLE_INTERRUPT_REPLIES",
        "ORACLE_SATELLITE_CONFIG_BIND_HOST",
        "ORACLE_SATELLITE_CONFIG_BIND_PORT",
        "ORACLE_WAKE_CAPTURE_ENABLED",
        "ORACLE_WAKE_CAPTURE_ACTIVATION",
        "ORACLE_WAKE_CAPTURE_NEAR_THRESHOLD",
        "ORACLE_WAKE_CAPTURE_PRE_ROLL_MS",
        "ORACLE_WAKE_CAPTURE_POST_ROLL_MS",
        "ORACLE_WAKE_CAPTURE_NEAR_THRESHOLD_FRACTION",
        "ORACLE_WAKE_CAPTURE_EVENT_COOLDOWN_SECONDS",
        "ORACLE_WAKE_CAPTURE_LOCAL_STORAGE_PATH",
        "ORACLE_WAKE_CAPTURE_SYNC_ENABLED",
        "ORACLE_WAKE_CAPTURE_SYNC_INTERVAL_SECONDS",
        "ORACLE_WAKE_CAPTURE_SERVER_SYNC_PATH",
        "ORACLE_WAKE_CAPTURE_DELETE_LOCAL_AFTER_SYNC",
        "ORACLE_WAKE_CAPTURE_SYNC_HOST",
        "ORACLE_WAKE_CAPTURE_SYNC_USER",
        "ORACLE_WAKE_CAPTURE_SYNC_SSH_KEY_PATH",
        "ORACLE_WAKE_CAPTURE_SYNC_TRANSPORT",
        "ORACLE_WAKE_CAPTURE_SYNCED_LOCAL_RETENTION_DAYS",
    }
)

KNOWN_CONTROL_SERVICE_ENV_NAMES = frozenset(
    {
        "ORACLE_SATELLITE_CONTROL_ADAPTER",
        "ORACLE_SATELLITE_CONTROL_BIND_HOST",
        "ORACLE_SATELLITE_CONTROL_BIND_PORT",
        "ORACLE_SATELLITE_CONTROL_API_KEY",
        "ORACLE_PLEXAMP_URL",
        "ORACLE_PLEX_SERVER_URL",
        "ORACLE_PLEX_TOKEN",
        "ORACLE_PLEX_MACHINE_IDENTIFIER",
        "ORACLE_DISABLE_PLEXAMP_EXTERNAL",
        "ORACLE_SUPPORTS_ORACLE_NATIVE_MUSIC",
        "ORACLE_NATIVE_MUSIC_PLAYER_BIN",
        "ORACLE_OUTPUT_VOLUME_BACKEND",
        "ORACLE_OUTPUT_VOLUME_CARD",
        "ORACLE_OUTPUT_VOLUME_CONTROL",
        "ORACLE_PLAY_LONGFORM_AUDIO_CMD",
        "ORACLE_PAUSE_LONGFORM_AUDIO_CMD",
        "ORACLE_RESUME_LONGFORM_AUDIO_CMD",
        "ORACLE_STOP_LONGFORM_AUDIO_CMD",
        "ORACLE_SEEK_LONGFORM_AUDIO_CMD",
        "ORACLE_LONGFORM_STATE_CMD",
        "ORACLE_REPLY_AUDIO_STATE_PATH",
        "ORACLE_REPLY_AUDIO_STOP_PATH",
    }
)


def findings_have_errors(findings: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").lower() == "error" for item in findings)


def findings_have_warnings(findings: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").lower() == "warning" for item in findings)


def flatten_report_sections(report_sections: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    return [item for _, findings in report_sections for item in findings]


def build_config_report_payload(
    *,
    service: str,
    report_sections: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    findings = flatten_report_sections(report_sections)
    return {
        "ok": not findings_have_errors(findings),
        "service": service,
        "has_errors": findings_have_errors(findings),
        "has_warnings": findings_have_warnings(findings),
        "sections": [
            {
                "heading": heading,
                "findings": section_findings,
            }
            for heading, section_findings in report_sections
        ],
    }


def format_report_lines(findings: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in findings:
        severity = str(item.get("severity") or "warning").upper()
        setting = str(item.get("setting") or "-")
        status = str(item.get("status") or "-")
        source = str(item.get("effective_source") or "-")
        message = str(item.get("message") or "").strip()
        line = f"- [{severity}] {setting} ({status}, source={source})"
        if message:
            line += f": {message}"
        lines.append(line)
    return lines


def render_config_report_text(report_sections: list[tuple[str, list[dict[str, Any]]]]) -> str:
    lines: list[str] = []
    for heading, findings in report_sections:
        lines.append(heading)
        if findings:
            lines.extend(format_report_lines(findings))
        else:
            lines.append("- OK")
    return "\n".join(lines)


def choose_config_report_format(
    query_params: Mapping[str, str],
    accept_header: str | None,
) -> str:
    requested = str(query_params.get("format") or "").strip().lower()
    if requested in {"text", "txt", "plain"}:
        return "text"
    if requested == "json":
        return "json"

    accept = str(accept_header or "").lower()
    if "text/plain" in accept and "application/json" not in accept:
        return "text"
    return "json"


def log_config_findings(findings: list[dict[str, Any]], *, logger_name: str) -> list[dict[str, Any]]:
    target_logger = logging.getLogger(logger_name)
    for finding in findings:
        severity = str(finding.get("severity") or "warning").lower()
        if severity == "error":
            log_method = target_logger.error
        elif severity == "info":
            log_method = target_logger.info
        else:
            log_method = target_logger.warning
        log_method(
            "config_%s subsystem=%s setting=%s status=%s source=%s message=%s",
            severity,
            finding.get("subsystem") or "-",
            finding.get("setting") or "-",
            finding.get("status") or "-",
            finding.get("effective_source") or "-",
            finding.get("message") or "",
        )
    return findings
