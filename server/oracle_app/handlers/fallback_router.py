from __future__ import annotations

import json
import logging
import socket
from typing import Any
from urllib import error

from oracle_app.config import get_fallback_router_settings
from oracle_app.configuration.brain_core_runtime_consumers import InferenceExecutionSettings
from oracle_app.constants import FALLBACK_ROUTER_SYSTEM_PROMPT
from oracle_app.llm_bridge import call_generate, warm_model
from oracle_app.runtime_contracts import ContractValidationError, build_failure_result, validate_fallback_router_decision
from oracle_app.schemas import DispatchPlan


logger = logging.getLogger("oracle-brain.fallback-router")


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


class FallbackRouterHandler:
    target = "fallback_router"

    def __init__(self, settings: InferenceExecutionSettings | None = None) -> None:
        self._settings = settings

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

        if self._settings is not None and not self._settings.enabled:
            dispatch.status = "failed"
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="router_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_disabled",
                detail="Fallback routing is disabled.",
            )
            return dispatch
        if self._settings is None:
            settings = get_fallback_router_settings()
        else:
            if self._settings.base_url is None or self._settings.fallback_model is None:
                raise ValueError("Canonical fallback routing lacks inference settings.")
            settings = {
                "base_url": self._settings.base_url,
                "model": self._settings.fallback_model,
                "timeout_seconds": self._settings.fallback_timeout_seconds,
                "keep_alive": self._settings.keep_alive,
                "options": self._settings.options,
            }
        try:
            result = call_generate(
                base_url=str(settings["base_url"]),
                model=str(settings["model"]),
                prompt=str(dispatch.payload.get("prompt") or ""),
                timeout_seconds=int(settings["timeout_seconds"]),
                keep_alive=settings["keep_alive"],
                options=dict(settings["options"]),
                system=FALLBACK_ROUTER_SYSTEM_PROMPT,
                format="json",
            )
            decision = parse_fallback_router_decision(str(result.get("response", "")).strip())
            if decision is None:
                raise ValueError("invalid_router_output")
        except (ValueError, ContractValidationError) as exc:
            dispatch.status = "failed"
            detail = "Fallback router returned invalid output."
            if isinstance(exc, ContractValidationError):
                detail = exc.detail
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="router_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_invalid_output",
                detail=detail,
            )
            logger.warning(
                "fallback_router_failed source=%s session_id=%s failure_class=%s owning_component=%s failure_code=%s",
                source,
                session_id,
                "router_failure",
                "brain.fallback_router",
                "fallback_router_invalid_output",
            )
            return dispatch
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            dispatch.status = "failed"
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="transport_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_http_error",
                detail=detail,
                status_code=exc.code,
            )
            logger.warning(
                "fallback_router_failed source=%s session_id=%s failure_class=%s owning_component=%s failure_code=%s",
                source,
                session_id,
                "transport_failure",
                "brain.fallback_router",
                "fallback_router_http_error",
            )
            return dispatch
        except error.URLError as exc:
            dispatch.status = "failed"
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="transport_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_unreachable",
                detail=str(exc.reason),
            )
            logger.warning(
                "fallback_router_failed source=%s session_id=%s failure_class=%s owning_component=%s failure_code=%s",
                source,
                session_id,
                "transport_failure",
                "brain.fallback_router",
                "fallback_router_unreachable",
            )
            return dispatch
        except (TimeoutError, socket.timeout):
            dispatch.status = "failed"
            dispatch.result = build_failure_result(
                action="router_failure",
                failure_class="transport_failure",
                owning_component="brain.fallback_router",
                error="fallback_router_timeout",
                detail="Fallback router did not respond before the timeout.",
            )
            logger.warning(
                "fallback_router_failed source=%s session_id=%s failure_class=%s owning_component=%s failure_code=%s",
                source,
                session_id,
                "transport_failure",
                "brain.fallback_router",
                "fallback_router_timeout",
            )
            return dispatch

        dispatch.status = "executed"
        dispatch.result = {
            "action": "route_proposed",
            "proposed_domain": decision["domain"],
            "normalized_text": decision["normalized_text"],
            "user_id": decision["user_id"],
        }
        logger.info(
            "fallback_router_succeeded source=%s session_id=%s proposed_domain=%s",
            source,
            session_id,
            decision["domain"],
        )
        return dispatch


def warm_fallback_router_model(settings: InferenceExecutionSettings | None = None) -> None:
    if settings is None:
        legacy = get_fallback_router_settings()
        base_url = str(legacy["base_url"])
        model = str(legacy["model"])
        timeout_seconds = int(legacy["timeout_seconds"])
        keep_alive = legacy["keep_alive"]
    else:
        if not settings.enabled:
            return
        if settings.base_url is None or settings.fallback_model is None:
            raise ValueError("Enabled canonical fallback routing lacks inference settings.")
        base_url = settings.base_url
        model = settings.fallback_model
        timeout_seconds = settings.fallback_timeout_seconds or settings.timeout_seconds or 0
        keep_alive = settings.keep_alive
    warm_model(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        keep_alive=keep_alive if keep_alive is not None else -1,
    )


def attempt_fallback_router_warmup(settings: InferenceExecutionSettings | None = None) -> None:
    try:
        if settings is None:
            warm_fallback_router_model()
        else:
            warm_fallback_router_model(settings)
    except Exception as exc:
        logger.warning("fallback_router_warmup_failed detail=%s", exc)
