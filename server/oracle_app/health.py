from __future__ import annotations

from urllib import error, request

from fastapi import HTTPException

from stt import SttError
from tts import TtsError

from .config import (
    get_home_assistant_settings,
    get_librenms_settings,
    get_ollama_settings,
    get_stt_provider,
    get_tts_provider,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .provider_bridges.librenms import LibreNmsBridge
from .schemas import (
    AudiobookHealthResponse,
    CalendarHealthResponse,
    HomeAssistantHealthResponse,
    LibreNmsHealthResponse,
    MusicHealthResponse,
    NewsHealthResponse,
    OllamaHealthResponse,
    SttHealthResponse,
    TtsHealthResponse,
)


def check_audiobook_health(
    execution: CanonicalAudiobookExecution | None = None,
    *,
    canonical_authority: bool = False,
) -> AudiobookHealthResponse:
    if canonical_authority:
        if execution is None:
            return AudiobookHealthResponse(
                status="disabled",
                service="oracle-brain",
                audiobookshelf_configured=False,
                configured_satellites=[],
                detail="Audiobooks are disabled in canonical configuration.",
            )
        user_ids = sorted(execution.settings.user_accounts)
        if not user_ids:
            return AudiobookHealthResponse(
                status="failed",
                service="oracle-brain",
                audiobookshelf_configured=False,
                configured_satellites=sorted(execution.settings.playback_targets),
                detail="No canonical audiobook user accounts are configured.",
            )
        try:
            for user_id in user_ids:
                payload = execution.request_json("/ping", method="GET", user_id=user_id)
                if not bool(payload.get("success")):
                    raise RuntimeError(f"Audiobookshelf ping failed for {user_id}")
        except Exception as exc:
            return AudiobookHealthResponse(
                status="failed",
                service="oracle-brain",
                audiobookshelf_configured=True,
                configured_satellites=sorted(execution.settings.playback_targets),
                detail=str(exc),
            )
        return AudiobookHealthResponse(
            status="ok",
            service="oracle-brain",
            audiobookshelf_configured=True,
            configured_satellites=sorted(execution.settings.playback_targets),
            detail=f"Audiobookshelf reachable for {len(user_ids)} configured user account(s)",
        )

    from .audiobook import check_audiobook_health as _check

    result = _check()
    return AudiobookHealthResponse(**result)


def check_home_assistant_health(
    settings: HomeAssistantRuntimeSettings | None = None,
    *,
    canonical_authority: bool = False,
) -> HomeAssistantHealthResponse:
    if canonical_authority and (settings is None or not settings.enabled):
        return HomeAssistantHealthResponse(
            status="disabled",
            service="oracle-brain",
            detail="Home Assistant is disabled in canonical configuration.",
        )
    try:
        if canonical_authority:
            assert settings is not None
            base_url = settings.base_url or ""
            token = settings.credential or ""
            timeout_seconds = float(settings.timeout_seconds or 5)
        else:
            base_url, token = get_home_assistant_settings()
            timeout_seconds = 5
    except HTTPException as exc:
        return HomeAssistantHealthResponse(
            status="failed",
            service="oracle-brain",
            detail=str(exc.detail),
        )

    endpoint = f"{base_url}/api/"
    req = request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            return HomeAssistantHealthResponse(
                status="ok",
                service="oracle-brain",
                home_assistant_url=base_url,
                detail=raw_body,
                http_status=response.status,
            )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return HomeAssistantHealthResponse(
            status="failed",
            service="oracle-brain",
            home_assistant_url=base_url,
            detail=detail,
            http_status=exc.code,
        )
    except error.URLError as exc:
        return HomeAssistantHealthResponse(
            status="failed",
            service="oracle-brain",
            home_assistant_url=base_url,
            detail=str(exc.reason),
        )


def check_ollama_health(
    *,
    inference=None,
    canonical_authority: bool = False,
) -> OllamaHealthResponse:
    if canonical_authority:
        if inference is None or not inference.enabled or not inference.base_url or not inference.model:
            return OllamaHealthResponse(
                status="disabled",
                service="oracle-brain",
                detail="Canonical inference is disabled or not configured.",
            )
        base_url = str(inference.base_url).rstrip("/")
        model = str(inference.model)
        timeout_seconds = float(inference.timeout_seconds or 5)
    else:
        try:
            base_url, model = get_ollama_settings()
        except HTTPException as exc:
            return OllamaHealthResponse(
                status="failed",
                service="oracle-brain",
                detail=str(exc.detail),
            )
        timeout_seconds = 5

    try:
        if canonical_authority:
            response_status, raw_body = inference.version()
        else:
            endpoint = f"{base_url}/api/version"
            req = request.Request(endpoint, method="GET")
            with request.urlopen(req, timeout=timeout_seconds) as response:
                response_status = response.status
                raw_body = response.read().decode("utf-8", errors="replace")
        return OllamaHealthResponse(
            status="ok",
            service="oracle-brain",
            ollama_url=base_url,
            model=model,
            detail=raw_body,
            http_status=response_status,
        )
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return OllamaHealthResponse(
            status="failed",
            service="oracle-brain",
            ollama_url=base_url,
            model=model,
            detail=detail,
            http_status=exc.code,
        )
    except error.URLError as exc:
        return OllamaHealthResponse(
            status="failed",
            service="oracle-brain",
            ollama_url=base_url,
            model=model,
            detail=str(exc.reason),
        )


def check_music_health(*, music_execution=None, canonical_authority: bool = False) -> MusicHealthResponse:
    from .music import check_music_health as build_music_health

    return MusicHealthResponse(**build_music_health(music_execution=music_execution, canonical_authority=canonical_authority))


def check_calendar_health(*, canonical_execution=None, canonical_authority: bool = False) -> CalendarHealthResponse:
    from .calendar import check_calendar_health as build_calendar_health

    return CalendarHealthResponse(
        **build_calendar_health(
            canonical_execution=canonical_execution,
            canonical_authority=canonical_authority,
        )
    )


def check_news_health(*, canonical_execution=None, canonical_authority: bool = False) -> NewsHealthResponse:
    from .news import check_news_health as build_news_health

    return NewsHealthResponse(
        **build_news_health(
            canonical_execution=canonical_execution,
            canonical_authority=canonical_authority,
        )
    )


def check_librenms_health(
    *,
    canonical_execution=None,
    canonical_authority: bool = False,
) -> LibreNmsHealthResponse:
    if canonical_execution is not None:
        return LibreNmsHealthResponse(**canonical_execution.librenms_health())
    if canonical_authority:
        return LibreNmsHealthResponse(
            status="disabled",
            service="oracle-brain",
            provider="librenms",
            configured=False,
            available=False,
            degraded=False,
            detail="Canonical network monitoring is not configured.",
            missing_config_keys=[],
        )
    result = LibreNmsBridge().check_health(settings=get_librenms_settings())
    return LibreNmsHealthResponse(**result)


def check_tts_health(*, provider=None, canonical_authority: bool = False) -> TtsHealthResponse:
    if canonical_authority and provider is None:
        return TtsHealthResponse(
            status="disabled",
            service="oracle-brain",
            provider="none",
            configured=False,
            available=False,
            detail="TTS is disabled in canonical configuration.",
        )
    try:
        selected = provider if canonical_authority else get_tts_provider()
        if selected is None:
            raise TtsError("Canonical TTS provider is unavailable.")
        status = selected.status()
    except TtsError as exc:
        return TtsHealthResponse(
            status="failed",
            service="oracle-brain",
            provider="unknown",
            configured=False,
            available=False,
            detail=str(exc),
        )

    return TtsHealthResponse(
        status=("disabled" if canonical_authority and not status.configured else "ok" if status.available else "failed"),
        service="oracle-brain",
        provider=status.provider,
        configured=status.configured,
        available=status.available,
        detail=status.detail,
    )


def check_stt_health(*, provider=None, canonical_authority: bool = False) -> SttHealthResponse:
    if canonical_authority and provider is None:
        return SttHealthResponse(
            status="disabled",
            service="oracle-brain",
            provider="none",
            configured=False,
            available=False,
            detail="STT is disabled in canonical configuration.",
        )
    try:
        selected = provider if canonical_authority else get_stt_provider()
        if selected is None:
            raise SttError("Canonical STT provider is unavailable.")
        status = selected.status()
    except SttError as exc:
        return SttHealthResponse(
            status="failed",
            service="oracle-brain",
            provider="unknown",
            configured=False,
            available=False,
            detail=str(exc),
        )

    return SttHealthResponse(
        status=("disabled" if canonical_authority and not status.configured else "ok" if status.available else "failed"),
        service="oracle-brain",
        provider=status.provider,
        configured=status.configured,
        available=status.available,
        detail=status.detail,
    )
