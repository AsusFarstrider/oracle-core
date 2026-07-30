from __future__ import annotations

from dataclasses import dataclass

from .effective import EffectiveConfig
from .models import BrainConfiguration, LoggingConfiguration
from .runtime_models import (
    AlertStorageConfiguration,
    BrainRuntimeConfiguration,
    FastWhisperProvider,
    FallbackRouterConfiguration,
    MemoryStorageConfiguration,
    OllamaProvider,
    PiperProvider,
    WhisperCppProvider,
)


SttProviderConfiguration = WhisperCppProvider | FastWhisperProvider


@dataclass(frozen=True)
class SelectedSttConfiguration:
    enabled: bool
    provider_id: str | None
    provider: SttProviderConfiguration | None


@dataclass(frozen=True)
class SelectedTtsConfiguration:
    enabled: bool
    provider_id: str | None
    provider: PiperProvider | None


@dataclass(frozen=True)
class SelectedInferenceConfiguration:
    enabled: bool
    provider_id: str | None
    provider: OllamaProvider | None
    fallback_router: FallbackRouterConfiguration


@dataclass(frozen=True)
class BrainRuntimeSettings:
    """Frozen execution settings derived only from one adopted Brain snapshot.

    This is the canonical construction seam for Brain-owned configuration.  It
    deliberately retains typed schema leaves instead of reproducing V1 getter
    dictionaries or historical environment names.
    """

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    runtime: BrainRuntimeConfiguration
    logging: LoggingConfiguration
    memory_storage: MemoryStorageConfiguration
    alert_storage: AlertStorageConfiguration
    stt: SelectedSttConfiguration
    tts: SelectedTtsConfiguration
    inference: SelectedInferenceConfiguration

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> BrainRuntimeSettings:
        role = effective.role("brain.yaml")
        if not isinstance(role, BrainConfiguration):
            raise TypeError("Effective brain.yaml role does not use the executable Brain schema.")

        stt_provider = None
        stt_provider_id = None
        if role.speech.stt.enabled:
            stt_provider_id = role.speech.stt.provider
            if stt_provider_id is None:
                raise ValueError("Enabled canonical STT has no selected provider.")
            stt_provider = role.speech.stt.providers[stt_provider_id]

        tts_provider = None
        tts_provider_id = None
        if role.speech.tts.enabled:
            tts_provider_id = role.speech.tts.provider
            if tts_provider_id is None:
                raise ValueError("Enabled canonical TTS has no selected provider.")
            tts_provider = role.speech.tts.providers[tts_provider_id]

        inference_provider = None
        inference_provider_id = None
        shared_inference = role.inference.shared_backend
        if shared_inference.enabled:
            inference_provider_id = shared_inference.provider
            if inference_provider_id is None:
                raise ValueError("Enabled canonical inference has no selected provider.")
            inference_provider = shared_inference.providers[inference_provider_id]

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            runtime=role.runtime,
            logging=role.logging,
            memory_storage=role.storage.memory,
            alert_storage=role.storage.alerts,
            stt=SelectedSttConfiguration(
                enabled=role.speech.stt.enabled,
                provider_id=stt_provider_id,
                provider=stt_provider,
            ),
            tts=SelectedTtsConfiguration(
                enabled=role.speech.tts.enabled,
                provider_id=tts_provider_id,
                provider=tts_provider,
            ),
            inference=SelectedInferenceConfiguration(
                enabled=shared_inference.enabled,
                provider_id=inference_provider_id,
                provider=inference_provider,
                fallback_router=shared_inference.fallback_router,
            ),
        )
