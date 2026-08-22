from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .models import (
    AccessConfiguration,
    ConfigurationModel,
    HouseholdConfiguration,
    SatellitesConfiguration,
)
from .home_assistant_action_semantics import (
    IMPLEMENTED_HOME_ASSISTANT_ACTION_OPERATIONS,
)


@dataclass(frozen=True, order=True)
class ConfigurationFinding:
    code: str
    file_role: str
    path: str
    message: str
    severity: str = "error"
    blocks_activation: bool = True
    category: str = "validation"
    owner: str = "configuration"


class BundleValidationError(ValueError):
    def __init__(self, findings: Iterable[ConfigurationFinding]) -> None:
        ordered = tuple(sorted(findings))
        if not ordered:
            raise ValueError("BundleValidationError requires at least one finding.")
        super().__init__(f"Canonical configuration bundle has {len(ordered)} validation finding(s).")
        self.findings = ordered


def _normalized_alias(value: str) -> str:
    return " ".join(value.casefold().split())


def _duplicate_ids(items: list[object], *, role: str, collection: str) -> list[ConfigurationFinding]:
    findings: list[ConfigurationFinding] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        item_id = str(getattr(item, "id"))
        if item_id in seen:
            findings.append(
                ConfigurationFinding(
                    code="config.identity.duplicate_id",
                    file_role=role,
                    path=f"{collection}[{index}].id",
                    message=f"Duplicate {collection} ID {item_id!r}; first declared at index {seen[item_id]}.",
                )
            )
        else:
            seen[item_id] = index
    return findings


def _alias_findings(items: list[object], *, collection: str) -> list[ConfigurationFinding]:
    findings: list[ConfigurationFinding] = []
    owners: dict[str, tuple[str, str]] = {}
    for index, item in enumerate(items):
        item_id = str(getattr(item, "id"))
        terms = [(item_id, f"{collection}[{index}].id")]
        terms.extend((str(alias), f"{collection}[{index}].aliases[{alias_index}]") for alias_index, alias in enumerate(getattr(item, "aliases", ())))
        for term, path in terms:
            normalized = _normalized_alias(term)
            previous = owners.get(normalized)
            if previous is not None:
                findings.append(
                    ConfigurationFinding(
                        code="config.identity.alias_collision",
                        file_role="household.yaml",
                        path=path,
                        message=f"Resolution term {term!r} collides with {previous[1]} owned by {previous[0]!r}.",
                    )
                )
            else:
                owners[normalized] = (item_id, path)
    return findings


