from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from stt import (
    DisabledSttProvider,
    FastWhisperProvider as FastWhisperRuntimeProvider,
    SttProvider,
    WhisperCppProvider as WhisperCppRuntimeProvider,
)
from tts import DisabledTtsProvider, PiperTtsProvider, TtsProvider

from oracle_app.inference import InferenceClient, InferenceExecutionSettings

from .brain_runtime_settings import BrainRuntimeSettings
from .runtime_models import FastWhisperProvider, PiperProvider, WhisperCppProvider


@dataclass(frozen=True)
class BrainCoreRuntimeConsumers:
    """Constructed Brain-owned providers with no startup or network side effects."""

    stt_provider: SttProvider
    tts_provider: TtsProvider
    inference: InferenceClient

    @classmethod
    def from_runtime_settings(cls, settings: BrainRuntimeSettings) -> BrainCoreRuntimeConsumers:
        return cls(
            stt_provider=_build_stt_provider(settings),
            tts_provider=_build_tts_provider(settings),
            inference=InferenceClient(_build_inference_settings(settings)),
        )


def _build_stt_provider(settings: BrainRuntimeSettings) -> SttProvider:
    selected = settings.stt
    if not selected.enabled:
        return DisabledSttProvider()
    provider = selected.provider
    if isinstance(provider, WhisperCppProvider):
        return WhisperCppRuntimeProvider(
            binary=provider.binary_path,
            model=provider.model_path,
            threads=provider.threads,
        )
    if isinstance(provider, FastWhisperProvider):
        return FastWhisperRuntimeProvider(
            model=provider.model,
            threads=provider.threads,
        )
    raise TypeError("Enabled canonical STT lacks an executable typed provider.")


def _build_tts_provider(settings: BrainRuntimeSettings) -> TtsProvider:
    selected = settings.tts
    if not selected.enabled:
        return DisabledTtsProvider()
    provider = selected.provider
    if isinstance(provider, PiperProvider):
        return PiperTtsProvider(binary=provider.binary_path, model=provider.model_path)
    raise TypeError("Enabled canonical TTS lacks an executable typed provider.")


def _build_inference_settings(settings: BrainRuntimeSettings) -> InferenceExecutionSettings:
    selected = settings.inference
    if not selected.enabled:
        return InferenceExecutionSettings(
            enabled=False,
            base_url=None,
            model=None,
            timeout_seconds=None,
            keep_alive=None,
            options=MappingProxyType({}),
            fallback_model=None,
            fallback_timeout_seconds=None,
        )
    provider = selected.provider
    if provider is None:
        raise TypeError("Enabled canonical inference lacks an executable typed provider.")
    fallback = selected.fallback_router
    return InferenceExecutionSettings(
        enabled=True,
        base_url=str(provider.base_url).rstrip("/"),
        model=provider.model,
        timeout_seconds=provider.timeout_seconds,
        keep_alive=provider.keep_alive,
        options=MappingProxyType(provider.options.model_dump(mode="python")),
        fallback_model=fallback.model or provider.model,
        fallback_timeout_seconds=fallback.timeout_seconds or provider.timeout_seconds,
    )
