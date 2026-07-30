from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from oracle_app.configuration.home_assistant_runtime_settings import (
    HomeAssistantAutomationRuntimeSettings,
    HomeAssistantRuntimeSettings,
)
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge
from oracle_app.runbook_kernel import (
    DuplicateRunActivationError,
    RunbookActivation,
    RunbookDefinitionRef,
    RunbookRepository,
)


_CONTROLLER_VERSION = "1"
_STORAGE_KIND = "routine"
_DOMAIN = "home_automation"
logger = logging.getLogger("oracle-brain.home-automation")


@dataclass(frozen=True)
class EntryRunbookDefinition:
    definition_version: str
    id: str
    subject: str
    entity_id: str
    open_state: str
    closed_state: str
    migration_mode: str
    notification_type: str
    notification_delivery_enabled: bool
    delay_seconds: int
    repeat_interval_seconds: int
    max_notifications: int
    max_lateness_seconds: int
    provider_retry_seconds: int
    max_provider_failures: int

    @classmethod
    def from_canonical(
        cls,
        runtime: HomeAssistantAutomationRuntimeSettings,
        *,
        config_revision: str,
    ) -> EntryRunbookDefinition:
        definition = runtime.definition
        mapping = runtime.event_mapping
        return cls(
            definition_version=config_revision,
            id=definition.id,
            subject=mapping.subject,
            entity_id=mapping.entity_id,
            open_state=mapping.active_state,
            closed_state=mapping.inactive_state or "",
            migration_mode=definition.migration_mode,
            notification_type=definition.notification_type,
            notification_delivery_enabled=definition.notification_delivery_enabled,
            delay_seconds=definition.delay_seconds,
            repeat_interval_seconds=int(definition.repeat_interval_seconds or 0),
            max_notifications=definition.max_notifications,
            max_lateness_seconds=definition.max_lateness_seconds,
            provider_retry_seconds=definition.provider_retry_seconds,
            max_provider_failures=definition.max_provider_failures,
        )

    @classmethod
    def from_persisted_payload(cls, definition: dict[str, Any]) -> EntryRunbookDefinition:
        return cls(
            definition_version=str(definition["definition_version"]),
            id=str(definition["id"]),
            subject=str(definition["subject"]),
            entity_id=str(definition["entity_id"]),
            open_state=str(definition["open_state"]),
            closed_state=str(definition["closed_state"]),
            migration_mode=str(definition["migration_mode"]),
            notification_type=str(definition["notification_type"]),
            notification_delivery_enabled=definition["notification_delivery_enabled"] is True,
            delay_seconds=int(definition["delay_seconds"]),
            repeat_interval_seconds=int(definition["repeat_interval_seconds"]),
            max_notifications=int(definition["max_notifications"]),
            max_lateness_seconds=int(definition["max_lateness_seconds"]),
            provider_retry_seconds=int(definition["provider_retry_seconds"]),
            max_provider_failures=int(definition["max_provider_failures"]),
        )

    def durable_payload(self) -> dict[str, object]:
        return {
            "definition_version": self.definition_version,
            "id": self.id,
            "subject": self.subject,
            "entity_id": self.entity_id,
            "open_state": self.open_state,
            "closed_state": self.closed_state,
            "migration_mode": self.migration_mode,
            "notification_type": self.notification_type,
            "notification_delivery_enabled": self.notification_delivery_enabled,
            "delay_seconds": self.delay_seconds,
            "repeat_interval_seconds": self.repeat_interval_seconds,
            "max_notifications": self.max_notifications,
            "max_lateness_seconds": self.max_lateness_seconds,
            "provider_retry_seconds": self.provider_retry_seconds,
            "max_provider_failures": self.max_provider_failures,
        }


