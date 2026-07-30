from __future__ import annotations

import json
import logging
import os
import re
import sys
from argparse import Namespace
from typing import Any
from urllib.parse import urlsplit

from oracle_runtime_config import (
    CONTROL_SERVICE_HOST_BOOTSTRAP_ENV_NAMES,
    KNOWN_CONTROL_SERVICE_ENV_NAMES,
    KNOWN_SATELLITE_ENV_NAMES,
    SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES,
    format_report_lines,
    render_config_report_text,
)

from .config import (
    load_home_automation_runbooks_config,
    load_local_config,
    load_network_control_config,
    load_network_inventory_config,
    load_notifications_config,
    load_orchestration_config,
)
from .memory.runtime import safe_record_event
from .network_control_preconditions import (
    ALLOWED_NETWORK_CONTROL_PRECONDITIONS,
    network_control_precondition_matches_target,
)
from .room_context.vocabulary import get_room_vocabulary


logger = logging.getLogger("oracle-brain.config")

_SYSTEMD_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")

_DEPLOY_SPECIFIC_LOCAL_KEYS = {
    "home_assistant_url",
    "home_assistant_token",
    "music_provider",
    "plex_url",
    "plex_token",
    "plex_machine_identifier",
    "satellite_controls",
    "audiobook_provider",
    "audiobookshelf_url",
    "audiobookshelf_token",
    "audiobookshelf_library_id",
    "calendar_provider",
    "calendar_ics_url",
    "calendar_write_base_url",
    "calendar_write_user",
    "calendar_write_app_password",
    "calendar_write_calendar_uri",
    "news_provider",
    "news_feeds",
    "network_probe_enabled",
    "network_probe_dns_host",
    "network_probe_http_url",
    "network_probe_timeout_seconds",
    "network_inventory",
    "network_control",
    "notifications",
    "orchestration",
    "ha_notification_ingress_token",
    "ha_event_ingress_token",
    "home_automation_runbooks",
    "librenms_enabled",
    "librenms_url",
    "librenms_token",
    "librenms_timeout_seconds",
    "apprise_enabled",
    "apprise_url",
    "apprise_timeout_seconds",
    "network_service_control",
    "network_router_control",
    "external_web_enabled",
    "external_web_auth_mode",
    "external_web_public_base_url",
    "external_web_trusted_proxy_headers",
    "external_web_public_health",
    "external_access_enabled",
    "external_access_token",
    "oracle_base_url",
    "source_registry",
    "tts_piper_binary",
    "tts_piper_model",
    "stt_whisper_binary",
    "stt_whisper_model",
}

_RETIRED_LOCAL_KEYS = {
    "ollama_split_enabled",
}

_BRAIN_SETTING_SPECS = (
    ("home_assistant_url", "ORACLE_HOME_ASSISTANT_URL", False),
    ("home_assistant_token", "ORACLE_HOME_ASSISTANT_TOKEN", True),
    ("music_provider", "ORACLE_MUSIC_PROVIDER", False),
    ("plex_url", "ORACLE_PLEX_URL", False),
    ("plex_token", "ORACLE_PLEX_TOKEN", True),
    ("plex_machine_identifier", "ORACLE_PLEX_MACHINE_IDENTIFIER", False),
    ("audiobook_provider", "ORACLE_AUDIOBOOK_PROVIDER", False),
    ("audiobookshelf_url", "ORACLE_AUDIOBOOKSHELF_URL", False),
    ("audiobookshelf_token", "ORACLE_AUDIOBOOKSHELF_TOKEN", True),
    ("audiobookshelf_library_id", "ORACLE_AUDIOBOOKSHELF_LIBRARY_ID", False),
    ("calendar_provider", "ORACLE_CALENDAR_PROVIDER", False),
    ("calendar_ics_url", "ORACLE_CALENDAR_ICS_URL", False),
    ("calendar_write_base_url", "ORACLE_CALENDAR_WRITE_BASE_URL", False),
    ("calendar_write_user", "ORACLE_CALENDAR_WRITE_USER", False),
    ("calendar_write_app_password", "ORACLE_CALENDAR_WRITE_APP_PASSWORD", True),
    ("calendar_write_calendar_uri", "ORACLE_CALENDAR_WRITE_CALENDAR_URI", False),
    ("news_provider", "ORACLE_NEWS_PROVIDER", False),
    ("network_probe_enabled", "ORACLE_NETWORK_PROBE_ENABLED", False),
    ("network_probe_dns_host", "ORACLE_NETWORK_PROBE_DNS_HOST", False),
    ("network_probe_http_url", "ORACLE_NETWORK_PROBE_HTTP_URL", False),
    ("network_probe_timeout_seconds", "ORACLE_NETWORK_PROBE_TIMEOUT_SECONDS", False),
    ("librenms_enabled", "ORACLE_LIBRENMS_ENABLED", False),
    ("librenms_url", "ORACLE_LIBRENMS_URL", False),
    ("librenms_token", "ORACLE_LIBRENMS_TOKEN", True),
    ("librenms_timeout_seconds", "ORACLE_LIBRENMS_TIMEOUT_SECONDS", False),
    ("apprise_enabled", "ORACLE_APPRISE_ENABLED", False),
    ("apprise_url", "ORACLE_APPRISE_URL", False),
    ("apprise_timeout_seconds", "ORACLE_APPRISE_TIMEOUT_SECONDS", False),
    ("external_web_enabled", "ORACLE_EXTERNAL_WEB_ENABLED", False),
    ("external_web_auth_mode", "ORACLE_EXTERNAL_WEB_AUTH_MODE", False),
    ("external_web_public_base_url", "ORACLE_EXTERNAL_WEB_PUBLIC_BASE_URL", False),
    ("external_web_trusted_proxy_headers", "ORACLE_EXTERNAL_WEB_TRUSTED_PROXY_HEADERS", False),
    ("external_web_public_health", "ORACLE_EXTERNAL_WEB_PUBLIC_HEALTH", False),
    ("external_access_enabled", "ORACLE_EXTERNAL_ACCESS_ENABLED", False),
    ("external_access_token", "ORACLE_EXTERNAL_ACCESS_TOKEN", True),
    ("oracle_base_url", "ORACLE_BASE_URL", False),
    ("tts_piper_binary", "ORACLE_TTS_PIPER_BINARY", False),
    ("tts_piper_model", "ORACLE_TTS_PIPER_MODEL", False),
    ("stt_whisper_binary", "ORACLE_STT_WHISPER_BINARY", False),
    ("stt_whisper_model", "ORACLE_STT_WHISPER_MODEL", False),
)

_JSON_ENV_SPECS = (
    ("satellite_controls", "ORACLE_SATELLITE_CONTROLS_JSON"),
    ("news_feeds", "ORACLE_NEWS_FEEDS_JSON"),
    ("source_registry", "ORACLE_SOURCE_REGISTRY_JSON"),
    ("network_inventory", "ORACLE_NETWORK_INVENTORY_JSON"),
    ("network_control", "ORACLE_NETWORK_CONTROL_JSON"),
    ("network_service_control", "ORACLE_NETWORK_SERVICE_CONTROL_JSON"),
    ("network_router_control", "ORACLE_NETWORK_ROUTER_CONTROL_JSON"),
    ("orchestration", "ORACLE_ORCHESTRATION_JSON"),
    ("notifications", "ORACLE_NOTIFICATIONS_JSON"),
)

