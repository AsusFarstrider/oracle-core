from __future__ import annotations

import copy
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, request

from .config import (
    get_librenms_settings,
    get_music_settings,
    get_network_inventory_settings,
    get_network_probe_settings,
    get_network_router_control_settings,
    get_network_service_control_settings,
)
from .provider_bridges.librenms import LibreNmsBridge
from .provider_bridges.network_probe import NetworkProbeBridge
from .provider_bridges.router_control import get_available_router_actions
from .provider_bridges.service_control import get_available_service_actions
from .network_status import build_network_status_snapshot
from .network_runtime import CanonicalNetworkExecution


_NETWORK_KEYWORDS = ("network", "internet", "connection", "online", "offline")
_NETWORK_STATUS_WORDS = (
    "okay",
    "ok",
    "working",
    "down",
    "up",
    "problem",
    "problems",
    "healthy",
    "health",
    "status",
    "issues",
)
_NETWORK_EXACT_PATTERNS = (
    r"^how(?:'s| is)? the network\??$",
    r"^is the network (?:okay|ok|up|working)\??$",
    r"^are there (?:any )?network problems\??$",
    r"^how(?:'s| is)? the internet\??$",
    r"^is the internet (?:down|up|working)\??$",
    r"^are there (?:any )?internet problems\??$",
)
_NETWORK_STATUS_CACHE_TTL_SECONDS = 30.0
_NETWORK_STATUS_CACHE: dict[str, Any] = {}
_NETWORK_STATUS_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class NetworkQuery:
    action: str
    original_text: str


def is_network_request(text: str) -> bool:
    return parse_network_query(text) is not None


def parse_network_query(text: str) -> NetworkQuery | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None
    if any(re.match(pattern, normalized) for pattern in _NETWORK_EXACT_PATTERNS):
        return NetworkQuery(action="network_summary", original_text=normalized)
    if any(keyword in normalized for keyword in _NETWORK_KEYWORDS) and any(word in normalized for word in _NETWORK_STATUS_WORDS):
        return NetworkQuery(action="network_summary", original_text=normalized)
    return None