def validate_cross_file_references(
    *,
    household: HouseholdConfiguration,
    access: AccessConfiguration,
    satellites: SatellitesConfiguration,
    roles: Mapping[str, ConfigurationModel] | None = None,
) -> tuple[ConfigurationFinding, ...]:
    findings: list[ConfigurationFinding] = []
    findings.extend(_duplicate_ids(household.users, role="household.yaml", collection="users"))
    findings.extend(_duplicate_ids(household.rooms, role="household.yaml", collection="rooms"))
    findings.extend(_duplicate_ids(household.sources, role="household.yaml", collection="sources"))
    findings.extend(_duplicate_ids(household.modes, role="household.yaml", collection="modes"))
    findings.extend(_duplicate_ids(satellites.satellites, role="satellites.yaml", collection="satellites"))
    findings.extend(_alias_findings(household.users, collection="users"))
    findings.extend(_alias_findings(household.rooms, collection="rooms"))
    findings.extend(_alias_findings(household.modes, collection="modes"))

    users = {item.id: item for item in household.users}
    rooms = {item.id: item for item in household.rooms}
    sources = {item.id: item for item in household.sources}

    default_user_id = household.defaults.user_id
    if default_user_id is not None:
        default_user = users.get(default_user_id)
        if default_user is None:
            findings.append(
                ConfigurationFinding(
                    "config.reference.unknown_default_user",
                    "household.yaml",
                    "defaults.user_id",
                    f"Default user {default_user_id!r} is not declared.",
                )
            )
        elif not default_user.enabled:
            findings.append(
                ConfigurationFinding(
                    "config.reference.disabled_default_user",
                    "household.yaml",
                    "defaults.user_id",
                    f"Default user {default_user_id!r} is disabled.",
                )
            )

    for index, source in enumerate(household.sources):
        if source.associated_user_id is not None:
            user = users.get(source.associated_user_id)
            if user is None:
                findings.append(ConfigurationFinding("config.reference.unknown_user", "household.yaml", f"sources[{index}].associated_user_id", f"Associated user {source.associated_user_id!r} is not declared."))
            elif source.enabled and not user.enabled:
                findings.append(ConfigurationFinding("config.reference.disabled_user", "household.yaml", f"sources[{index}].associated_user_id", f"Enabled source cannot associate with disabled user {source.associated_user_id!r}."))
        if source.associated_room_id is not None:
            room = rooms.get(source.associated_room_id)
            if room is None:
                findings.append(ConfigurationFinding("config.reference.unknown_room", "household.yaml", f"sources[{index}].associated_room_id", f"Associated room {source.associated_room_id!r} is not declared."))
            elif source.enabled and not room.enabled:
                findings.append(ConfigurationFinding("config.reference.disabled_room", "household.yaml", f"sources[{index}].associated_room_id", f"Enabled source cannot associate with disabled room {source.associated_room_id!r}."))

    bindings = access.source_authentication.credential_bindings if access.source_authentication else []
    bound_source_ids = {binding.source_id for binding in bindings}
    for index, binding in enumerate(bindings):
        source = sources.get(binding.source_id)
        if source is None:
            findings.append(ConfigurationFinding("config.reference.unknown_source", "access.yaml", f"source_authentication.credential_bindings[{index}].source_id", f"Bound source {binding.source_id!r} is not declared."))
        elif source.type == "satellite":
            findings.append(ConfigurationFinding("config.access.satellite_binding", "access.yaml", f"source_authentication.credential_bindings[{index}].source_id", "Satellite sources authenticate through satellite credentials, not access bindings."))
    for index, source in enumerate(household.sources):
        if source.enabled and source.type != "satellite" and source.id not in bound_source_ids:
            findings.append(ConfigurationFinding("config.access.missing_source_binding", "access.yaml", "source_authentication.credential_bindings", f"Enabled non-satellite source {source.id!r} requires exactly one credential binding."))

    owned_satellite_sources: dict[str, int] = {}
    authentication_secret_owners: dict[str, str] = {
        binding.credential_secret: f"access credential for {binding.source_id}"
        for binding in bindings
    }
    for index, satellite in enumerate(satellites.satellites):
        if satellite.source_id is not None:
            source = sources.get(satellite.source_id)
            path = f"satellites[{index}].source_id"
            if source is None:
                findings.append(ConfigurationFinding("config.reference.unknown_source", "satellites.yaml", path, f"Satellite source {satellite.source_id!r} is not declared."))
            elif source.type != "satellite":
                findings.append(ConfigurationFinding("config.reference.source_type", "satellites.yaml", path, f"Satellite source {satellite.source_id!r} must have type satellite."))
            elif satellite.enabled and not source.enabled:
                findings.append(ConfigurationFinding("config.reference.disabled_source", "satellites.yaml", path, f"Enabled satellite cannot reference disabled source {satellite.source_id!r}."))
            if satellite.enabled:
                previous = owned_satellite_sources.get(satellite.source_id)
                if previous is not None:
                    findings.append(ConfigurationFinding("config.satellite.duplicate_source", "satellites.yaml", path, f"Enabled satellite source {satellite.source_id!r} is already owned by satellites[{previous}]."))
                else:
                    owned_satellite_sources[satellite.source_id] = index

        for field_name in ("brain_client", "control_service", "enrollment"):
            credential = getattr(satellite, field_name)
            if credential is None:
                continue
            secret = credential.credential_secret
            owner = f"satellites[{index}].{field_name}.credential_secret"
            previous_owner = authentication_secret_owners.get(secret)
            if previous_owner is not None:
                findings.append(ConfigurationFinding("config.secret.credential_reuse", "satellites.yaml", owner, f"Authentication credential reference {secret!r} is already used by {previous_owner}."))
            else:
                authentication_secret_owners[secret] = owner

    if roles is not None:
        findings.extend(_validate_domain_references(roles, household, satellites))

    return tuple(sorted(findings))