_KNOWN_BRAIN_ENV_NAMES = {
    "ORACLE_CONFIG_AUTHORING_MODE",
    "ORACLE_CONFIG_BUNDLE_ROOT",
    "ORACLE_CONFIG_SOCKET_PATH",
    "ORACLE_CONFIG_STORE_ROOT",
    "ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT",
    "ORACLE_ALLOW_LEGACY_CONFIGURATION",
    "ORACLE_STT_PROVIDER",
    "ORACLE_STT_WHISPER_BINARY",
    "ORACLE_STT_WHISPER_MODEL",
    "ORACLE_STT_WHISPER_THREADS",
    "ORACLE_TTS_PROVIDER",
    "ORACLE_TTS_PIPER_BINARY",
    "ORACLE_TTS_PIPER_MODEL",
    "ORACLE_HOME_ASSISTANT_URL",
    "ORACLE_HOME_ASSISTANT_TOKEN",
    "ORACLE_HA_EVENT_INGRESS_TOKEN",
    "ORACLE_APPRISE_ENABLED",
    "ORACLE_APPRISE_URL",
    "ORACLE_APPRISE_TIMEOUT_SECONDS",
    "ORACLE_HOME_AUTOMATION_RUNBOOKS_JSON",
    "ORACLE_OLLAMA_URL",
    "ORACLE_OLLAMA_MODEL",
    "ORACLE_OLLAMA_TIMEOUT_SECONDS",
    "ORACLE_OLLAMA_KEEP_ALIVE",
    "ORACLE_OLLAMA_NUM_PREDICT",
    "ORACLE_OLLAMA_TEMPERATURE",
    "ORACLE_OLLAMA_TOP_P",
    "ORACLE_OLLAMA_SEED",
    "ORACLE_FALLBACK_ROUTER_MODEL",
    "ORACLE_FALLBACK_ROUTER_TIMEOUT_SECONDS",
    "ORACLE_FACTS_ENABLED",
    "ORACLE_FACTS_PROVIDER",
    "ORACLE_FACTS_SUMMARIZER_ENABLED",
    "ORACLE_FACTS_ACK_ENABLED",
    "ORACLE_FACTS_TIMEOUT_SECONDS",
    "ORACLE_FACTS_CACHE_ENABLED",
    "ORACLE_FACTS_CACHE_TTL_SECONDS",
    "ORACLE_FACTS_WIKIPEDIA_LANGUAGE",
    "ORACLE_FACTS_WIKIPEDIA_TIMEOUT_SECONDS",
    "ORACLE_MUSIC_PROVIDER",
    "ORACLE_WEATHER_WEEWX_URL",
    "ORACLE_WEATHER_TIMEOUT_SECONDS",
    "ORACLE_WEATHER_STALE_AFTER_SECONDS",
    "ORACLE_FORECAST_LATITUDE",
    "ORACLE_FORECAST_LONGITUDE",
    "ORACLE_FORECAST_TIMEOUT_SECONDS",
    "ORACLE_FORECAST_USER_AGENT",
    "ORACLE_FORECAST_OFFICE",
    "ORACLE_PLEX_URL",
    "ORACLE_PLEX_TOKEN",
    "ORACLE_PLEX_TIMEOUT_SECONDS",
    "ORACLE_PLEX_MUSIC_SECTION_ID",
    "ORACLE_PLEX_MACHINE_IDENTIFIER",
    "ORACLE_SATELLITE_CONTROL_TIMEOUT_SECONDS",
    "ORACLE_SATELLITE_CONTROLS_JSON",
    "ORACLE_BASE_URL",
    "ORACLE_AUDIOBOOK_PROVIDER",
    "ORACLE_AUDIOBOOKSHELF_URL",
    "ORACLE_AUDIOBOOKSHELF_TOKEN",
    "ORACLE_AUDIOBOOKSHELF_LIBRARY_ID",
    "ORACLE_AUDIOBOOKSHELF_TIMEOUT_SECONDS",
    "ORACLE_CALENDAR_PROVIDER",
    "ORACLE_CALENDAR_ICS_URL",
    "ORACLE_CALENDAR_WRITE_BASE_URL",
    "ORACLE_CALENDAR_WRITE_USER",
    "ORACLE_CALENDAR_WRITE_APP_PASSWORD",
    "ORACLE_CALENDAR_WRITE_CALENDAR_URI",
    "ORACLE_CALENDAR_TIMEZONE",
    "ORACLE_CALENDAR_TIMEOUT_SECONDS",
    "ORACLE_NEWS_PROVIDER",
    "ORACLE_NEWS_FEEDS_JSON",
    "ORACLE_SOURCE_REGISTRY_JSON",
    "ORACLE_NEWS_TIMEOUT_SECONDS",
    "ORACLE_NEWS_MAX_HEADLINES",
    "ORACLE_NETWORK_PROBE_ENABLED",
    "ORACLE_NETWORK_PROBE_DNS_HOST",
    "ORACLE_NETWORK_PROBE_HTTP_URL",
    "ORACLE_NETWORK_PROBE_TIMEOUT_SECONDS",
    "ORACLE_NETWORK_INVENTORY_JSON",
    "ORACLE_NETWORK_CONTROL_JSON",
    "ORACLE_LIBRENMS_ENABLED",
    "ORACLE_LIBRENMS_URL",
    "ORACLE_LIBRENMS_TOKEN",
    "ORACLE_LIBRENMS_TIMEOUT_SECONDS",
    "ORACLE_NETWORK_SERVICE_CONTROL_JSON",
    "ORACLE_NETWORK_ROUTER_CONTROL_JSON",
    "ORACLE_ORCHESTRATION_JSON",
    "ORACLE_NOTIFICATIONS_JSON",
    "ORACLE_EXTERNAL_WEB_ENABLED",
    "ORACLE_EXTERNAL_WEB_AUTH_MODE",
    "ORACLE_EXTERNAL_WEB_PUBLIC_BASE_URL",
    "ORACLE_EXTERNAL_WEB_TRUSTED_PROXY_HEADERS",
    "ORACLE_EXTERNAL_WEB_PUBLIC_HEALTH",
    "ORACLE_EXTERNAL_ACCESS_ENABLED",
    "ORACLE_EXTERNAL_ACCESS_TOKEN",
    "ORACLE_OPENCLAW_ADAPTER",
    "ORACLE_OPENCLAW_URL",
    "ORACLE_OPENCLAW_ENDPOINT_PATH",
    "ORACLE_OPENCLAW_TIMEOUT_SECONDS",
    "ORACLE_OPENCLAW_MAX_SUGGESTIONS",
    "ORACLE_OPENCLAW_SSH_TARGET",
    "ORACLE_OPENCLAW_SSH_PASSWORD",
    "ORACLE_OPENCLAW_SSH_IDENTITY_FILE",
    "ORACLE_OPENCLAW_SSH_CONNECT_TIMEOUT_SECONDS",
    "ORACLE_OPENCLAW_CLI_PATH",
    "ORACLE_OPENCLAW_CLI_MODE",
    "ORACLE_OPENCLAW_AGENT",
    "ORACLE_OPENCLAW_MODEL",
    "ORACLE_OPENCLAW_START_GATEWAY",
    "ORACLE_OPENCLAW_GATEWAY_PORT",
    "ORACLE_HOLIDAY_CALENDAR_ICS_URL",
    "ORACLE_USER_REGISTRY_JSON",
    "ORACLE_WAKE_ARBITRATION_LOSER_SUPPRESSION_MS",
    "ORACLE_WAKE_ARBITRATION_SCORING_STRATEGY",
    "ORACLE_WAKE_ARBITRATION_WINDOW_MS",
    "ORACLE_WEATHER_CURRENT_PROVIDER",
    "ORACLE_WEATHER_FORECAST_PROVIDER",
    "ORACLE_WEATHER_HISTORY_DB_PATH",
    "ORACLE_WEATHER_HISTORY_JSON_URL",
    "ORACLE_WEATHER_HISTORY_SSH_HOST",
    "ORACLE_WEATHER_HISTORY_SSH_PASSWORD",
    "ORACLE_WEATHER_HISTORY_SSH_USER",
    "ORACLE_WEATHER_HISTORY_TIMEOUT_SECONDS",
}

_SATELLITE_DEPRECATED_ENV_NAMES = {
    "ORACLE_URL": "Use ORACLE_BRAIN_URL instead.",
    "ORACLE_SATELLITE_SOURCE": "Use ORACLE_SOURCE instead.",
    "ORACLE_MUSIC_CONTROL_URL": "A later naming phase should replace this with a control-service-oriented name.",
    "ORACLE_MUSIC_API_KEY": "A later naming phase should replace this with a control-service-oriented name.",
}


def _redact(value: Any, *, secret: bool) -> str:
    if value in (None, ""):
        return ""
    if secret:
        return "<redacted>"
    return str(value)


def _values_differ(left: Any, right: Any) -> bool:
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        try:
            return json.dumps(left, sort_keys=True) != json.dumps(right, sort_keys=True)
        except TypeError:
            return left != right
    return str(left) != str(right)


def build_brain_config_report() -> list[dict[str, Any]]:
    config = load_local_config()
    findings: list[dict[str, Any]] = []

    deploy_specific_keys = sorted(key for key in _DEPLOY_SPECIFIC_LOCAL_KEYS if key in config)
    if deploy_specific_keys:
        findings.append(
            {
                "subsystem": "brain",
                "setting": "server_config_local_json",
                "severity": "warning",
                "status": "deprecated_local_truth",
                "effective_source": "local_config",
                "message": (
                    "server/config.local.json still contains deploy-specific or secret runtime values: "
                    + ", ".join(deploy_specific_keys)
                ),
            }
        )

    retired_keys = sorted(key for key in _RETIRED_LOCAL_KEYS if key in config)
    for key in retired_keys:
        findings.append(
            {
                "subsystem": "brain",
                "setting": key,
                "severity": "warning",
                "status": "retired_no_effect",
                "effective_source": "local_config",
                "message": f"server/config.local.json key {key} is retired and has no runtime effect.",
            }
        )

    for config_key, env_name, secret in _BRAIN_SETTING_SPECS:
        env_value = os.getenv(env_name)
        local_value = config.get(config_key)
        if env_value not in (None, "") and local_value not in (None, "") and _values_differ(env_value, local_value):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": config_key,
                    "severity": "warning",
                    "status": "conflicting_sources",
                    "effective_source": "env",
                    "message": f"{env_name} overrides server/config.local.json for {config_key}.",
                    "effective_value_redacted": _redact(env_value, secret=secret),
                    "conflicting_sources": [
                        {"source": "env", "value_redacted": _redact(env_value, secret=secret)},
                        {"source": "local_config", "value_redacted": _redact(local_value, secret=secret)},
                    ],
                }
            )

    for config_key, env_name in _JSON_ENV_SPECS:
        env_value = os.getenv(env_name)
        local_value = config.get(config_key)
        if env_value in (None, ""):
            continue
        try:
            env_parsed = json.loads(env_value)
        except json.JSONDecodeError:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": config_key,
                    "severity": "error",
                    "status": "invalid_env_json",
                    "effective_source": "env",
                    "message": f"{env_name} must be valid JSON.",
                }
            )
            continue
        if not isinstance(env_parsed, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": config_key,
                    "severity": "error",
                    "status": "invalid_env_json_shape",
                    "effective_source": "env",
                    "message": f"{env_name} must be a JSON object.",
                }
            )
            continue
        if local_value not in (None, "") and _values_differ(env_parsed, local_value):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": config_key,
                    "severity": "warning",
                    "status": "conflicting_sources",
                    "effective_source": "env",
                    "message": f"{env_name} overrides server/config.local.json for {config_key}.",
                }
            )

    source_registry = None
    source_registry_source = "local_config"
    env_source_registry = os.getenv("ORACLE_SOURCE_REGISTRY_JSON")
    if env_source_registry not in (None, ""):
        try:
            source_registry = json.loads(env_source_registry)
        except json.JSONDecodeError:
            source_registry = None
        else:
            source_registry_source = "env"
    elif "source_registry" in config:
        source_registry = config.get("source_registry")

    findings.extend(_validate_source_registry(source_registry, effective_source=source_registry_source))
    network_inventory, network_inventory_source = _load_effective_json_config(
        config,
        config_key="network_inventory",
        env_name="ORACLE_NETWORK_INVENTORY_JSON",
        file_loader=load_network_inventory_config,
        file_source="config/network-inventory.json",
    )
    findings.extend(
        _validate_network_inventory(
            network_inventory,
            effective_source=network_inventory_source,
        )
    )
    network_control, network_control_source = _load_effective_json_config(
        config,
        config_key="network_control",
        env_name="ORACLE_NETWORK_CONTROL_JSON",
        file_loader=load_network_control_config,
        file_source="config/network-control.json",
    )
    findings.extend(
        _validate_network_control_policy(
            network_control,
            network_inventory=network_inventory,
            effective_source=network_control_source,
        )
    )
    orchestration, orchestration_source = _load_effective_json_config(
        config,
        config_key="orchestration",
        env_name="ORACLE_ORCHESTRATION_JSON",
        file_loader=load_orchestration_config,
        file_source="config/orchestration.json",
    )
    findings.extend(
        _validate_orchestration_config(
            orchestration,
            effective_source=orchestration_source,
        )
    )
    notifications, notifications_source = _load_effective_json_config(
        config,
        config_key="notifications",
        env_name="ORACLE_NOTIFICATIONS_JSON",
        file_loader=load_notifications_config,
        file_source="config/notifications.json",
    )
    findings.extend(
        _validate_notifications_config(
            notifications,
            source_registry=source_registry,
            effective_source=notifications_source,
        )
    )
    home_automation, home_automation_source = _load_effective_json_config(
        config,
        config_key="home_automation_runbooks",
        env_name="ORACLE_HOME_AUTOMATION_RUNBOOKS_JSON",
        file_loader=load_home_automation_runbooks_config,
        file_source="config/home-automation-runbooks.json",
    )
    findings.extend(
        _validate_home_automation_runbooks_config(
            home_automation,
            notification_ids={
                str(value.get("id") or "").strip().lower()
                for value in (notifications or {}).get("notifications", [])
                if isinstance(value, dict)
            }
            if isinstance(notifications, dict)
            else set(),
            effective_source=home_automation_source,
        )
    )

    for env_name in (
        "ORACLE_CONFIG_AUTHORING_MODE",
        "ORACLE_CONFIG_BUNDLE_ROOT",
        "ORACLE_CONFIG_SOCKET_PATH",
        "ORACLE_CONFIG_STORE_ROOT",
        "ORACLE_ALLOW_LEGACY_CONFIGURATION",
    ):
        if env_name in os.environ:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": env_name,
                    "severity": "info",
                    "status": "bootstrap_metadata",
                    "effective_source": "bootstrap_env",
                    "message": "Canonical configuration infrastructure selector is present.",
                }
            )

    if "ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT" in os.environ:
        findings.append(
            {
                "subsystem": "brain",
                "setting": "ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT",
                "severity": "info",
                "status": "bootstrap_metadata",
                "effective_source": "bootstrap_env",
                "message": "Deployment-owned wake-capture archive root is present.",
            }
        )

    for env_name in sorted(name for name in os.environ if name.startswith("ORACLE_")):
        if env_name not in _KNOWN_BRAIN_ENV_NAMES:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": env_name,
                    "severity": "warning",
                    "status": "unknown_env",
                    "effective_source": "env",
                    "message": f"Unknown Oracle environment variable: {env_name}",
                }
            )

    return findings


