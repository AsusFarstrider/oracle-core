from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib import request

from oracle_app.llm_bridge import call_generate, warm_model


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


class InferenceClient:
    """Typed shared inference dependency; prompts and policy stay with consumers."""

    def __init__(self, settings: InferenceExecutionSettings) -> None:
        self.settings = settings

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

    def warm(self, *, fallback_router: bool = False) -> None:
        if not self.enabled or self.base_url is None:
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

    def version(self) -> tuple[int, str]:
        if not self.enabled or self.base_url is None:
            raise ValueError("Inference is disabled or not configured.")
        req = request.Request(f"{self.base_url}/api/version", method="GET")
        with request.urlopen(req, timeout=float(self.timeout_seconds or 5)) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
