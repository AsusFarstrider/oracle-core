from __future__ import annotations

from datetime import UTC, datetime
from urllib import parse as urlparse

from fastapi import HTTPException, Response

from .configuration.domain_models import (
    HomeAssistantControlViewReference,
    HomeAssistantObjectMapping,
    HomeAssistantViewReference,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .home_assistant_camera import HomeAssistantSnapshotError, fetch_snapshot, fetch_snapshot_metadata
from .provider_bridges.home_assistant import HomeAssistantBridge


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fetch_entity_state(base_url: str, token: str, entity_id: str) -> dict[str, object] | None:
    return HomeAssistantBridge(base_url=base_url, token=token).fetch_entity_state(entity_id)


def _resolve_house_entity_label(entity_id: str, label: str, cached_names: dict[str, str]) -> str:
    return str(cached_names.get(entity_id) or label or entity_id).strip() or entity_id


def _summarize_house_door_contact_state(state_payload: dict[str, object] | None) -> str:
    if not isinstance(state_payload, dict):
        return "unknown"
    normalized = str(state_payload.get("state") or "").strip().lower()
    if normalized in {"on", "open"}:
        return "open"
    if normalized in {"off", "closed"}:
        return "closed"
    return normalized or "unknown"


def _summarize_house_lock_state(state_payload: dict[str, object] | None) -> str:
    if not isinstance(state_payload, dict):
        return "unknown"
    normalized = str(state_payload.get("state") or "").strip().lower()
    if normalized in {"locked", "unlocked", "locking", "unlocking", "jammed"}:
        return normalized
    return normalized or "unknown"


def _serialize_house_temperature_state(entity_id: str, label: str, state_payload: dict[str, object] | None) -> dict[str, object]:
    result: dict[str, object] = {
        "entity_id": entity_id,
        "label": label,
        "available": isinstance(state_payload, dict),
        "value_f": None,
        "unit": None,
        "state": None,
    }
    if not isinstance(state_payload, dict):
        return result
    attributes = state_payload.get("attributes") or {}
    result["state"] = state_payload.get("state")
    if isinstance(attributes, dict):
        result["unit"] = attributes.get("unit_of_measurement")
    try:
        result["value_f"] = float(state_payload.get("state"))
    except (TypeError, ValueError):
        result["value_f"] = None
    return result


def _serialize_house_climate_state(
    entity_id: str,
    label: str,
    state_payload: dict[str, object] | None,
    *,
    actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "entity_id": entity_id,
        "label": label,
        "available": isinstance(state_payload, dict),
        "state": None,
        "target_temperature_f": None,
        "current_temperature_f": None,
        "hvac_action": None,
        "actions": [],
    }
    if not isinstance(state_payload, dict):
        return result
    attributes = state_payload.get("attributes") or {}
    result["state"] = state_payload.get("state")
    if isinstance(attributes, dict):
        result["target_temperature_f"] = attributes.get("temperature")
        result["current_temperature_f"] = attributes.get("current_temperature")
        result["hvac_action"] = attributes.get("hvac_action")
    result["actions"] = actions or []
    return result


def _serialize_house_light_state(
    entity_id: str,
    label: str,
    state_payload: dict[str, object] | None,
    *,
    actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "entity_id": entity_id,
        "label": label,
        "available": isinstance(state_payload, dict),
        "state": None,
        "brightness_pct": None,
        "actions": [],
    }
    if not isinstance(state_payload, dict):
        return result
    attributes = state_payload.get("attributes") or {}
    result["state"] = state_payload.get("state")
    if isinstance(attributes, dict):
        brightness = attributes.get("brightness")
        try:
            result["brightness_pct"] = round((float(brightness) / 255.0) * 100)
        except (TypeError, ValueError, ZeroDivisionError):
            result["brightness_pct"] = None
    result["actions"] = actions or []
    return result


def _serialize_house_camera_state(
    *,
    camera_id: str,
    entity_id: str,
    label: str,
    state_payload: dict[str, object] | None,
    snapshot_path: str | None,
    snapshot_metadata,
) -> dict[str, object]:
    result: dict[str, object] = {
        "camera_id": camera_id,
        "entity_id": entity_id,
        "label": label,
        "available": isinstance(state_payload, dict),
        "state": None,
        "view_supported": False,
        "snapshot_supported": bool(snapshot_path),
        "snapshot_available": bool(getattr(snapshot_metadata, "available", False)),
    }
    if snapshot_path:
        version = getattr(snapshot_metadata, "last_modified", None) or _build_ui_generated_at()
        result["snapshot_url"] = f"/api/ui/house/cameras/{camera_id}/snapshot?v={urlparse.quote(str(version), safe='')}"
        result["snapshot_last_modified"] = getattr(snapshot_metadata, "last_modified", None)
        result["snapshot_content_type"] = getattr(snapshot_metadata, "content_type", None)
        result["snapshot_content_length"] = getattr(snapshot_metadata, "content_length", None)
    if not isinstance(state_payload, dict):
        return result
    result["state"] = state_payload.get("state")
    return result


def build_ui_house_snapshot(
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
) -> dict[str, object]:
    return _build_canonical_ui_house_snapshot(home_assistant_settings)


def ui_house_camera_snapshot_impl(
    camera_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
) -> Response:
    return _canonical_camera_snapshot(camera_id, home_assistant_settings)


def build_canonical_ui_home_assistant_snapshot(
    settings: HomeAssistantRuntimeSettings | None,
) -> dict[str, object]:
    if settings is None or not settings.enabled:
        return {"home_assistant": {"ok": False, "detail": "Home Assistant is disabled."}, "controls": [], "actions": []}
    bridge = _runtime_bridge(settings)
    controls = [
        _serialize_canonical_control(reference, settings, bridge)
        for reference in settings.views.home.controls
    ]
    return {
        "home_assistant": {"ok": True, "detail": None},
        "controls": controls,
        "actions": [_serialize_canonical_action(item, settings) for item in settings.views.home.actions],
    }


def _build_canonical_ui_house_snapshot(
    settings: HomeAssistantRuntimeSettings | None,
) -> dict[str, object]:
    if settings is None or not settings.enabled:
        return {
            "generated_at": _build_ui_generated_at(),
            "home_assistant": {"ok": False, "detail": "Home Assistant is disabled."},
            "front_door": _empty_front_door(),
            "temperatures": [],
            "climate": [],
            "lights": [],
            "cameras": [],
            "actions": [],
            "notice": "Camera still snapshots are proxied through Oracle from Home Assistant. Live camera streams are not exposed through Alpha /api/ui yet.",
            "refresh_after_seconds": 30,
        }
    bridge = _runtime_bridge(settings)
    view = settings.views.house
    front_door = (
        _serialize_canonical_control(view.front_door, settings, bridge, house_entry=True)
        if view.front_door is not None
        else _empty_front_door()
    )
    temperatures = []
    for item in view.temperatures:
        mapping = _required_object_mapping(settings, item.mapping_id, "entity")
        temperatures.append(
            _serialize_house_temperature_state(
                mapping.entity_id,
                item.label or mapping.oracle_id.replace("_", " ").title(),
                bridge.fetch_entity_state(mapping.entity_id),
            )
        )
    climate = []
    for item in view.climate:
        mapping = _required_object_mapping(settings, item.mapping_id, "entity")
        state_payload = bridge.fetch_entity_state(mapping.entity_id)
        climate.append(
            _serialize_house_climate_state(
                mapping.entity_id,
                item.label or mapping.oracle_id.replace("_", " ").title(),
                state_payload,
                actions=_canonical_control_actions(item, settings, state_payload),
            )
        )
    lights = []
    for item in view.lights:
        mapping = _required_object_mapping(settings, item.mapping_id, "entity")
        state_payload = bridge.fetch_entity_state(mapping.entity_id)
        lights.append(
            _serialize_house_light_state(
                mapping.entity_id,
                item.label or mapping.oracle_id.replace("_", " ").title(),
                state_payload,
                actions=_canonical_control_actions(item, settings, state_payload),
            )
        )
    cameras = []
    for item in view.cameras:
        mapping = _required_object_mapping(settings, item.mapping_id, "camera")
        snapshot_path = _snapshot_path(settings, item.snapshot_ref)
        cameras.append(
            _serialize_house_camera_state(
                camera_id=mapping.oracle_id,
                entity_id=mapping.entity_id,
                label=item.label or mapping.oracle_id.replace("_", " ").title(),
                state_payload=bridge.fetch_entity_state(mapping.entity_id),
                snapshot_path=snapshot_path,
                snapshot_metadata=(
                    fetch_snapshot_metadata(
                        base_url=settings.base_url or "",
                        token=settings.credential or "",
                        snapshot_path=snapshot_path,
                        snapshot_root=settings.snapshot_root or "",
                        timeout_seconds=float(settings.timeout_seconds or 3),
                    )
                    if snapshot_path
                    else None
                ),
            )
        )
    return {
        "generated_at": _build_ui_generated_at(),
        "home_assistant": {"ok": True, "detail": None},
        "front_door": front_door,
        "temperatures": temperatures,
        "climate": climate,
        "lights": lights,
        "cameras": cameras,
        "actions": [_serialize_canonical_action(item, settings) for item in view.actions],
        "notice": "Camera still snapshots are proxied through Oracle from Home Assistant. Live camera streams are not exposed through Alpha /api/ui yet.",
        "refresh_after_seconds": 30,
    }


def _serialize_canonical_control(
    reference: HomeAssistantControlViewReference,
    settings: HomeAssistantRuntimeSettings,
    bridge: HomeAssistantBridge,
    *,
    house_entry: bool = False,
) -> dict[str, object]:
    mapping = _required_object_mapping(settings, reference.mapping_id, "entity")
    state_payload = bridge.fetch_entity_state(mapping.entity_id)
    domain = mapping.entity_id.split(".", 1)[0]
    label = reference.label or mapping.oracle_id.replace("_", " ").title()
    if house_entry or domain == "lock":
        status_payload = None
        if reference.status_mapping_id is not None:
            status_mapping = settings.mapping(reference.status_mapping_id)
            status_payload = bridge.fetch_entity_state(status_mapping.entity_id) if status_mapping is not None else None
        lock_state = _summarize_house_lock_state(state_payload)
        open_state = _summarize_house_door_contact_state(status_payload)
        actions = _canonical_control_actions(reference, settings, state_payload)
        action = actions[0] if actions else None
        if house_entry:
            return {
                "entity_id": mapping.entity_id,
                "label": label,
                "available": isinstance(state_payload, dict) or isinstance(status_payload, dict),
                "lock_state": lock_state,
                "open_state": open_state,
                "action": action,
            }
        return {
            "kind": "door",
            "entity_id": mapping.entity_id,
            "label": label,
            "icon": "lock" if lock_state == "locked" else "door-front",
            "available": isinstance(state_payload, dict) or isinstance(status_payload, dict),
            "state": lock_state,
            "status_label": open_state.title() if open_state != "unknown" else "Status unknown",
            "detail": lock_state.title() if lock_state != "unknown" else "Lock state unavailable",
            "open_state": open_state,
            "lock_state": lock_state,
            "action": action,
        }
    if domain == "climate":
        item = _serialize_house_climate_state(
            mapping.entity_id,
            label,
            state_payload,
            actions=_canonical_control_actions(reference, settings, state_payload),
        )
        return {"kind": "climate", **item}
    item = _serialize_house_light_state(
        mapping.entity_id,
        label,
        state_payload,
        actions=_canonical_control_actions(reference, settings, state_payload),
    )
    return {"kind": domain, **item, "action": (item["actions"][0] if item["actions"] else None)}


def _canonical_control_actions(
    reference: HomeAssistantControlViewReference,
    settings: HomeAssistantRuntimeSettings,
    state_payload: dict[str, object] | None,
) -> list[dict[str, object]]:
    state_name = str((state_payload or {}).get("state") or "").strip().lower()
    actions = [
        (
            _serialize_canonical_action(HomeAssistantViewReference(mapping_id=action_id), settings),
            _required_object_mapping(settings, action_id, "action").allowed_operations[0],
        )
        for action_id in reference.action_ids
    ]
    if not isinstance(state_payload, dict):
        return []
    desired = {
        "on": {"turn_off"},
        "off": {"turn_on"},
        "locked": {"unlock"},
        "unlocked": {"lock"},
        "unlocking": {"lock"},
    }.get(state_name)
    if desired is None:
        return [item for item, _operation in actions]
    return [item for item, operation in actions if operation in desired]


def _serialize_canonical_action(
    reference: HomeAssistantViewReference,
    settings: HomeAssistantRuntimeSettings,
) -> dict[str, object]:
    mapping = _required_object_mapping(settings, reference.mapping_id, "action")
    operation = mapping.allowed_operations[0] if len(mapping.allowed_operations) == 1 else ""
    metadata = {
        "turn_on": ("Turn On", "lightbulb"),
        "turn_off": ("Turn Off", "lightbulb-off"),
        "lock": ("Lock", "lock"),
        "unlock": ("Unlock", "lock-open"),
        "cooler": ("Cooler", "thermostat"),
        "warmer": ("Warmer", "thermostat"),
    }
    default_label, icon = metadata.get(operation, (reference.mapping_id.replace("_", " ").title(), "home"))
    return {
        "action_id": reference.mapping_id,
        "label": reference.label or default_label,
        "type": "secondary" if operation in {"cooler", "warmer"} else "button",
        "icon": icon,
        "requires_confirmation": False,
    }


def _required_object_mapping(
    settings: HomeAssistantRuntimeSettings,
    mapping_id: str,
    kind: str,
) -> HomeAssistantObjectMapping:
    mapping = settings.mapping(mapping_id)
    if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != kind:
        raise RuntimeError(f"Applied Home Assistant view mapping {mapping_id!r} is invalid.")
    return mapping


def _runtime_bridge(settings: HomeAssistantRuntimeSettings) -> HomeAssistantBridge:
    return HomeAssistantBridge(
        base_url=settings.base_url or "",
        token=settings.credential or "",
        timeout_seconds=settings.timeout_seconds,
    )


def _snapshot_path(settings: HomeAssistantRuntimeSettings, snapshot_ref: str | None) -> str | None:
    if snapshot_ref is None:
        return None
    if settings.snapshot_root is None:
        raise RuntimeError("Applied Home Assistant camera view lacks snapshot_root.")
    return f"{settings.snapshot_root}/{snapshot_ref}"


def _canonical_camera_snapshot(
    camera_id: str,
    settings: HomeAssistantRuntimeSettings | None,
) -> Response:
    if settings is None or not settings.enabled:
        raise HTTPException(status_code=404, detail="Home Assistant camera views are disabled")
    camera = next(
        (
            item
            for item in settings.views.house.cameras
            if _required_object_mapping(settings, item.mapping_id, "camera").oracle_id == str(camera_id or "").strip()
        ),
        None,
    )
    if camera is None:
        raise HTTPException(status_code=404, detail=f"Unknown house camera {camera_id}")
    snapshot_path = _snapshot_path(settings, camera.snapshot_ref)
    if snapshot_path is None:
        raise HTTPException(status_code=404, detail=f"House camera {camera_id} has no snapshot configured")
    try:
        snapshot = fetch_snapshot(
            base_url=settings.base_url or "",
            token=settings.credential or "",
            snapshot_path=snapshot_path,
            snapshot_root=settings.snapshot_root or "",
            timeout_seconds=float(settings.timeout_seconds or 8),
        )
    except HomeAssistantSnapshotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    headers = {"Cache-Control": "no-store, max-age=0"}
    if snapshot.last_modified:
        headers["Last-Modified"] = snapshot.last_modified
    return Response(content=snapshot.content, media_type=snapshot.content_type, headers=headers)


def _empty_front_door() -> dict[str, object]:
    return {
        "entity_id": "entry",
        "label": "Entry",
        "available": False,
        "lock_state": "unknown",
        "open_state": "unknown",
        "action": None,
    }
