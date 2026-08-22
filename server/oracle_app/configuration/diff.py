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
    return tuple(changes)