_NOTIFICATION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_NOTIFICATION_AUDIO_POLICIES = {"pause_resume"}
_NOTIFICATION_EXTERNAL_PROVIDERS = {"apprise"}
_NOTIFICATION_QUIET_HOURS_POLICIES = {"respect", "bypass"}
_NOTIFICATION_REPEAT_POLICIES = {"every_occurrence", "first_per_correlation"}
_NOTIFICATION_FAILURE_POLICIES = {"best_effort", "required"}
_NOTIFICATION_RECIPIENT_GROUP_KEYS = {
    "id",
    "enabled",
    "provider",
    "config_key",
    "routing_tag",
}
_NOTIFICATION_EXTERNAL_DELIVERY_KEYS = {
    "enabled",
    "recipient_groups",
    "delivery_ttl_seconds",
    "max_attempts",
    "retry_seconds",
    "quiet_hours_policy",
    "repeat_policy",
    "failure_policy",
}
_NOTIFICATION_TTL_MIN_SECONDS = 5
_NOTIFICATION_TTL_MAX_SECONDS = 3600
_NOTIFICATION_UNSAFE_KEY_FRAGMENTS = ("authorization", "password", "secret", "token")


def _notification_error(
    setting: str,
    message: str,
    effective_source: str,
    *,
    status: str = "invalid_config_shape",
) -> dict[str, Any]:
    return {
        "subsystem": "brain",
        "setting": setting,
        "severity": "error",
        "status": status,
        "effective_source": effective_source,
        "message": message,
    }


def _validate_notifications_config(
    notifications: Any,
    *,
    source_registry: Any,
    effective_source: str,
) -> list[dict[str, Any]]:
    if notifications in (None, ""):
        return []
    if not isinstance(notifications, dict):
        return [_notification_error("notifications", "notifications must be a JSON object.", effective_source)]

    findings: list[dict[str, Any]] = []
    findings.extend(
        _find_unsafe_notification_keys(
            notifications,
            setting="notifications",
            effective_source=effective_source,
        )
    )
    if notifications.get("version", 1) != 1:
        findings.append(
            _notification_error(
                "notifications.version",
                "notifications.version must be 1.",
                effective_source,
            )
        )

    raw_modes = notifications.get("modes", [])
    known_modes: set[str] = set()
    if not isinstance(raw_modes, list):
        findings.append(
            _notification_error("notifications.modes", "notifications.modes must be a list.", effective_source)
        )
        raw_modes = []
    for index, mode in enumerate(raw_modes):
        setting = f"notifications.modes[{index}]"
        if not isinstance(mode, dict):
            findings.append(_notification_error(setting, "Notification mode must be an object.", effective_source))
            continue
        mode_id = str(mode.get("id") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(mode_id):
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    "Mode id must use lowercase letters, numbers, and underscores.",
                    effective_source,
                )
            )
        elif mode_id in known_modes:
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    f"Duplicate notification mode id {mode_id}.",
                    effective_source,
                    status="duplicate_id",
                )
            )
        else:
            known_modes.add(mode_id)

        entity_id = str(mode.get("entity_id") or "").strip()
        if not entity_id.startswith("input_boolean.") or len(entity_id) <= len("input_boolean."):
            findings.append(
                _notification_error(
                    f"{setting}.entity_id",
                    "Notification mode entity_id must reference a Home Assistant input_boolean.",
                    effective_source,
                    status="invalid_entity_reference",
                )
            )
        if not str(mode.get("active_state") or "").strip():
            findings.append(
                _notification_error(
                    f"{setting}.active_state",
                    "Notification mode active_state is required.",
                    effective_source,
                    status="missing_required_config",
                )
            )

    raw_recipient_groups = notifications.get("recipient_groups", [])
    known_recipient_groups: dict[str, bool] = {}
    if not isinstance(raw_recipient_groups, list):
        findings.append(
            _notification_error(
                "notifications.recipient_groups",
                "Notification recipient_groups must be a list.",
                effective_source,
            )
        )
        raw_recipient_groups = []
    for index, group in enumerate(raw_recipient_groups):
        setting = f"notifications.recipient_groups[{index}]"
        if not isinstance(group, dict):
            findings.append(
                _notification_error(setting, "Notification recipient group must be an object.", effective_source)
            )
            continue
        for key in group:
            if key not in _NOTIFICATION_RECIPIENT_GROUP_KEYS:
                findings.append(
                    _notification_error(
                        f"{setting}.{key}",
                        f"Unsupported recipient group field {key}.",
                        effective_source,
                        status="unsupported_config_key",
                    )
                )
        group_id = str(group.get("id") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(group_id):
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    "Recipient group id must use lowercase letters, numbers, and underscores.",
                    effective_source,
                )
            )
        elif group_id in known_recipient_groups:
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    f"Duplicate notification recipient group id {group_id}.",
                    effective_source,
                    status="duplicate_id",
                )
            )
        enabled = group.get("enabled")
        if not isinstance(enabled, bool):
            findings.append(
                _notification_error(f"{setting}.enabled", "Recipient group enabled must be a boolean.", effective_source)
            )
        if group_id and group_id not in known_recipient_groups:
            known_recipient_groups[group_id] = enabled is True
        provider = str(group.get("provider") or "").strip().lower()
        if provider not in _NOTIFICATION_EXTERNAL_PROVIDERS:
            findings.append(
                _notification_error(
                    f"{setting}.provider",
                    "Recipient group provider must be apprise.",
                    effective_source,
                )
            )
        config_key = str(group.get("config_key") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(config_key):
            findings.append(
                _notification_error(
                    f"{setting}.config_key",
                    "Apprise config_key must use lowercase letters, numbers, and underscores.",
                    effective_source,
                )
            )
        routing_tag = str(group.get("routing_tag") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(routing_tag):
            findings.append(
                _notification_error(
                    f"{setting}.routing_tag",
                    "Apprise routing_tag must use lowercase letters, numbers, and underscores.",
                    effective_source,
                )
            )

    raw_definitions = notifications.get("notifications", [])
    if not isinstance(raw_definitions, list):
        findings.append(
            _notification_error(
                "notifications.notifications",
                "notifications.notifications must be a list.",
                effective_source,
            )
        )
        return findings

    known_sources = source_registry if isinstance(source_registry, dict) else {}
    seen_ids: set[str] = set()
    for index, definition in enumerate(raw_definitions):
        setting = f"notifications.notifications[{index}]"
        if not isinstance(definition, dict):
            findings.append(_notification_error(setting, "Notification definition must be an object.", effective_source))
            continue

        notification_id = str(definition.get("id") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(notification_id):
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    "Notification id must use lowercase letters, numbers, and underscores.",
                    effective_source,
                )
            )
        elif notification_id in seen_ids:
            findings.append(
                _notification_error(
                    f"{setting}.id",
                    f"Duplicate notification id {notification_id}.",
                    effective_source,
                    status="duplicate_id",
                )
            )
        else:
            seen_ids.add(notification_id)

        if not isinstance(definition.get("enabled"), bool):
            findings.append(
                _notification_error(
                    f"{setting}.enabled",
                    "Notification enabled must be a boolean.",
                    effective_source,
                )
            )

        if not str(definition.get("message") or "").strip():
            findings.append(
                _notification_error(
                    f"{setting}.message",
                    "Notification message is required.",
                    effective_source,
                    status="missing_required_config",
                )
            )

        targets = definition.get("targets")
        if not isinstance(targets, list) or not targets:
            findings.append(
                _notification_error(
                    f"{setting}.targets",
                    "Notification targets must be a non-empty list.",
                    effective_source,
                )
            )
        else:
            seen_targets: set[str] = set()
            for target in targets:
                target_id = str(target or "").strip()
                if not target_id:
                    findings.append(
                        _notification_error(
                            f"{setting}.targets",
                            "Notification targets must not contain empty values.",
                            effective_source,
                        )
                    )
                    continue
                if target_id in seen_targets:
                    findings.append(
                        _notification_error(
                            f"{setting}.targets",
                            f"Duplicate notification target {target_id}.",
                            effective_source,
                            status="duplicate_reference",
                        )
                    )
                    continue
                seen_targets.add(target_id)
                source = known_sources.get(target_id)
                if not isinstance(source, dict) or str(source.get("source_type") or "").strip().lower() != "satellite":
                    findings.append(
                        _notification_error(
                            f"{setting}.targets",
                            f"Unknown satellite source {target_id}.",
                            effective_source,
                            status="unknown_source_reference",
                        )
                    )

        suppressed_by = definition.get("suppressed_by", [])
        if not isinstance(suppressed_by, list):
            findings.append(
                _notification_error(
                    f"{setting}.suppressed_by",
                    "Notification suppressed_by must be a list.",
                    effective_source,
                )
            )
        else:
            seen_suppression_modes: set[str] = set()
            for mode in suppressed_by:
                mode_id = str(mode or "").strip().lower()
                if mode_id in seen_suppression_modes:
                    findings.append(
                        _notification_error(
                            f"{setting}.suppressed_by",
                            f"Duplicate notification suppression mode {mode_id}.",
                            effective_source,
                            status="duplicate_reference",
                        )
                    )
                elif mode_id not in known_modes:
                    findings.append(
                        _notification_error(
                            f"{setting}.suppressed_by",
                            f"Unknown notification suppression mode {mode_id}.",
                            effective_source,
                            status="unknown_mode_reference",
                        )
                    )
                seen_suppression_modes.add(mode_id)

        ttl = definition.get("delivery_ttl_seconds")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not (
            _NOTIFICATION_TTL_MIN_SECONDS <= ttl <= _NOTIFICATION_TTL_MAX_SECONDS
        ):
            findings.append(
                _notification_error(
                    f"{setting}.delivery_ttl_seconds",
                    f"Notification delivery_ttl_seconds must be an integer from {_NOTIFICATION_TTL_MIN_SECONDS} to {_NOTIFICATION_TTL_MAX_SECONDS}.",
                    effective_source,
                )
            )

        audio_policy = str(definition.get("audio_policy") or "").strip().lower()
        if audio_policy not in _NOTIFICATION_AUDIO_POLICIES:
            findings.append(
                _notification_error(
                    f"{setting}.audio_policy",
                    "Notification audio_policy must be pause_resume.",
                    effective_source,
                )
            )

        if "phone_delivery" in definition:
            findings.append(
                _notification_error(
                    f"{setting}.phone_delivery",
                    "phone_delivery was replaced by channel-neutral external_delivery.",
                    effective_source,
                    status="retired_config_key",
                )
            )
        external_delivery = definition.get("external_delivery")
        if external_delivery is not None and not isinstance(external_delivery, dict):
            findings.append(
                _notification_error(
                    f"{setting}.external_delivery",
                    "Notification external_delivery must be an object.",
                    effective_source,
                )
            )
            continue
        if isinstance(external_delivery, dict):
            for key in external_delivery:
                if key not in _NOTIFICATION_EXTERNAL_DELIVERY_KEYS:
                    findings.append(
                        _notification_error(
                            f"{setting}.external_delivery.{key}",
                            f"Unsupported external delivery field {key}.",
                            effective_source,
                            status="unsupported_config_key",
                        )
                    )
            external_enabled = external_delivery.get("enabled")
            if not isinstance(external_enabled, bool):
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.enabled",
                        "External delivery enabled must be a boolean.",
                        effective_source,
                    )
                )
            groups = external_delivery.get("recipient_groups")
            if not isinstance(groups, list) or (external_enabled is True and not groups):
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.recipient_groups",
                        "Enabled external delivery requires a non-empty recipient group list.",
                        effective_source,
                    )
                )
                groups = []
            seen_groups: set[str] = set()
            for group in groups:
                group_id = str(group or "").strip().lower()
                if group_id in seen_groups:
                    findings.append(
                        _notification_error(
                            f"{setting}.external_delivery.recipient_groups",
                            f"Duplicate recipient group {group_id}.",
                            effective_source,
                            status="duplicate_reference",
                        )
                    )
                elif group_id not in known_recipient_groups:
                    findings.append(
                        _notification_error(
                            f"{setting}.external_delivery.recipient_groups",
                            f"Unknown recipient group {group_id}.",
                            effective_source,
                            status="unknown_recipient_group_reference",
                        )
                    )
                elif external_enabled is True and known_recipient_groups[group_id] is not True:
                    findings.append(
                        _notification_error(
                            f"{setting}.external_delivery.recipient_groups",
                            f"Enabled external delivery cannot use disabled recipient group {group_id}.",
                            effective_source,
                            status="disabled_recipient_group_reference",
                        )
                    )
                seen_groups.add(group_id)
            for field, minimum, maximum in (
                ("delivery_ttl_seconds", _NOTIFICATION_TTL_MIN_SECONDS, _NOTIFICATION_TTL_MAX_SECONDS),
                ("max_attempts", 1, 5),
                ("retry_seconds", 1, 3600),
            ):
                value = external_delivery.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                    findings.append(
                        _notification_error(
                            f"{setting}.external_delivery.{field}",
                            f"External delivery {field} must be an integer from {minimum} to {maximum}.",
                            effective_source,
                        )
                    )
            quiet_policy = str(external_delivery.get("quiet_hours_policy") or "").strip().lower()
            if quiet_policy not in _NOTIFICATION_QUIET_HOURS_POLICIES:
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.quiet_hours_policy",
                        "External delivery quiet_hours_policy must be respect or bypass.",
                        effective_source,
                    )
                )
            elif external_enabled is True and quiet_policy != "bypass":
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.quiet_hours_policy",
                        "Enabled external delivery currently requires quiet_hours_policy bypass.",
                        effective_source,
                        status="unsupported_capability",
                    )
                )
            repeat_policy = str(external_delivery.get("repeat_policy") or "").strip().lower()
            if repeat_policy not in _NOTIFICATION_REPEAT_POLICIES:
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.repeat_policy",
                        "External delivery repeat_policy must be every_occurrence or first_per_correlation.",
                        effective_source,
                    )
                )
            failure_policy = str(external_delivery.get("failure_policy") or "").strip().lower()
            if failure_policy not in _NOTIFICATION_FAILURE_POLICIES:
                findings.append(
                    _notification_error(
                        f"{setting}.external_delivery.failure_policy",
                        "External delivery failure_policy must be best_effort or required.",
                        effective_source,
                    )
                )
    return findings