def start_entry_runbook(
    definition: EntryRunbookDefinition,
    *,
    event_id: str,
    occurred_at: datetime | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    resolved = definition
    repository = RunbookRepository(db_path=db_path)
    subject = resolved.subject
    correlation_key = _correlation_key(subject)
    active = _active_run(repository, correlation_key)
    if active is not None:
        return {"status": "duplicate", "run_id": str(active["run_id"])}

    started = occurred_at or _utc_datetime()
    run_id = f"home-{uuid4().hex}"
    due_at = started + timedelta(seconds=resolved.delay_seconds)
    state = {
        "phase": "initial_wait",
        "next_due_at": due_at.isoformat(),
        "notification_count": 0,
        "provider_failure_count": 0,
        "cycle": 1,
    }
    try:
        run = repository.create_run(
            RunbookDefinitionRef(
                definition_id=resolved.id,
                kind=_STORAGE_KIND,
                domain=_DOMAIN,
                version=resolved.definition_version,
                controller_version=_CONTROLLER_VERSION,
            ),
            RunbookActivation(
                run_id=run_id,
                started_at=started.isoformat(),
                correlation_key=correlation_key,
                idempotency_key=f"ha-event:{event_id}",
                client_id="home_assistant",
            ),
            status="waiting",
            summary=f"Waiting to verify {subject} remains open.",
            controller_state=state,
            payload={"definition": resolved.durable_payload(), "trigger_event_id": event_id},
        )
    except DuplicateRunActivationError:
        existing = repository.find_by_activation_idempotency_key(f"ha-event:{event_id}")
        return {"status": "duplicate", "run_id": str((existing or {}).get("run_id") or "")}
    _record_wait(repository, run_id, state, subject)
    return {"status": "started", "run_id": str(run["run_id"])}


def cancel_entry_runbook(
    subject: str,
    *,
    event_id: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    repository = RunbookRepository(db_path=db_path)
    run = _active_run(repository, _correlation_key(subject))
    if run is None:
        return {"status": "no_active_run", "run_id": ""}
    if str(run["status"]) != "waiting":
        return {"status": "busy", "run_id": str(run["run_id"])}

    now = _utc_datetime().isoformat()
    for operation in run.get("steps") or []:
        if operation.get("status") != "waiting":
            continue
        repository.record_operation(
            run_id=str(run["run_id"]),
            operation_id=str(operation["step_id"]),
            ordinal=int(operation["ordinal"]),
            status="canceled",
            operation_kind=str(operation.get("target_type") or "wait"),
            target_id=str(operation.get("target_id") or subject),
            target_label=str(operation.get("target_label") or subject),
            summary="Canceled by a correlated closed event.",
            started_at=str(operation.get("started_at") or ""),
            completed_at=now,
            payload=dict(operation.get("payload") or {}),
        )
    repository.transition_run(
        str(run["run_id"]),
        status="canceled",
        summary=f"{subject} closed before the runbook completed.",
        cancellation_reason="correlated_entry_closed",
        cancellation_requester=f"home_assistant:{event_id}",
    )
    return {"status": "canceled", "run_id": str(run["run_id"])}


def resume_due_home_automation_runbooks(
    *,
    now: datetime | None = None,
    db_path: Path | None = None,
    state_fetcher: Callable[[str], dict[str, Any] | None] | None = None,
    notification_submitter: Callable[..., dict[str, Any]] | None = None,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
) -> list[dict[str, Any]]:
    if notification_submitter is None:
        raise ValueError(
            "Canonical home automation requires an injected notification capability."
        )
    resolved_submitter = notification_submitter
    current = now or _utc_datetime()
    repository = RunbookRepository(db_path=db_path)
    resumed: list[dict[str, Any]] = []
    for run in repository.list_runs(
        kind=_STORAGE_KIND,
        domain=_DOMAIN,
        status="waiting",
        limit=100,
    ):
        state = dict(run.get("controller_state") or {})
        due_at = _parse_datetime(state.get("next_due_at"))
        if due_at is None or current < due_at:
            continue
        resumed.append(
            _resume_run(
                run,
                current,
                repository=repository,
                state_fetcher=(
                    state_fetcher
                    or _canonical_state_fetcher(home_assistant_settings)
                ),
                notification_submitter=resolved_submitter,
            )
        )
    return resumed


async def home_automation_scheduler_loop(
    *,
    poll_seconds: float = 5.0,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    notification_submitter: Callable[..., dict[str, Any]] | None = None,
) -> None:
    if notification_submitter is None:
        raise ValueError(
            "Canonical home automation requires an injected notification capability."
        )
    while True:
        try:
            await asyncio.to_thread(
                resume_due_home_automation_runbooks,
                home_assistant_settings=home_assistant_settings,
                notification_submitter=notification_submitter,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("home_automation_runbook_scheduler_failed")
        await asyncio.sleep(max(0.25, poll_seconds))


def _resume_run(
    run: dict[str, Any],
    now: datetime,
    *,
    repository: RunbookRepository,
    state_fetcher: Callable[[str], dict[str, Any] | None] | None,
    notification_submitter: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    run_id = str(run["run_id"])
    payload = dict(run.get("payload") or {})
    definition = EntryRunbookDefinition.from_persisted_payload(
        dict(payload.get("definition") or {})
    )
    state = dict(run.get("controller_state") or {})
    subject = definition.subject
    cycle = int(state.get("cycle") or 1)
    due_at = _parse_datetime(state.get("next_due_at")) or now
    lateness = max(0, int((now - due_at).total_seconds()))
    if lateness > definition.max_lateness_seconds:
        _finish_current_wait(run, repository, status="failed", now=now, lateness=lateness)
        return repository.transition_run(
            run_id,
            status="failed",
            summary="Home-automation continuation exceeded its maximum lateness.",
            completed_at=now.isoformat(),
        )

    _finish_current_wait(run, repository, status="completed", now=now, lateness=lateness)
    repository.transition_run(
        run_id,
        status="running",
        summary=f"Verifying {subject} state.",
    )
    entity_id = definition.entity_id
    try:
        if state_fetcher is None:
            raise ValueError("Canonical Home Assistant state verification is unavailable.")
        fetch = state_fetcher
        provider_state = fetch(entity_id)
    except Exception:
        provider_state = None
    normalized_state = str((provider_state or {}).get("state") or "").strip().lower()
    state_available = normalized_state not in {"", "unknown", "unavailable"}
    verify_id = f"verify-{cycle}"
    repository.record_operation(
        run_id=run_id,
        operation_id=verify_id,
        ordinal=(cycle * 3) - 1,
        status="completed" if state_available else "failed",
        operation_kind="state_check",
        target_id=subject,
        target_label=subject,
        capability_id="home_automation.entry_state",
        summary="Provider state verified." if state_available else "Provider state was unavailable.",
        error_class="" if state_available else "provider_state_unavailable",
        verification_status="verified" if state_available else "unavailable",
        started_at=now.isoformat(),
        completed_at=now.isoformat(),
        payload={"entity_id": entity_id, "observed_state": normalized_state or None},
    )
    if not state_available:
        return _retry_or_fail_provider(run_id, definition, state, now, repository)
    if normalized_state == definition.closed_state:
        return repository.transition_run(
            run_id,
            status="completed",
            summary=f"{subject} is closed; no notification was needed.",
            completed_at=now.isoformat(),
        )
    if normalized_state != definition.open_state:
        return _retry_or_fail_provider(run_id, definition, state, now, repository)

    occurrence_number = int(
        state.get("submission_count") or state.get("notification_count") or 0
    ) + 1
    occurrence_id = f"{run_id}:{occurrence_number}"
    delivery_enabled = definition.notification_delivery_enabled
    if delivery_enabled:
        try:
            result = notification_submitter(
                definition.notification_type,
                occurrence_id,
                caller="home_automation_runbook",
                correlation_id=run_id,
            )
        except Exception as exc:
            repository.record_operation(
                run_id=run_id,
                operation_id=f"notify-{occurrence_number}",
                ordinal=cycle * 3,
                status="failed",
                operation_kind="capability_call",
                target_id=definition.notification_type,
                target_label=definition.notification_type,
                capability_id="notifications.submit",
                summary="Notification submission failed.",
                error_class=type(exc).__name__,
                started_at=now.isoformat(),
                completed_at=now.isoformat(),
            )
            return repository.transition_run(
                run_id,
                status="failed",
                summary="Notification submission failed.",
                completed_at=now.isoformat(),
            )
    else:
        result = {"status": "simulated"}
    repository.record_operation(
        run_id=run_id,
        operation_id=f"notify-{occurrence_number}",
        ordinal=cycle * 3,
        status="completed",
        operation_kind="capability_call" if delivery_enabled else "notification_simulation",
        target_id=definition.notification_type,
        target_label=definition.notification_type,
        capability_id="notifications.submit",
        summary=(
            f"Notification submission returned {result.get('status')}."
            if delivery_enabled
            else "Notification delivery is disabled; occurrence was simulated."
        ),
        verification_status=str(result.get("status") or ""),
        started_at=now.isoformat(),
        completed_at=now.isoformat(),
        payload={"occurrence_id": occurrence_id, "result_status": result.get("status")},
    )
    notification_count = int(state.get("notification_count") or 0)
    if str(result.get("status") or "") != "suppressed":
        notification_count += 1
    state.update(
        notification_count=notification_count,
        submission_count=occurrence_number,
        provider_failure_count=0,
    )
    if notification_count >= definition.max_notifications:
        return repository.transition_run(
            run_id,
            status="completed",
            summary=f"Reached the configured notification limit for {subject}.",
            controller_state=state,
            completed_at=now.isoformat(),
        )

    state.update(
        phase="repeat_wait",
        cycle=cycle + 1,
        next_due_at=(now + timedelta(seconds=definition.repeat_interval_seconds)).isoformat(),
    )
    updated = repository.transition_run(
        run_id,
        status="waiting",
        summary=f"Waiting before rechecking {subject}.",
        controller_state=state,
    )
    _record_wait(repository, run_id, state, subject)
    return updated


def _retry_or_fail_provider(
    run_id: str,
    definition: EntryRunbookDefinition,
    state: dict[str, Any],
    now: datetime,
    repository: RunbookRepository,
) -> dict[str, Any]:
    failures = int(state.get("provider_failure_count") or 0) + 1
    state["provider_failure_count"] = failures
    if failures > definition.max_provider_failures:
        return repository.transition_run(
            run_id,
            status="failed",
            summary="Home Assistant state remained unavailable or invalid.",
            controller_state=state,
            completed_at=now.isoformat(),
        )
    state.update(
        phase="provider_retry",
        cycle=int(state.get("cycle") or 1) + 1,
        next_due_at=(now + timedelta(seconds=definition.provider_retry_seconds)).isoformat(),
    )
    updated = repository.transition_run(
        run_id,
        status="waiting",
        summary="Home Assistant state unavailable; retry scheduled.",
        controller_state=state,
    )
    _record_wait(repository, run_id, state, definition.subject)
    return updated


def _record_wait(
    repository: RunbookRepository,
    run_id: str,
    state: dict[str, Any],
    subject: str,
) -> None:
    cycle = int(state.get("cycle") or 1)
    repository.record_operation(
        run_id=run_id,
        operation_id=f"wait-{cycle}",
        ordinal=(cycle * 3) - 2,
        status="waiting",
        operation_kind="wait",
        target_id=subject,
        target_label=subject,
        summary="Durable wait scheduled.",
        started_at=_utc_datetime().isoformat(),
        payload={"due_at": state["next_due_at"], "phase": state["phase"]},
    )


def _finish_current_wait(
    run: dict[str, Any],
    repository: RunbookRepository,
    *,
    status: str,
    now: datetime,
    lateness: int,
) -> None:
    for operation in reversed(run.get("steps") or []):
        if operation.get("status") != "waiting" or operation.get("target_type") != "wait":
            continue
        repository.record_operation(
            run_id=str(run["run_id"]),
            operation_id=str(operation["step_id"]),
            ordinal=int(operation["ordinal"]),
            status=status,
            operation_kind="wait",
            target_id=str(operation.get("target_id") or ""),
            target_label=str(operation.get("target_label") or ""),
            summary=(
                f"Durable wait completed with {lateness} seconds of lateness."
                if status == "completed"
                else f"Durable wait exceeded maximum lateness by {lateness} seconds."
            ),
            started_at=str(operation.get("started_at") or ""),
            completed_at=now.isoformat(),
            payload={**dict(operation.get("payload") or {}), "lateness_seconds": lateness},
        )
        return


def _active_run(repository: RunbookRepository, correlation_key: str) -> dict[str, Any] | None:
    for status in ("waiting", "running"):
        runs = repository.list_runs(
            kind=_STORAGE_KIND,
            domain=_DOMAIN,
            status=status,
            correlation_key=correlation_key,
            limit=1,
        )
        if runs:
            return runs[0]
    return None


def _canonical_state_fetcher(
    settings: HomeAssistantRuntimeSettings | None,
) -> Callable[[str], dict[str, Any] | None]:
    if (
        settings is None
        or not settings.enabled
        or not settings.base_url
        or not settings.credential
    ):
        def unavailable(_entity_id: str) -> dict[str, Any] | None:
            raise ValueError("Canonical Home Assistant state verification is unavailable.")

        return unavailable
    return HomeAssistantBridge(
        base_url=settings.base_url,
        token=settings.credential,
        timeout_seconds=settings.timeout_seconds,
    ).fetch_entity_state


def _correlation_key(subject: str) -> str:
    return f"home_automation:entry:{subject}"


def _utc_datetime() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
