from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException

from .alerts import list_alerts
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.routine_runtime_settings import RoutineRuntimeSettings
from .configuration.satellite_ui_runtime_settings import SatelliteUiRuntimeSettings
from .provider_bridges.home_assistant import HomeAssistantBridge
from .ui_house import (
    _canonical_control_actions,
    _required_object_mapping,
    _resolve_house_entity_label,
    _runtime_bridge,
    _serialize_house_climate_state,
    _serialize_house_light_state,
    _summarize_house_lock_state,
)


BuildNoArgSnapshot = Callable[[], dict[str, object]]
BuildAudioSnapshot = Callable[[str | None, str | None], dict[str, object]]
BuildCalendarSnapshot = Callable[..., dict[str, object]]

_SATELLITE_UI_PAGES = ["home", "weather", "calendar", "audio", "house"]
_SATELLITE_UI_MODULES = {
    "home": {"label": "Home", "icon": "home"},
    "weather": {"label": "Weather", "icon": "cloud"},
    "calendar": {"label": "Calendar", "icon": "calendar_today"},
    "audio": {"label": "Audio", "icon": "speaker"},
    "music": {"label": "Music", "icon": "music_note"},
    "audiobooks": {"label": "Audiobooks", "icon": "book"},
    "house": {"label": "House", "icon": "house"},
}

def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _serialize_next_alarm(source: str) -> dict[str, object]:
    alarms = list_alerts(source, "alarm")
    if not alarms:
        return {
            "active": False,
            "next": None,
            "count": 0,
        }
    next_alarm = alarms[0]
    return {
        "active": True,
        "count": len(alarms),
        "next": {
            "alert_id": next_alarm.alert_id,
            "due_at": next_alarm.due_at.isoformat(),
            "message": next_alarm.message,
        },
    }


def _serialize_routine_actions(
    source: str,
    *,
    routine_settings: RoutineRuntimeSettings | None,
) -> list[dict[str, object]]:
    definitions = (
        [runtime.definition for runtime in routine_settings.definitions.values()]
        if routine_settings is not None and routine_settings.enabled
        else []
    )
    output: list[dict[str, object]] = []
    for raw_definition in definitions:
        definition = (
            raw_definition.model_dump(mode="json")
            if hasattr(raw_definition, "model_dump")
            else raw_definition
        )
        if not isinstance(definition, dict):
            continue
        triggers = definition.get("triggers") or {}
        if (
            definition.get("enabled") is not True
            or triggers.get("ui") is not True
            or source not in (definition.get("source_ids") or [])
        ):
            continue
        output.append(
            {
                "orchestration_id": str(definition.get("id") or ""),
                "label": str(definition.get("display_name") or definition.get("id") or "Routine"),
                "description": str(definition.get("description") or ""),
                "icon": "bedtime",
                "inputs": dict(definition.get("inputs") or {}),
            }
        )
    return output


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_numeric_attribute(attributes: dict[str, object], names: list[str]) -> float | None:
    for name in names:
        value = _coerce_float(attributes.get(name))
        if value is not None:
            return value
    return None


