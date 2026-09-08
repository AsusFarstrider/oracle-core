from __future__ import annotations

from typing import Any

from oracle_app.inference_bridges import InferenceBridgeError, OpenAILunaInferenceBridge
from oracle_app.suggestions.inference_contract import (
    MAX_SUGGESTIONS_PROVIDER_OUTPUT_CHARACTERS,
    build_suggestions_prompt,
    normalize_suggestion_items,
    parse_suggestion_json,
    suggestions_result_schema,
)
from oracle_app.suggestions.redaction import redact_secrets

from .openclaw.schemas import SuggestionsBridgeResult


def generate_suggestions_openai_luna(
    packet: dict[str, Any],
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout_seconds: int,
    max_output_tokens: int,
    max_suggestions: int,
) -> SuggestionsBridgeResult:
    if not api_key.strip():
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class="configuration",
            errors=["Direct Luna Suggestions credential is not configured."],
        )
    bridge = OpenAILunaInferenceBridge(
        base_url=base_url,
        model=model,
        api_key=api_key,
        max_output_tokens=max_output_tokens,
    )
    try:
        response = bridge.generate(
            prompt=build_suggestions_prompt(packet, max_suggestions),
            system=None,
            json_schema=suggestions_result_schema(max_suggestions),
            timeout_seconds=timeout_seconds,
        )
    except InferenceBridgeError as exc:
        failure_class = (
            "response_validation"
            if exc.code in {"incomplete_response", "malformed_response"}
            else "transport"
        )
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class=failure_class,
            errors=[f"Direct Luna Suggestions failed: {exc.code}."],
        )

    if len(response.text) > MAX_SUGGESTIONS_PROVIDER_OUTPUT_CHARACTERS:
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class="response_validation",
            errors=["Direct Luna Suggestions output exceeded the 524288-character limit."],
        )
    parsed = parse_suggestion_json(response.text)
    if parsed is None:
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class="response_validation",
            errors=["Direct Luna Suggestions output was not valid suggestion JSON."],
        )
    suggestions = parsed.get("suggestions")
    if not isinstance(suggestions, list):
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class="response_validation",
            errors=["Direct Luna Suggestions output did not include a suggestions array."],
        )
    normalized, validation_errors = normalize_suggestion_items(suggestions)
    if suggestions and not normalized:
        return SuggestionsBridgeResult(
            ok=False,
            provider="openai_luna",
            adapter="openai_luna",
            failure_class="response_validation",
            raw_response={"parsed": redact_secrets(parsed)},
            errors=validation_errors,
        )
    return SuggestionsBridgeResult(
        ok=True,
        provider="openai_luna",
        adapter="openai_luna",
        raw_response={
            "model": response.model,
            "request_id": response.request_id,
            "parsed": redact_secrets(parsed),
        },
        suggestions=normalized[:max_suggestions],
        errors=validation_errors,
    )
