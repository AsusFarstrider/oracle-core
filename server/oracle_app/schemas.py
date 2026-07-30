from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RouteTarget = Literal[
    "audiobook",
    "calendar",
    "facts",
    "fallback_router",
    "home_assistant",
    "music",
    "network",
    "news",
    "system",
    "weather",
]


class RouteRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Transcript or normalized user text")
    source: str | None = Field(
        default=None,
        description="Optional satellite or client identifier",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional conversation or request session ID",
    )


class RouteResponse(BaseModel):
    target: RouteTarget
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    normalized_text: str


class HookInfo(BaseModel):
    name: str
    method: str
    path: str
    description: str


class DispatchPlan(BaseModel):
    target: RouteTarget
    hook: str
    payload: dict[str, Any]
    status: Literal[
        "planned",
        "pending_integration",
        "pending_confirmation",
        "pending_clarification",
        "executed",
        "failed",
    ]
    result: dict[str, Any] | None = None


class CommandRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Transcript or normalized user text")
    source: str | None = Field(
        default=None,
        description="Optional satellite or client identifier",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional conversation or request session ID",
    )
    playback_target_source_id: str | None = Field(
        default=None,
        description="Optional media playback destination; never request-source proof",
    )


class SatelliteActivityRequest(BaseModel):
    source_id: str = Field(..., min_length=1, description="Satellite source identifier")
    event_type: str | None = Field(default=None, description="Approved satellite activity event type")
    status: str | None = Field(default=None, description="Satellite status snapshot value")
    correlation_id: str | None = Field(default=None, description="Optional interaction correlation ID")
    observed_at: str | None = Field(default=None, description="Optional observation timestamp")
    payload: dict[str, Any] = Field(default_factory=dict, description="Metadata-only event payload")
    snapshot: dict[str, Any] = Field(default_factory=dict, description="Latest satellite status snapshot metadata")


class SatelliteActivityResponse(BaseModel):
    accepted: bool


class WakeClaimRequest(BaseModel):
    satellite_id: str = Field(..., min_length=1, description="Satellite claiming local wake detection")
    room_id: str | None = Field(default=None, description="Optional room metadata for the claiming satellite")
    profile: str | None = Field(default=None, description="Optional satellite profile metadata")
    timestamp: str | None = Field(default=None, description="Optional client-side wake timestamp")
    wake_confidence: float | None = Field(default=None, description="Optional wake model confidence")
    audio_level: float | None = Field(default=None, description="Optional local wake audio level/RMS")
    correlation_id: str | None = Field(default=None, description="Optional interaction correlation ID")


class WakeClaimResponse(BaseModel):
    interaction_id: str
    satellite_id: str
    winner_satellite_id: str
    decision: Literal["proceed", "stand_down"]
    reason: Literal["highest_audio_level", "highest_wake_confidence", "most_recent"]
    participants: list[str] = Field(default_factory=list)
    window_ms: int
    room_id: str | None = None
    profile: str | None = None


class UiActionRequest(BaseModel):
    action_id: str = Field(..., min_length=1, description="Curated public UI action identifier")
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    source: str | None = Field(
        default=None,
        description="Optional explicit playback-capable Oracle source for source-scoped UI actions",
    )


class UiContextStartRequest(BaseModel):
    action: Literal["set_alarm", "music_search", "audiobook_search"] = Field(..., description="Contextual UI action to start")
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    ui_session_id: str | None = Field(default=None, min_length=1, description="Bounded UI session id for temporary state")
    target_source_id: str | None = Field(default=None, min_length=1, description="Explicit Oracle target for the contextual action")
    source: str | None = Field(default=None, min_length=1, description="Deprecated request-source or target compatibility alias")
    session_id: str | None = Field(default=None, min_length=1, description="Deprecated ui_session_id compatibility alias")


class UiAlarmCancelRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    source: str = Field(..., min_length=1, description="Oracle source id that owns the alarm")


class UiCalendarDraftRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    title: str = Field(..., description="Calendar event title")
    date: str = Field(..., description="Calendar event date in YYYY-MM-DD format")
    all_day: bool = Field(default=False, description="Whether this is an all-day event")
    start_time: str | None = Field(default=None, description="Start time in HH:MM format for timed events")
    end_time: str | None = Field(default=None, description="End time in HH:MM format for timed events")
    duration_minutes: int | None = Field(default=None, description="Optional duration in minutes for timed events")


class UiCalendarDraftConfirmRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    draft_id: str = Field(..., min_length=1, description="UI-owned calendar draft identifier")


class UiCalendarDraftCancelRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    draft_id: str = Field(..., min_length=1, description="UI-owned calendar draft identifier")


class UiAudioSearchRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    kind: Literal["audiobook", "music"] = Field(..., description="Explicit search domain")
    query: str = Field(..., description="Search text")
    source: str | None = Field(default=None, description="Optional Oracle source id for source-scoped defaults")
    user_id: str | None = Field(default=None, description="Optional user id for audiobook-scoped searches")
    limit: int = Field(default=10, ge=1, le=25, description="Maximum normalized result cards to return")


class UiAudioPlayRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    target: str = Field(..., min_length=1, description="Playback-capable Oracle source id")
    result: dict[str, Any] = Field(..., description="Normalized selected result card")
    user_id: str | None = Field(default=None, description="Optional user id for audiobook playback")
    sleep_timer_minutes: int | None = Field(default=None, ge=0, le=240, description="Optional audiobook sleep timer")


class UiAudioControlRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    target: str = Field(..., min_length=1, description="Playback-capable Oracle source id")
    operation: Literal["pause", "resume", "stop", "volume_up", "volume_down"] = Field(..., description="Playback control operation")
    media_kind: Literal["audiobook", "music"] | None = Field(default=None, description="Optional expected active media kind")


class UiAudioSleepTimerRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier")
    target: str = Field(..., min_length=1, description="Playback-capable Oracle source id")
    operation: Literal["set", "cancel", "status"] = Field(..., description="Sleep timer operation")
    minutes: int | None = Field(default=None, ge=0, le=240, description="Timer duration for set operations")


class UiOrchestrationPreviewRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier requesting the preview")


class UiOrchestrationApprovalRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier approving the preview")
    preview_id: str = Field(..., min_length=1, description="Frozen recovery preview identifier")
    digest: str = Field(..., min_length=64, max_length=64, description="SHA-256 digest returned with the preview")
    approved: bool = Field(..., description="Explicit approval of only the frozen listed plan")


class UiRoutineRunRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier starting the routine")
    source: str | None = Field(default=None, description="Optional known Oracle source initiating the routine")
    inputs: dict[str, Any] = Field(default_factory=dict, description="Bounded declared routine input overrides")


class UiRoutineCancelRequest(BaseModel):
    client_id: str = Field(..., min_length=1, description="Stable UI client identifier canceling the routine")


class CommandResponse(BaseModel):
    route: RouteResponse
    dispatch: DispatchPlan
    reply_text: str
    session_id: str | None = None
    effective_session_id: str | None = None


class VoiceDeferredResumeRequest(BaseModel):
    source: str = Field(..., min_length=1, description="Playback-capable satellite source id")
    deferred_session: dict[str, Any] = Field(..., description="Deferred playback session returned by a command")


class CommandInterimEvent(BaseModel):
    event_id: int = Field(..., ge=1)
    event_type: Literal["facts_summarizer_ack"]
    source: str
    session_id: str
    domain: str
    message: str
    created_at: str


class CommandInterimEventsResponse(BaseModel):
    events: list[CommandInterimEvent] = Field(default_factory=list)


class AlertNotification(BaseModel):
    alert_id: str
    kind: str
    message: str
    due_at: str
    source: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingAlertsResponse(BaseModel):
    alerts: list[AlertNotification]


class HomeAssistantEventIngressRequest(BaseModel):
    event_id: str = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    entity_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
    state: str = Field(..., min_length=1, max_length=64)
    occurred_at: datetime | None = None


class HomeAssistantEventIngressResponse(BaseModel):
    status: str
    event_id: str
    event_type: str = ""
    subject: str = ""
    state: str = ""
    run_id: str = ""
    reason: str = ""


class FactsRequestContext(BaseModel):
    user_id: str | None = None
    room_id: str | None = None
    interface: str | None = None
    conversation_id: str | None = None
    timezone: str | None = None


class FactsRequestOptions(BaseModel):
    prefer_short_answer: bool = True
    include_evidence: bool = True
    max_evidence_items: int = Field(default=5, ge=0, le=20)


class FactsProviderRequest(BaseModel):
    query: str = Field(..., min_length=1)
    context: FactsRequestContext = Field(default_factory=FactsRequestContext)
    options: FactsRequestOptions = Field(default_factory=FactsRequestOptions)


class FactsAnswer(BaseModel):
    text: str
    answer_type: str = "extractive"


class FactsEvidence(BaseModel):
    title: str
    snippet: str
    source_name: str
    source_type: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class FactsProviderInfo(BaseModel):
    id: str
    name: str


class FactsRetrievalInfo(BaseModel):
    method: str
    notes: list[str] = Field(default_factory=list)


class FactsProviderResult(BaseModel):
    status: Literal["answered", "evidence_only", "no_result", "provider_error", "disabled"]
    query: str
    answer: FactsAnswer | None = None
    evidence: list[FactsEvidence] = Field(default_factory=list)
    provider: FactsProviderInfo
    retrieval: FactsRetrievalInfo
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    home_assistant_configured: bool
    ollama_configured: bool


class HomeAssistantHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    home_assistant_url: str | None = None
    detail: str
    http_status: int | None = None


class CalendarHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    calendar_configured: bool
    timezone: str | None = None
    detail: str


class MusicHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    plex_configured: bool
    configured_satellites: list[str]
    detail: str


class AudiobookHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    audiobookshelf_configured: bool
    configured_satellites: list[str]
    detail: str


class NewsHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    configured_sources: list[str]
    detail: str


class LibreNmsHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    provider: str
    configured: bool
    available: bool
    degraded: bool = False
    detail: str
    missing_config_keys: list[str] = Field(default_factory=list)
    checked_at: str | None = None
    http_status: int | None = None
    active_alert_count: int | None = None


class OllamaHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    ollama_url: str | None = None
    model: str | None = None
    detail: str
    http_status: int | None = None


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to synthesize")


class TtsHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    provider: str
    configured: bool
    available: bool
    detail: str


class SttResponse(BaseModel):
    text: str
    provider: str


class SttHealthResponse(BaseModel):
    status: Literal["ok", "failed", "disabled"]
    service: str
    provider: str
    configured: bool
    available: bool
    detail: str