def _find_unsafe_notification_keys(
    value: Any,
    *,
    setting: str,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_setting = f"{setting}.{key}"
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _NOTIFICATION_UNSAFE_KEY_FRAGMENTS):
                findings.append(
                    _notification_error(
                        child_setting,
                        "Notification configuration must not contain credentials or authorization values.",
                        effective_source,
                        status="unsafe_config_key",
                    )
                )
            findings.extend(
                _find_unsafe_notification_keys(
                    child,
                    setting=child_setting,
                    effective_source=effective_source,
                )
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(
                _find_unsafe_notification_keys(
                    child,
                    setting=f"{setting}[{index}]",
                    effective_source=effective_source,
                )
            )
    return findings


def _validate_home_automation_runbooks_config(
    config: Any,
    *,
    notification_ids: set[str],
    effective_source: str,
) -> list[dict[str, Any]]:
    if config in (None, ""):
        return []
    if not isinstance(config, dict):
        return [_notification_error("home_automation_runbooks", "Home-automation runbooks must be a JSON object.", effective_source)]
    findings = _find_unsafe_notification_keys(
        config,
        setting="home_automation_runbooks",
        effective_source=effective_source,
    )
    if config.get("version", 1) != 1:
        findings.append(_notification_error("home_automation_runbooks.version", "Version must be 1.", effective_source))

    mappings = config.get("event_mappings", [])
    known_subjects: set[str] = set()
    known_entry_subjects: set[str] = set()
    seen_entities: set[str] = set()
    seen_mapping_ids: set[str] = set()
    if not isinstance(mappings, list):
        findings.append(_notification_error("home_automation_runbooks.event_mappings", "event_mappings must be a list.", effective_source))
        mappings = []
    for index, mapping in enumerate(mappings):
        setting = f"home_automation_runbooks.event_mappings[{index}]"
        if not isinstance(mapping, dict):
            findings.append(_notification_error(setting, "Event mapping must be an object.", effective_source))
            continue
        entity_id = str(mapping.get("entity_id") or "").strip().lower()
        mapping_id = str(mapping.get("id") or "").strip().lower()
        subject = str(mapping.get("subject") or "").strip().lower()
        event_type = str(mapping.get("event_type") or "").strip().lower()
        if not _NOTIFICATION_ID_PATTERN.fullmatch(mapping_id):
            findings.append(_notification_error(f"{setting}.id", "Mapping id must use lowercase letters, numbers, and underscores.", effective_source))
        elif mapping_id in seen_mapping_ids:
            findings.append(_notification_error(f"{setting}.id", f"Duplicate mapping id {mapping_id}.", effective_source, status="duplicate_id"))
        seen_mapping_ids.add(mapping_id)
        if "." not in entity_id:
            findings.append(_notification_error(f"{setting}.entity_id", "A provider entity id is required at the HA bridge boundary.", effective_source))
        elif entity_id in seen_entities:
            findings.append(_notification_error(f"{setting}.entity_id", f"Duplicate entity mapping {entity_id}.", effective_source, status="duplicate_id"))
        seen_entities.add(entity_id)
        if not _NOTIFICATION_ID_PATTERN.fullmatch(subject):
            findings.append(_notification_error(f"{setting}.subject", "Canonical subject must use lowercase letters, numbers, and underscores.", effective_source))
        if event_type not in {"entry_state", "mode_state"}:
            findings.append(_notification_error(f"{setting}.event_type", "event_type must be entry_state or mode_state.", effective_source))
        elif event_type == "entry_state":
            open_state = str(mapping.get("open_state") or "").strip().lower()
            closed_state = str(mapping.get("closed_state") or "").strip().lower()
            if not open_state or not closed_state or open_state == closed_state:
                findings.append(_notification_error(setting, "Entry mappings require distinct open_state and closed_state values.", effective_source))
            if subject:
                known_entry_subjects.add(subject)
        elif not str(mapping.get("active_state") or "").strip():
            findings.append(_notification_error(f"{setting}.active_state", "Mode mappings require active_state.", effective_source))
        if subject:
            known_subjects.add(subject)

    runbooks = config.get("runbooks", [])
    seen_runbooks: set[str] = set()
    seen_runbook_subjects: set[str] = set()
    if not isinstance(runbooks, list):
        findings.append(_notification_error("home_automation_runbooks.runbooks", "runbooks must be a list.", effective_source))
        return findings
    for index, runbook in enumerate(runbooks):
        setting = f"home_automation_runbooks.runbooks[{index}]"
        if not isinstance(runbook, dict):
            findings.append(_notification_error(setting, "Runbook must be an object.", effective_source))
            continue
        runbook_id = str(runbook.get("id") or "").strip().lower()
        subject = str(runbook.get("subject") or "").strip().lower()
        notification_type = str(runbook.get("notification_type") or "").strip().lower()
        if not isinstance(runbook.get("enabled"), bool):
            findings.append(_notification_error(f"{setting}.enabled", "Runbook enabled must be a boolean.", effective_source))
        if not isinstance(runbook.get("notification_delivery_enabled"), bool):
            findings.append(_notification_error(f"{setting}.notification_delivery_enabled", "notification_delivery_enabled must be a boolean.", effective_source))
        if not _NOTIFICATION_ID_PATTERN.fullmatch(runbook_id):
            findings.append(_notification_error(f"{setting}.id", "Runbook id must use lowercase letters, numbers, and underscores.", effective_source))
        elif runbook_id in seen_runbooks:
            findings.append(_notification_error(f"{setting}.id", f"Duplicate runbook id {runbook_id}.", effective_source, status="duplicate_id"))
        seen_runbooks.add(runbook_id)
        if subject not in known_subjects:
            findings.append(_notification_error(f"{setting}.subject", f"Unknown canonical subject {subject}.", effective_source, status="unknown_subject_reference"))
        elif subject not in known_entry_subjects:
            findings.append(_notification_error(f"{setting}.subject", f"Subject {subject} is not an entry_state mapping.", effective_source, status="invalid_subject_reference"))
        elif subject in seen_runbook_subjects:
            findings.append(_notification_error(f"{setting}.subject", f"Only one entry runbook may own {subject}.", effective_source, status="duplicate_reference"))
        seen_runbook_subjects.add(subject)
        if notification_type not in notification_ids:
            findings.append(_notification_error(f"{setting}.notification_type", f"Unknown notification type {notification_type}.", effective_source, status="unknown_notification_reference"))
        if runbook.get("migration_mode") not in {"direct_notification", "runbook"}:
            findings.append(_notification_error(f"{setting}.migration_mode", "migration_mode must be direct_notification or runbook.", effective_source))
        for field, minimum, maximum in (
            ("delay_seconds", 1, 86400),
            ("repeat_interval_seconds", 1, 86400),
            ("max_notifications", 1, 20),
            ("max_lateness_seconds", 0, 3600),
            ("provider_retry_seconds", 1, 3600),
            ("max_provider_failures", 0, 20),
        ):
            value = runbook.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                findings.append(_notification_error(f"{setting}.{field}", f"{field} must be an integer from {minimum} to {maximum}.", effective_source))
    return findings


_ORCHESTRATION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ORCHESTRATION_STEP_TYPES = {
    "ui_action",
    "audiobook_start",
    "audiobook_resume",
    "sleep_timer",
    "wait",
    "state_check",
    "playback_check",
}
_ORCHESTRATION_UNSAFE_KEYS = {
    "command",
    "commands",
    "shell",
    "script",
    "args",
    "argv",
    "password",
    "token",
    "secret",
    "entity_id",
    "service_name",
    "service_call",
    "provider_target",
    "url",
}


def _validate_orchestration_config(
    orchestration: Any,
    *,
    effective_source: str,
) -> list[dict[str, Any]]:
    if orchestration in (None, ""):
        return []
    if not isinstance(orchestration, dict):
        return [_orchestration_error("orchestration", "orchestration must be a JSON object.", effective_source)]

    findings: list[dict[str, Any]] = []
    if orchestration.get("version", 1) != 1:
        findings.append(
            _orchestration_error(
                "orchestration.version",
                "orchestration.version must be 1.",
                effective_source,
            )
        )

    seen_ids: set[str] = set()
    seen_global_phrases: dict[str, str] = {}
    seen_source_phrases: dict[str, list[tuple[str, set[str]]]] = {}
    for collection, kind in (("recoveries", "recovery"), ("routines", "routine")):
        definitions = orchestration.get(collection, [])
        if not isinstance(definitions, list):
            findings.append(
                _orchestration_error(
                    f"orchestration.{collection}",
                    f"orchestration.{collection} must be a list.",
                    effective_source,
                )
            )
            continue
        for index, definition in enumerate(definitions):
            setting = f"orchestration.{collection}[{index}]"
            if not isinstance(definition, dict):
                findings.append(_orchestration_error(setting, "Definition must be an object.", effective_source))
                continue
            findings.extend(_find_unsafe_orchestration_keys(definition, setting, effective_source))
            definition_id = str(definition.get("id") or "").strip()
            if not _ORCHESTRATION_ID_PATTERN.fullmatch(definition_id):
                findings.append(
                    _orchestration_error(
                        f"{setting}.id",
                        "Definition id must use lowercase letters, numbers, and underscores.",
                        effective_source,
                    )
                )
            elif definition_id in seen_ids:
                findings.append(
                    _orchestration_error(
                        f"{setting}.id",
                        f"Duplicate orchestration id {definition_id}.",
                        effective_source,
                    )
                )
            else:
                seen_ids.add(definition_id)
            for required_key in ("display_name", "description"):
                if not str(definition.get(required_key) or "").strip():
                    findings.append(
                        _orchestration_error(
                            f"{setting}.{required_key}",
                            f"{required_key} is required.",
                            effective_source,
                        )
                    )
            findings.extend(
                _validate_orchestration_triggers(
                    definition.get("triggers"),
                    definition_id=definition_id,
                    source_ids={
                        str(item or "").strip()
                        for item in definition.get("source_ids") or []
                        if str(item or "").strip()
                    },
                    setting=setting,
                    seen_global_phrases=seen_global_phrases,
                    seen_source_phrases=seen_source_phrases,
                    effective_source=effective_source,
                )
            )
            if kind == "recovery":
                if str(definition.get("approval_mode") or "plan").strip().lower() != "plan":
                    findings.append(
                        _orchestration_error(
                            f"{setting}.approval_mode",
                            "V1 recovery approval_mode must be plan.",
                            effective_source,
                        )
                    )
                for profile_key in ("diagnostic_profile", "remediation_profile"):
                    if not str(definition.get(profile_key) or "").strip():
                        findings.append(
                            _orchestration_error(
                                f"{setting}.{profile_key}",
                                f"{profile_key} is required.",
                                effective_source,
                            )
                        )
                continue
            findings.extend(_validate_routine_definition(definition, setting, effective_source))
    return findings


def _validate_orchestration_triggers(
    triggers: Any,
    *,
    definition_id: str,
    source_ids: set[str],
    setting: str,
    seen_global_phrases: dict[str, str],
    seen_source_phrases: dict[str, list[tuple[str, set[str]]]],
    effective_source: str,
) -> list[dict[str, Any]]:
    if triggers in (None, {}):
        return []
    if not isinstance(triggers, dict):
        return [_orchestration_error(f"{setting}.triggers", "triggers must be an object.", effective_source)]
    findings: list[dict[str, Any]] = []
    for phrase_key in ("global_phrases", "source_phrases"):
        phrases = triggers.get(phrase_key, [])
        phrase_setting = f"{setting}.triggers.{phrase_key}"
        if phrases not in (None, []) and not isinstance(phrases, list):
            findings.append(_orchestration_error(phrase_setting, f"{phrase_key} must be a list.", effective_source))
            continue
        for phrase in phrases or []:
            normalized = " ".join(str(phrase or "").strip().lower().split())
            if not normalized:
                findings.append(
                    _orchestration_error(
                        phrase_setting,
                        "trigger phrases must not be empty.",
                        effective_source,
                    )
                )
                continue
            if phrase_key == "global_phrases":
                source_owners = seen_source_phrases.get(normalized, [])
                if normalized in seen_global_phrases:
                    findings.append(
                        _orchestration_error(
                            phrase_setting,
                            f"Global trigger phrase {normalized!r} is already owned by {seen_global_phrases[normalized]}.",
                            effective_source,
                        )
                    )
                elif source_owners:
                    findings.append(
                        _orchestration_error(
                            phrase_setting,
                            f"Global trigger phrase {normalized!r} conflicts with source trigger owned by {source_owners[0][0]}.",
                            effective_source,
                        )
                    )
                else:
                    seen_global_phrases[normalized] = definition_id
                continue
            if not source_ids:
                findings.append(
                    _orchestration_error(
                        phrase_setting,
                        "source_phrases require routine source_ids.",
                        effective_source,
                    )
                )
                continue
            conflicts = [
                owner
                for owner, owner_sources in seen_source_phrases.get(normalized, [])
                if source_ids.intersection(owner_sources)
            ]
            global_owner = seen_global_phrases.get(normalized)
            if global_owner:
                findings.append(
                    _orchestration_error(
                        phrase_setting,
                        f"Source trigger phrase {normalized!r} conflicts with global trigger owned by {global_owner}.",
                        effective_source,
                    )
                )
            elif conflicts:
                findings.append(
                    _orchestration_error(
                        phrase_setting,
                        f"Source trigger phrase {normalized!r} overlaps sources with {conflicts[0]}.",
                        effective_source,
                    )
                )
            else:
                seen_source_phrases.setdefault(normalized, []).append((definition_id, source_ids))
    return findings


def _validate_routine_definition(
    definition: dict[str, Any],
    setting: str,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    source_ids = definition.get("source_ids", [])
    if not isinstance(source_ids, list) or not [item for item in source_ids if str(item or "").strip()]:
        findings.append(
            _orchestration_error(
                f"{setting}.source_ids",
                "Routine source_ids must contain at least one Oracle source id.",
                effective_source,
            )
        )
    if not str(definition.get("user_id") or "").strip():
        findings.append(_orchestration_error(f"{setting}.user_id", "Routine user_id is required.", effective_source))

    inputs = definition.get("inputs", {})
    if inputs not in (None, {}) and not isinstance(inputs, dict):
        findings.append(_orchestration_error(f"{setting}.inputs", "inputs must be an object.", effective_source))
    elif isinstance(inputs, dict):
        for input_id, input_spec in inputs.items():
            input_setting = f"{setting}.inputs.{input_id}"
            if not _ORCHESTRATION_ID_PATTERN.fullmatch(str(input_id or "")):
                findings.append(_orchestration_error(input_setting, "Input id has an invalid format.", effective_source))
                continue
            if not isinstance(input_spec, dict):
                findings.append(_orchestration_error(input_setting, "Input must be an object.", effective_source))
                continue
            input_type = str(input_spec.get("type") or "").strip().lower()
            if input_type not in {"integer", "string"}:
                findings.append(
                    _orchestration_error(
                        f"{input_setting}.type",
                        "Input type must be integer or string.",
                        effective_source,
                    )
                )
            if input_type == "integer":
                minimum = input_spec.get("minimum")
                maximum = input_spec.get("maximum")
                default = input_spec.get("default")
                if not all(isinstance(value, int) for value in (minimum, maximum, default)):
                    findings.append(
                        _orchestration_error(
                            input_setting,
                            "Integer inputs require integer minimum, maximum, and default.",
                            effective_source,
                        )
                    )
                elif minimum > maximum or default < minimum or default > maximum:
                    findings.append(
                        _orchestration_error(
                            input_setting,
                            "Integer input bounds must contain the default value.",
                            effective_source,
                        )
                    )

    steps = definition.get("steps", [])
    if not isinstance(steps, list) or not steps:
        findings.append(
            _orchestration_error(
                f"{setting}.steps",
                "Routine steps must be a non-empty list.",
                effective_source,
            )
        )
        return findings
    seen_step_ids: set[str] = set()
    for index, step in enumerate(steps):
        step_setting = f"{setting}.steps[{index}]"
        if not isinstance(step, dict):
            findings.append(_orchestration_error(step_setting, "Step must be an object.", effective_source))
            continue
        step_id = str(step.get("id") or "").strip()
        if not _ORCHESTRATION_ID_PATTERN.fullmatch(step_id):
            findings.append(_orchestration_error(f"{step_setting}.id", "Step id has an invalid format.", effective_source))
        elif step_id in seen_step_ids:
            findings.append(_orchestration_error(f"{step_setting}.id", f"Duplicate step id {step_id}.", effective_source))
        else:
            seen_step_ids.add(step_id)
        step_type = str(step.get("type") or "").strip().lower()
        if step_type not in _ORCHESTRATION_STEP_TYPES:
            findings.append(
                _orchestration_error(
                    f"{step_setting}.type",
                    f"Unknown routine step type {step_type or '<missing>'}.",
                    effective_source,
                )
            )
        required_fields = {
            "ui_action": ("action_id",),
            "audiobook_start": ("source_id", "user_id"),
            "audiobook_resume": ("source_id", "user_id"),
            "sleep_timer": ("source_id",),
            "state_check": ("check_id", "expected_state"),
            "playback_check": ("source_id", "check_id"),
        }.get(step_type, ())
        for required_field in required_fields:
            if not str(step.get(required_field) or "").strip():
                findings.append(
                    _orchestration_error(
                        f"{step_setting}.{required_field}",
                        f"{required_field} is required for {step_type}.",
                        effective_source,
                    )
                )
        if step_type in {"audiobook_start", "sleep_timer", "wait"}:
            duration_input = str(step.get("duration_input") or "").strip()
            duration_seconds = step.get("duration_seconds")
            has_duration = bool(duration_input) or duration_seconds is not None
            if step_type != "audiobook_start" and bool(duration_input) == (duration_seconds is not None):
                findings.append(
                    _orchestration_error(
                        step_setting,
                        f"{step_type} requires exactly one of duration_input or duration_seconds.",
                        effective_source,
                    )
                )
            elif step_type == "audiobook_start" and not has_duration:
                pass
            elif bool(duration_input) == (duration_seconds is not None):
                findings.append(
                    _orchestration_error(
                        step_setting,
                        f"{step_type} requires at most one of duration_input or duration_seconds.",
                        effective_source,
                    )
                )
            elif duration_input and duration_input not in inputs:
                findings.append(
                    _orchestration_error(
                        f"{step_setting}.duration_input",
                        f"Unknown routine input {duration_input!r}.",
                        effective_source,
                    )
                )
            elif duration_input and str(step.get("duration_unit") or "").strip().lower() not in {"seconds", "minutes"}:
                findings.append(
                    _orchestration_error(
                        f"{step_setting}.duration_unit",
                        "duration_unit must be seconds or minutes when duration_input is used.",
                        effective_source,
                    )
                )
            elif duration_seconds is not None and (
                not isinstance(duration_seconds, int) or duration_seconds < 0 or duration_seconds > 86400
            ):
                findings.append(
                    _orchestration_error(
                        f"{step_setting}.duration_seconds",
                        "duration_seconds must be an integer from 0 through 86400.",
                        effective_source,
                    )
                )
        if step_type == "wait" and step.get("max_lateness_seconds") is None:
            findings.append(
                _orchestration_error(
                    f"{step_setting}.max_lateness_seconds",
                    "wait steps require max_lateness_seconds.",
                    effective_source,
                )
            )
        if str(step.get("on_failure") or "stop").strip().lower() not in {"stop", "continue"}:
            findings.append(
                _orchestration_error(
                    f"{step_setting}.on_failure",
                    "on_failure must be stop or continue.",
                    effective_source,
                )
            )
        for bounded_key in ("timeout_seconds", "max_lateness_seconds"):
            value = step.get(bounded_key)
            if value is not None and (not isinstance(value, int) or value < 0 or value > 86400):
                findings.append(
                    _orchestration_error(
                        f"{step_setting}.{bounded_key}",
                        f"{bounded_key} must be an integer from 0 through 86400.",
                        effective_source,
                    )
                )
    return findings


def _find_unsafe_orchestration_keys(
    value: Any,
    setting: str,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key or "").strip().lower() in _ORCHESTRATION_UNSAFE_KEYS:
                findings.append(
                    _orchestration_error(
                        f"{setting}.{key}",
                        f"Unsafe orchestration key {key!r} is forbidden.",
                        effective_source,
                    )
                )
            findings.extend(_find_unsafe_orchestration_keys(nested, f"{setting}.{key}", effective_source))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_find_unsafe_orchestration_keys(nested, f"{setting}[{index}]", effective_source))
    return findings


def _orchestration_error(setting: str, message: str, effective_source: str) -> dict[str, Any]:
    return {
        "subsystem": "brain",
        "setting": setting,
        "severity": "error",
        "status": "invalid_orchestration_config",
        "effective_source": effective_source,
        "message": message,
    }


def _load_effective_json_config(
    config: dict[str, Any],
    *,
    config_key: str,
    env_name: str,
    file_loader: Any | None = None,
    file_source: str = "local_config",
) -> tuple[Any, str]:
    raw_env = os.getenv(env_name)
    if raw_env not in (None, ""):
        try:
            return json.loads(raw_env), "env"
        except json.JSONDecodeError:
            return None, "env"
    if file_loader is not None:
        file_value = file_loader()
        if file_value is not None:
            return file_value, file_source
    return config.get(config_key), "local_config"


def _find_duplicate_ids(raw_entries: list[Any]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            continue
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)
    return duplicates


def _network_inventory_target_exists(
    *,
    target_type: str,
    target_id: str,
    host_ids: set[str],
    service_ids: set[str],
    dependency_ids: set[str],
) -> bool:
    if target_type == "host":
        return target_id in host_ids
    if target_type == "service":
        return target_id in service_ids
    if target_type == "dependency":
        return target_id in dependency_ids
    return False


def _network_control_target_exists(
    *,
    target_type: str,
    target_id: str,
    host_ids: set[str],
    service_ids: set[str],
    power_target_ids: set[str],
) -> bool:
    if target_type == "host":
        return target_id in host_ids
    if target_type == "service":
        return target_id in service_ids
    if target_type == "power_target":
        return target_id in power_target_ids
    return False


def _network_inventory_ids(network_inventory: Any) -> tuple[set[str], set[str], set[str]]:
    if not isinstance(network_inventory, dict):
        return set(), set(), set()
    hosts = network_inventory.get("hosts", []) if isinstance(network_inventory.get("hosts", []), list) else []
    services = network_inventory.get("services", []) if isinstance(network_inventory.get("services", []), list) else []
    power_targets = (
        network_inventory.get("power_targets", [])
        if isinstance(network_inventory.get("power_targets", []), list)
        else []
    )
    return (
        {str(item.get("id") or "").strip() for item in hosts if isinstance(item, dict) and str(item.get("id") or "").strip()},
        {
            str(item.get("id") or "").strip()
            for item in services
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        },
        {
            str(item.get("id") or "").strip()
            for item in power_targets
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        },
    )


def _validate_network_control_policy(
    network_control: Any,
    *,
    network_inventory: Any,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if network_control in (None, ""):
        return findings
    if not isinstance(network_control, dict):
        findings.append(
            {
                "subsystem": "brain",
                "setting": "network_control",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": "network_control must be a JSON object.",
            }
        )
        return findings

    actions = network_control.get("actions", [])
    if actions in (None, ""):
        return findings
    if not isinstance(actions, list):
        findings.append(
            {
                "subsystem": "brain",
                "setting": "network_control.actions",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": "network_control.actions must be a list.",
            }
        )
        return findings

    host_ids, service_ids, power_target_ids = _network_inventory_ids(network_inventory)
    for duplicate_id in sorted(_find_duplicate_ids(actions)):
        findings.append(
            {
                "subsystem": "brain",
                "setting": f"network_control.actions.{duplicate_id}",
                "severity": "error",
                "status": "duplicate_id",
                "effective_source": effective_source,
                "message": f"network_control.actions contains duplicate id {duplicate_id}.",
            }
        )

    forbidden_key_fragments = ("command", "shell", "token", "secret", "password")
    forbidden_key_names = {"script"}
    for index, raw in enumerate(actions):
        setting = f"network_control.actions[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue

        for key in sorted(str(key) for key in raw):
            lowered = key.lower()
            if lowered in forbidden_key_names or any(fragment in lowered for fragment in forbidden_key_fragments):
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{key}",
                        "severity": "error",
                        "status": "unsafe_control_config",
                        "effective_source": effective_source,
                        "message": f"{setting}.{key} is not allowed in network control policy.",
                    }
                )

        action_entry_id = str(raw.get("id") or "").strip()
        target_type = str(raw.get("target_type") or "").strip().lower()
        target_id = str(raw.get("target_id") or "").strip()
        action_id = str(raw.get("action_id") or "").strip()
        provider = str(raw.get("provider") or "").strip()
        adapter = str(raw.get("adapter") or "").strip()
        for field_name, value in (
            ("id", action_entry_id),
            ("target_type", target_type),
            ("target_id", target_id),
            ("action_id", action_id),
            ("provider", provider),
            ("adapter", adapter),
        ):
            if not value:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting} is missing {field_name}.",
                    }
                )

        if target_type and target_type not in {"host", "service", "power_target"}:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.target_type",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.target_type must be host, service, or power_target.",
                }
            )
        elif target_type and target_id and not _network_control_target_exists(
            target_type=target_type,
            target_id=target_id,
            host_ids=host_ids,
            service_ids=service_ids,
            power_target_ids=power_target_ids,
        ):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.target_id",
                    "severity": "error",
                    "status": "unknown_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.target_id references unknown {target_type} {target_id}.",
                }
            )

        if raw.get("enabled") not in (None, True, False):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.enabled",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.enabled must be a boolean.",
                }
            )
        if raw.get("requires_confirmation") not in (None, True, False):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.requires_confirmation",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.requires_confirmation must be a boolean.",
                }
            )
        if raw.get("requires_graceful_lifecycle") not in (None, True, False):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.requires_graceful_lifecycle",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.requires_graceful_lifecycle must be a boolean.",
                }
            )
        if raw.get("requires_graceful_lifecycle") is True and (
            target_type != "host" or action_id != "restart_host" or adapter != "service_control"
        ):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.requires_graceful_lifecycle",
                    "severity": "error",
                    "status": "invalid_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.requires_graceful_lifecycle is only valid for service-control host restarts.",
                }
            )
        if raw.get("enabled") is True and raw.get("requires_confirmation") is not True:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.requires_confirmation",
                    "severity": "error",
                    "status": "confirmation_required",
                    "effective_source": effective_source,
                    "message": f"{setting} must require confirmation before an enabled control action can execute.",
                }
            )
        required_preconditions = raw.get("required_preconditions")
        if required_preconditions not in (None, ""):
            if not isinstance(required_preconditions, list):
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.required_preconditions",
                        "severity": "error",
                        "status": "invalid_config_shape",
                        "effective_source": effective_source,
                        "message": f"{setting}.required_preconditions must be a list.",
                    }
                )
            else:
                for precondition in required_preconditions:
                    precondition_id = str(precondition or "").strip()
                    if not precondition_id:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.required_preconditions",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": f"{setting}.required_preconditions contains an empty precondition id.",
                            }
                        )
                    elif precondition_id not in ALLOWED_NETWORK_CONTROL_PRECONDITIONS:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.required_preconditions",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": f"{setting}.required_preconditions contains unknown id {precondition_id}.",
                            }
                        )
                    elif not network_control_precondition_matches_target(
                        precondition_id=precondition_id,
                        target_type=target_type,
                        target_id=target_id,
                        action_id=action_id,
                    ):
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.required_preconditions",
                                "severity": "error",
                                "status": "invalid_reference",
                                "effective_source": effective_source,
                                "message": f"{precondition_id} is not valid for {target_type}:{target_id}:{action_id}.",
                            }
                        )
        execution = raw.get("execution")
        if execution not in (None, ""):
            if not isinstance(execution, dict):
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.execution",
                        "severity": "error",
                        "status": "invalid_config_shape",
                        "effective_source": effective_source,
                        "message": f"{setting}.execution must be an object.",
                    }
                )
            else:
                method = str(execution.get("method") or "").strip().lower()
                unit = str(execution.get("unit") or "").strip()
                wait_seconds = execution.get("wait_seconds")
                restart_timeout_seconds = execution.get("restart_timeout_seconds")
                shutdown_timeout_seconds = execution.get("shutdown_timeout_seconds")
                recovery_timeout_seconds = execution.get("recovery_timeout_seconds")
                recovery_poll_seconds = execution.get("recovery_poll_seconds")
                readiness_timeout_seconds = execution.get("readiness_timeout_seconds")
                cooldown_seconds = execution.get("cooldown_seconds")
                if adapter in {"service_control", "switch_power_cycle", "router_control"}:
                    if method or unit:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": f"{setting}.execution must not define service manager details for {adapter}.",
                            }
                        )
                elif method != "systemd":
                    findings.append(
                        {
                            "subsystem": "brain",
                            "setting": f"{setting}.execution.method",
                            "severity": "error",
                            "status": "invalid_config_value",
                            "effective_source": effective_source,
                            "message": f"{setting}.execution.method must be systemd for the current control slice.",
                        }
                    )
                if adapter not in {"service_control", "switch_power_cycle", "router_control"} and not _SYSTEMD_UNIT_PATTERN.match(unit):
                    findings.append(
                        {
                            "subsystem": "brain",
                            "setting": f"{setting}.execution.unit",
                            "severity": "error",
                            "status": "invalid_config_value",
                            "effective_source": effective_source,
                            "message": f"{setting}.execution.unit must be an explicit systemd service unit name.",
                        }
                    )
                if wait_seconds not in (None, ""):
                    try:
                        parsed_wait_seconds = int(wait_seconds)
                    except (TypeError, ValueError):
                        parsed_wait_seconds = -1
                    if parsed_wait_seconds < 0 or parsed_wait_seconds > 120:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.wait_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": f"{setting}.execution.wait_seconds must be between 0 and 120.",
                            }
                        )
                if restart_timeout_seconds not in (None, ""):
                    try:
                        parsed_restart_timeout_seconds = int(restart_timeout_seconds)
                    except (TypeError, ValueError):
                        parsed_restart_timeout_seconds = -1
                    if parsed_restart_timeout_seconds < 5 or parsed_restart_timeout_seconds > 60:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.restart_timeout_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": (
                                    f"{setting}.execution.restart_timeout_seconds must be between 5 and 60."
                                ),
                            }
                        )
                if recovery_timeout_seconds not in (None, ""):
                    try:
                        parsed_recovery_timeout_seconds = int(recovery_timeout_seconds)
                    except (TypeError, ValueError):
                        parsed_recovery_timeout_seconds = -1
                    if parsed_recovery_timeout_seconds < 15 or parsed_recovery_timeout_seconds > 300:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.recovery_timeout_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": (
                                    f"{setting}.execution.recovery_timeout_seconds must be between 15 and 300."
                                ),
                            }
                        )
                if shutdown_timeout_seconds not in (None, ""):
                    try:
                        parsed_shutdown_timeout_seconds = int(shutdown_timeout_seconds)
                    except (TypeError, ValueError):
                        parsed_shutdown_timeout_seconds = -1
                    if parsed_shutdown_timeout_seconds < 15 or parsed_shutdown_timeout_seconds > 300:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.shutdown_timeout_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": (
                                    f"{setting}.execution.shutdown_timeout_seconds must be between 15 and 300."
                                ),
                            }
                        )
                if readiness_timeout_seconds not in (None, ""):
                    try:
                        parsed_readiness_timeout_seconds = int(readiness_timeout_seconds)
                    except (TypeError, ValueError):
                        parsed_readiness_timeout_seconds = -1
                    if parsed_readiness_timeout_seconds < 15 or parsed_readiness_timeout_seconds > 300:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.readiness_timeout_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": (
                                    f"{setting}.execution.readiness_timeout_seconds must be between 15 and 300."
                                ),
                            }
                        )
                if cooldown_seconds not in (None, ""):
                    try:
                        parsed_cooldown_seconds = int(cooldown_seconds)
                    except (TypeError, ValueError):
                        parsed_cooldown_seconds = -1
                    if parsed_cooldown_seconds < 0 or parsed_cooldown_seconds > 3600:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.cooldown_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": f"{setting}.execution.cooldown_seconds must be between 0 and 3600.",
                            }
                        )
                if recovery_poll_seconds not in (None, ""):
                    try:
                        parsed_recovery_poll_seconds = int(recovery_poll_seconds)
                    except (TypeError, ValueError):
                        parsed_recovery_poll_seconds = -1
                    if parsed_recovery_poll_seconds < 2 or parsed_recovery_poll_seconds > 30:
                        findings.append(
                            {
                                "subsystem": "brain",
                                "setting": f"{setting}.execution.recovery_poll_seconds",
                                "severity": "error",
                                "status": "invalid_config_value",
                                "effective_source": effective_source,
                                "message": (
                                    f"{setting}.execution.recovery_poll_seconds must be between 2 and 30."
                                ),
                            }
                        )

    return findings


