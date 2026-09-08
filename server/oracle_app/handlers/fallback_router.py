from __future__ import annotations

import json
import logging
import re
from typing import Any

from oracle_app.inference import (
    InferenceAttempt,
    InferenceClient,
    InferenceContractError,
    InferenceExecutionError,
)
from oracle_app.constants import FALLBACK_ROUTER_SYSTEM_PROMPT
from oracle_app.runtime_contracts import ContractValidationError, build_failure_result, validate_fallback_router_decision
from oracle_app.schemas import DispatchPlan


logger = logging.getLogger("oracle-brain.fallback-router")


FALLBACK_ROUTER_RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["resolved", "unresolved", "unsupported"]},
        "domain": {
            "type": "string",
            "enum": ["", "facts", "home_assistant", "calendar", "music", "news", "audiobook", "weather", "system"],
        },
        "normalized_text": {"type": "string", "maxLength": 4096},
        "user_id": {"type": "string", "maxLength": 128},
    },
    "required": ["status", "domain", "normalized_text", "user_id"],
    "additionalProperties": False,
}

_TEMPORAL_MARKERS = frozenset(
    {"today", "tomorrow", "tonight", "yesterday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)
_UNIT_MARKERS = frozenset(
    {"second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "week", "weeks", "degree", "degrees", "percent", "am", "pm"}
)
_NEGATION_MARKERS = frozenset({"no", "not", "never", "dont", "don't", "cannot", "cant", "can't", "without"})


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def parse_fallback_router_decision(raw_text: str) -> dict[str, str] | None:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    cleaned = _extract_json_object(cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    try:
        return validate_fallback_router_decision(parsed)
    except ContractValidationError:
        return None


def validate_fallback_router_result(raw_text: str, *, original_text: str) -> dict[str, str]:
    decision = parse_fallback_router_decision(raw_text)
    if decision is None:
        raise InferenceContractError("Fallback router returned invalid output.")
    if decision["status"] == "resolved":
        _validate_faithful_normalization(original_text, decision["normalized_text"])
    return decision


def _validate_faithful_normalization(original_text: str, normalized_text: str) -> None:
    original = _semantic_tokens(original_text)
    normalized = _semantic_tokens(normalized_text)
    required = {
        token
        for token in original
        if token in _TEMPORAL_MARKERS or token in _UNIT_MARKERS or token in _NEGATION_MARKERS or any(char.isdigit() for char in token)
    }
    missing = sorted(required - normalized)
    if missing:
        raise InferenceContractError("Fallback router normalization lost required semantic values.")


def _semantic_tokens(value: str) -> set[str]:
    lowered = str(value or "").lower().replace("’", "'")
    return set(re.findall(r"[a-z]+(?:'[a-z]+)?|\d+(?::\d+)?(?:\.\d+)?", lowered))


def _attempt_payload(attempt: InferenceAttempt) -> dict[str, str | None]:
    return {
        "provider_id": attempt.provider_id,
        "provider_type": attempt.provider_type,
        "model": attempt.model,
        "outcome": attempt.outcome,
        "detail_code": attempt.detail_code,
    }


class FallbackRouterHandler:
    target = "fallback_router"

    def __init__(self, inference: InferenceClient | None = None) -> None:
        self._inference = inference

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        del registry
        source = str(dispatch.payload.get("source") or "-")
        session_id = str(dispatch.payload.get("session_id") or "-")
        logger.info(
            "fallback_router_requested source=%s session_id=%s dispatch_hook=%s",
            source,
            session_id,
            dispatch.hook,
        )

        if self._inference is None or not self._inference.enabled:
            dispatch.status = "failed"
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="router_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_disabled",
                detail="Fallback routing is disabled.",
            )
            return dispatch
        prompt = str(dispatch.payload.get("prompt") or "").strip()
        try:
            execution = self._inference.execute(
                "fallback_router",
                prompt=prompt,
                system=FALLBACK_ROUTER_SYSTEM_PROMPT,
                json_schema=FALLBACK_ROUTER_RESULT_SCHEMA,
                validate=lambda raw: validate_fallback_router_result(raw, original_text=prompt),
            )
            decision = execution.value
        except InferenceExecutionError as exc:
            dispatch.status = "failed"
            error_code = {
                "consumer_disabled": "fallback_router_disabled",
                "total_timeout": "fallback_router_timeout",
                "providers_exhausted": "fallback_router_providers_exhausted",
            }.get(exc.code, "fallback_router_providers_exhausted")
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="transport_failure" if exc.code == "total_timeout" else "router_failure",
                owning_component="brain.fallback_router",
                error=error_code,
                detail="Fallback routing is unavailable right now.",
                attempts=[_attempt_payload(attempt) for attempt in exc.attempts],
            )
            logger.warning(
                "fallback_router_failed source=%s session_id=%s failure_class=%s owning_component=%s failure_code=%s",
                source,
                session_id,
                "transport_failure" if exc.code == "total_timeout" else "router_failure",
                "brain.fallback_router",
                error_code,
            )
            return dispatch

        dispatch.status = "executed"
        dispatch.result = {
            "action": "route_proposed" if decision["status"] == "resolved" else f"fallback_{decision['status']}",
            "semantic_status": decision["status"],
            "proposed_domain": decision["domain"],
            "normalized_text": decision["normalized_text"],
            "user_id": decision["user_id"],
            "inference": {
                "provider_id": execution.provider_id,
                "provider_type": execution.provider_type,
                "model": execution.model,
                "request_id": execution.request_id,
                "attempts": [_attempt_payload(attempt) for attempt in execution.attempts],
            },
        }
        logger.info(
            "fallback_router_succeeded source=%s session_id=%s semantic_status=%s proposed_domain=%s provider_id=%s model=%s",
            source,
            session_id,
            decision["status"],
            decision["domain"],
            execution.provider_id,
            execution.model,
        )
        return dispatch


def warm_fallback_router_model(inference: InferenceClient) -> None:
    inference.warm_consumer("fallback_router")


def attempt_fallback_router_warmup(inference: InferenceClient) -> None:
    try:
        warm_fallback_router_model(inference)
    except Exception as exc:
        logger.warning("fallback_router_warmup_failed detail=%s", exc)
