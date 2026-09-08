from __future__ import annotations

from dataclasses import dataclass, field
import json
from time import monotonic
from typing import Any, Callable, Generic, Literal, Mapping, TypeVar
from urllib import request

from oracle_app.llm_bridge import call_generate, warm_model
from oracle_app.inference_bridges import (
    InferenceBridgeError,
    OllamaInferenceBridge,
    OpenAILunaInferenceBridge,
)


InferenceConsumer = Literal["fallback_router", "facts_summarizer"]
T = TypeVar("T")


@dataclass(frozen=True)
class InferenceProviderSettings:
    provider_type: Literal["ollama", "openai_luna"]
    enabled: bool
    base_url: str
    model: str
    timeout_seconds: float
    keep_alive: int | str = -1
    options: Mapping[str, int | float] = field(default_factory=dict)
    api_key: str | None = field(default=None, repr=False)
    max_output_tokens: int = 512


@dataclass(frozen=True)
class InferenceAttempt:
    provider_id: str
    provider_type: str
    model: str
    outcome: Literal["success", "operational_failure", "contract_failure", "bypassed"]
    detail_code: str | None = None


@dataclass(frozen=True)
class InferenceExecutionResult(Generic[T]):
    value: T
    provider_id: str
    provider_type: str
    model: str
    request_id: str | None
    attempts: tuple[InferenceAttempt, ...]


class InferenceContractError(ValueError):
    pass


class InferenceExecutionError(RuntimeError):
    def __init__(self, code: str, attempts: tuple[InferenceAttempt, ...]) -> None:
        super().__init__(code)
        self.code = code
        self.attempts = attempts


@dataclass(frozen=True)
class InferenceExecutionSettings:
    enabled: bool
    base_url: str | None
    model: str | None
    timeout_seconds: float | None
    keep_alive: int | str | None
    options: Mapping[str, int | float]
    fallback_model: str | None
    fallback_timeout_seconds: float | None
    local_provider_id: str | None = None
    providers: Mapping[str, InferenceProviderSettings] = field(default_factory=dict)
    consumer_orders: Mapping[InferenceConsumer, tuple[str, ...]] = field(default_factory=dict)
    consumer_total_timeout_seconds: Mapping[InferenceConsumer, float] = field(default_factory=dict)
    consumer_provider_overrides: Mapping[tuple[InferenceConsumer, str], InferenceProviderSettings] = field(default_factory=dict)
    unhealthy_cooldown_seconds: float = 30.0


