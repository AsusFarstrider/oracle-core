from __future__ import annotations

from typing import Any

from .provider_bridges.plex_music import MusicBridgeError, PlexMusicBridge
from .provider_bridges.service_control import check_service_available, check_storage_safety


ALLOWED_NETWORK_CONTROL_PRECONDITIONS = {
    "plex_no_active_streams",
    "pihole_restart_continuity",
    "host_storage_safe_for_restart",
}

_PRECONDITION_TARGET_OPERATIONS = {
    "plex_no_active_streams": {("service", "restart_service"), ("host", "restart_host")},
    "pihole_restart_continuity": {("service", "restart_service"), ("host", "restart_host")},
    "host_storage_safe_for_restart": {("host", "restart_host")},
}


def network_control_precondition_matches_target(
    *,
    precondition_id: str,
    target_type: str,
    target_id: str,
    action_id: str,
) -> bool:
    del target_id
    return (target_type, action_id) in _PRECONDITION_TARGET_OPERATIONS.get(precondition_id, set())


def evaluate_network_control_preconditions(
    *,
    action_policy: dict[str, Any],
    target_type: str,
    target_id: str,
    music_settings: dict[str, Any],
    service_control_settings: dict[str, Any],
    inventory: dict[str, Any] | None = None,
    control_policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for raw_id in action_policy.get("required_preconditions") or []:
        precondition_id = str(raw_id or "").strip()
        if precondition_id == "plex_no_active_streams":
            results.append(_plex_no_active_streams(music_settings))
        elif precondition_id == "pihole_restart_continuity":
            results.append(
                _pihole_restart_continuity(
                    target_type=target_type,
                    target_id=target_id,
                    service_control_settings=service_control_settings,
                    inventory=inventory or {},
                    control_policy=control_policy or {},
                )
            )
        elif precondition_id == "host_storage_safe_for_restart":
            results.append(
                _host_storage_safe_for_restart(
                    target_type=target_type,
                    target_id=target_id,
                    service_control_settings=service_control_settings,
                )
            )
        else:
            results.append(
                {
                    "id": precondition_id,
                    "provider": "oracle",
                    "status": "unavailable",
                    "observed_value": None,
                    "summary": "The required network-control precondition is not implemented.",
                }
            )
    return results


def with_inherited_host_preconditions(
    *,
    action_policy: dict[str, Any],
    target_type: str,
    target_id: str,
    inventory: dict[str, Any],
    control_policy: dict[str, Any],
) -> dict[str, Any]:
    effective = dict(action_policy)
    required = [
        str(item).strip()
        for item in action_policy.get("required_preconditions") or []
        if str(item).strip()
    ]
    if target_type != "host" or str(action_policy.get("action_id") or "").strip() != "restart_host":
        effective["required_preconditions"] = required
        return effective

    service_ids = {
        str(service.get("id") or "").strip()
        for service in inventory.get("services") or []
        if isinstance(service, dict)
        and str(service.get("host_id") or "").strip() == target_id
        and str(service.get("id") or "").strip()
    }
    for service_policy in control_policy.get("actions") or []:
        if not isinstance(service_policy, dict):
            continue
        if str(service_policy.get("target_type") or "").strip().lower() != "service":
            continue
        if str(service_policy.get("target_id") or "").strip() not in service_ids:
            continue
        if str(service_policy.get("action_id") or "").strip() != "restart_service":
            continue
        for raw_id in service_policy.get("required_preconditions") or []:
            precondition_id = str(raw_id or "").strip()
            if precondition_id and precondition_id not in required:
                required.append(precondition_id)
    effective["required_preconditions"] = required
    return effective


def _plex_no_active_streams(music_settings: dict[str, Any]) -> dict[str, Any]:
    try:
        status = PlexMusicBridge(settings=music_settings).get_active_sessions_status()
    except MusicBridgeError as exc:
        return {
            "id": "plex_no_active_streams",
            "provider": "plex",
            "status": "unavailable",
            "observed_value": None,
            "summary": f"Plex active stream check is unavailable: {exc.detail}",
        }
    active_count = int(status.get("active_stream_count") or 0)
    if active_count > 0:
        return {
            "id": "plex_no_active_streams",
            "provider": "plex",
            "status": "failed",
            "observed_value": active_count,
            "summary": f"Plex has {active_count} active stream(s), so Oracle will not restart it.",
        }
    return {
        "id": "plex_no_active_streams",
        "provider": "plex",
        "status": "passed",
        "observed_value": active_count,
        "summary": "Plex has no active streams.",
    }


def _pihole_restart_continuity(
    *,
    target_type: str,
    target_id: str,
    service_control_settings: dict[str, Any],
    inventory: dict[str, Any],
    control_policy: dict[str, Any],
) -> dict[str, Any]:
    target, alternate = _continuity_service_pair(
        target_type=target_type,
        target_id=target_id,
        inventory=inventory,
        control_policy=control_policy,
    )
    if target is None or alternate is None:
        return _invalid_target("pihole_restart_continuity")
    target_result = check_service_available(
        settings=service_control_settings,
        host_id=target[0],
        service_name=target[1],
    )
    alternate_result = check_service_available(
        settings=service_control_settings,
        host_id=alternate[0],
        service_name=alternate[1],
    )
    target_healthy = target_result.get("ok") is True
    target_known_down = target_result.get("available") is False
    alternate_healthy = alternate_result.get("ok") is True
    if alternate_healthy:
        status = "passed"
        summary = "The alternate Pi-hole is healthy, so DNS continuity is preserved."
    elif target_known_down:
        status = "passed"
        summary = "The target Pi-hole is already down, so a recovery restart does not create a new DNS outage."
    elif target_healthy:
        status = "failed"
        summary = "The alternate Pi-hole is not healthy, so Oracle will not take the healthy Pi-hole offline."
    else:
        status = "unavailable"
        summary = "Pi-hole health could not be determined safely, so Oracle will not restart it."
    return {
        "id": "pihole_restart_continuity",
        "provider": "service_control",
        "status": status,
        "observed_value": {
            "target": "healthy" if target_healthy else "down" if target_known_down else "unknown",
            "alternate": "healthy" if alternate_healthy else "down" if alternate_result.get("available") is False else "unknown",
        },
        "summary": summary,
    }


def _host_storage_safe_for_restart(
    *,
    target_type: str,
    target_id: str,
    service_control_settings: dict[str, Any],
) -> dict[str, Any]:
    if target_type != "host" or not target_id:
        return _invalid_target("host_storage_safe_for_restart")
    result = check_storage_safety(
        settings=service_control_settings,
        host_id=target_id,
        profile_id="host_storage_safe_for_restart",
    )
    if result.get("configured") is not True:
        status = "unavailable"
    else:
        status = "passed" if result.get("ok") is True else "failed"
    check_count = int(result.get("check_count") or 0)
    passed_count = int(result.get("passed_count") or 0)
    return {
        "id": "host_storage_safe_for_restart",
        "provider": "service_control",
        "status": status,
        "observed_value": f"{passed_count}/{check_count}",
        "summary": (
            "Host storage preflight passed for RAID, writable mount, and sharing service."
            if status == "passed"
            else "Host storage preflight did not pass, so Oracle will not restart the host."
        ),
    }


def _continuity_service_pair(
    *,
    target_type: str,
    target_id: str,
    inventory: dict[str, Any],
    control_policy: dict[str, Any],
) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    services = {
        str(item.get("id") or "").strip(): str(item.get("host_id") or "").strip()
        for item in inventory.get("services") or []
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and str(item.get("host_id") or "").strip()
    }
    candidate_ids = [
        str(item.get("target_id") or "").strip()
        for item in control_policy.get("actions") or []
        if isinstance(item, dict)
        and str(item.get("target_type") or "").strip() == "service"
        and str(item.get("action_id") or "").strip() == "restart_service"
        and "pihole_restart_continuity" in {
            str(value or "").strip() for value in item.get("required_preconditions") or []
        }
    ]
    candidates = [
        (services[service_id], service_id)
        for service_id in candidate_ids
        if service_id in services
    ]
    target: tuple[str, str] | None = None
    if target_type == "service":
        target = next((item for item in candidates if item[1] == target_id), None)
    elif target_type == "host":
        hosted = [item for item in candidates if item[0] == target_id]
        if len(hosted) == 1:
            target = hosted[0]
    peers = [item for item in candidates if item != target]
    return target, peers[0] if len(peers) == 1 else None


def _invalid_target(precondition_id: str) -> dict[str, Any]:
    return {
        "id": precondition_id,
        "provider": "oracle",
        "status": "unavailable",
        "observed_value": None,
        "summary": "The required precondition is not valid for this Oracle target.",
    }