def _validate_domain_references(
    roles: Mapping[str, ConfigurationModel],
    household: HouseholdConfiguration,
    satellites: SatellitesConfiguration,
) -> list[ConfigurationFinding]:
    findings: list[ConfigurationFinding] = []
    enabled_users = {item.id for item in household.users if item.enabled}
    enabled_audiobook_users = {
        item.id
        for item in household.users
        if item.enabled
        and item.capabilities.audiobooks is not None
        and item.capabilities.audiobooks.enabled
    }
    enabled_rooms = {item.id for item in household.rooms if item.enabled}
    enabled_sources = {item.id for item in household.sources if item.enabled}
    enabled_modes = {item.id for item in household.modes if item.enabled}
    satellite_by_source = {
        item.source_id: item
        for item in satellites.satellites
        if item.enabled and item.source_id is not None
    }

    def unknown(role: str, path: str, message: str, code: str = "config.reference.unknown_id") -> None:
        findings.append(ConfigurationFinding(code, role, path, message))

    information = roles.get("domains/information.yaml")
    brain = roles.get("brain.yaml")
    if (
        information is not None
        and information.facts.enabled  # type: ignore[attr-defined]
        and information.facts.summarizer_enabled  # type: ignore[attr-defined]
        and brain is not None
        and not brain.inference.shared_backend.enabled  # type: ignore[attr-defined]
    ):
        unknown(
            "domains/information.yaml",
            "facts.summarizer_enabled",
            "Facts summarization requires the shared Brain inference backend.",
            "config.reference.disabled_inference_backend",
        )

    for role_path, playback_capability in (
        ("domains/music.yaml", "music_playback"),
        ("domains/audiobooks.yaml", "audiobook_playback"),
    ):
        role = roles.get(role_path)
        if role is None:
            continue
        for index, source_id in enumerate(role.playback.source_ids):  # type: ignore[attr-defined]
            satellite = satellite_by_source.get(source_id)
            if source_id not in enabled_sources or satellite is None:
                unknown(role_path, f"playback.source_ids[{index}]", f"Playback source {source_id!r} is not owned by an enabled satellite.")
            elif not getattr(satellite.capabilities, playback_capability):
                unknown(role_path, f"playback.source_ids[{index}]", f"Satellite source {source_id!r} lacks {playback_capability} capability.", "config.reference.source_capability")

    weather = roles.get("domains/weather.yaml")
    if weather is not None and weather.forecast.enabled:  # type: ignore[attr-defined]
        provider = weather.providers[weather.forecast.provider]  # type: ignore[attr-defined,index]
        home_location = household.household.home_location
        household_coordinates = bool(
            home_location is not None
            and home_location.latitude is not None
            and home_location.longitude is not None
        )
        if provider.latitude is None and not household_coordinates:
            unknown("domains/weather.yaml", "forecast.provider", "Enabled home forecast requires coordinates in its NWS provider mapping or household home_location.", "config.reference.missing_home_location")

    notifications = roles.get("domains/notifications.yaml")
    notification_ids: set[str] = set()
    enabled_notification_ids: set[str] = set()
    if notifications is not None:
        notification_ids = {item.id for item in notifications.types}  # type: ignore[attr-defined]
        enabled_notification_ids = {item.id for item in notifications.types if item.enabled}  # type: ignore[attr-defined]
        for type_index, notification in enumerate(notifications.types):  # type: ignore[attr-defined]
            for audience_index, audience in enumerate(notification.audience):
                if audience.id not in enabled_sources:
                    unknown("domains/notifications.yaml", f"types[{type_index}].audience[{audience_index}].id", f"Notification audience source {audience.id!r} is not enabled.")
                elif audience.id not in satellite_by_source:
                    unknown(
                        "domains/notifications.yaml",
                        f"types[{type_index}].audience[{audience_index}].id",
                        f"Notification audience source {audience.id!r} is not owned by an enabled satellite.",
                        "config.reference.satellite_source",
                    )
            for mode_index, mode_id in enumerate(notification.suppressed_by):
                if mode_id not in enabled_modes:
                    unknown("domains/notifications.yaml", f"types[{type_index}].suppressed_by[{mode_index}]", f"Suppression mode {mode_id!r} is not enabled.")

    home_assistant = roles.get("domains/home-assistant.yaml")
    home_assistant_enabled = bool(home_assistant is not None and home_assistant.enabled)  # type: ignore[attr-defined]
    if notifications is not None and not home_assistant_enabled:
        for type_index, notification in enumerate(notifications.types):  # type: ignore[attr-defined]
            if notification.enabled and notification.suppressed_by:
                unknown(
                    "domains/notifications.yaml",
                    f"types[{type_index}].suppressed_by",
                    "Enabled notification suppression requires enabled Home Assistant evidence.",
                    "config.reference.suppression_mapping",
                )
    action_ids: set[str] = set()
    state_check_ids: set[str] = set()
    if home_assistant is not None:
        event_mapping_ids: set[str] = set()
        event_mapping_ids_by_entity: dict[str, str] = {}
        mode_state_mapping_ids_by_subject: dict[str, list[str]] = {}
        for mapping_id, mapping in home_assistant.mappings.items():  # type: ignore[attr-defined]
            if mapping.kind == "event":
                event_mapping_ids.add(mapping_id)
                if mapping.event_type == "mode_state":
                    mode_state_mapping_ids_by_subject.setdefault(mapping.subject, []).append(mapping_id)
                normalized_entity = mapping.entity_id.casefold()
                previous_mapping_id = event_mapping_ids_by_entity.get(normalized_entity)
                if previous_mapping_id is not None:
                    unknown(
                        "domains/home-assistant.yaml",
                        f"mappings.{mapping_id}.entity_id",
                        f"Home Assistant event entity {mapping.entity_id!r} is already owned by mapping {previous_mapping_id!r}.",
                        "config.identity.duplicate_provider_mapping",
                    )
                else:
                    event_mapping_ids_by_entity[normalized_entity] = mapping_id
                continue
            if mapping.kind == "room" and mapping.oracle_id not in enabled_rooms:
                unknown("domains/home-assistant.yaml", f"mappings.{mapping_id}.oracle_id", f"Room {mapping.oracle_id!r} is not enabled.")
            if mapping.kind == "mode" and mapping.oracle_id not in enabled_modes:
                unknown("domains/home-assistant.yaml", f"mappings.{mapping_id}.oracle_id", f"Mode {mapping.oracle_id!r} is not enabled.")
            if mapping.kind == "action":
                action_ids.add(mapping_id)
            if mapping.kind == "entity":
                state_check_ids.add(mapping_id)
        if notifications is not None:
            for type_index, notification in enumerate(notifications.types):  # type: ignore[attr-defined]
                if not notification.enabled:
                    continue
                for mode_index, mode_id in enumerate(notification.suppressed_by):
                    mapping_ids = mode_state_mapping_ids_by_subject.get(mode_id, [])
                    if len(mapping_ids) != 1:
                        unknown(
                            "domains/notifications.yaml",
                            f"types[{type_index}].suppressed_by[{mode_index}]",
                            f"Enabled notification suppression mode {mode_id!r} requires exactly one Home Assistant mode-state mapping.",
                            "config.reference.suppression_mapping",
                        )
        enabled_automation_by_mapping: dict[str, str] = {}
        enabled_automation_by_subject: dict[str, str] = {}
        for index, automation in enumerate(home_assistant.automations):  # type: ignore[attr-defined]
            if automation.event_mapping_id not in event_mapping_ids:
                unknown("domains/home-assistant.yaml", f"automations[{index}].event_mapping_id", f"Event mapping {automation.event_mapping_id!r} is not declared.")
            elif home_assistant.mappings[automation.event_mapping_id].event_type != "entry_state":  # type: ignore[attr-defined]
                unknown("domains/home-assistant.yaml", f"automations[{index}].event_mapping_id", "Home Assistant runbook automation requires an entry-state event mapping.", "config.reference.mapping_type")
            if automation.notification_type not in notification_ids:
                unknown("domains/home-assistant.yaml", f"automations[{index}].notification_type", f"Notification type {automation.notification_type!r} is not declared.")
            elif automation.enabled and automation.notification_delivery_enabled and automation.notification_type not in enabled_notification_ids:
                unknown("domains/home-assistant.yaml", f"automations[{index}].notification_type", f"Enabled automation cannot deliver disabled notification type {automation.notification_type!r}.", "config.reference.disabled_notification")
            if automation.enabled and automation.event_mapping_id in event_mapping_ids:
                mapping = home_assistant.mappings[automation.event_mapping_id]  # type: ignore[attr-defined]
                previous_automation_id = enabled_automation_by_mapping.get(automation.event_mapping_id)
                if previous_automation_id is not None:
                    unknown(
                        "domains/home-assistant.yaml",
                        f"automations[{index}].event_mapping_id",
                        f"Enabled event mapping {automation.event_mapping_id!r} is already owned by automation {previous_automation_id!r}.",
                        "config.identity.duplicate_lifecycle_owner",
                    )
                else:
                    enabled_automation_by_mapping[automation.event_mapping_id] = automation.id
                previous_subject_owner = enabled_automation_by_subject.get(mapping.subject)
                if previous_subject_owner is not None:
                    unknown(
                        "domains/home-assistant.yaml",
                        f"automations[{index}].event_mapping_id",
                        f"Home-automation subject {mapping.subject!r} is already owned by automation {previous_subject_owner!r}.",
                        "config.identity.duplicate_lifecycle_owner",
                    )
                else:
                    enabled_automation_by_subject[mapping.subject] = automation.id

        mappings = home_assistant.mappings  # type: ignore[attr-defined]
        views = home_assistant.views  # type: ignore[attr-defined]

        def view_mapping(
            mapping_id: str,
            path: str,
            *,
            kinds: set[str],
            domains: set[str] | None = None,
            required_operation: str | None = None,
            view_action: bool = False,
        ):
            mapping = mappings.get(mapping_id)
            if mapping is None:
                unknown("domains/home-assistant.yaml", path, f"Home Assistant view mapping {mapping_id!r} is not declared.")
                return None
            if mapping.kind not in kinds:
                unknown("domains/home-assistant.yaml", path, f"Home Assistant view mapping {mapping_id!r} has incompatible kind {mapping.kind!r}.", "config.reference.mapping_type")
                return None
            if domains is not None:
                domain = str(mapping.entity_id).split(".", 1)[0]
                if domain not in domains:
                    unknown("domains/home-assistant.yaml", path, f"Home Assistant view mapping {mapping_id!r} has incompatible entity domain {domain!r}.", "config.reference.mapping_type")
                    return None
            if required_operation is not None and required_operation not in getattr(mapping, "allowed_operations", ()):
                unknown("domains/home-assistant.yaml", path, f"Home Assistant view mapping {mapping_id!r} does not permit required operation {required_operation!r}.", "config.reference.mapping_operation")
                return None
            if view_action and (
                len(mapping.allowed_operations) != 1
                or mapping.allowed_operations[0] not in IMPLEMENTED_HOME_ASSISTANT_ACTION_OPERATIONS
            ):
                unknown("domains/home-assistant.yaml", path, f"Home Assistant view action {mapping_id!r} does not select one implemented Oracle operation.", "config.reference.mapping_operation")
                return None
            return mapping

        def validate_control(control, path: str) -> None:
            primary = view_mapping(control.mapping_id, f"{path}.mapping_id", kinds={"entity"}, domains={"light", "lock", "fan", "climate"}, required_operation="read")
            if control.status_mapping_id is not None:
                status = view_mapping(control.status_mapping_id, f"{path}.status_mapping_id", kinds={"entity", "event"})
                if primary is not None and status is not None:
                    status_identity = status.subject if status.kind == "event" else status.oracle_id
                    if status_identity != primary.oracle_id:
                        unknown("domains/home-assistant.yaml", f"{path}.status_mapping_id", "Home Assistant status mapping must describe the same Oracle object as its control mapping.", "config.reference.mapping_identity")
            for action_index, action_id in enumerate(control.action_ids):
                action = view_mapping(action_id, f"{path}.action_ids[{action_index}]", kinds={"action"}, view_action=True)
                if primary is not None and action is not None and action.oracle_id != primary.oracle_id:
                    unknown("domains/home-assistant.yaml", f"{path}.action_ids[{action_index}]", "Home Assistant view action must describe the same Oracle object as its control mapping.", "config.reference.mapping_identity")

        for index, control in enumerate(views.home.controls):
            validate_control(control, f"views.home.controls[{index}]")
        for index, item in enumerate(views.home.actions):
            view_mapping(item.mapping_id, f"views.home.actions[{index}].mapping_id", kinds={"action"}, view_action=True)
        if views.house.front_door is not None:
            validate_control(views.house.front_door, "views.house.front_door")
        for index, item in enumerate(views.house.temperatures):
            view_mapping(item.mapping_id, f"views.house.temperatures[{index}].mapping_id", kinds={"entity"}, domains={"sensor"}, required_operation="read")
        for index, item in enumerate(views.house.climate):
            validate_control(item, f"views.house.climate[{index}]")
            view_mapping(item.mapping_id, f"views.house.climate[{index}].mapping_id", kinds={"entity"}, domains={"climate"})
        for index, item in enumerate(views.house.lights):
            validate_control(item, f"views.house.lights[{index}]")
            view_mapping(item.mapping_id, f"views.house.lights[{index}].mapping_id", kinds={"entity"}, domains={"light"})
        snapshot_refs = False
        for index, item in enumerate(views.house.cameras):
            view_mapping(item.mapping_id, f"views.house.cameras[{index}].mapping_id", kinds={"camera"}, domains={"camera"}, required_operation="read")
            snapshot_refs = snapshot_refs or item.snapshot_ref is not None
        for index, item in enumerate(views.house.actions):
            view_mapping(item.mapping_id, f"views.house.actions[{index}].mapping_id", kinds={"action"}, view_action=True)
        if snapshot_refs:
            selected_provider = home_assistant.providers.get(home_assistant.provider)  # type: ignore[attr-defined]
            if selected_provider is None or selected_provider.snapshot_root is None:
                unknown("domains/home-assistant.yaml", "providers", "Home Assistant camera snapshot references require snapshot_root on the selected provider.", "config.reference.snapshot_root")
        for room_id, room_view in views.rooms.items():
            if room_id not in enabled_rooms:
                unknown("domains/home-assistant.yaml", f"views.rooms.{room_id}", f"Room view {room_id!r} does not reference an enabled canonical room.")
            for index, control in enumerate(room_view.controls):
                validate_control(control, f"views.rooms.{room_id}.controls[{index}]")
            for index, item in enumerate(room_view.environment):
                domains = {"climate"} if item.metric == "climate" else {"sensor", "climate"}
                view_mapping(item.mapping_id, f"views.rooms.{room_id}.environment[{index}].mapping_id", kinds={"entity"}, domains=domains, required_operation="read")

    routine_native_action_ids = {"stop_audiobook"}
    audiobooks = roles.get("domains/audiobooks.yaml")
    audiobook_playback_sources = set()
    if audiobooks is not None and audiobooks.enabled:  # type: ignore[attr-defined]
        audiobook_playback_sources = set(audiobooks.playback.source_ids)  # type: ignore[attr-defined]
    routines = roles.get("domains/routines.yaml")
    if routines is not None:
        routine_action_operations = {"lock", "turn_off", "turn_on", "unlock"}
        for definition_index, definition in enumerate(routines.definitions):  # type: ignore[attr-defined]
            if definition.user_id is not None and definition.user_id not in enabled_users:
                unknown("domains/routines.yaml", f"definitions[{definition_index}].user_id", f"Routine user {definition.user_id!r} is not enabled.")
            for source_index, source_id in enumerate(definition.source_ids):
                if source_id not in enabled_sources:
                    unknown("domains/routines.yaml", f"definitions[{definition_index}].source_ids[{source_index}]", f"Routine source {source_id!r} is not enabled.")
            for step_index, step in enumerate(definition.steps):
                step_path = f"definitions[{definition_index}].steps[{step_index}]"
                if step.type == "ui_action" and step.action_id not in action_ids:
                    unknown("domains/routines.yaml", f"{step_path}.action_id", f"Routine action {step.action_id!r} is not a registered Home Assistant action mapping.")
                elif definition.enabled and step.type == "ui_action" and not home_assistant_enabled:
                    unknown("domains/routines.yaml", f"{step_path}.action_id", "Enabled routine action requires the Home Assistant domain to be enabled.", "config.reference.disabled_capability")
                elif step.type == "ui_action":
                    mapping = home_assistant.mappings[step.action_id]  # type: ignore[union-attr]
                    if len(mapping.allowed_operations) != 1 or mapping.allowed_operations[0] not in routine_action_operations:
                        unknown("domains/routines.yaml", f"{step_path}.action_id", f"Routine action {step.action_id!r} does not select one implemented routine operation.", "config.reference.mapping_operation")
                remediation_action_id = getattr(step, "remediation_action_id", None)
                if remediation_action_id is not None and remediation_action_id not in action_ids and remediation_action_id not in routine_native_action_ids:
                    unknown("domains/routines.yaml", f"{step_path}.remediation_action_id", f"Routine remediation {remediation_action_id!r} is not a registered Home Assistant action mapping.")
                elif definition.enabled and remediation_action_id in action_ids and not home_assistant_enabled:
                    unknown("domains/routines.yaml", f"{step_path}.remediation_action_id", "Enabled routine remediation requires the Home Assistant domain to be enabled.", "config.reference.disabled_capability")
                elif remediation_action_id in action_ids:
                    mapping = home_assistant.mappings[remediation_action_id]  # type: ignore[union-attr]
                    if len(mapping.allowed_operations) != 1 or mapping.allowed_operations[0] not in routine_action_operations:
                        unknown("domains/routines.yaml", f"{step_path}.remediation_action_id", f"Routine remediation {remediation_action_id!r} does not select one implemented routine operation.", "config.reference.mapping_operation")
                if step.type == "state_check" and step.check_id not in state_check_ids:
                    unknown("domains/routines.yaml", f"{step_path}.check_id", f"Routine state check {step.check_id!r} is not a registered Home Assistant entity mapping.")
                elif definition.enabled and step.type == "state_check" and not home_assistant_enabled:
                    unknown("domains/routines.yaml", f"{step_path}.check_id", "Enabled routine state check requires the Home Assistant domain to be enabled.", "config.reference.disabled_capability")
                if step.type == "audiobook_start":
                    if step.user_id not in enabled_users:
                        unknown("domains/routines.yaml", f"{step_path}.user_id", f"Audiobook user {step.user_id!r} is not enabled.")
                    elif definition.enabled and step.user_id not in enabled_audiobook_users:
                        unknown("domains/routines.yaml", f"{step_path}.user_id", f"Audiobook user {step.user_id!r} does not have an enabled canonical audiobook account.", "config.reference.disabled_capability")
                    satellite = satellite_by_source.get(step.source_id)
                    if satellite is None or not satellite.capabilities.audiobook_playback:
                        unknown("domains/routines.yaml", f"{step_path}.source_id", f"Audiobook source {step.source_id!r} is not an enabled audiobook-capable satellite source.", "config.reference.source_capability")
                    elif definition.enabled and step.source_id not in audiobook_playback_sources:
                        unknown("domains/routines.yaml", f"{step_path}.source_id", f"Audiobook source {step.source_id!r} is not an enabled canonical audiobook playback target.", "config.reference.disabled_capability")
                if step.type in {"sleep_timer", "playback_check"}:
                    satellite = satellite_by_source.get(step.source_id)
                    if satellite is None or not satellite.capabilities.audiobook_playback:
                        unknown("domains/routines.yaml", f"{step_path}.source_id", f"Playback source {step.source_id!r} is not an enabled audiobook-capable satellite source.", "config.reference.source_capability")
                    elif definition.enabled and step.source_id not in audiobook_playback_sources:
                        unknown("domains/routines.yaml", f"{step_path}.source_id", f"Playback source {step.source_id!r} is not an enabled canonical audiobook playback target.", "config.reference.disabled_capability")

    inventory = roles.get("domains/network/inventory.yaml")
    policy = roles.get("domains/network/policy.yaml")
    adapters = roles.get("domains/network/adapters.yaml")
    if inventory is not None:
        targets = {
            "host": {item.id for item in inventory.hosts},  # type: ignore[attr-defined]
            "device": {item.id for item in inventory.devices},  # type: ignore[attr-defined]
            "service": {item.id for item in inventory.services},  # type: ignore[attr-defined]
            "power_target": {item.id for item in inventory.power_targets},  # type: ignore[attr-defined]
        }
        services_by_id = {item.id: item for item in inventory.services}  # type: ignore[attr-defined]
        power_targets_by_id = {item.id: item for item in inventory.power_targets}  # type: ignore[attr-defined]
        adapter_ids = set() if adapters is None else set(adapters.providers)  # type: ignore[attr-defined]
        internet_probe_adapter_id = inventory.internet_health_probe_adapter_id  # type: ignore[attr-defined]
        if internet_probe_adapter_id is not None:
            if internet_probe_adapter_id not in adapter_ids:
                unknown(
                    "domains/network/inventory.yaml",
                    "internet_health_probe_adapter_id",
                    f"Internet-health probe adapter {internet_probe_adapter_id!r} is not declared.",
                )
            elif adapters.providers[internet_probe_adapter_id].type != "direct_probe":  # type: ignore[attr-defined]
                unknown(
                    "domains/network/inventory.yaml",
                    "internet_health_probe_adapter_id",
                    f"Internet-health probe adapter {internet_probe_adapter_id!r} is not a direct probe.",
                    "config.reference.adapter_type",
                )
        for index, device in enumerate(inventory.devices):  # type: ignore[attr-defined]
            if device.host_id is not None and device.host_id not in targets["host"]:
                unknown("domains/network/inventory.yaml", f"devices[{index}].host_id", f"Device host {device.host_id!r} is not declared.")
        for index, monitor in enumerate(inventory.monitors):  # type: ignore[attr-defined]
            if monitor.target_id not in targets[monitor.target_type]:
                unknown("domains/network/inventory.yaml", f"monitors[{index}].target_id", f"Monitor target {monitor.target_id!r} is not declared.")
            if monitor.adapter_id not in adapter_ids:
                unknown("domains/network/inventory.yaml", f"monitors[{index}].adapter_id", f"Monitor adapter {monitor.adapter_id!r} is not declared.")
            elif adapters.providers[monitor.adapter_id].type not in {"direct_probe", "librenms"}:  # type: ignore[attr-defined]
                unknown("domains/network/inventory.yaml", f"monitors[{index}].adapter_id", f"Monitor adapter {monitor.adapter_id!r} is not observation-capable.", "config.reference.adapter_type")
        for index, power_target in enumerate(inventory.power_targets):  # type: ignore[attr-defined]
            if power_target.host_id not in targets["host"]:
                unknown("domains/network/inventory.yaml", f"power_targets[{index}].host_id", f"Power target host {power_target.host_id!r} is not declared.")
            if power_target.adapter_id not in adapter_ids:
                unknown("domains/network/inventory.yaml", f"power_targets[{index}].adapter_id", f"Power adapter {power_target.adapter_id!r} is not declared.")
            elif adapters.providers[power_target.adapter_id].type != "home_assistant_power":  # type: ignore[attr-defined]
                unknown("domains/network/inventory.yaml", f"power_targets[{index}].adapter_id", f"Power target adapter {power_target.adapter_id!r} has the wrong type.", "config.reference.adapter_type")
            elif adapters.providers[power_target.adapter_id].power_target_id != power_target.id:  # type: ignore[attr-defined]
                unknown("domains/network/inventory.yaml", f"power_targets[{index}].adapter_id", f"Power target adapter {power_target.adapter_id!r} is bound to another Oracle target.", "config.reference.adapter_target")
            elif power_target.enabled and inventory.enabled and not home_assistant_enabled:  # type: ignore[attr-defined]
                unknown("domains/network/inventory.yaml", f"power_targets[{index}].adapter_id", "Enabled Home Assistant power target requires the Home Assistant domain to be enabled.", "config.reference.disabled_capability")
        for index, dependency in enumerate(inventory.dependencies):  # type: ignore[attr-defined]
            if dependency.from_id not in targets[dependency.from_type]:
                unknown("domains/network/inventory.yaml", f"dependencies[{index}].from_id", f"Dependency source {dependency.from_type} {dependency.from_id!r} is not declared.")
            if dependency.to_id not in targets[dependency.to_type]:
                unknown("domains/network/inventory.yaml", f"dependencies[{index}].to_id", f"Dependency target {dependency.to_type} {dependency.to_id!r} is not declared.")
        if policy is not None:
            for index, action in enumerate(policy.actions):  # type: ignore[attr-defined]
                if action.target_id not in targets[action.target_type]:
                    unknown("domains/network/policy.yaml", f"actions[{index}].target_id", f"Network action target {action.target_id!r} is not declared.")
                if action.adapter_id not in adapter_ids:
                    unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", f"Network action adapter {action.adapter_id!r} is not declared.")
                else:
                    adapter = adapters.providers[action.adapter_id]  # type: ignore[attr-defined]
                    adapter_type = adapter.type
                    expected_type = (
                        "home_assistant_power"
                        if action.operation == "power_cycle"
                        else "router_control"
                        if action.operation == "restart_router"
                        else "service_control"
                    )
                    if adapter_type != expected_type:
                        unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", f"Operation {action.operation!r} requires adapter type {expected_type!r}.", "config.reference.adapter_type")
                    elif adapter_type == "service_control":
                        if action.operation == "restart_host":
                            compatible = action.target_type == "host" and adapter.target_kind == "host" and adapter.host_id == action.target_id
                        elif action.operation in {"restart_runtime", "restart_ui"}:
                            compatible = action.target_type == "host" and adapter.target_kind == "service" and adapter.host_id == action.target_id
                        else:
                            service = services_by_id.get(action.target_id)
                            compatible = (
                                action.target_type == "service"
                                and service is not None
                                and adapter.target_kind == "service"
                                and adapter.host_id == service.host_id
                            )
                        if not compatible:
                            unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", "Service-control adapter does not belong to the Oracle action target.", "config.reference.adapter_target")
                        elif action.requires_graceful_lifecycle and adapter.lifecycle is None:
                            unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", "Graceful lifecycle policy requires a typed adapter lifecycle profile.", "config.reference.lifecycle_required")
                    elif adapter_type == "router_control" and (action.target_type != "host" or adapter.host_id != action.target_id):
                        unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", "Router-control adapter does not belong to the Oracle host target.", "config.reference.adapter_target")
                    elif adapter_type == "home_assistant_power" and (action.target_type != "power_target" or adapter.power_target_id != action.target_id):
                        unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", "Power adapter does not belong to the Oracle power target.", "config.reference.adapter_target")
                    elif action.enabled and adapter_type == "home_assistant_power" and not home_assistant_enabled:
                        unknown("domains/network/policy.yaml", f"actions[{index}].adapter_id", "Enabled Home Assistant power action requires the Home Assistant domain to be enabled.", "config.reference.disabled_capability")
                if action.enabled and not inventory.enabled:  # type: ignore[attr-defined]
                    unknown("domains/network/policy.yaml", f"actions[{index}].enabled", "An enabled network action requires enabled network inventory.", "config.reference.disabled_inventory")
                if (
                    action.enabled
                    and action.target_type == "power_target"
                    and action.target_id in power_targets_by_id
                    and not power_targets_by_id[action.target_id].enabled
                ):
                    unknown("domains/network/policy.yaml", f"actions[{index}].target_id", "An enabled power-cycle action requires an enabled network power target.", "config.reference.disabled_capability")
            for index, recovery in enumerate(policy.recoveries):  # type: ignore[attr-defined]
                if recovery.enabled and not inventory.enabled:  # type: ignore[attr-defined]
                    unknown("domains/network/policy.yaml", f"recoveries[{index}].enabled", "An enabled network recovery requires enabled network inventory.", "config.reference.disabled_inventory")

        if adapters is not None:
            for adapter_id, adapter in adapters.providers.items():  # type: ignore[attr-defined]
                if adapter.type != "service_control":
                    if adapter.type == "router_control" and adapter.host_id not in targets["host"]:
                        unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.host_id", f"Router adapter host {adapter.host_id!r} is not declared.")
                    if adapter.type == "home_assistant_power" and adapter.power_target_id not in targets["power_target"]:
                        unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.power_target_id", f"Power adapter target {adapter.power_target_id!r} is not declared.")
                    continue
                if adapter.host_id not in targets["host"]:
                    unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.host_id", f"Service-control adapter host {adapter.host_id!r} is not declared.")
                for readiness_id in adapter.readiness_service_adapter_ids:
                    readiness = adapters.providers.get(readiness_id)  # type: ignore[attr-defined]
                    if readiness is None or readiness.type != "service_control" or readiness.target_kind != "service":
                        unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.readiness_service_adapter_ids", f"Readiness service adapter {readiness_id!r} is not declared as a service target.")
                lifecycle = adapter.lifecycle
                if lifecycle is not None:
                    lifecycle_ids = list(lifecycle.prepare_service_adapter_ids)
                    if lifecycle.client_release is not None:
                        lifecycle_ids.extend(lifecycle.client_release.service_adapter_ids)
                        if lifecycle.client_release.host_id not in targets["host"]:
                            unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.lifecycle.client_release.host_id", f"Lifecycle client host {lifecycle.client_release.host_id!r} is not declared.")
                    if lifecycle.storage is not None:
                        lifecycle_ids.append(lifecycle.storage.sharing_service_adapter_id)
                    for lifecycle_id in lifecycle_ids:
                        referenced = adapters.providers.get(lifecycle_id)  # type: ignore[attr-defined]
                        if referenced is None or referenced.type != "service_control" or referenced.target_kind != "service":
                            unknown("domains/network/adapters.yaml", f"providers.{adapter_id}.lifecycle", f"Lifecycle service adapter {lifecycle_id!r} is not declared as a service target.", "config.reference.lifecycle_adapter")

    if routines is not None and policy is not None:
        routine_phrases = {
            " ".join(phrase.casefold().split())
            for definition in routines.definitions  # type: ignore[attr-defined]
            for phrase in definition.triggers.global_phrases
        }
        for recovery_index, recovery in enumerate(policy.recoveries):  # type: ignore[attr-defined]
            for phrase_index, phrase in enumerate(recovery.triggers.global_phrases):
                if " ".join(phrase.casefold().split()) in routine_phrases:
                    unknown("domains/network/policy.yaml", f"recoveries[{recovery_index}].triggers.global_phrases[{phrase_index}]", f"Network recovery phrase {phrase!r} conflicts with a routine trigger.", "config.identity.trigger_collision")

    return findings
