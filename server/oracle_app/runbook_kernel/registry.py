"""Code-owned runbook definition/controller registration boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RunbookControllerRegistration:
    kind: str
    definition_format: str
    owner: str
    execution_available: bool


_REGISTRATIONS = {
    ("routine", "compatibility_sequence_v1"): RunbookControllerRegistration(
        kind="routine", definition_format="compatibility_sequence_v1", owner="composite", execution_available=True,
    ),
    ("routine", "bounded_composite_v2"): RunbookControllerRegistration(
        kind="routine", definition_format="bounded_composite_v2", owner="composite", execution_available=True,
    ),
    ("recovery", "network_recovery_v1"): RunbookControllerRegistration(
        kind="recovery", definition_format="network_recovery_v1", owner="network", execution_available=True,
    ),
    ("home_automation", "door_controller_v1"): RunbookControllerRegistration(
        kind="home_automation", definition_format="door_controller_v1", owner="home_assistant", execution_available=True,
    ),
}


def registered_runbook_controller(kind: str, definition_format: str) -> RunbookControllerRegistration:
    try:
        return _REGISTRATIONS[(kind, definition_format)]
    except KeyError as exc:
        raise ValueError("Runbook controller/definition format is not registered in Oracle code.") from exc


def routine_controller_registration(definition: dict[str, object]) -> RunbookControllerRegistration:
    definition_format = "bounded_composite_v2" if definition.get("composition") is not None else "compatibility_sequence_v1"
    return registered_runbook_controller("routine", definition_format)