def _validate_network_inventory(
    network_inventory: Any,
    *,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if network_inventory in (None, ""):
        return findings
    if not isinstance(network_inventory, dict):
        findings.append(
            {
                "subsystem": "brain",
                "setting": "network_inventory",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": "network_inventory must be a JSON object.",
            }
        )
        return findings

    section_names = ("hosts", "services", "service_groups", "monitors", "dependencies", "power_targets")
    for section_name in section_names:
        section = network_inventory.get(section_name, [])
        if section in (None, ""):
            continue
        if not isinstance(section, list):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"network_inventory.{section_name}",
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"network_inventory.{section_name} must be a list.",
                }
            )

    hosts = network_inventory.get("hosts", []) if isinstance(network_inventory.get("hosts", []), list) else []
    services = network_inventory.get("services", []) if isinstance(network_inventory.get("services", []), list) else []
    service_groups = (
        network_inventory.get("service_groups", [])
        if isinstance(network_inventory.get("service_groups", []), list)
        else []
    )
    monitors = network_inventory.get("monitors", []) if isinstance(network_inventory.get("monitors", []), list) else []
    dependencies = (
        network_inventory.get("dependencies", [])
        if isinstance(network_inventory.get("dependencies", []), list)
        else []
    )
    power_targets = (
        network_inventory.get("power_targets", [])
        if isinstance(network_inventory.get("power_targets", []), list)
        else []
    )

    host_ids: set[str] = set()
    service_ids: set[str] = set()
    service_host_ids: dict[str, str] = {}
    dependency_ids: set[str] = set()

    for section_name, entries in (
        ("hosts", hosts),
        ("services", services),
        ("service_groups", service_groups),
        ("monitors", monitors),
        ("dependencies", dependencies),
        ("power_targets", power_targets),
    ):
        for duplicate_id in sorted(_find_duplicate_ids(entries)):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"network_inventory.{section_name}.{duplicate_id}",
                    "severity": "error",
                    "status": "duplicate_id",
                    "effective_source": effective_source,
                    "message": f"network_inventory.{section_name} contains duplicate id {duplicate_id}.",
                }
            )

    for index, raw in enumerate(hosts):
        setting = f"network_inventory.hosts[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        host_id = str(raw.get("id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        if not host_id:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.id",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting} is missing id.",
                }
            )
        else:
            host_ids.add(host_id)
        if not display_name:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.display_name",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting} is missing display_name.",
                }
            )
        if "provider_refs" in raw and not isinstance(raw.get("provider_refs"), dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.provider_refs",
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"{setting}.provider_refs must be an object.",
                }
            )
        findings.extend(
            _validate_service_control_refs(
                raw.get("control_refs"),
                setting=f"{setting}.control_refs",
                effective_source=effective_source,
            )
        )

    for index, raw in enumerate(services):
        setting = f"network_inventory.services[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        service_id = str(raw.get("id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        host_id = str(raw.get("host_id") or "").strip()
        if not service_id:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.id",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting} is missing id.",
                }
            )
        else:
            service_ids.add(service_id)
            service_host_ids[service_id] = host_id
        if not display_name:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.display_name",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting} is missing display_name.",
                }
            )
        if not host_id:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.host_id",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting} is missing host_id.",
                }
            )
        elif host_id not in host_ids:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.host_id",
                    "severity": "error",
                    "status": "unknown_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.host_id references unknown host {host_id}.",
                }
            )
        if "provider_refs" in raw and not isinstance(raw.get("provider_refs"), dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.provider_refs",
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"{setting}.provider_refs must be an object.",
                }
            )
        findings.extend(
            _validate_service_control_refs(
                raw.get("control_refs"),
                setting=f"{setting}.control_refs",
                effective_source=effective_source,
            )
        )

    for index, raw in enumerate(service_groups):
        setting = f"network_inventory.service_groups[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        group_id = str(raw.get("id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        host_id = str(raw.get("host_id") or "").strip()
        group_service_ids = raw.get("service_ids")
        for field_name, value in (
            ("id", group_id),
            ("display_name", display_name),
            ("host_id", host_id),
        ):
            if not value:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting} is missing {field_name}.",
                    }
                )
        if host_id and host_id not in host_ids:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.host_id",
                    "severity": "error",
                    "status": "unknown_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.host_id references unknown host {host_id}.",
                }
            )
        if not isinstance(group_service_ids, list) or not group_service_ids:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.service_ids",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting}.service_ids must be a non-empty list.",
                }
            )
            continue
        seen_service_ids: set[str] = set()
        for raw_service_id in group_service_ids:
            service_id = str(raw_service_id or "").strip()
            if not service_id:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.service_ids",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting}.service_ids contains an empty service id.",
                    }
                )
                continue
            if service_id in seen_service_ids:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.service_ids.{service_id}",
                        "severity": "error",
                        "status": "duplicate_id",
                        "effective_source": effective_source,
                        "message": f"{setting}.service_ids contains duplicate service id {service_id}.",
                    }
                )
            seen_service_ids.add(service_id)
            if service_id not in service_ids:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.service_ids.{service_id}",
                        "severity": "error",
                        "status": "unknown_reference",
                        "effective_source": effective_source,
                        "message": f"{setting}.service_ids references unknown service {service_id}.",
                    }
                )
            elif host_id and service_host_ids.get(service_id) != host_id:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.service_ids.{service_id}",
                        "severity": "error",
                        "status": "host_reference_mismatch",
                        "effective_source": effective_source,
                        "message": (
                            f"{setting}.service_ids includes service {service_id}, "
                            f"but that service belongs to host {service_host_ids.get(service_id)}."
                        ),
                    }
                )
    for index, raw in enumerate(dependencies):
        setting = f"network_inventory.dependencies[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        dependency_id = str(raw.get("id") or "").strip()
        if dependency_id:
            dependency_ids.add(dependency_id)

    for index, raw in enumerate(monitors):
        setting = f"network_inventory.monitors[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        monitor_id = str(raw.get("id") or "").strip()
        target_type = str(raw.get("target_type") or "").strip().lower()
        target_id = str(raw.get("target_id") or "").strip()
        source = str(raw.get("source") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        for field_name, value in (
            ("id", monitor_id),
            ("target_type", target_type),
            ("target_id", target_id),
            ("source", source),
            ("kind", kind),
        ):
            if not value:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting} is missing {field_name}.",
                    }
                )
        if target_type and target_type not in {"host", "service", "dependency"}:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.target_type",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.target_type must be host, service, or dependency.",
                }
            )
        elif target_type and target_id and not _network_inventory_target_exists(
            target_type=target_type,
            target_id=target_id,
            host_ids=host_ids,
            service_ids=service_ids,
            dependency_ids=dependency_ids,
        ):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.target_id",
                    "severity": "error",
                    "status": "unknown_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.target_id references unknown {target_type} {target_id}.",
                }
            )
        if "match" in raw and not isinstance(raw.get("match"), dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.match",
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"{setting}.match must be an object.",
                }
            )

    for index, raw in enumerate(dependencies):
        setting = f"network_inventory.dependencies[{index}]"
        if not isinstance(raw, dict):
            continue
        dependency_id = str(raw.get("id") or "").strip()
        from_type = str(raw.get("from_type") or "").strip().lower()
        from_id = str(raw.get("from_id") or "").strip()
        to_type = str(raw.get("to_type") or "").strip().lower()
        to_id = str(raw.get("to_id") or "").strip()
        relationship = str(raw.get("relationship") or "").strip()
        for field_name, value in (
            ("id", dependency_id),
            ("from_type", from_type),
            ("from_id", from_id),
            ("to_type", to_type),
            ("to_id", to_id),
            ("relationship", relationship),
        ):
            if not value:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting} is missing {field_name}.",
                    }
                )
        for direction, target_type, target_id in (("from", from_type, from_id), ("to", to_type, to_id)):
            if target_type and target_type not in {"host", "service", "dependency"}:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{direction}_type",
                        "severity": "error",
                        "status": "invalid_config_value",
                        "effective_source": effective_source,
                        "message": f"{setting}.{direction}_type must be host, service, or dependency.",
                    }
                )
            elif target_type and target_id and not _network_inventory_target_exists(
                target_type=target_type,
                target_id=target_id,
                host_ids=host_ids,
                service_ids=service_ids,
                dependency_ids=dependency_ids,
            ):
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{direction}_id",
                        "severity": "error",
                        "status": "unknown_reference",
                        "effective_source": effective_source,
                        "message": f"{setting}.{direction}_id references unknown {target_type} {target_id}.",
                    }
                )

    for index, raw in enumerate(power_targets):
        setting = f"network_inventory.power_targets[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"{setting} must be an object.",
                }
            )
            continue
        power_id = str(raw.get("id") or "").strip()
        host_id = str(raw.get("host_id") or "").strip()
        provider = str(raw.get("provider") or "").strip()
        entity_id = str(raw.get("entity_id") or "").strip()
        for field_name, value in (
            ("id", power_id),
            ("host_id", host_id),
            ("provider", provider),
            ("entity_id", entity_id),
        ):
            if not value:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{setting} is missing {field_name}.",
                    }
                )
        if host_id and host_id not in host_ids:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.host_id",
                    "severity": "error",
                    "status": "unknown_reference",
                    "effective_source": effective_source,
                    "message": f"{setting}.host_id references unknown host {host_id}.",
                }
            )
        if raw.get("enabled") not in (None, True, False):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.enabled",
                    "severity": "error",
                    "status": "invalid_config_value",
                    "effective_source": effective_source,
                    "message": f"{setting}.enabled must be a boolean.",
                }
            )
        capabilities = {str(item).strip() for item in raw.get("capabilities") or [] if str(item).strip()}
        if raw.get("enabled") is True and (
            provider != "home_assistant"
            or not entity_id.startswith("switch.")
            or "power_cycle" not in capabilities
        ):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": setting,
                    "severity": "error",
                    "status": "invalid_power_target",
                    "effective_source": effective_source,
                    "message": f"{setting} must use a Home Assistant switch entity with the power_cycle capability.",
                }
            )
        readiness = raw.get("readiness")
        checks = readiness.get("checks") if isinstance(readiness, dict) else None
        if raw.get("enabled") is True and (not isinstance(checks, list) or not checks):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"{setting}.readiness",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"{setting}.readiness must declare at least one power-cycle readiness check.",
                }
            )
        for check_index, check in enumerate(checks or []):
            check_setting = f"{setting}.readiness.checks[{check_index}]"
            if not isinstance(check, dict):
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": check_setting,
                        "severity": "error",
                        "status": "invalid_entry_shape",
                        "effective_source": effective_source,
                        "message": f"{check_setting} must be an object.",
                    }
                )
                continue
            check_id = str(check.get("id") or "").strip()
            kind = str(check.get("kind") or "").strip().lower()
            address = str(check.get("address") or "").strip()
            if not check_id or kind not in {"host_reachable", "tcp_reachable", "internet"}:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": check_setting,
                        "severity": "error",
                        "status": "invalid_config_value",
                        "effective_source": effective_source,
                        "message": f"{check_setting} must have an id and an allowlisted readiness kind.",
                    }
                )
            if kind in {"host_reachable", "tcp_reachable"} and not address:
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{check_setting}.address",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{check_setting}.address is required.",
                    }
                )
            if kind == "tcp_reachable":
                try:
                    port = int(check.get("port"))
                except (TypeError, ValueError):
                    port = 0
                if port < 1 or port > 65535:
                    findings.append(
                        {
                            "subsystem": "brain",
                            "setting": f"{check_setting}.port",
                            "severity": "error",
                            "status": "invalid_config_value",
                            "effective_source": effective_source,
                            "message": f"{check_setting}.port must be between 1 and 65535.",
                        }
                    )

    return findings