def get_network_summary(
    *,
    canonical_execution: CanonicalNetworkExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.summary()
    if canonical_authority:
        return _unconfigured_network_summary()
    probe = _observation_dict(NetworkProbeBridge().get_internet_status(settings=get_network_probe_settings()))
    monitoring = _observation_dict(LibreNmsBridge().get_monitoring_status(settings=get_librenms_settings()))
    actions_available = get_available_actions()
    problems = _collect_problems(probe, monitoring)
    status = _summarize_network_status(
        internet_status=str(probe.get("status") or "unknown"),
        monitoring_status=str(monitoring.get("status") or "unknown"),
    )
    generated_at = str(probe.get("checked_at") or monitoring.get("checked_at") or "")
    return {
        "status": status,
        "internet": {
            "status": str(probe.get("status") or "unknown"),
            "checked_at": probe.get("checked_at"),
            "source": probe.get("source"),
            "detail": probe.get("detail"),
        },
        "monitoring": {
            "status": str(monitoring.get("status") or "unknown"),
            "checked_at": monitoring.get("checked_at"),
            "source": monitoring.get("source"),
            "detail": monitoring.get("detail"),
        },
        "problems": problems,
        "actions_available": actions_available,
        "generated_at": generated_at,
    }


def get_network_status_snapshot(
    *,
    force_refresh: bool = False,
    canonical_execution: CanonicalNetworkExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    if canonical_execution is not None:
        return canonical_execution.status_snapshot(force_refresh=force_refresh)
    if canonical_authority:
        raise RuntimeError("Canonical network capability is not configured.")
    with _NETWORK_STATUS_CACHE_LOCK:
        now_monotonic = time.monotonic()
        cached_snapshot = _NETWORK_STATUS_CACHE.get("snapshot")
        cached_monotonic = float(_NETWORK_STATUS_CACHE.get("stored_monotonic") or 0.0)
        if not force_refresh and isinstance(cached_snapshot, dict) and cached_monotonic:
            age_seconds = max(0.0, now_monotonic - cached_monotonic)
            if age_seconds <= _NETWORK_STATUS_CACHE_TTL_SECONDS:
                return _with_cache_metadata(
                    cached_snapshot,
                    cached_at=str(_NETWORK_STATUS_CACHE.get("cached_at") or ""),
                    age_seconds=age_seconds,
                    cache_hit=True,
                )

        raw_probe = NetworkProbeBridge().get_internet_status(settings=get_network_probe_settings())
        raw_monitoring = LibreNmsBridge().get_monitoring_status(settings=get_librenms_settings())
        probe = _observation_dict(raw_probe)
        monitoring = _observation_dict(raw_monitoring)
        inventory = get_network_inventory_settings()
        snapshot = build_network_status_snapshot(
            inventory=inventory,
            probe=probe,
            monitoring=monitoring,
            satellite_control=_get_satellite_control_status(inventory=inventory),
        )
        cached_at = datetime.now().astimezone().isoformat()
        _NETWORK_STATUS_CACHE["snapshot"] = copy.deepcopy(snapshot)
        _NETWORK_STATUS_CACHE["stored_monotonic"] = now_monotonic
        _NETWORK_STATUS_CACHE["cached_at"] = cached_at
        return _with_cache_metadata(
            snapshot,
            cached_at=cached_at,
            age_seconds=0.0,
            cache_hit=False,
        )


def _observation_dict(value: Any) -> dict[str, Any]:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return dict(value)


def _get_satellite_control_status(*, inventory: dict[str, Any]) -> dict[str, Any]:
    monitors = [
        monitor
        for monitor in inventory.get("monitors") or []
        if isinstance(monitor, dict)
        and str(monitor.get("source") or "").strip().lower() == "oracle_satellite_control"
    ]
    checked_at = datetime.now().astimezone().isoformat()
    if not monitors:
        return {
            "status": "unconfigured",
            "checked_at": checked_at,
            "source": "oracle_satellite_control",
            "detail": "No Oracle satellite-control monitors are configured.",
            "checks": [],
        }

    satellite_controls = dict(get_music_settings().get("satellites") or {})
    checks = [_probe_satellite_control_monitor(monitor, satellite_controls=satellite_controls) for monitor in monitors]
    statuses = [str(item.get("status") or "unknown") for item in checks]
    if any(status == "down" for status in statuses):
        status = "degraded"
    elif all(status == "healthy" for status in statuses):
        status = "healthy"
    elif any(status == "healthy" for status in statuses):
        status = "degraded"
    else:
        status = "unknown"

    return {
        "status": status,
        "checked_at": checked_at,
        "source": "oracle_satellite_control",
        "detail": "Oracle satellite-control health checks completed.",
        "checks": checks,
    }


def _probe_satellite_control_monitor(
    monitor: dict[str, Any],
    *,
    satellite_controls: dict[str, Any],
) -> dict[str, Any]:
    monitor_id = str(monitor.get("id") or "").strip()
    match = monitor.get("match") if isinstance(monitor.get("match"), dict) else {}
    source_id = str(match.get("source_id") or "").strip()
    if not source_id:
        return {
            "monitor_id": monitor_id,
            "source_id": "",
            "status": "unknown",
            "detail": "Satellite-control monitor is missing match.source_id.",
        }
    settings = satellite_controls.get(source_id)
    if not isinstance(settings, dict):
        return {
            "monitor_id": monitor_id,
            "source_id": source_id,
            "status": "unknown",
            "detail": f"No satellite control endpoint is configured for {source_id}.",
        }
    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return {
            "monitor_id": monitor_id,
            "source_id": source_id,
            "status": "unknown",
            "detail": f"Satellite control endpoint for {source_id} has no base_url.",
        }
    timeout_seconds = max(1, int(settings.get("timeout_seconds") or 5))
    api_key = str(settings.get("api_key") or "").strip()
    req = request.Request(f"{base_url}/health", method="GET")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 200) or 200)
    except error.HTTPError as exc:
        return {
            "monitor_id": monitor_id,
            "source_id": source_id,
            "status": "down",
            "detail": f"Satellite control health for {source_id} returned HTTP {exc.code}.",
        }
    except error.URLError as exc:
        return {
            "monitor_id": monitor_id,
            "source_id": source_id,
            "status": "down",
            "detail": f"Satellite control health for {source_id} is unreachable: {exc.reason}",
        }
    if 200 <= status_code < 300:
        return {
            "monitor_id": monitor_id,
            "source_id": source_id,
            "status": "healthy",
            "detail": f"Satellite control health for {source_id} is reachable.",
        }
    return {
        "monitor_id": monitor_id,
        "source_id": source_id,
        "status": "down",
        "detail": f"Satellite control health for {source_id} returned HTTP {status_code}.",
    }


