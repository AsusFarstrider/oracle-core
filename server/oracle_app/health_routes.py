from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .config import get_home_assistant_settings, get_ollama_settings
from .config_reporting import (
    build_config_report_payload,
    choose_config_report_format,
    render_config_report_text,
)
from .config_validation import build_brain_config_report
from .health import (
    check_audiobook_health,
    check_calendar_health,
    check_home_assistant_health,
    check_librenms_health,
    check_music_health,
    check_news_health,
    check_ollama_health,
    check_stt_health,
    check_tts_health,
)
from .memory.provider_status import safe_observe_provider_health
from .schemas import (
    AudiobookHealthResponse,
    CalendarHealthResponse,
    HealthResponse,
    HomeAssistantHealthResponse,
    HookInfo,
    LibreNmsHealthResponse,
    MusicHealthResponse,
    NewsHealthResponse,
    OllamaHealthResponse,
    SttHealthResponse,
    TtsHealthResponse,
)


AVAILABLE_HOOKS = (
    HookInfo(
        name="health",
        method="GET",
        path="/health",
        description="Basic liveness check for the Oracle brain.",
    ),
    HookInfo(
        name="health_config",
        method="GET",
        path="/health/config",
        description="Report sanitized config validation findings for the running Oracle brain.",
    ),
    HookInfo(
        name="session",
        method="GET",
        path="/api/voice/session",
        description="Inspect a specific brain-owned session by source and effective session ID.",
    ),
    HookInfo(
        name="route",
        method="POST",
        path="/api/voice/route",
        description="Classify text and return the chosen backend target.",
    ),
    HookInfo(
        name="command",
        method="POST",
        path="/command",
        description="Primary hook for clients. Routes a request and returns a dispatch plan.",
    ),
    HookInfo(
        name="ingest_text",
        method="POST",
        path="/api/voice/ingest/text",
        description="Command-style submission for text-only clients.",
    ),
    HookInfo(
        name="health_audiobook",
        method="GET",
        path="/health/audiobook",
        description="Verify connectivity and authentication for the configured Audiobookshelf server.",
    ),
    HookInfo(
        name="health_calendar",
        method="GET",
        path="/health/calendar",
        description="Verify connectivity and parsing for the configured calendar feed.",
    ),
    HookInfo(
        name="health_home_assistant",
        method="GET",
        path="/health/home-assistant",
        description="Verify live connectivity and authentication to Home Assistant.",
    ),
    HookInfo(
        name="health_ollama",
        method="GET",
        path="/health/ollama",
        description="Verify live connectivity to the local Ollama API.",
    ),
    HookInfo(
        name="health_music",
        method="GET",
        path="/health/music",
        description="Verify Plex and satellite control configuration for music.",
    ),
    HookInfo(
        name="health_news",
        method="GET",
        path="/health/news",
        description="Verify configuration for live news feeds.",
    ),
    HookInfo(
        name="health_tts",
        method="GET",
        path="/health/tts",
        description="Verify TTS provider configuration and local availability.",
    ),
    HookInfo(
        name="health_stt",
        method="GET",
        path="/health/stt",
        description="Verify STT provider configuration and local availability.",
    ),
    HookInfo(
        name="audiobook_stream",
        method="GET",
        path="/audiobooks/stream/{playback_id}/{track_index}",
        description="Proxy a prepared audiobook track stream to a playback target.",
    ),
    HookInfo(
        name="tts",
        method="POST",
        path="/tts",
        description="Synthesize speech audio for satellite playback.",
    ),
    HookInfo(
        name="stt",
        method="POST",
        path="/stt",
        description="Transcribe uploaded audio into text.",
    ),
    HookInfo(
        name="pending_alerts",
        method="GET",
        path="/alerts/pending",
        description="Fetch due timers, alarms, and reminders for a source.",
    ),
    HookInfo(
        name="system_refresh_cache",
        method="POST",
        path="/command",
        description="Internal maintenance action that refreshes Oracle's Home Assistant cache.",
    ),
)


