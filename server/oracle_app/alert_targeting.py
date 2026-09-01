from __future__ import annotations

from dataclasses import dataclass

from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings


_TARGET_SCOPES = frozenset({"local", "room", "household", "recipient"})


@dataclass(frozen=True)
class AlertDestination:
    source_id: str
    role: str = "destination"
    recipient_user_id: str | None = None


@dataclass(frozen=True)
class AlertTargetResolution:
    scope: str
    target_id: str | None
    destinations: tuple[AlertDestination, ...]
    config_revision: str


def resolve_alert_targets(
    *,
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    scope: str,
    requesting_source_id: str,
    target_id: str | None = None,
    recipient_user_ids: tuple[str, ...] = (),
    include_common_copy: bool = False,
) -> AlertTargetResolution:
    clean_scope = str(scope or "").strip()
    if clean_scope not in _TARGET_SCOPES:
        raise ValueError(f"Unsupported alert target scope {clean_scope!r}")
    eligible = tuple(
        source_id
        for source_id in sorted(satellites.enabled_satellite_ids_by_source)
        if _eligible_source(household, satellites, source_id)
    )
    destinations: list[AlertDestination] = []

    if clean_scope == "local":
        source_id = str(requesting_source_id or "").strip()
        if source_id not in eligible:
            raise ValueError("Local alert target is not an enabled alert-capable satellite")
        destinations.append(AlertDestination(source_id))
        clean_target = source_id
    elif clean_scope == "room":
        clean_target = str(target_id or "").strip()
        if household.room(clean_target) is None:
            raise ValueError("Alert room target is not an enabled canonical room")
        destinations.extend(
            AlertDestination(source_id)
            for source_id in eligible
            if household.configured_associated_room_id(source_id) == clean_target
        )
    elif clean_scope == "household":
        clean_target = household.household.id
        destinations.extend(AlertDestination(source_id) for source_id in eligible)
    else:
        clean_target = str(target_id or "").strip() or None
        recipients = tuple(dict.fromkeys(str(item or "").strip() for item in recipient_user_ids))
        if not recipients or any(household.user(user_id) is None for user_id in recipients):
            raise ValueError("Reminder recipients must be enabled canonical users")
        for user_id in recipients:
            destinations.extend(
                AlertDestination(source_id, recipient_user_id=user_id)
                for source_id in eligible
                if household.configured_associated_user_id(source_id) == user_id
            )
        if include_common_copy:
            destinations.extend(
                AlertDestination(source_id, role="common")
                for source_id in eligible
                if household.configured_associated_user_id(source_id) is None
            )

    return AlertTargetResolution(
        scope=clean_scope,
        target_id=clean_target,
        destinations=tuple(dict.fromkeys(destinations)),
        config_revision=household.config_revision,
    )


def _eligible_source(
    household: HouseholdRuntimeSettings,
    satellites: SatelliteFleetRuntimeSettings,
    source_id: str,
) -> bool:
    source = household.source(source_id)
    satellite = satellites.satellite_for_source(source_id)
    return bool(source is not None and satellite is not None and satellite.alert_capable)
