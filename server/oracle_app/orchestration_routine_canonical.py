from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from oracle_app import state
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
    resume_due_routines,
    start_routine,
)
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge
from oracle_app.ui_audio_control import (
    set_audiobook_sleep_timer_seconds,
    start_current_audiobook_for_user,
)


class CanonicalRoutineExecution:
    """Composite-routine controller inputs bound to one applied snapshot."""

    def __init__(
        self,
        *,
        settings: RoutineRuntimeSettings,
        home_assistant: HomeAssistantRuntimeSettings | None,
        audiobooks: CanonicalAudiobookExecution | None,
    ) -> None:
        self.settings = settings
        self.home_assistant = home_assistant
        self.audiobooks = audiobooks
        adapters: dict[str, RoutineAdapter] = {
            "ui_action": self.ui_action,
            "audiobook_start": self.audiobook_start,
            "audiobook_resume": self.audiobook_start,
            "sleep_timer": self.sleep_timer,
            "state_check": self.state_check,
            "playback_check": self.playback_check,
        }
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
            defer_audible_start=defer_audible_start,
            db_path=db_path,
        )

    def resume_due(self, *, now=None, db_path=None) -> list[dict[str, Any]]:
        return resume_due_routines(
            now=now,
            db_path=db_path,
            adapters=self.adapters,
            required_config_revision=self.settings.config_revision,
        )

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
                get_active_playback_for_source=state.get_active_audiobook_playback_for_source,
                execute_satellite_command=self.audiobooks.execute_satellite_command,
                close_audiobook_session=self.audiobooks.close_session,
                sync_audiobook_session=self.audiobooks.sync_session,
                clear_active_playback=state.clear_active_audiobook_playback,
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
        operation = mapping.allowed_operations[0]
        expected_state = {
            "turn_on": "on",
            "turn_off": "off",
            "lock": "locked",
            "unlock": "unlocked",
        }.get(operation)
        if expected_state is None:
            return {
                "ok": False,
                "error": "home_assistant_action_unconfigured",
                "detail": f"Canonical routine action {action_id} has no implemented operation.",
            }
        bridge = self._home_assistant_bridge()
        bridge.call_service(
            service_domain=mapping.entity_id.split(".", 1)[0],
            service_name=operation,
            entity_id=mapping.entity_id,
        )
        latest = bridge.wait_for_entity_state(mapping.entity_id, expected_state)
        actual_state = str((latest or {}).get("state") or "").strip().lower()
        ok = actual_state == expected_state
        return {
            "ok": ok,
            "status": "executed" if ok else "failed",
            "action_id": action_id,
            "expected_state": expected_state,
            "actual_state": actual_state or "unknown",
            "detail": (
                f"{action_id} completed."
                if ok
                else f"Expected {expected_state}, got {actual_state or 'unknown'}."
            ),
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