def _validate_service_control_refs(
    control_refs: Any,
    *,
    setting: str,
    effective_source: str,
) -> list[dict[str, Any]]:
    if control_refs in (None, ""):
        return []
    if not isinstance(control_refs, dict):
        return [
            {
                "subsystem": "brain",
                "setting": setting,
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": f"{setting} must be an object.",
            }
        ]
    service_control = control_refs.get("service_control")
    if service_control in (None, ""):
        return []
    if not isinstance(service_control, dict):
        return [
            {
                "subsystem": "brain",
                "setting": f"{setting}.service_control",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": f"{setting}.service_control must be an object.",
            }
        ]

    actions = service_control.get("actions")
    refs: list[tuple[str, Any]]
    if actions is None:
        refs = [(f"{setting}.service_control", service_control)]
    elif isinstance(actions, dict):
        refs = [
            (f"{setting}.service_control.actions.{action_id}", action_ref)
            for action_id, action_ref in actions.items()
        ]
    else:
        return [
            {
                "subsystem": "brain",
                "setting": f"{setting}.service_control.actions",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": f"{setting}.service_control.actions must be an object.",
            }
        ]

    findings: list[dict[str, Any]] = []
    for ref_setting, ref in refs:
        if not isinstance(ref, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": ref_setting,
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"{ref_setting} must be an object.",
                }
            )
            continue
        for field_name in ("host_id", "service_name"):
            if not str(ref.get(field_name) or "").strip():
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"{ref_setting}.{field_name}",
                        "severity": "error",
                        "status": "missing_required_config",
                        "effective_source": effective_source,
                        "message": f"{ref_setting} is missing {field_name}.",
                    }
                )
    return findings


