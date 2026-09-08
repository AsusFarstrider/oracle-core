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

from oracle_app.inference import (
    InferenceClient,
    InferenceConsumer,
    InferenceExecutionSettings,
    InferenceProviderSettings,
)

from .brain_runtime_settings import BrainRuntimeSettings
from .information_runtime_settings import FactsRuntimeSettings
from .runtime_models import (
    FastWhisperProvider,
    OllamaProvider,
    OpenAILunaProvider,
    PiperProvider,
    WhisperCppProvider,
)
from .secrets import SecretSnapshot


@dataclass(frozen=True)
class BrainCoreRuntimeConsumers:
    """Constructed Brain-owned providers with no startup or network side effects."""

    stt_provider: SttProvider
    tts_provider: TtsProvider
    inference: InferenceClient

    @classmethod
    def from_runtime_settings(
        cls,
        settings: BrainRuntimeSettings,
        *,
        facts: FactsRuntimeSettings | None = None,
        secrets: SecretSnapshot | None = None,
    ) -> BrainCoreRuntimeConsumers:
        return cls(
            stt_provider=_build_stt_provider(settings),
            tts_provider=_build_tts_provider(settings),
            inference=InferenceClient(
                _build_inference_settings(settings, facts=facts, secrets=secrets)
            ),
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


def _build_inference_settings(
    settings: BrainRuntimeSettings,
    *,
    facts: FactsRuntimeSettings | None,
    secrets: SecretSnapshot | None,
) -> InferenceExecutionSettings:
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
    fallback = selected.fallback_router
    consumer_orders: dict[InferenceConsumer, tuple[str, ...]] = {}
    consumer_timeouts: dict[InferenceConsumer, float] = {}
    if fallback.enabled:
        consumer_orders["fallback_router"] = tuple(fallback.provider_order)
        consumer_timeouts["fallback_router"] = fallback.total_timeout_seconds
    if facts is not None and facts.enabled and facts.summarizer_enabled:
        consumer_orders["facts_summarizer"] = facts.summarizer_provider_order
        consumer_timeouts["facts_summarizer"] = facts.summarizer_total_timeout_seconds

    active_ids = {provider_id for order in consumer_orders.values() for provider_id in order}
    if selected.provider_id is not None:
        active_ids.add(selected.provider_id)
    runtime_providers: dict[str, InferenceProviderSettings] = {}
    for provider_id in active_ids:
        definition = selected.providers[provider_id]
        if isinstance(definition, OllamaProvider):
            runtime_providers[provider_id] = InferenceProviderSettings(
                provider_type="ollama",
                enabled=definition.enabled,
                base_url=str(definition.base_url).rstrip("/"),
                model=definition.model,
                timeout_seconds=definition.timeout_seconds,
                keep_alive=definition.keep_alive,
                options=MappingProxyType(definition.options.model_dump(mode="python")),
            )
        elif isinstance(definition, OpenAILunaProvider):
            api_key = (
                None
                if secrets is None or definition.credential_secret is None
                else secrets.resolve(definition.credential_secret)
            )
            runtime_providers[provider_id] = InferenceProviderSettings(
                provider_type="openai_luna",
                enabled=definition.enabled,
                base_url=str(definition.base_url).rstrip("/"),
                model=definition.model,
                timeout_seconds=definition.timeout_seconds,
                api_key=api_key,
                max_output_tokens=definition.max_output_tokens,
            )
        else:
            raise TypeError("Canonical inference provider has no executable implementation.")

    consumer_provider_overrides: dict[tuple[InferenceConsumer, str], InferenceProviderSettings] = {}
    local_id = selected.provider_id
    if local_id is not None and local_id in consumer_orders.get("fallback_router", ()):
        local_runtime = runtime_providers.get(local_id)
        if local_runtime is not None and local_runtime.provider_type == "ollama":
            consumer_provider_overrides[("fallback_router", local_id)] = InferenceProviderSettings(
                provider_type="ollama",
                enabled=local_runtime.enabled,
                base_url=local_runtime.base_url,
                model=fallback.model or local_runtime.model,
                timeout_seconds=fallback.timeout_seconds or local_runtime.timeout_seconds,
                keep_alive=local_runtime.keep_alive,
                options=local_runtime.options,
            )

    return InferenceExecutionSettings(
        enabled=True,
        base_url=None if provider is None else str(provider.base_url).rstrip("/"),
        model=None if provider is None else provider.model,
        timeout_seconds=None if provider is None else provider.timeout_seconds,
        keep_alive=None if provider is None else provider.keep_alive,
        options=(
            MappingProxyType({})
            if provider is None
            else MappingProxyType(provider.options.model_dump(mode="python"))
        ),
        fallback_model=None if provider is None else fallback.model or provider.model,
        fallback_timeout_seconds=None if provider is None else fallback.timeout_seconds or provider.timeout_seconds,
        local_provider_id=selected.provider_id,
        providers=MappingProxyType(runtime_providers),
        consumer_orders=MappingProxyType(consumer_orders),
        consumer_total_timeout_seconds=MappingProxyType(consumer_timeouts),
        consumer_provider_overrides=MappingProxyType(consumer_provider_overrides),
    )