def _normalize_satellite_ui_nav(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        value = _SATELLITE_UI_PAGES
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in value:
        if isinstance(raw_item, dict):
            page_id = str(raw_item.get("id") or raw_item.get("page") or "").strip().lower()
        else:
            page_id = str(raw_item or "").strip().lower()
        if page_id not in _SATELLITE_UI_MODULES or page_id in seen:
            continue
        seen.add(page_id)
        spec = _SATELLITE_UI_MODULES[page_id]
        output.append({"id": page_id, "label": spec["label"], "icon": spec["icon"]})
    return output or [{"id": "home", **_SATELLITE_UI_MODULES["home"]}]


def build_satellite_ui_config(
    satellite_id: str | None,
    *,
    fleet_settings: SatelliteUiRuntimeSettings | None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    return _build_canonical_satellite_ui_config(
        satellite_id,
        fleet_settings,
        household_settings,
    )


def _build_canonical_satellite_ui_config(
    satellite_id: str | None,
    fleet: SatelliteUiRuntimeSettings | None,
    household: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    if fleet is None or household is None:
        raise HTTPException(status_code=503, detail="Canonical satellite UI configuration is unavailable")
    satellite = None
    requested = str(satellite_id or "").strip()
    if requested:
        satellite = fleet.entry(requested)
        if satellite is None:
            raise HTTPException(status_code=404, detail=f"Unknown satellite_id {requested}")
    else:
        satellite = next(
            (
                item
                for item in fleet.entries.values()
            ),
            None,
        )
        if satellite is None:
            raise HTTPException(status_code=404, detail="No display-capable satellite UI is configured")
    ui = satellite.ui
    if not ui.enabled:
        raise HTTPException(status_code=404, detail=f"Satellite {satellite.satellite_id} has no enabled UI")
    source = household.source(satellite.source_id)
    room_id = household.configured_associated_room_id(satellite.source_id)
    room = household.room(room_id)
    pages = list(ui.pages)
    bottom_nav = _normalize_satellite_ui_nav(list(ui.bottom_nav or pages))
    capabilities = satellite.capabilities
    return {
        "satellite_id": satellite.satellite_id,
        "source_id": satellite.source_id,
        "display_name": (
            room.display_name
            if room is not None
            else source.id.replace("_", " ").title() if source is not None else satellite.satellite_id
        ),
        "room": room.display_name if room is not None else "",
        "room_id": room.id if room is not None else None,
        "capabilities": {
            "audio_input": bool(capabilities and capabilities.voice),
            "audio_output": bool(capabilities and (capabilities.music_playback or capabilities.audiobook_playback)),
            "display": bool(capabilities and capabilities.display),
            "touch": bool(ui.touch),
        },
        "profile": {
            "profile_id": ui.profile,
            "layout": ui.layout,
            "pages": pages,
            "bottom_nav": bottom_nav,
            "home": {
                "primary_card": "room_controls",
                "secondary_cards": ["weather", "calendar", "audio"],
                "room_environment": {},
            },
            "room_controls": {
                "default_scope": "assigned_room",
                "allow_nearby_devices": False,
                "items": [],
                "room_terms": [],
            },
        },
    }


def _build_canonical_room_controls_snapshot(
    satellite_id: str,
    settings: HomeAssistantRuntimeSettings | None,
    fleet: SatelliteUiRuntimeSettings | None,
    household: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    config = _build_canonical_satellite_ui_config(satellite_id, fleet, household)
    room_id = str(config.get("room_id") or "")
    room_view = settings.views.rooms.get(room_id) if settings is not None and settings.enabled else None
    title = f"{config.get('room') or config.get('display_name') or satellite_id} Controls"
    if settings is None or not settings.enabled:
        return {"title": title, "state": "unavailable", "selection_source": "canonical_view", "detail": "Home Assistant is disabled.", "items": []}
    if room_view is None or not room_view.controls:
        return {"title": title, "state": "not_configured", "selection_source": "canonical_view", "items": []}
    bridge = _runtime_bridge(settings)
    serialized: list[dict[str, object]] = []
    for reference in room_view.controls:
        mapping = _required_object_mapping(settings, reference.mapping_id, "entity")
        state_payload = bridge.fetch_entity_state(mapping.entity_id)

        def fetch_state(entity_id: str, *, _state=state_payload, _mapping_id=mapping.entity_id):
            return _state if entity_id == _mapping_id else bridge.fetch_entity_state(entity_id)

        serialized.append(
            _serialize_satellite_room_control_item(
                entity_id=mapping.entity_id,
                label=reference.label or mapping.oracle_id.replace("_", " ").title(),
                icon="",
                kind=mapping.entity_id.split(".", 1)[0],
                cached_names={},
                fetch_state=fetch_state,
                prefer_configured_label=True,
                canonical_actions=_canonical_control_actions(reference, settings, state_payload),
            )
        )
    return {
        "title": title,
        "state": "ready",
        "selection_source": "canonical_view",
        "detail": "Room controls come from the applied canonical Home Assistant view.",
        "items": serialized,
    }


def _build_canonical_room_environment_snapshot(
    satellite_id: str,
    settings: HomeAssistantRuntimeSettings | None,
    fleet: SatelliteUiRuntimeSettings | None,
    household: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    config = _build_canonical_satellite_ui_config(satellite_id, fleet, household)
    room_id = str(config.get("room_id") or "")
    room_view = settings.views.rooms.get(room_id) if settings is not None and settings.enabled else None
    title = room_view.environment_title if room_view is not None and room_view.environment_title else "Room Climate"
    if settings is None or not settings.enabled:
        return {"title": title, "state": "unavailable", "detail": "Home Assistant is disabled.", "items": []}
    if room_view is None or not room_view.environment:
        return {"title": title, "state": "not_configured", "items": []}
    bridge = _runtime_bridge(settings)
    serialized = []
    for reference in room_view.environment:
        mapping = _required_object_mapping(settings, reference.mapping_id, "entity")
        item = _serialize_satellite_room_environment_item(
            entity_id=mapping.entity_id,
            label=reference.label or mapping.oracle_id.replace("_", " ").title(),
            humidity_source=reference.metric == "humidity",
            fetch_state=bridge.fetch_entity_state,
        )
        if reference.metric == "humidity":
            item["humidity_source"] = True
        serialized.append(item)
    return {"title": title, "state": "ready", "items": serialized}


def _serialize_satellite_room_control_item(
    *,
    entity_id: str,
    label: str,
    icon: str,
    kind: str,
    cached_names: dict[str, str],
    fetch_state,
    prefer_configured_label: bool = False,
    canonical_actions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    normalized_kind = str(kind or "").strip().lower()
    resolved_label = str(label or "").strip() if prefer_configured_label else ""
    if not resolved_label:
        resolved_label = _resolve_house_entity_label(entity_id, label, cached_names)
    if normalized_kind == "climate" or entity_id.startswith("climate."):
        item = _serialize_house_climate_state(
            entity_id,
            resolved_label,
            fetch_state(entity_id),
            actions=canonical_actions,
        )
        detail = "Unavailable"
        if item.get("available"):
            current = item.get("current_temperature_f")
            target = item.get("target_temperature_f")
            if current is not None and target is not None:
                detail = f"{round(float(current))}F now · {round(float(target))}F target"
            elif current is not None:
                detail = f"{round(float(current))}F now"
        return {
            "entity_id": entity_id,
            "label": resolved_label,
            "kind": "climate",
            "icon": icon or "thermostat",
            "available": bool(item.get("available")),
            "status_label": str(item.get("state") or "Unavailable").replace("_", " ").title(),
            "detail": detail,
            "actions": list(item.get("actions") or []),
        }

    if normalized_kind == "lock" or entity_id.startswith("lock."):
        lock_payload = fetch_state(entity_id)
        lock_state = _summarize_house_lock_state(lock_payload)
        actions = canonical_actions or []
        return {
            "entity_id": entity_id,
            "label": resolved_label,
            "kind": "lock",
            "icon": icon or "lock",
            "available": isinstance(lock_payload, dict),
            "status_label": (
                "Locked"
                if lock_state == "locked"
                else "Unlocked"
                if lock_state == "unlocked"
                else "Unavailable"
                if lock_state in {"unknown", "unavailable", ""}
                else lock_state.replace("_", " ").title()
            ),
            "detail": "Lock state unavailable" if lock_state == "unknown" else lock_state.replace("_", " ").title(),
            "actions": actions,
        }

    if normalized_kind == "fan" or entity_id.startswith("fan."):
        fan_payload = fetch_state(entity_id)
        fan_state = str((fan_payload or {}).get("state") or "").strip().lower()
        actions: list[dict[str, object]] = canonical_actions or []
        return {
            "entity_id": entity_id,
            "label": resolved_label,
            "kind": "fan",
            "icon": icon or "air",
            "available": isinstance(fan_payload, dict),
            "status_label": (
                "On"
                if fan_state == "on"
                else "Off"
                if fan_state == "off"
                else "Unavailable"
                if fan_state in {"unknown", "unavailable", ""}
                else fan_state.replace("_", " ").title()
            ),
            "detail": "Running" if fan_state == "on" else "Off now" if fan_state == "off" else "Unavailable",
            "actions": actions,
        }

    item = _serialize_house_light_state(
        entity_id,
        resolved_label,
        fetch_state(entity_id),
        actions=canonical_actions,
    )
    brightness = item.get("brightness_pct")
    if item.get("available"):
        detail = "On now" if str(item.get("state") or "").strip().lower() == "on" else "Off now"
        if brightness is not None and str(item.get("state") or "").strip().lower() == "on":
            detail = f"{brightness}% brightness"
    else:
        detail = "Unavailable"
    light_state = str(item.get("state") or "").strip().lower()
    if not item.get("available"):
        status_label = "Unavailable"
    elif light_state == "on":
        status_label = f"On {brightness}%" if brightness is not None else "On"
    else:
        status_label = "Off"
    return {
        "entity_id": entity_id,
        "label": resolved_label,
        "kind": "light",
        "icon": icon or "lightbulb",
        "available": bool(item.get("available")),
        "status_label": status_label,
        "detail": detail,
        "actions": list(item.get("actions") or []),
    }


def _serialize_satellite_room_environment_item(
    *,
    entity_id: str,
    label: str,
    humidity_source: bool = False,
    fetch_state,
) -> dict[str, object]:
    state_payload = fetch_state(entity_id)
    result: dict[str, object] = {
        "entity_id": entity_id,
        "label": label,
        "available": isinstance(state_payload, dict),
        "temperature_f": None,
        "humidity_pct": None,
        "state": None,
    }
    if not isinstance(state_payload, dict):
        return result
    result["state"] = state_payload.get("state")
    attributes = state_payload.get("attributes") or {}
    if isinstance(attributes, dict):
        result["temperature_f"] = _first_numeric_attribute(
            attributes,
            ["current_temperature", "temperature", "ambient_temperature"],
        )
        result["humidity_pct"] = _first_numeric_attribute(
            attributes,
            ["current_humidity", "humidity", "ambient_humidity"],
        )
    if humidity_source and result["humidity_pct"] is None:
        result["humidity_pct"] = _coerce_float(state_payload.get("state"))
    if result["temperature_f"] is None and not humidity_source and not entity_id.startswith("climate."):
        result["temperature_f"] = _coerce_float(state_payload.get("state"))
    return result


def build_satellite_room_environment_snapshot(
    satellite_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None,
    fleet_settings: SatelliteUiRuntimeSettings | None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    return _build_canonical_room_environment_snapshot(
        satellite_id,
        home_assistant_settings,
        fleet_settings,
        household_settings,
    )
def build_satellite_room_controls_snapshot(
    satellite_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None,
    fleet_settings: SatelliteUiRuntimeSettings | None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    return _build_canonical_room_controls_snapshot(
        satellite_id,
        home_assistant_settings,
        fleet_settings,
        household_settings,
    )
def build_satellite_ui_home_snapshot(
    satellite_id: str | None,
    *,
    build_ui_home_snapshot: BuildNoArgSnapshot,
    build_ui_audio_snapshot: BuildAudioSnapshot,
    build_ui_calendar_snapshot: BuildCalendarSnapshot,
    home_assistant_settings: HomeAssistantRuntimeSettings | None,
    fleet_settings: SatelliteUiRuntimeSettings | None,
    household_settings: HouseholdRuntimeSettings | None,
    routine_settings: RoutineRuntimeSettings | None,
) -> dict[str, object]:
    config = build_satellite_ui_config(
        satellite_id,
        fleet_settings=fleet_settings,
        household_settings=household_settings,
    )
    resolved_id = str(config["satellite_id"])
    resolved_source_id = str(config.get("source_id") or resolved_id)
    home_snapshot = build_ui_home_snapshot()
    try:
        audio_snapshot = build_ui_audio_snapshot(resolved_source_id, None)
    except HTTPException as exc:
        audio_snapshot = {
            "source": resolved_source_id,
            "available_sources": [],
            "playback": {
                "ok": False,
                "active": False,
                "output_owner": None,
                "detail": str(exc.detail),
            },
        }
    selected_audio_source = {
        "source": audio_snapshot.get("source"),
        "label": next(
            (
                str(item.get("label") or item.get("source") or "")
                for item in list(audio_snapshot.get("available_sources") or [])
                if str(item.get("source") or "") == resolved_source_id
            ),
            str(config.get("display_name") or resolved_id),
        ),
    }
    output_owner = dict(audio_snapshot.get("playback") or {}).get("output_owner") or {}
    playback_snapshot = dict(audio_snapshot.get("playback") or {})
    playback_payload = {
        "ok": bool(playback_snapshot.get("ok")),
        "active": bool(playback_snapshot.get("active")),
        "output_owner": output_owner,
    }
    if playback_snapshot.get("detail"):
        playback_payload["detail"] = str(playback_snapshot.get("detail"))
    return {
        "generated_at": _build_ui_generated_at(),
        "satellite": {
            "satellite_id": resolved_id,
            "display_name": config.get("display_name"),
            "room": config.get("room"),
        },
        "room_controls": build_satellite_room_controls_snapshot(
            resolved_id,
            home_assistant_settings=home_assistant_settings,
            fleet_settings=fleet_settings,
            household_settings=household_settings,
        ),
        "room_environment": build_satellite_room_environment_snapshot(
            resolved_id,
            home_assistant_settings=home_assistant_settings,
            fleet_settings=fleet_settings,
            household_settings=household_settings,
        ),
        "routine_actions": _serialize_routine_actions(
            resolved_source_id,
            routine_settings=routine_settings,
        ),
        "weather": dict(home_snapshot.get("weather") or {}),
        "calendar": build_ui_calendar_snapshot(limit=4),
        "alarm": _serialize_next_alarm(resolved_source_id),
        "audio": {
            "selected_source": selected_audio_source,
            "playback": playback_payload,
        },
        "refresh_after_seconds": 30,
    }