def health() -> HealthResponse:
    ha_settings_present = False
    ollama_settings_present = False
    try:
        get_home_assistant_settings()
        ha_settings_present = True
    except HTTPException:
        ha_settings_present = False
    try:
        get_ollama_settings()
        ollama_settings_present = True
    except HTTPException:
        ollama_settings_present = False

    return HealthResponse(
        status="ok",
        service="oracle-brain",
        home_assistant_configured=ha_settings_present,
        ollama_configured=ollama_settings_present,
    )


def health_config(request: Request) -> Response:
    request_app = request.scope.get("app")
    composition = getattr(
        getattr(request_app, "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    canonical = (
        composition
        if isinstance(composition, CanonicalBrainApplicationComposition)
        else None
    )
    report_sections = (
        []
        if canonical is not None
        else [("Brain config check:", build_brain_config_report())]
    )
    response_format = choose_config_report_format(request.query_params, request.headers.get("accept"))
    if response_format == "text":
        rendered = render_config_report_text(report_sections)
        if canonical is not None:
            rendered = _render_applied_configuration(canonical)
        return PlainTextResponse(rendered)
    payload = build_config_report_payload(
        service="oracle-brain",
        report_sections=report_sections,
    )
    if canonical is not None:
        payload["configuration"] = canonical.applied_configuration_payload()
    return JSONResponse(payload)


def _render_applied_configuration(
    composition: CanonicalBrainApplicationComposition,
) -> str:
    configuration = composition.applied_configuration_payload()
    applied = configuration["applied_generation"]
    if not isinstance(applied, dict):
        raise TypeError("Canonical applied configuration identity is invalid.")
    projection_ids = applied["satellite_projection_activation_ids"]
    if not isinstance(projection_ids, dict):
        raise TypeError("Canonical applied projection identity is invalid.")
    rendered_projection_ids = ", ".join(
        f"{satellite_id}={projection_ids[satellite_id]}"
        for satellite_id in sorted(projection_ids)
    ) or "-"
    return "\n".join(
        (
            "Applied configuration:",
            f"mode: {configuration['mode']}",
            f"activation_generation_id: {applied['activation_generation_id']}",
            f"config_generation_id: {applied['config_generation_id']}",
            f"secret_generation_id: {applied['secret_generation_id']}",
            f"config_revision: {applied['config_revision']}",
            f"selection_operation_id: {applied['selection_operation_id']}",
            f"selection_revision: {applied['selection_revision']}",
            f"satellite_projection_activation_ids: {rendered_projection_ids}",
        )
    ) + "\n"


def health_home_assistant() -> HomeAssistantHealthResponse:
    response = check_home_assistant_health()
    safe_observe_provider_health("home_assistant", response)
    return response


def _canonical_composition_from_request(request: Request) -> CanonicalBrainApplicationComposition | None:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    return composition if isinstance(composition, CanonicalBrainApplicationComposition) else None


def health_http(request: Request) -> HealthResponse:
    canonical = _canonical_composition_from_request(request)
    if canonical is None:
        return health()
    home_assistant = canonical.runtime.home_assistant
    return HealthResponse(
        status="ok",
        service="oracle-brain",
        home_assistant_configured=bool(home_assistant is not None and home_assistant.enabled),
        ollama_configured=bool(canonical.runtime.brain.inference.enabled),
    )


def health_home_assistant_http(request: Request) -> HomeAssistantHealthResponse:
    canonical = _canonical_composition_from_request(request)
    if canonical is None:
        return health_home_assistant()
    response = check_home_assistant_health(
        canonical.runtime.home_assistant,
        canonical_authority=True,
    )
    safe_observe_provider_health("home_assistant", response)
    return response


def health_audiobook() -> AudiobookHealthResponse:
    response = check_audiobook_health()
    safe_observe_provider_health("audiobookshelf", response)
    return response


def health_audiobook_http(request: Request) -> AudiobookHealthResponse:
    canonical = _canonical_composition_from_request(request)
    response = (
        health_audiobook()
        if canonical is None
        else check_audiobook_health(
            canonical.audiobook_execution,
            canonical_authority=True,
        )
    )
    if canonical is not None:
        safe_observe_provider_health("audiobookshelf", response)
    return response


def health_calendar() -> CalendarHealthResponse:
    response = check_calendar_health()
    safe_observe_provider_health("calendar", response)
    return response


def health_calendar_http(request: Request) -> CalendarHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_calendar_health(
        canonical_execution=None if composition is None else composition.calendar_execution,
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("calendar", response)
    return response


def health_ollama() -> OllamaHealthResponse:
    response = check_ollama_health()
    safe_observe_provider_health("ollama", response)
    return response


def health_ollama_http(request: Request) -> OllamaHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_ollama_health(
        inference=None if composition is None else composition.core_consumers.inference,
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("ollama", response)
    return response


def health_music() -> MusicHealthResponse:
    response = check_music_health()
    safe_observe_provider_health("music", response)
    return response


def health_music_http(request: Request) -> MusicHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_music_health(
        music_execution=None if composition is None else composition.music_execution,
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("music", response)
    return response


def health_news() -> NewsHealthResponse:
    return check_news_health()


def health_news_http(request: Request) -> NewsHealthResponse:
    composition = _canonical_composition_from_request(request)
    return check_news_health(
        canonical_execution=None if composition is None else composition.news_execution,
        canonical_authority=composition is not None,
    )


def health_librenms() -> LibreNmsHealthResponse:
    response = check_librenms_health()
    safe_observe_provider_health("librenms", response)
    return response


def health_librenms_http(request: Request) -> LibreNmsHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_librenms_health(
        canonical_execution=None if composition is None else composition.network_execution,
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("librenms", response)
    return response


def health_tts() -> TtsHealthResponse:
    response = check_tts_health()
    safe_observe_provider_health("tts", response)
    return response


def health_tts_http(request: Request) -> TtsHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_tts_health(
        provider=None if composition is None else composition.tts_provider(),
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("tts", response)
    return response


def health_stt() -> SttHealthResponse:
    response = check_stt_health()
    safe_observe_provider_health("stt", response)
    return response


def health_stt_http(request: Request) -> SttHealthResponse:
    composition = _canonical_composition_from_request(request)
    response = check_stt_health(
        provider=None if composition is None else composition.stt_provider(),
        canonical_authority=composition is not None,
    )
    safe_observe_provider_health("stt", response)
    return response


def list_hooks() -> list[HookInfo]:
    return list(AVAILABLE_HOOKS)


def register_health_routes(app: FastAPI) -> None:
    app.get("/api/admin/health", response_model=HealthResponse)(health_http)
    app.get("/health", response_model=HealthResponse)(health_http)
    app.get("/api/admin/health/config")(health_config)
    app.get("/health/config")(health_config)
    app.get("/api/admin/health/home-assistant", response_model=HomeAssistantHealthResponse)(health_home_assistant_http)
    app.get("/health/home-assistant", response_model=HomeAssistantHealthResponse)(health_home_assistant_http)
    app.get("/api/admin/health/audiobook", response_model=AudiobookHealthResponse)(health_audiobook_http)
    app.get("/health/audiobook", response_model=AudiobookHealthResponse)(health_audiobook_http)
    app.get("/api/admin/health/calendar", response_model=CalendarHealthResponse)(health_calendar_http)
    app.get("/health/calendar", response_model=CalendarHealthResponse)(health_calendar_http)
    app.get("/api/admin/health/ollama", response_model=OllamaHealthResponse)(health_ollama_http)
    app.get("/health/ollama", response_model=OllamaHealthResponse)(health_ollama_http)
    app.get("/api/admin/health/music", response_model=MusicHealthResponse)(health_music_http)
    app.get("/health/music", response_model=MusicHealthResponse)(health_music_http)
    app.get("/api/admin/health/news", response_model=NewsHealthResponse)(health_news_http)
    app.get("/health/news", response_model=NewsHealthResponse)(health_news_http)
    app.get("/api/admin/health/librenms", response_model=LibreNmsHealthResponse)(health_librenms_http)
    app.get("/health/librenms", response_model=LibreNmsHealthResponse)(health_librenms_http)
    app.get("/api/admin/health/tts", response_model=TtsHealthResponse)(health_tts_http)
    app.get("/health/tts", response_model=TtsHealthResponse)(health_tts_http)
    app.get("/api/admin/health/stt", response_model=SttHealthResponse)(health_stt_http)
    app.get("/health/stt", response_model=SttHealthResponse)(health_stt_http)
    app.get("/api/admin/hooks", response_model=list[HookInfo])(list_hooks)
