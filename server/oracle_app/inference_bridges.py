from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Mapping
from urllib import error, request


class InferenceBridgeError(RuntimeError):
    """Provider transport or envelope failure safe for Oracle failover."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InferenceProviderResponse:
    text: str
    model: str
    request_id: str | None = None


class OllamaInferenceBridge:
    provider_type = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        keep_alive: int | str,
        options: Mapping[str, int | float],
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.options = dict(options)

    def generate(
        self,
        *,
        prompt: str,
        system: str | None,
        json_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> InferenceProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options,
            # Preserve Oracle's mature Phi/Ollama JSON-mode behavior.  Oracle
            # validates the consumer contract after generation; translating
            # the full schema into an Ollama grammar is both unnecessary and
            # unsafe on the production Ollama build.
            "format": "json",
        }
        if system:
            payload["system"] = system
        raw = _post_json(
            f"{self.base_url}/api/generate",
            payload,
            timeout_seconds=timeout_seconds,
        )
        text = raw.get("response")
        if not isinstance(text, str) or not text.strip():
            raise InferenceBridgeError("malformed_response", "Ollama returned no generated text.")
        model = raw.get("model")
        return InferenceProviderResponse(
            text=text,
            model=model if isinstance(model, str) and model else self.model,
        )

    def version(self, *, timeout_seconds: float) -> tuple[int, str]:
        req = request.Request(f"{self.base_url}/api/version", method="GET")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise _translate_error(exc, provider="Ollama") from exc


class OpenAILunaInferenceBridge:
    provider_type = "openai_luna"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        max_output_tokens: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.max_output_tokens = max_output_tokens

    def generate(
        self,
        *,
        prompt: str,
        system: str | None,
        json_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> InferenceProviderResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "oracle_consumer_result",
                    "strict": True,
                    "schema": dict(json_schema),
                }
            },
        }
        if system:
            payload["instructions"] = system
        raw = _post_json(
            f"{self.base_url}/v1/responses",
            payload,
            timeout_seconds=timeout_seconds,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if raw.get("status") != "completed":
            raise InferenceBridgeError("incomplete_response", "OpenAI response did not complete.")
        text = _openai_output_text(raw)
        model = raw.get("model")
        request_id = raw.get("id")
        return InferenceProviderResponse(
            text=text,
            model=model if isinstance(model, str) and model else self.model,
            request_id=request_id if isinstance(request_id, str) and request_id else None,
        )


def _post_json(
    endpoint: str,
    payload: Mapping[str, object],
    *,
    timeout_seconds: float,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    req = request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise _translate_error(exc, provider="Inference provider") from exc
    if not isinstance(decoded, dict):
        raise InferenceBridgeError("malformed_response", "Inference provider returned a non-object response.")
    return decoded


def _openai_output_text(payload: Mapping[str, object]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    output = payload.get("output")
    if not isinstance(output, list):
        raise InferenceBridgeError("malformed_response", "OpenAI response has no output items.")
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text
    raise InferenceBridgeError("malformed_response", "OpenAI response has no generated text.")


def _translate_error(exc: Exception, *, provider: str) -> InferenceBridgeError:
    if isinstance(exc, InferenceBridgeError):
        return exc
    if isinstance(exc, error.HTTPError):
        code = "authentication" if exc.code in {401, 403} else "rate_limit" if exc.code == 429 else "http_error"
        return InferenceBridgeError(code, f"{provider} request failed with HTTP {exc.code}.")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return InferenceBridgeError("timeout", f"{provider} request timed out.")
    if isinstance(exc, error.URLError):
        return InferenceBridgeError("unreachable", f"{provider} is unreachable.")
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return InferenceBridgeError("malformed_response", f"{provider} returned malformed JSON.")
    return InferenceBridgeError("transport", f"{provider} request failed.")