class InferenceClient:
    """Typed shared inference dependency; prompts and policy stay with consumers."""

    def __init__(
        self,
        settings: InferenceExecutionSettings,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._unhealthy_until: dict[tuple[InferenceConsumer, str], float] = {}
        self._providers = self._build_provider_bridges()
        self._consumer_providers = {
            key: self._build_provider_bridge(provider)
            for key, provider in self.settings.consumer_provider_overrides.items()
        }

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def base_url(self) -> str | None:
        return self.settings.base_url

    @property
    def model(self) -> str | None:
        return self.settings.model

    @property
    def timeout_seconds(self) -> float | None:
        return self.settings.timeout_seconds

    @property
    def keep_alive(self) -> int | str | None:
        return self.settings.keep_alive

    @property
    def options(self):
        return self.settings.options

    @property
    def fallback_model(self) -> str | None:
        return self.settings.fallback_model

    @property
    def fallback_timeout_seconds(self) -> float | None:
        return self.settings.fallback_timeout_seconds

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        format: str | None = None,
        fallback_router: bool = False,
    ) -> dict[str, Any]:
        if not self.enabled or self.base_url is None:
            raise ValueError("Inference is disabled or not configured.")
        if (
            fallback_router
            and self.settings.providers
            and not self.settings.consumer_orders.get("fallback_router")
        ):
            raise ValueError("Fallback inference is disabled or has no configured provider order.")
        model = self.fallback_model if fallback_router else self.model
        timeout = self.fallback_timeout_seconds if fallback_router else self.timeout_seconds
        if model is None or timeout is None:
            raise ValueError("Inference lacks an executable model or timeout.")
        return call_generate(
            base_url=self.base_url,
            model=model,
            prompt=prompt,
            timeout_seconds=int(timeout),
            keep_alive=self.keep_alive if self.keep_alive is not None else -1,
            options=dict(self.options),
            system=system,
            format=format,
        )

    def execute(
        self,
        consumer: InferenceConsumer,
        *,
        prompt: str,
        system: str | None,
        json_schema: Mapping[str, object],
        validate: Callable[[str], T],
    ) -> InferenceExecutionResult[T]:
        """Run one consumer contract through its configured bounded provider order."""
        order = self.settings.consumer_orders.get(consumer, ())
        if not self.enabled or not order:
            raise InferenceExecutionError("consumer_disabled", ())
        if len(prompt) > 32768 or (system is not None and len(system) > 16384):
            raise ValueError("Inference consumer input exceeds Oracle's bounded request size.")
        try:
            schema_size = len(json.dumps(json_schema, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Inference consumer schema must be JSON serializable.") from exc
        if schema_size > 32768:
            raise ValueError("Inference consumer schema exceeds Oracle's bounded request size.")
        total_timeout = self.settings.consumer_total_timeout_seconds.get(consumer)
        if total_timeout is None or total_timeout <= 0:
            raise ValueError("Inference consumer lacks a positive total timeout.")

        started = self._clock()
        attempts: list[InferenceAttempt] = []
        for provider_id in order:
            provider_settings = self.settings.consumer_provider_overrides.get(
                (consumer, provider_id), self.settings.providers.get(provider_id)
            )
            bridge = self._consumer_providers.get((consumer, provider_id), self._providers.get(provider_id))
            if provider_settings is None or bridge is None or not provider_settings.enabled:
                raise ValueError(f"Configured inference provider {provider_id!r} is not executable.")
            now = self._clock()
            unhealthy_until = self._unhealthy_until.get((consumer, provider_id), 0.0)
            if now < unhealthy_until:
                attempts.append(
                    InferenceAttempt(
                        provider_id,
                        provider_settings.provider_type,
                        provider_settings.model,
                        "bypassed",
                        "cooldown",
                    )
                )
                continue
            remaining = total_timeout - (now - started)
            if remaining <= 0:
                break
            try:
                response = bridge.generate(
                    prompt=prompt,
                    system=system,
                    json_schema=json_schema,
                    timeout_seconds=min(provider_settings.timeout_seconds, remaining),
                )
            except InferenceBridgeError as exc:
                self._mark_unhealthy(consumer, provider_id)
                attempts.append(
                    InferenceAttempt(
                        provider_id,
                        provider_settings.provider_type,
                        provider_settings.model,
                        "operational_failure",
                        exc.code,
                    )
                )
                continue
            try:
                if len(response.text) > 65536:
                    raise InferenceContractError("Inference provider output exceeds Oracle's bound.")
                value = validate(response.text)
            except InferenceContractError:
                self._mark_unhealthy(consumer, provider_id)
                attempts.append(
                    InferenceAttempt(
                        provider_id,
                        provider_settings.provider_type,
                        response.model,
                        "contract_failure",
                        "invalid_output",
                    )
                )
                continue
            self._unhealthy_until.pop((consumer, provider_id), None)
            attempts.append(
                InferenceAttempt(
                    provider_id,
                    provider_settings.provider_type,
                    response.model,
                    "success",
                )
            )
            return InferenceExecutionResult(
                value=value,
                provider_id=provider_id,
                provider_type=provider_settings.provider_type,
                model=response.model,
                request_id=response.request_id,
                attempts=tuple(attempts),
            )
        code = "total_timeout" if self._clock() - started >= total_timeout else "providers_exhausted"
        raise InferenceExecutionError(code, tuple(attempts))

    def operational_status(self) -> tuple[dict[str, object], ...]:
        now = self._clock()
        rows: list[dict[str, object]] = []
        for consumer in ("fallback_router", "facts_summarizer"):
            for provider_id in self.settings.consumer_orders.get(consumer, ()):
                provider = self.settings.consumer_provider_overrides.get(
                    (consumer, provider_id), self.settings.providers[provider_id]
                )
                remaining = max(0.0, self._unhealthy_until.get((consumer, provider_id), 0.0) - now)
                rows.append(
                    {
                        "consumer": consumer,
                        "provider_id": provider_id,
                        "provider_type": provider.provider_type,
                        "model": provider.model,
                        "available_for_attempt": remaining == 0.0,
                        "cooldown_remaining_seconds": remaining,
                    }
                )
        return tuple(rows)

    def can_attempt(self, consumer: InferenceConsumer) -> bool:
        """Return whether the consumer has an enabled provider eligible now."""
        if not self.enabled:
            return False
        now = self._clock()
        for provider_id in self.settings.consumer_orders.get(consumer, ()):
            provider = self.settings.consumer_provider_overrides.get(
                (consumer, provider_id), self.settings.providers.get(provider_id)
            )
            if provider is None or not provider.enabled:
                continue
            if now >= self._unhealthy_until.get((consumer, provider_id), 0.0):
                return True
        return False

    def _mark_unhealthy(self, consumer: InferenceConsumer, provider_id: str) -> None:
        self._unhealthy_until[(consumer, provider_id)] = (
            self._clock() + self.settings.unhealthy_cooldown_seconds
        )

    def _build_provider_bridges(self) -> dict[str, OllamaInferenceBridge | OpenAILunaInferenceBridge]:
        bridges: dict[str, OllamaInferenceBridge | OpenAILunaInferenceBridge] = {}
        for provider_id, provider in self.settings.providers.items():
            if not provider.enabled:
                continue
            bridges[provider_id] = self._build_provider_bridge(provider)
        return bridges

    @staticmethod
    def _build_provider_bridge(
        provider: InferenceProviderSettings,
    ) -> OllamaInferenceBridge | OpenAILunaInferenceBridge:
        if provider.provider_type == "ollama":
            return OllamaInferenceBridge(
                base_url=provider.base_url,
                model=provider.model,
                keep_alive=provider.keep_alive,
                options=provider.options,
            )
        if provider.api_key is None:
            raise ValueError("Enabled selected OpenAI Luna provider lacks its canonical credential.")
        return OpenAILunaInferenceBridge(
            base_url=provider.base_url,
            model=provider.model,
            api_key=provider.api_key,
            max_output_tokens=provider.max_output_tokens,
        )

    def warm(self, *, fallback_router: bool = False) -> None:
        if fallback_router and self.settings.providers:
            self.warm_consumer("fallback_router")
            return
        if not self.enabled or self.base_url is None:
            return
        if (
            fallback_router
            and self.settings.providers
            and not self.settings.consumer_orders.get("fallback_router")
        ):
            return
        model = self.fallback_model if fallback_router else self.model
        timeout = self.fallback_timeout_seconds if fallback_router else self.timeout_seconds
        if model is None or timeout is None:
            raise ValueError("Inference lacks an executable model or timeout.")
        warm_model(
            base_url=self.base_url,
            model=model,
            timeout_seconds=timeout,
            keep_alive=self.keep_alive if self.keep_alive is not None else -1,
        )

    def warm_consumer(self, consumer: InferenceConsumer) -> None:
        """Warm only a selected local provider; cloud providers never generate for warmup."""
        if not self.enabled:
            return
        for provider_id in self.settings.consumer_orders.get(consumer, ()):
            provider = self.settings.consumer_provider_overrides.get(
                (consumer, provider_id), self.settings.providers.get(provider_id)
            )
            if provider is None or not provider.enabled or provider.provider_type != "ollama":
                continue
            warm_model(
                base_url=provider.base_url,
                model=provider.model,
                timeout_seconds=provider.timeout_seconds,
                keep_alive=provider.keep_alive,
            )
            return

    def version(self) -> tuple[int, str]:
        if not self.enabled or self.base_url is None:
            raise ValueError("Inference is disabled or not configured.")
        req = request.Request(f"{self.base_url}/api/version", method="GET")
        with request.urlopen(req, timeout=float(self.timeout_seconds or 5)) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
