from __future__ import annotations

from datetime import UTC, datetime, time
import hashlib
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from oracle_app import audiobook_state
from oracle_app.alerts import create_alert_batch
from oracle_app.audiobook_runtime.canonical import CanonicalAudiobookExecution
from oracle_app.audiobook_runtime.playback import sync_then_control as sync_then_control_audiobook
from oracle_app.configuration.domain_models import HomeAssistantObjectMapping
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.routine_runtime_settings import (
    RoutineDefinitionRuntimeSettings,
    RoutineRuntimeSettings,
)
from oracle_app.orchestration_routines import (
    RoutineAdapter,
    cancel_routine,
    resume_due_routines,
    start_routine,
)
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge
from oracle_app.home_assistant_actions import (
    execute_home_assistant_ui_action,
    execute_home_semantic_capability,
)
from oracle_app.notifications.canonical import CanonicalNotificationExecution
from oracle_app.ui_audio_control import (
    set_audiobook_sleep_timer_seconds,
    start_current_audiobook_for_user,
)
from oracle_app.runbook_kernel import RunbookRepository
from oracle_app.temporal import resolve_local_wall_time
from oracle_app.memory.orchestration_trigger_evidence import (
    complete_trigger_transition,
    observe_trigger_transition,
)


class CanonicalRoutineExecution:
    """Composite-routine controller inputs bound to one applied snapshot."""

    def __init__(
        self,
        *,
        settings: RoutineRuntimeSettings,
        home_assistant: HomeAssistantRuntimeSettings | None,
        audiobooks: CanonicalAudiobookExecution | None,
        notifications: CanonicalNotificationExecution | None = None,
    ) -> None:
        self.settings = settings
        self.home_assistant = home_assistant
        self.audiobooks = audiobooks
        self.notifications = notifications
        adapters: dict[str, RoutineAdapter] = {
            "ui_action": self.ui_action,
            "audiobook_start": self.audiobook_start,
            "audiobook_resume": self.audiobook_start,
            "sleep_timer": self.sleep_timer,
            "state_check": self.state_check,
            "playback_check": self.playback_check,
            "notification": self.notification,
            "timer_sound": self.timer_sound,
            "state:home_target_state": self.home_target_state,
            "state:audiobook_playback": self.audiobook_playback_state,
        }
        for capability_id in (
            "home.lights.set",
            "home.power.set",
            "home.environment.setpoint",
            "home.access.set",
            "home.provider_action.invoke",
            "audiobooks.start_current",
            "audiobooks.sleep_timer.set",
            "alerts.sound",
            "notifications.trigger",
        ):
            adapters[capability_id] = self._capability_adapter(capability_id)
        self.adapters: Mapping[str, RoutineAdapter] = MappingProxyType(adapters)

    def definition_payload(self, routine_id: str) -> dict[str, Any]:
        runtime = self.settings.definition(routine_id)
        if runtime is None:
            raise KeyError(routine_id)
        return _definition_payload(runtime)

    def resolve_voice_trigger(self, phrase: str, *, source_id: str | None) -> dict[str, Any] | None:
        runtime = self.settings.resolve_voice_trigger(phrase, source_id=source_id)
        return None if runtime is None else _definition_payload(runtime)

    def start(
        self,
        routine_id: str,
        *,
        client_id: str,
        inputs: dict[str, Any] | None = None,
        defer_audible_start: bool = False,
        invocation: str = "manual",
        activation_idempotency_key: str = "",
        trigger: Mapping[str, Any] | None = None,
        db_path=None,
    ) -> dict[str, Any]:
        try:
            definition = self.definition_payload(routine_id)
        except KeyError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Routine definition was not found.") from exc
        return start_routine(
            routine_id,
            client_id=client_id,
            inputs=inputs,
            definition=definition,
            adapters=self.adapters,
            config_revision=self.settings.config_revision,
            definition_catalog={
                definition_id: _definition_payload(runtime)
                for definition_id, runtime in self.settings.definitions.items()
            },
            invocation=invocation,
            activation_idempotency_key=activation_idempotency_key,
            trigger=trigger,
            defer_audible_start=defer_audible_start,
            db_path=db_path,
        )

    def activate_evidence(
        self,
        *,
        kind: str,
        evidence_id: str,
        state: str,
        occurrence_id: str,
        db_path=None,
    ) -> list[dict[str, Any]]:
        """Activate exact configured evidence triggers through one bounded seam."""

        results: list[dict[str, Any]] = []
        for routine_id, runtime in self.settings.definitions.items():
            composition = runtime.definition.composition
            if composition is None:
                continue
            for trigger in composition.automatic_triggers:
                if (
                    trigger.kind != kind
                    or getattr(trigger, "evidence_id", None) != evidence_id
                    or getattr(trigger, "expected_state", None) != state
                ):
                    continue
                results.append(
                    self.start(
                        routine_id,
                        client_id=f"automatic-{kind}",
                        inputs=dict(trigger.inputs),
                        invocation="automatic",
                        activation_idempotency_key=_trigger_idempotency_key(
                            routine_id, trigger.id, occurrence_id
                        ),
                        trigger={
                            "kind": kind,
                            "trigger_id": trigger.id,
                            "evidence_id": evidence_id,
                            "state": state,
                            "occurrence_id": occurrence_id,
                        },
                        db_path=db_path,
                    )
                )
        return results

    def activate_due_schedules(
        self,
        *,
        now: datetime | None = None,
        db_path=None,
    ) -> list[dict[str, Any]]:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Schedule evaluation requires a timezone-aware instant.")
        zone = ZoneInfo(self.settings.household_timezone)
        local = current.astimezone(zone)
        results: list[dict[str, Any]] = []
        for routine_id, runtime in self.settings.definitions.items():
            composition = runtime.definition.composition
            if composition is None:
                continue
            for trigger in composition.automatic_triggers:
                if trigger.kind != "schedule" or local.weekday() not in trigger.weekdays:
                    continue
                due = resolve_local_wall_time(
                    local.date(),
                    time(trigger.household_local_hour, trigger.household_local_minute),
                    self.settings.household_timezone,
                )
                lateness = (current.astimezone(UTC) - due.astimezone(UTC)).total_seconds()
                if lateness < 0 or lateness >= 60:
                    continue
                intended = f"{local.date().isoformat()}T{trigger.household_local_hour:02d}:{trigger.household_local_minute:02d}"
                results.append(
                    self.start(
                        routine_id,
                        client_id="automatic-schedule",
                        inputs=dict(trigger.inputs),
                        invocation="automatic",
                        activation_idempotency_key=_trigger_idempotency_key(
                            routine_id, trigger.id, intended
                        ),
                        trigger={
                            "kind": "schedule",
                            "trigger_id": trigger.id,
                            "occurrence_id": intended,
                            "due_at": due.astimezone(UTC).isoformat(),
                        },
                        db_path=db_path,
                    )
                )
        return results

    def automatic_trigger_tick(
        self,
        *,
        network_execution=None,
        now: datetime | None = None,
        db_path=None,
    ) -> list[dict[str, Any]]:
        results = self.activate_due_schedules(now=now, db_path=db_path)
        if network_execution is None or not self._has_automatic_trigger_kind("network_event"):
            return results
        observation = network_execution.internet_health()
        raw_state = str(getattr(observation, "status", "") or "").casefold()
        state = "recovered" if raw_state == "healthy" else "degraded" if raw_state in {"degraded", "down"} else ""
        if not state:
            return results
        occurred = observe_trigger_transition(
            kind="network_event",
            evidence_id="internet_health",
            state=state,
            observed_at=str(getattr(observation, "checked_at", "") or datetime.now(UTC).isoformat()),
            db_path=db_path,
        )
        if occurred is None:
            return results
        results.extend(
            self.activate_evidence(
                kind="network_event",
                evidence_id="internet_health",
                state=state,
                occurrence_id=occurred["occurrence_id"],
                db_path=db_path,
            )
        )
        complete_trigger_transition(
            occurred["occurrence_id"],
            kind="network_event",
            evidence_id="internet_health",
            db_path=db_path,
        )
        return results

    def _has_automatic_trigger_kind(self, kind: str) -> bool:
        return any(
            runtime.definition.composition is not None
            and any(item.kind == kind for item in runtime.definition.composition.automatic_triggers)
            for runtime in self.settings.definitions.values()
        )

    def list_runs(
        self,
        *,
        source_id: str | None = None,
        active_only: bool = False,
        limit: int = 100,
        db_path=None,
    ) -> list[dict[str, Any]]:
        allowed = {
            routine_id
            for routine_id, runtime in self.settings.definitions.items()
            if source_id is None or source_id in runtime.sources
        }
        repository = RunbookRepository(db_path=db_path)
        runs = repository.list_runs(kind="routine", domain="composite", limit=limit)
        return [
            run
            for run in runs
            if run["orchestration_id"] in allowed
            and (not active_only or run["status"] in {"running", "waiting"})
        ]

    def voice_run_command(self, text: str, *, source_id: str, db_path=None) -> dict[str, Any] | None:
        normalized = " ".join(str(text or "").casefold().replace("_", " ").split())
        if normalized in {"what routines are running", "which routines are running", "routine status"}:
            active = self.list_runs(source_id=source_id, active_only=True, db_path=db_path)
            if not active:
                return {"action": "routine_status", "ok": True, "reply_text": "No routines are running."}
            names = [self._run_display_name(item) for item in active]
            return {
                "action": "routine_status",
                "ok": True,
                "runs": active,
                "reply_text": "Active routines: " + ", ".join(names) + ".",
            }
        action = ""
        target = ""
        for prefix in ("cancel ", "stop "):
            if normalized.startswith(prefix):
                action, target = "routine_cancel", normalized.removeprefix(prefix).strip()
                break
        if not action and normalized.startswith("status of "):
            action, target = "routine_status", normalized.removeprefix("status of ").strip()
        if not action and normalized.startswith("what is ") and normalized.endswith(" doing"):
            action, target = "routine_status", normalized[8:-6].strip()
        if not action:
            return None
        matches = [
            routine_id
            for routine_id, runtime in self.settings.definitions.items()
            if source_id in runtime.sources
            and target in {
                " ".join(routine_id.casefold().replace("_", " ").split()),
                " ".join(runtime.definition.display_name.casefold().split()),
            }
        ]
        if len(matches) > 1:
            return {"action": action, "ok": False, "clarify": True, "reply_text": "Which routine did you mean?"}
        if not matches:
            return None
        routine_id = matches[0]
        runs = [item for item in self.list_runs(source_id=source_id, db_path=db_path) if item["orchestration_id"] == routine_id]
        active = [item for item in runs if item["status"] in {"running", "waiting"}]
        name = self.settings.definitions[routine_id].definition.display_name
        if action == "routine_cancel":
            if not active:
                return {"action": action, "ok": False, "reply_text": f"{name} is not running."}
            run = self.cancel(active[0]["run_id"], requester=f"voice:{source_id}", db_path=db_path)
            return {"action": action, "ok": run["status"] == "canceled", "run": run, "reply_text": str(run["summary"])}
        if not runs:
            return {"action": action, "ok": True, "reply_text": f"{name} has not run yet."}
        run = active[0] if active else runs[0]
        return {
            "action": action,
            "ok": True,
            "run": run,
            "reply_text": f"{name} is {str(run['status']).replace('_', ' ')}. {run['summary']}",
        }

    def _run_display_name(self, run: Mapping[str, Any]) -> str:
        runtime = self.settings.definition(str(run.get("orchestration_id") or ""))
        return str(runtime.definition.display_name if runtime is not None else run.get("orchestration_id") or "Routine")

    def resume_due(self, *, now=None, db_path=None) -> list[dict[str, Any]]:
        return resume_due_routines(
            now=now,
            db_path=db_path,
            adapters=self.adapters,
            required_config_revision=self.settings.config_revision,
        )

    def cancel(self, run_id: str, *, requester: str, db_path=None) -> dict[str, Any]:
        run = RunbookRepository(db_path=db_path).require_run(run_id)
        if (
            run.get("kind") != "routine"
            or run.get("definition_domain") != "composite"
            or self.settings.definition(str(run.get("orchestration_id") or "")) is None
        ):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Routine run was not found.")
        return cancel_routine(
            run_id,
            cancellation_requester=requester,
            db_path=db_path,
            adapters=self.adapters,
        )

    def _capability_adapter(self, capability_id: str) -> RoutineAdapter:
        def execute(
            *,
            arguments: dict[str, object],
            run_id: str,
            operation_id: str,
            preauthorized: bool,
        ) -> dict[str, Any]:
            if capability_id.startswith("home."):
                return dict(
                    execute_home_semantic_capability(
                        capability_id,
                        arguments,
                        home_assistant_settings=self.home_assistant,
                        confirmed=preauthorized,
                    )
                )
            occurrence_id = f"{run_id}:{operation_id}"
            if capability_id == "audiobooks.start_current":
                result = self.audiobook_start(
                    user_id=str(arguments["user_id"]),
                    source_id=str(arguments["source_id"]),
                    client_id="orchestration",
                    defer_audible_start=False,
                    sleep_timer_seconds=None,
                )
            elif capability_id == "audiobooks.sleep_timer.set":
                result = self.sleep_timer(
                    source_id=str(arguments["source_id"]),
                    duration_seconds=int(arguments["duration_seconds"]),
                    client_id="orchestration",
                )
            elif capability_id == "notifications.trigger":
                result = self.notification(
                    notification_id=str(arguments["notification_id"]),
                    occurrence_id=occurrence_id,
                    correlation_id=run_id,
                    client_id="orchestration",
                )
            elif capability_id == "alerts.sound":
                result = self.timer_sound(
                    source_id=str(arguments["source_id"]),
                    occurrence_id=occurrence_id,
                    client_id="orchestration",
                )
            else:
                return {"ok": False, "outcome": "unsupported", "error": "capability_unavailable"}
            outcome = "accepted" if result.get("ok") is True else "failed"
            return {
                **result,
                "outcome": outcome,
                "result": {
                    "outcome": outcome,
                    "message_code": "capability_accepted" if result.get("ok") is True else "capability_failed",
                    "operation_id": operation_id,
                },
            }

        return execute

    def home_target_state(self, *, target_id: str) -> dict[str, object]:
        settings = self.home_assistant
        candidates = [] if settings is None else [
            mapping
            for mapping in settings.mappings.values()
            if isinstance(mapping, HomeAssistantObjectMapping)
            and mapping.kind == "entity"
            and mapping.oracle_id == target_id
            and "read" in mapping.allowed_operations
        ]
        if len(candidates) != 1:
            return {"state": "unknown"}
        payload = self._home_assistant_bridge().fetch_entity_state(candidates[0].entity_id) or {}
        return {"state": str(payload.get("state") or "unknown").casefold()}

    def audiobook_playback_state(self, *, target_id: str) -> dict[str, object]:
        if self.audiobooks is None:
            return {"state": "unknown"}
        authority = self.audiobooks.fetch_playback_authority(target_id)
        owner = authority.get("output_owner") if isinstance(authority, dict) else None
        if not isinstance(owner, dict):
            return {"state": "unknown"}
        media_kind = str(owner.get("media_kind") or "").casefold()
        state_name = str(owner.get("state") or "").casefold()
        if not state_name:
            return {"state": "unknown"}
        if media_kind != "audiobook" or state_name in {"idle", "stopped", "ended", "closed"}:
            return {"state": "stopped"}
        if state_name == "paused":
            return {"state": "paused"}
        if state_name in {"playing", "buffering", "loading"}:
            return {"state": "playing"}
        return {"state": "unknown"}

    def ui_action(
        self,
        *,
        action_id: str,
        client_id: str,
        source_id: str | None = None,
    ) -> dict[str, object]:
        del client_id
        if action_id == "stop_audiobook":
            if self.audiobooks is None:
                return {"ok": False, "error": "audiobooks_disabled", "detail": "Audiobooks are disabled."}
            status, result = sync_then_control_audiobook(
                source=source_id,
                action="stop_longform_audio",
                close_session=True,
                get_active_playback_for_source=audiobook_state.get_active_audiobook_playback_for_source,
                execute_satellite_command=self.audiobooks.execute_satellite_command,
                close_audiobook_session=self.audiobooks.close_session,
                sync_audiobook_session=self.audiobooks.sync_session,
                clear_active_playback=audiobook_state.clear_active_audiobook_playback,
            )
            return {"ok": status == "executed", "status": status, **result}
        mapping = None if self.home_assistant is None else self.home_assistant.mapping(action_id)
        if (
            not isinstance(mapping, HomeAssistantObjectMapping)
            or mapping.kind != "action"
            or len(mapping.allowed_operations) != 1
            or "." not in mapping.entity_id
        ):
            return {
                "ok": False,
                "error": "home_assistant_action_unconfigured",
                "detail": f"Canonical routine action {action_id} is unavailable.",
            }
        result = execute_home_assistant_ui_action(
            action_id,
            home_assistant_settings=self.home_assistant,
            interface="runbook",
        )
        return result or {
            "ok": False,
            "error": "home_assistant_action_unconfigured",
            "detail": f"Canonical routine action {action_id} has no implemented operation.",
        }

    def audiobook_start(self, **kwargs: Any) -> dict[str, object]:
        if self.audiobooks is None:
            return {"ok": False, "error": "audiobooks_disabled", "detail": "Audiobooks are disabled."}
        return start_current_audiobook_for_user(
            **kwargs,
            audiobook_execution=self.audiobooks,
        )

    def sleep_timer(self, **kwargs: Any) -> dict[str, object]:
        if self.audiobooks is None:
            return {"ok": False, "error": "audiobooks_disabled", "detail": "Audiobooks are disabled."}
        return set_audiobook_sleep_timer_seconds(
            **kwargs,
            audiobook_execution=self.audiobooks,
        )

    def state_check(
        self,
        *,
        check_id: str,
        expected_state: str,
        client_id: str,
    ) -> dict[str, object]:
        del client_id
        mapping = None if self.home_assistant is None else self.home_assistant.mapping(check_id)
        if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != "entity":
            return {"ok": False, "error": "unknown_state_check", "detail": f"Unknown state check {check_id}."}
        bridge = self._home_assistant_bridge()
        payload = bridge.fetch_entity_state(mapping.entity_id) or {}
        actual_state = str(payload.get("state") or "").strip().lower()
        expected = str(expected_state).strip().lower()
        return {
            "ok": actual_state == expected,
            "status": "passed" if actual_state == expected else "failed",
            "expected_state": expected,
            "actual_state": actual_state or "unknown",
            "detail": (
                f"{check_id} is {expected}."
                if actual_state == expected
                else f"Expected {expected}, got {actual_state or 'unknown'}."
            ),
        }

    def playback_check(
        self,
        *,
        source_id: str,
        check_id: str,
        client_id: str,
    ) -> dict[str, object]:
        del client_id
        if check_id != "routine_audiobook_stopped" or self.audiobooks is None:
            return {"ok": False, "error": "unknown_playback_check", "detail": f"Unknown playback check {check_id}."}
        authority = self.audiobooks.fetch_playback_authority(source_id)
        owner = authority.get("output_owner") if isinstance(authority, dict) else None
        owner = owner if isinstance(owner, dict) else {}
        media_kind = str(owner.get("media_kind") or "").strip().lower()
        state_name = str(owner.get("state") or "").strip().lower()
        active = media_kind == "audiobook" and state_name not in {"", "idle", "stopped", "ended", "closed"}
        return {
            "ok": not active,
            "status": "passed" if not active else "failed",
            "media_kind": media_kind or None,
            "playback_state": state_name or None,
            "detail": "Audiobook playback is stopped." if not active else "Audiobook playback is still active.",
        }

    def notification(
        self,
        *,
        notification_id: str,
        occurrence_id: str,
        correlation_id: str,
        client_id: str,
    ) -> dict[str, object]:
        del client_id
        if self.notifications is None:
            return {"ok": False, "error": "notifications_disabled", "detail": "Notifications are disabled."}
        result = self.notifications.submit(
            notification_id,
            occurrence_id,
            caller="orchestration",
            correlation_id=correlation_id,
        )
        status = str(result.get("status") or "")
        return {
            **result,
            "ok": status in {"queued", "duplicate"},
            "detail": f"{notification_id} announcement {status or 'failed'}.",
        }

    def timer_sound(
        self,
        *,
        source_id: str,
        occurrence_id: str,
        client_id: str,
    ) -> dict[str, object]:
        del client_id
        alerts, duplicate = create_alert_batch(
            kind="timer",
            due_at=datetime.now().astimezone(),
            message="Timer finished.",
            sources=[source_id],
            session_id=occurrence_id,
            metadata={
                "caller": "orchestration",
                "operation": "timer_sound",
                "completion_policy": "delivery_accepted",
                "completion_owner_type": "routine",
                "completion_owner_id": occurrence_id,
            },
            idempotency_key=f"routine-timer-sound:{occurrence_id}",
        )
        return {
            "ok": True,
            "status": "duplicate" if duplicate else "queued",
            "alert_id": None if duplicate or not alerts else alerts[0].alert_id,
            "occurrence_id": None if duplicate or not alerts else alerts[0].occurrence_id,
            "source_id": source_id,
            "detail": "Timer sound was already queued." if duplicate else "Timer sound queued.",
        }

    def _home_assistant_bridge(self) -> HomeAssistantBridge:
        settings = self.home_assistant
        if settings is None or not settings.enabled or not settings.base_url or not settings.credential:
            raise RuntimeError("Home Assistant is disabled in canonical configuration.")
        return HomeAssistantBridge(
            base_url=settings.base_url,
            token=settings.credential,
            timeout_seconds=settings.timeout_seconds,
        )


def _definition_payload(runtime: RoutineDefinitionRuntimeSettings) -> dict[str, Any]:
    return runtime.definition.model_dump(mode="json", exclude_none=True)


def _trigger_idempotency_key(routine_id: str, trigger_id: str, occurrence_id: str) -> str:
    raw = f"{routine_id}\0{trigger_id}\0{occurrence_id}".encode("utf-8")
    return "routine-trigger:" + hashlib.sha256(raw).hexdigest()
