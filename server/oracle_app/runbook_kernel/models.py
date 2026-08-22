from __future__ import annotations

from dataclasses import dataclass


ACTIVE_RUN_STATUSES = frozenset({"running", "waiting"})
TERMINAL_RUN_STATUSES = frozenset(
    {
        "canceled",
        "completed",
        "completed_with_issues",
        "failed",
        "interrupted",
        "plan_changed",
        "stopped",
    }
)
RUN_STATUSES = ACTIVE_RUN_STATUSES | TERMINAL_RUN_STATUSES

_ALLOWED_RUN_TRANSITIONS = {
    "running": frozenset(
        {
            "waiting",
            "canceled",
            "completed",
            "completed_with_issues",
            "failed",
            "interrupted",
            "plan_changed",
            "stopped",
        }
    ),
    "waiting": frozenset(
        {
            "running",
            "canceled",
            "failed",
            "interrupted",
        }
    ),
}


class InvalidRunTransitionError(ValueError):
    pass


class DuplicateRunActivationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunbookDefinitionRef:
    definition_id: str
    kind: str
    domain: str
    version: str = ""
    controller_version: str = ""

    def __post_init__(self) -> None:
        for field_name in ("definition_id", "kind", "domain"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} is required")


@dataclass(frozen=True)
class RunbookActivation:
    run_id: str
    started_at: str
    correlation_key: str = ""
    idempotency_key: str = ""
    client_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.run_id or "").strip():
            raise ValueError("run_id is required")
        if not str(self.started_at or "").strip():
            raise ValueError("started_at is required")


def validate_run_transition(current_status: str, next_status: str) -> None:
    current = str(current_status or "").strip().lower()
    target = str(next_status or "").strip().lower()
    if current not in RUN_STATUSES:
        raise InvalidRunTransitionError(f"Unknown current run status {current!r}.")
    if target not in RUN_STATUSES:
        raise InvalidRunTransitionError(f"Unknown target run status {target!r}.")
    if current == target:
        if current in TERMINAL_RUN_STATUSES:
            raise InvalidRunTransitionError(
                f"Terminal run status {current!r} cannot be rewritten."
            )
        return
    if target not in _ALLOWED_RUN_TRANSITIONS.get(current, frozenset()):
        raise InvalidRunTransitionError(
            f"Run status cannot transition from {current!r} to {target!r}."
        )