def _validate_source_registry(
    source_registry: Any,
    *,
    effective_source: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if source_registry in (None, ""):
        return findings
    if not isinstance(source_registry, dict):
        findings.append(
            {
                "subsystem": "brain",
                "setting": "source_registry",
                "severity": "error",
                "status": "invalid_config_shape",
                "effective_source": effective_source,
                "message": "source_registry must be a JSON object keyed by source id.",
            }
        )
        return findings

    known_rooms = {
        str(value).strip().lower()
        for item in get_room_vocabulary()
        for value in (item.get("spoken_name"), *(item.get("aliases") or []))
        if str(value or "").strip()
    }
    room_vocabulary_available = bool(known_rooms)

    for source_id, raw_entry in sorted(source_registry.items()):
        if not isinstance(raw_entry, dict):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"source_registry.{source_id}",
                    "severity": "error",
                    "status": "invalid_entry_shape",
                    "effective_source": effective_source,
                    "message": f"source_registry entry for {source_id} must be an object.",
                }
            )
            continue

        source_type = str(raw_entry.get("source_type") or "").strip()
        if not source_type:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"source_registry.{source_id}.source_type",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"source_registry entry for {source_id} is missing source_type.",
                }
            )
        if "fixed" not in raw_entry or not isinstance(raw_entry.get("fixed"), bool):
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"source_registry.{source_id}.fixed",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"source_registry entry for {source_id} must define boolean fixed.",
                }
            )
            continue
        fixed = bool(raw_entry.get("fixed"))
        default_room = str(raw_entry.get("default_room") or "").strip().lower()
        if fixed and not default_room:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"source_registry.{source_id}.default_room",
                    "severity": "error",
                    "status": "missing_required_config",
                    "effective_source": effective_source,
                    "message": f"Fixed source {source_id} is missing default_room.",
                }
            )
            continue
        if not fixed and default_room:
            findings.append(
                {
                    "subsystem": "brain",
                    "setting": f"source_registry.{source_id}.default_room",
                    "severity": "error",
                    "status": "invalid_config_shape",
                    "effective_source": effective_source,
                    "message": f"Non-fixed source {source_id} must not define default_room.",
                }
            )
            continue
        if fixed and default_room:
            if default_room not in known_rooms:
                status = "unknown_room_reference" if room_vocabulary_available else "room_vocabulary_unavailable"
                message = (
                    f"Fixed source {source_id} default_room {default_room!r} does not match current HA room vocabulary."
                    if room_vocabulary_available
                    else f"Fixed source {source_id} default_room {default_room!r} could not be checked because HA room vocabulary is unavailable."
                )
                findings.append(
                    {
                        "subsystem": "brain",
                        "setting": f"source_registry.{source_id}.default_room",
                        "severity": "warning",
                        "status": status,
                        "effective_source": effective_source,
                        "message": message,
                    }
                )

    return findings


