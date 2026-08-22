from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .normalization import NormalizedBundle
from .validation import ConfigurationFinding


@dataclass(frozen=True)
class TransitionValidationContext:
    activation_generation_id: str
    config_generation_id: str
    config_revision: str
    selection_operation_id: str
    selection_revision: int


@dataclass(frozen=True)
class TransitionValidationResult:
    context: TransitionValidationContext
    blockers: tuple[ConfigurationFinding, ...]


_ORDINARY_OPTIONAL_ROLES = frozenset(
    {
        "domains/music.yaml",
        "domains/audiobooks.yaml",
        "domains/weather.yaml",
        "domains/calendar.yaml",
        "domains/home-assistant.yaml",
        "domains/notifications.yaml",
        "domains/routines.yaml",
    }
)


def _blocker(code: str, role: str, path: str, message: str) -> ConfigurationFinding:
    return ConfigurationFinding(
        code=code,
        file_role=role,
        path=path,
        message=message,
        category="activation",
        owner="configuration_transition",
    )


def validate_configuration_transition(
    selected: NormalizedBundle,
    candidate: NormalizedBundle,
    *,
    context: TransitionValidationContext,
) -> TransitionValidationResult:
    selected_roles = selected.configuration["roles"]
    candidate_roles = candidate.configuration["roles"]
    if not isinstance(selected_roles, Mapping) or not isinstance(candidate_roles, Mapping):
        raise TypeError("Normalized configuration roles must be mappings.")
    blockers: list[ConfigurationFinding] = []

    for role_path in sorted(_ORDINARY_OPTIONAL_ROLES):
        previous = selected_roles.get(role_path)
        if isinstance(previous, Mapping) and previous.get("enabled") is True and role_path not in candidate_roles:
            blockers.append(
                _blocker(
                    "config.transition.enabled_role_removed",
                    role_path,
                    "enabled",
                    "Enabled optional role must be selected as disabled before its file can be removed.",
                )
            )

    previous_information = selected_roles.get("domains/information.yaml")
    if isinstance(previous_information, Mapping) and "domains/information.yaml" not in candidate_roles:
        for capability in ("facts", "news", "suggestions"):
            section = previous_information.get(capability)
            if isinstance(section, Mapping) and section.get("enabled") is True:
                blockers.append(
                    _blocker(
                        "config.transition.enabled_information_removed",
                        "domains/information.yaml",
                        f"{capability}.enabled",
                        f"Enabled information capability {capability!r} must be selected as disabled before removal.",
                    )
                )

    previous_inventory = selected_roles.get("domains/network/inventory.yaml")
    removed_network_roles = sorted(
        role
        for role in (
            "domains/network/inventory.yaml",
            "domains/network/policy.yaml",
            "domains/network/adapters.yaml",
        )
        if role in selected_roles and role not in candidate_roles
    )
    if isinstance(previous_inventory, Mapping) and previous_inventory.get("enabled") is True:
        for role_path in removed_network_roles:
            blockers.append(
                _blocker(
                    "config.transition.enabled_network_removed",
                    role_path,
                    "enabled",
                    "Network inventory must be selected as disabled before any configured network role is removed.",
                )
            )

    for role_path, collection in (
        ("household.yaml", "users"),
        ("household.yaml", "rooms"),
        ("household.yaml", "sources"),
        ("satellites.yaml", "satellites"),
    ):
        previous_role = selected_roles[role_path]
        candidate_role = candidate_roles[role_path]
        previous_items = previous_role[collection]
        candidate_ids = {item["id"] for item in candidate_role[collection]}
        for item in previous_items:
            if item["enabled"] is True and item["id"] not in candidate_ids:
                blockers.append(
                    _blocker(
                        "config.transition.enabled_identity_removed",
                        role_path,
                        f"{collection}[id={item['id']}].enabled",
                        "Enabled identity must be selected as disabled before removal or rekeying.",
                    )
                )

    return TransitionValidationResult(context=context, blockers=tuple(sorted(blockers)))
