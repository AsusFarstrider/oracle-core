from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .normalization import NormalizedBundle
from .access_safety import classify_access_safety


_MISSING = object()


@dataclass(frozen=True)
class SemanticChange:
    path: str
    operation: str
    before: Any
    after: Any
    restart_required: bool
    safety_acknowledgements: tuple[str, ...]


def _identity_map(value: list[Any]) -> dict[str, Any] | None:
    if not all(isinstance(item, Mapping) and isinstance(item.get("id"), str) for item in value):
        return None
    return {str(item["id"]): item for item in value}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _safety_acknowledgements(path: str, operation: str, before: Any, after: Any) -> tuple[str, ...]:
    acknowledgements: set[str] = set()
    if operation == "remove" and any(token in path for token in (".users[id=", ".rooms[id=", ".sources[id=", ".satellites[id=")):
        acknowledgements.add("identity_removal")
    if path.endswith(".credential_secret") and before != after:
        acknowledgements.add("credential_role_change")
    mutating_roles = (
        "roles.domains/home-assistant.yaml.enabled",
        "roles.domains/routines.yaml.enabled",
        "roles.domains/network/inventory.yaml.enabled",
    )
    if path in mutating_roles and before is False and after is True:
        acknowledgements.add("mutating_control_enablement")
    return tuple(sorted(acknowledgements))


def _walk(before: Any, after: Any, path: str, changes: list[SemanticChange]) -> None:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else str(key)
            _walk(before.get(key, _MISSING), after.get(key, _MISSING), child_path, changes)
        return
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        before_list = list(before)
        after_list = list(after)
        before_identities = _identity_map(before_list)
        after_identities = _identity_map(after_list)
        if before_identities is not None and after_identities is not None:
            for item_id in sorted(set(before_identities) | set(after_identities)):
                _walk(
                    before_identities.get(item_id, _MISSING),
                    after_identities.get(item_id, _MISSING),
                    f"{path}[id={item_id}]",
                    changes,
                )
            return
        if before_list == after_list:
            return
    if before == after:
        return
    operation = "add" if before is _MISSING else "remove" if after is _MISSING else "replace"
    before_value = None if before is _MISSING else _plain(before)
    after_value = None if after is _MISSING else _plain(after)
    changes.append(
        SemanticChange(
            path=path,
            operation=operation,
            before=before_value,
            after=after_value,
            restart_required=True,
            safety_acknowledgements=_safety_acknowledgements(path, operation, before_value, after_value),
        )
    )


def semantic_diff(before: NormalizedBundle, after: NormalizedBundle) -> tuple[SemanticChange, ...]:
    changes: list[SemanticChange] = []
    _walk(before.configuration, after.configuration, "", changes)
    access_classifications = classify_access_safety(before.configuration, after.configuration)
    for path, acknowledgements in access_classifications.items():
        for index, change in enumerate(changes):
            if change.path == path:
                changes[index] = replace(
                    change,
                    safety_acknowledgements=tuple(
                        sorted(set(change.safety_acknowledgements) | set(acknowledgements))
                    ),
                )
                break
        else:
            raise RuntimeError(f"Access safety classification lacks semantic change path {path!r}.")
    if _composite_power_expanded(before.configuration, after.configuration):
        reviewed_prefixes = (
            "roles.domains/routines.yaml",
            "roles.domains/home-assistant.yaml",
            "roles.domains/audiobooks.yaml",
            "roles.domains/notifications.yaml",
            "roles.household.yaml",
        )
        for index, change in enumerate(changes):
            if change.path.startswith(reviewed_prefixes):
                changes[index] = replace(
                    change,
                    safety_acknowledgements=tuple(sorted(
                        set(change.safety_acknowledgements) | {"runbook_power_expansion"}
                    )),
                )
                break
        else:
            raise RuntimeError("Composite safety expansion lacks a reviewed semantic change.")
    return tuple(changes)


def _composite_power_expanded(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    from oracle_app.capabilities.preauthorization import required_safety_acknowledgements
    from oracle_app.capabilities.semantic import SEMANTIC_CAPABILITY_REGISTRY

    from .composite_semantics import composite_review_digest, validate_composite_collection
    from .domain_models import RoutinesConfiguration

    def routines(configuration: Mapping[str, Any]) -> RoutinesConfiguration | None:
        role = configuration.get("roles", {}).get("domains/routines.yaml")
        return RoutinesConfiguration.model_validate(_plain(role)) if role is not None else None

    before_role = routines(before)
    after_role = routines(after)
    if after_role is None or not after_role.enabled:
        return False
    before_by_id = {} if before_role is None else {item.id: item for item in before_role.definitions}
    before_manifests = {} if before_role is None else validate_composite_collection(before_role.definitions)
    after_manifests = validate_composite_collection(after_role.definitions)
    for definition in after_role.definitions:
        composition = definition.composition
        if not definition.enabled or composition is None or not composition.preauthorize_consequential:
            continue
        previous = before_by_id.get(definition.id)
        previous_authorized = (
            previous is not None and previous.enabled and previous.composition is not None
            and previous.composition.preauthorize_consequential
        )
        previous_manifest = before_manifests.get(definition.id) if previous_authorized else None
        if previous_manifest is None:
            return True
        if required_safety_acknowledgements(
            SEMANTIC_CAPABILITY_REGISTRY, previous_manifest, after_manifests[definition.id]
        ):
            return True
        if composite_review_digest(previous) != composite_review_digest(definition):
            return True
        if _reviewed_target_binding_changed(before, after, after_manifests[definition.id], definition):
            return True
    return False


def _reviewed_target_binding_changed(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    manifest: Any,
    definition: Any,
) -> bool:
    """A stable Oracle ID cannot hide a changed provider or household binding."""
    before_roles = before.get("roles", {})
    after_roles = after.get("roles", {})
    capability_ids = {power.capability_id for power in manifest.powers}
    targets = {target for power in manifest.powers for target in power.target_ids}
    if any(item.startswith("home.") for item in capability_ids):
        role_name = "domains/home-assistant.yaml"
        previous, current = before_roles.get(role_name, {}), after_roles.get(role_name, {})
        if (previous.get("provider"), previous.get("providers")) != (current.get("provider"), current.get("providers")):
            return True
        mapping_ids = {
            scope
            for power in manifest.powers
            for name, scope in power.argument_bindings
            if name == "mapping_id"
        }

        def selected_mappings(role: Mapping[str, Any]) -> dict[str, Any]:
            return {
                mapping_id: mapping for mapping_id, mapping in role.get("mappings", {}).items()
                if mapping_id in mapping_ids or mapping.get("oracle_id") in targets
            }

        if selected_mappings(previous) != selected_mappings(current):
            return True
    if any(item.startswith("audiobooks.") for item in capability_ids):
        if before_roles.get("domains/audiobooks.yaml") != after_roles.get("domains/audiobooks.yaml"):
            return True
    if any(item.startswith("notifications.") for item in capability_ids):
        if before_roles.get("domains/notifications.yaml") != after_roles.get("domains/notifications.yaml"):
            return True
    selected_ids = targets | {definition.user_id, *definition.source_ids}

    def household_bindings(role: Mapping[str, Any]) -> dict[str, Any]:
        return {
            category: {
                item["id"]: item for item in role.get(category, ()) if item["id"] in selected_ids
            }
            for category in ("users", "sources", "rooms")
        }

    return household_bindings(before_roles.get("household.yaml", {})) != household_bindings(
        after_roles.get("household.yaml", {})
    )