def build_satellite_config_report() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

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
        if env_name in (
            _KNOWN_BRAIN_ENV_NAMES
            | KNOWN_CONTROL_SERVICE_ENV_NAMES
            | CONTROL_SERVICE_HOST_BOOTSTRAP_ENV_NAMES
        ):
            continue
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


def build_control_service_config_report() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    api_key = os.getenv("ORACLE_SATELLITE_CONTROL_API_KEY")
    if api_key in (None, ""):
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "ORACLE_SATELLITE_CONTROL_API_KEY",
                "severity": "warning",
                "status": "missing_required_env",
                "effective_source": "env",
                "message": "Satellite control service API key is not configured.",
            }
        )

    for env_name in sorted(name for name in os.environ if name.startswith("ORACLE_")):
        if env_name in _KNOWN_BRAIN_ENV_NAMES or env_name in (
            KNOWN_SATELLITE_ENV_NAMES | SATELLITE_AUTHORITY_BOOTSTRAP_ENV_NAMES
        ):
            continue
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


def build_control_service_runtime_report(args: Namespace) -> list[dict[str, Any]]:
    findings = [
        item
        for item in build_control_service_config_report()
        if not (
            item.get("setting") == "ORACLE_SATELLITE_CONTROL_API_KEY"
            and str(getattr(args, "api_key", "") or "").strip()
        )
    ]

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
    if output_volume_backend and output_volume_backend != "alsa":
        findings.append(
            {
                "subsystem": "satellite_control_service",
                "setting": "output_volume_backend",
                "severity": "error",
                "status": "invalid_output_volume_backend",
                "effective_source": "argv",
                "message": "Satellite control service output volume backend must be alsa when configured.",
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

    return findings


def brain_config_has_errors(findings: list[dict[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").lower() == "error" for item in findings)


def format_brain_config_report(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "Brain config check: OK"

    lines = ["Brain config check:"]
    lines.extend(format_report_lines(findings))
    return "\n".join(lines)


def build_full_config_report() -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        ("Brain config check:", build_brain_config_report()),
        ("Pi satellite config check:", build_satellite_config_report()),
        ("Satellite control service config check:", build_control_service_config_report()),
    ]


def format_full_config_report(report_sections: list[tuple[str, list[dict[str, Any]]]]) -> str:
    return render_config_report_text(report_sections)


def log_brain_config_report() -> list[dict[str, Any]]:
    findings = build_brain_config_report()
    log_config_findings(findings, logger_name="oracle-brain.config")
    return findings


def _memory_event_type_for_config_finding(finding: dict[str, Any]) -> str:
    status = str(finding.get("status") or "").strip()
    if status in {"deprecated_local_truth", "deprecated_env"} or bool(finding.get("deprecated")):
        return "deprecated_config_source"
    if status in {"missing_required_config", "missing_required_env", "invalid_required_config"}:
        return "missing_required_config"
    return "config_warning"


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
        if severity == "warning":
            event_type = _memory_event_type_for_config_finding(finding)
            safe_record_event(
                event_type,
                severity="warning",
                source_id="brain",
                domain="config",
                status=str(finding.get("status") or "warning"),
                payload={
                    "subsystem": finding.get("subsystem") or "-",
                    "setting": finding.get("setting") or "-",
                    "effective_source": finding.get("effective_source") or "-",
                    "message": finding.get("message") or "",
                    "logger_name": logger_name,
                    "finding_status": finding.get("status") or "warning",
                },
            )
    return findings


def main() -> int:
    report_sections = build_full_config_report()
    print(format_full_config_report(report_sections))
    findings = [item for _, section in report_sections for item in section]
    return 1 if brain_config_has_errors(findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
