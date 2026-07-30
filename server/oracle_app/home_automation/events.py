from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from oracle_app.configuration.domain_models import HomeAssistantEventMapping
from oracle_app.configuration.home_assistant_runtime_settings import (
    HomeAssistantRuntimeSettings,
)
from oracle_app.memory.runtime import safe_record_event

from .controller import (
    EntryRunbookDefinition,
    cancel_entry_runbook,
    start_entry_runbook,
)
from .state import observe_canonical_state


def handle_home_assistant_event(
    *,
    entity_id: str,
    state: str,
    event_id: str,
    occurred_at: datetime | None = None,
    home_assistant_settings: HomeAssistantRuntimeSettings,
    db_path: Path | None = None,
) -> dict[str, Any]:
    clean_entity = str(entity_id or "").strip().lower()
    clean_state = str(state or "").strip().lower()
    resolved = _resolve_canonical_event(clean_entity, home_assistant_settings)
    if resolved is None:
        return _result(event_id, status="ignored", reason="unmapped_entity")

    canonical_state = _canonical_state(resolved, clean_state)
    safe_record_event(
        "home_automation_event_received",
        source_id="brain",
        provider="home_assistant",
        domain="home_automation",
        status="mapped" if canonical_state else "ignored",
        correlation_id=event_id,
        db_path=db_path,
        payload={
            "mapping_id": resolved.mapping_id,
            "event_type": resolved.event_type,
            "subject": resolved.subject,
            "state": canonical_state or None,
        },
    )
    if not canonical_state:
        return _result(
            event_id,
            status="ignored",
            event_type=resolved.event_type,
            subject=resolved.subject,
            reason="unmapped_state",
        )
    observed_at = occurred_at or datetime.now(UTC)
    if not observe_canonical_state(
        subject=resolved.subject,
        event_id=event_id,
        state=canonical_state,
        observed_at=observed_at,
        db_path=db_path,
    ):
        return _result(
            event_id,
            status="ignored",
            event_type=resolved.event_type,
            subject=resolved.subject,
            state=canonical_state,
            reason="stale_or_duplicate_event",
        )
    if resolved.event_type == "mode_state":
        return _result(
            event_id,
            status="observed",
            event_type=resolved.event_type,
            subject=resolved.subject,
            state=canonical_state,
        )

    definition = resolved.runbook
    if definition is None:
        return _result(
            event_id,
            status="ignored",
            event_type=resolved.event_type,
            subject=resolved.subject,
            state=canonical_state,
            reason="no_enabled_runbook",
        )
    if definition.migration_mode != "runbook":
        return _result(
            event_id,
            status="compatibility_active",
            event_type=resolved.event_type,
            subject=resolved.subject,
            state=canonical_state,
            reason="direct_notification_owns_delivery",
        )

    if canonical_state == "open":
        outcome = start_entry_runbook(
            definition,
            event_id=event_id,
            occurred_at=occurred_at,
            db_path=db_path,
        )
    else:
        outcome = cancel_entry_runbook(resolved.subject, event_id=event_id, db_path=db_path)
    return _result(
        event_id,
        status=str(outcome["status"]),
        event_type=resolved.event_type,
        subject=resolved.subject,
        state=canonical_state,
        run_id=str(outcome.get("run_id") or ""),
    )


@dataclass(frozen=True)
class _ResolvedHomeAutomationEvent:
    mapping_id: str
    entity_id: str
    event_type: str
    subject: str
    active_state: str
    inactive_state: str
    runbook: EntryRunbookDefinition | None


def _resolve_canonical_event(
    entity_id: str,
    settings: HomeAssistantRuntimeSettings,
) -> _ResolvedHomeAutomationEvent | None:
    if not settings.enabled:
        return None
    matches = [
        (mapping_id, mapping)
        for mapping_id, mapping in settings.mappings.items()
        if isinstance(mapping, HomeAssistantEventMapping)
        and mapping.entity_id.casefold() == entity_id
    ]
    if len(matches) != 1:
        return None
    mapping_id, mapping = matches[0]
    automation = next(
        (
            value
            for value in settings.automations.values()
            if value.definition.event_mapping_id == mapping_id
        ),
        None,
    )
    return _ResolvedHomeAutomationEvent(
        mapping_id=mapping_id,
        entity_id=mapping.entity_id,
        event_type=mapping.event_type,
        subject=mapping.subject,
        active_state=mapping.active_state.casefold(),
        inactive_state=str(mapping.inactive_state or "").casefold(),
        runbook=(
            EntryRunbookDefinition.from_canonical(
                automation,
                config_revision=settings.config_revision,
            )
            if automation is not None
            else None
        ),
    )


def _canonical_state(mapping: _ResolvedHomeAutomationEvent, state: str) -> str:
    if mapping.event_type == "mode_state":
        return "active" if state == mapping.active_state else "inactive"
    if state == mapping.active_state:
        return "open"
    if state == mapping.inactive_state:
        return "closed"
    return ""


def _result(
    event_id: str,
    *,
    status: str,
    event_type: str = "",
    subject: str = "",
    state: str = "",
    run_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "event_id": event_id,
        "event_type": event_type,
        "subject": subject,
        "state": state,
        "run_id": run_id,
        "reason": reason,
    }
