"""Canonical runtime boundary for retired process-local configuration access.

The standard Oracle runtime receives typed settings through the canonical
application composition.  The former environment and local-JSON authority is
retained only in the private compatibility boundary until Stage 5.

These fail-closed names keep imports explicit while mixed modules finish their
bounded Stage 5 simplification.  Canonical execution must inject its typed
settings before reaching any of them.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from .constants import CACHE_PATH


class CanonicalConfigurationRequired(RuntimeError):
    """Raised when a retired process-local configuration path is reached."""


def _canonical_only(name: str) -> NoReturn:
    raise CanonicalConfigurationRequired(
        f"{name} is unavailable in canonical runtime; use the selected "
        "canonical application composition."
    )


def load_home_assistant_cache() -> dict[str, Any]:
    """Load reconstructible Home Assistant cache data, not configuration authority."""

    if not CACHE_PATH.exists():
        return {}
    with CACHE_PATH.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def load_local_config() -> dict[str, Any]:
    """Expose an empty retired-input surface for rejection reporting only."""

    return {}


def load_network_inventory_config() -> None:
    return None


def load_network_control_config() -> None:
    return None


def load_network_service_control_config() -> None:
    return None


def load_network_router_control_config() -> None:
    return None


def load_orchestration_config() -> None:
    return None


def load_notifications_config() -> None:
    return None


def load_home_automation_runbooks_config() -> None:
    return None


def get_tts_provider() -> NoReturn:
    return _canonical_only("get_tts_provider")


def get_stt_provider() -> NoReturn:
    return _canonical_only("get_stt_provider")


def get_tts_settings() -> NoReturn:
    return _canonical_only("get_tts_settings")


def get_stt_settings() -> NoReturn:
    return _canonical_only("get_stt_settings")


def get_wake_arbitration_settings() -> NoReturn:
    return _canonical_only("get_wake_arbitration_settings")


def get_home_assistant_settings() -> NoReturn:
    return _canonical_only("get_home_assistant_settings")


def get_ollama_settings() -> NoReturn:
    return _canonical_only("get_ollama_settings")


def get_ollama_request_settings() -> NoReturn:
    return _canonical_only("get_ollama_request_settings")


def get_fallback_router_settings() -> NoReturn:
    return _canonical_only("get_fallback_router_settings")


def get_facts_settings() -> NoReturn:
    return _canonical_only("get_facts_settings")


def get_weather_current_settings() -> NoReturn:
    return _canonical_only("get_weather_current_settings")


def get_weather_settings() -> NoReturn:
    return _canonical_only("get_weather_settings")


def get_weather_history_settings() -> NoReturn:
    return _canonical_only("get_weather_history_settings")


def get_forecast_settings() -> NoReturn:
    return _canonical_only("get_forecast_settings")


def get_music_settings() -> NoReturn:
    return _canonical_only("get_music_settings")


def get_oracle_base_url() -> NoReturn:
    return _canonical_only("get_oracle_base_url")


def get_satellite_control_target(source: str | None) -> NoReturn:
    del source
    return _canonical_only("get_satellite_control_target")


def get_satellite_music_backend_hint(
    source: str | None,
    *,
    media_type: str | None = None,
) -> NoReturn:
    del source, media_type
    return _canonical_only("get_satellite_music_backend_hint")


def get_audiobook_settings() -> NoReturn:
    return _canonical_only("get_audiobook_settings")


def get_audiobook_connection_settings(user_id: str | None = None) -> NoReturn:
    del user_id
    return _canonical_only("get_audiobook_connection_settings")


def get_calendar_settings() -> NoReturn:
    return _canonical_only("get_calendar_settings")


def get_news_settings() -> NoReturn:
    return _canonical_only("get_news_settings")


def get_network_probe_settings() -> NoReturn:
    return _canonical_only("get_network_probe_settings")


def get_librenms_settings() -> NoReturn:
    return _canonical_only("get_librenms_settings")


def get_apprise_settings() -> NoReturn:
    return _canonical_only("get_apprise_settings")


def get_openclaw_settings() -> NoReturn:
    return _canonical_only("get_openclaw_settings")


def get_network_service_control_settings() -> NoReturn:
    return _canonical_only("get_network_service_control_settings")


def get_network_router_control_settings() -> NoReturn:
    return _canonical_only("get_network_router_control_settings")


def get_network_inventory_settings() -> NoReturn:
    return _canonical_only("get_network_inventory_settings")


def get_network_control_policy_settings() -> NoReturn:
    return _canonical_only("get_network_control_policy_settings")


def get_orchestration_settings() -> NoReturn:
    return _canonical_only("get_orchestration_settings")


def get_home_assistant_event_ingress_token() -> NoReturn:
    return _canonical_only("get_home_assistant_event_ingress_token")


def get_home_automation_runbook_settings() -> NoReturn:
    return _canonical_only("get_home_automation_runbook_settings")


def get_notification_settings() -> NoReturn:
    return _canonical_only("get_notification_settings")


def get_source_registry() -> NoReturn:
    return _canonical_only("get_source_registry")


def get_user_registry() -> NoReturn:
    return _canonical_only("get_user_registry")