def clear_network_status_cache() -> None:
    with _NETWORK_STATUS_CACHE_LOCK:
        _NETWORK_STATUS_CACHE.clear()


def _with_cache_metadata(
    snapshot: dict[str, Any],
    *,
    cached_at: str,
    age_seconds: float,
    cache_hit: bool,
) -> dict[str, Any]:
    payload = copy.deepcopy(snapshot)
    payload["cached_at"] = cached_at
    payload["cache_age_seconds"] = round(max(0.0, age_seconds), 3)
    payload["cache_ttl_seconds"] = int(_NETWORK_STATUS_CACHE_TTL_SECONDS)
    payload["cache_hit"] = cache_hit
    return payload


def get_available_actions() -> list[dict[str, str]]:
    actions = get_available_service_actions(get_network_service_control_settings())
    actions.extend(get_available_router_actions(get_network_router_control_settings()))
    return actions


def build_network_response(
    text: str,
    *,
    canonical_execution: CanonicalNetworkExecution | None = None,
    canonical_authority: bool = False,
) -> tuple[str, dict[str, Any]]:
    del text
    summary = get_network_summary(
        canonical_execution=canonical_execution,
        canonical_authority=canonical_authority,
    )
    speech = summarize_network_speech(summary)
    return speech, summary


def summarize_network_speech(summary: dict[str, Any]) -> str:
    status = str(summary.get("status") or "unknown")
    problems = [str(item).strip() for item in summary.get("problems") or [] if str(item).strip()]
    internet_status = str((summary.get("internet") or {}).get("status") or "unknown")
    monitoring_detail = str((summary.get("monitoring") or {}).get("detail") or "").strip()

    if status == "healthy":
        return "The network looks healthy."
    if status == "down" or internet_status == "down":
        return "The internet appears to be down."
    if status == "degraded":
        if problems:
            return f"The network looks degraded. I see one problem: {problems[0]}"
        return "The network looks degraded."
    if status == "pending":
        return "I'm still checking the network."
    if monitoring_detail == "LibreNMS not configured." and internet_status == "unknown":
        return "I'm not sure yet. Network monitoring is not fully configured."
    return "I'm not sure yet. Network monitoring is not fully configured."


def build_ui_network_health_snapshot(
    *,
    canonical_execution: CanonicalNetworkExecution | None = None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    summary = get_network_summary(
        canonical_execution=canonical_execution,
        canonical_authority=canonical_authority,
    )
    return {
        "status": str(summary.get("status") or "unknown"),
        "label": "Network",
        "summary": summarize_network_speech(summary),
        "detail": _ui_detail(summary),
        "generated_at": summary.get("generated_at"),
    }


def _collect_problems(probe: dict[str, Any], monitoring: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for candidate in (probe, monitoring):
        for problem in candidate.get("problems") or []:
            normalized = str(problem or "").strip()
            if normalized:
                problems.append(normalized)
    return problems[:5]


def _summarize_network_status(*, internet_status: str, monitoring_status: str) -> str:
    if internet_status == "down":
        return "down"
    if internet_status == "degraded":
        return "degraded"
    if internet_status == "healthy" and monitoring_status in {"healthy", "unknown"}:
        return "healthy"
    if internet_status == "healthy" and monitoring_status in {"degraded", "down"}:
        return "degraded"
    if internet_status == "unknown" and monitoring_status in {"degraded", "down"}:
        return "degraded"
    if internet_status == "unknown" and monitoring_status == "healthy":
        return "pending"
    if internet_status == "unknown" and monitoring_status == "unknown":
        return "unknown"
    return "pending"


def _ui_detail(summary: dict[str, Any]) -> str:
    problems = [str(item).strip() for item in summary.get("problems") or [] if str(item).strip()]
    if problems:
        return problems[0]
    internet_detail = str((summary.get("internet") or {}).get("detail") or "").strip()
    if internet_detail:
        return internet_detail
    monitoring_detail = str((summary.get("monitoring") or {}).get("detail") or "").strip()
    if monitoring_detail:
        return monitoring_detail
    return "Network status is unavailable."


def _unconfigured_network_summary() -> dict[str, Any]:
    return {
        "status": "unknown",
        "internet": {"status": "unknown", "detail": "Network is not configured."},
        "monitoring": {"status": "unknown", "detail": "Network is not configured."},
        "problems": [],
        "actions_available": [],
        "generated_at": "",
    }
