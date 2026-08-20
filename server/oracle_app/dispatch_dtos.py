from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .schemas import DispatchPlan, RouteTarget


DispatchStatus = Literal[
    "planned",
    "pending_integration",
    "pending_confirmation",
    "pending_clarification",
    "executed",
    "failed",
]


class DispatchContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str | None = None
    session_id: str | None = None
    playback_target_source_id: str | None = None
    alert_delivery_target_source_id: str | None = None
    alert_delivery_target_error: str | None = None


class DispatchFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    detail: str | None = None
    failure_class: str | None = None
    owning_component: str | None = None


class _TargetPayload(BaseModel):
    """Finite cross-target vocabulary; target subclasses own its interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str | None = None
    normalized_text: str | None = None
    source: str | None = None
    session_id: str | None = None
    action: str | None = None
    query: str | None = None
    prompt: str | None = None
    intent: str | None = None
    event_draft: dict[str, Any] | None = None
    collected: dict[str, Any] | None = None
    missing_field: str | None = None
    room_context: dict[str, Any] | None = None
    playback_target_source_id: str | None = None
    playback_target_resolution: str | None = None
    playback_target_error: str | None = None
    alert_delivery_target_source_id: str | None = None
    effective_user_id: str | None = None
    requested_user_name: str | None = None
    user_resolution_source: str | None = None
    user_resolution_error: str | None = None

    def context(self) -> DispatchContext:
        return DispatchContext(
            source_id=self.source,
            session_id=self.session_id,
            playback_target_source_id=self.playback_target_source_id,
            alert_delivery_target_source_id=self.alert_delivery_target_source_id,
        )


class AudiobookDispatchPayload(_TargetPayload):
    pass


class CalendarDispatchPayload(_TargetPayload):
    pass


class FactsDispatchPayload(_TargetPayload):
    pass


class FallbackRouterDispatchPayload(_TargetPayload):
    pass


class HomeAssistantDispatchPayload(_TargetPayload):
    pass


class MusicDispatchPayload(_TargetPayload):
    pass


class NetworkDispatchPayload(_TargetPayload):
    pass


class NewsDispatchPayload(_TargetPayload):
    pass


class SystemDispatchPayload(_TargetPayload):
    pass


class WeatherDispatchPayload(_TargetPayload):
    pass


TARGET_PAYLOAD_MODELS: dict[RouteTarget, type[_TargetPayload]] = {
    "audiobook": AudiobookDispatchPayload,
    "calendar": CalendarDispatchPayload,
    "facts": FactsDispatchPayload,
    "fallback_router": FallbackRouterDispatchPayload,
    "home_assistant": HomeAssistantDispatchPayload,
    "music": MusicDispatchPayload,
    "network": NetworkDispatchPayload,
    "news": NewsDispatchPayload,
    "system": SystemDispatchPayload,
    "weather": WeatherDispatchPayload,
}


class TargetDispatchOutcome(BaseModel):
    """Typed canonical outcome; provider/domain detail remains private data."""

    model_config = ConfigDict(frozen=True)

    target: RouteTarget
    status: DispatchStatus
    action: str | None = None
    failure: DispatchFailure | None = None
    details: dict[str, Any]


def validate_target_payload(dispatch: DispatchPlan) -> _TargetPayload:
    model = TARGET_PAYLOAD_MODELS[dispatch.target]
    return model.model_validate(dispatch.payload)


def target_outcome(dispatch: DispatchPlan) -> TargetDispatchOutcome:
    result = dict(dispatch.result or {})
    code = str(result.get("error") or "").strip()
    failure = None
    if dispatch.status == "failed":
        failure = DispatchFailure(
            code=code or "dispatch_failed",
            detail=str(result.get("detail") or "").strip() or None,
            failure_class=str(result.get("failure_class") or "").strip() or None,
            owning_component=str(result.get("owning_component") or "").strip() or None,
        )
    return TargetDispatchOutcome(
        target=dispatch.target,
        status=dispatch.status,
        action=str(result.get("action") or dispatch.payload.get("action") or "").strip() or None,
        failure=failure,
        details=result,
    )
