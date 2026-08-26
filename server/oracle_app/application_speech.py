from __future__ import annotations

from pathlib import Path

from fastapi import File, Form, HTTPException, Response, UploadFile

from stt import SttError, SttProvider
from tts import TtsError, TtsProvider

from .application_runtime import app, brain_application_composition
from .memory.retention import retention_policy_from_configuration
from .memory.transcripts import safe_record_transcript
from .schemas import SttResponse, TtsRequest


def synthesize_speech(payload: TtsRequest) -> Response:
    composition = brain_application_composition(app)
    return synthesize_speech_with_provider(payload, composition.tts_provider())


def synthesize_speech_with_provider(payload: TtsRequest, provider: TtsProvider) -> Response:
    try:
        result = provider.synthesize(payload.text)
    except TtsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=result.audio_bytes,
        media_type=result.media_type,
        headers={"X-Oracle-TTS-Provider": result.provider},
    )


async def transcribe_audio(
    audio: UploadFile = File(...),
    source: str | None = Form(default=None),
) -> SttResponse:
    composition = brain_application_composition(app)
    return await transcribe_audio_with_provider(
        audio,
        composition.stt_provider(),
        source=source,
        retention_policy=retention_policy_from_configuration(
            composition.runtime.brain.memory_storage.retention
        ),
    )


async def transcribe_audio_with_provider(
    audio: UploadFile,
    provider: SttProvider,
    *,
    source: str | None = None,
    retention_policy,
) -> SttResponse:
    audio_bytes = b""
    filename = audio.filename or "audio.wav"
    try:
        audio_bytes = await audio.read()
        result = provider.transcribe(audio_bytes, filename)
    except SttError as exc:
        safe_record_transcript(
            source_id=source,
            raw_transcript=None,
            normalized_text=None,
            stt_provider=_memory_stt_provider_name(provider),
            stt_model=_memory_stt_model(provider),
            confidence=None,
            fallback_used=False,
            final_status="failed",
            failure_stage="stt",
            retention_policy=retention_policy,
            payload=_memory_stt_payload(
                filename=filename,
                audio_bytes=audio_bytes,
                error_type=type(exc).__name__,
            ),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    safe_record_transcript(
        source_id=source,
        raw_transcript=result.text,
        normalized_text=None,
        stt_provider=result.provider,
        stt_model=_memory_stt_model(provider),
        confidence=None,
        fallback_used=False,
        final_status="succeeded",
        failure_stage=None,
        retention_policy=retention_policy,
        payload=_memory_stt_payload(filename=filename, audio_bytes=audio_bytes),
    )
    return SttResponse(text=result.text, provider=result.provider)


def _memory_stt_model(provider: object | None) -> str | None:
    if provider is None:
        return None
    model = getattr(provider, "model", None)
    if model:
        return str(model)
    source_model = getattr(provider, "source_model", None)
    return str(source_model) if source_model else None


def _memory_stt_provider_name(provider: object | None) -> str | None:
    if provider is None:
        return None
    provider_name = getattr(provider, "provider", None)
    if provider_name:
        return str(provider_name)
    class_name = provider.__class__.__name__
    return class_name if class_name and class_name != "object" else None


def _memory_stt_payload(
    *,
    filename: str,
    audio_bytes: bytes,
    error_type: str | None = None,
) -> dict[str, object]:
    suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"
    payload: dict[str, object] = {
        "endpoint": "/api/speech/stt",
        "filename_suffix": suffix,
        "audio_bytes": len(audio_bytes),
    }
    if error_type:
        payload["error_type"] = error_type
    return payload
