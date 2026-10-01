from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from .domain_models import HomeAssistantObjectMapping


IMPLEMENTED_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"arm", "close", "cooler", "disarm", "invoke", "lock", "open", "turn_off", "turn_on", "unlock", "warmer"}
)

DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset(
    {"arm", "close", "disarm", "invoke", "lock", "open", "turn_off", "turn_on", "unlock"}
)

CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS = frozenset({"cooler", "warmer"})


def collapse_equivalent_home_mappings(
    candidates: Iterable[tuple[str, HomeAssistantObjectMapping]],
) -> list[tuple[str, HomeAssistantObjectMapping]]:
    """Coalesce names, never distinct provider bindings or execution policy.

    All candidates belong to one selected provider. Mapping IDs and lexical
    aliases are not additional execution authority; select the smallest ID
    only after proving every execution-relevant mapping field identical.
    """
    unique: dict[tuple[object, ...], tuple[str, HomeAssistantObjectMapping]] = {}
    for mapping_id, mapping in sorted(candidates, key=lambda item: item[0]):
        key = (
            mapping.kind, mapping.oracle_id, mapping.entity_id,
            tuple(mapping.allowed_operations), mapping.normal_temperature_min,
            mapping.normal_temperature_max, mapping.temperature_unit,
        )
        unique.setdefault(key, (mapping_id, mapping))
    return list(unique.values())
