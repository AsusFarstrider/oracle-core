from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from fastapi import HTTPException

from .configuration.domain_models import HomeAssistantObjectMapping
from .configuration.home_assistant_action_semantics import (
    CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS,
    DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .provider_bridges.home_assistant import HomeAssistantBridge, HomeAssistantBridgeServiceError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomeAssistantUiAction:
    entity_id: str
    service_domain: str
    service_name: str
    expected_state: str
    label: str
    refresh_pages: tuple[str, ...]
    verification_timeout_seconds: float | None = None


def fetch_home_assistant_entity_state(
    base_url: str,
    token: str,
    entity_id: str,
) -> dict[str, Any] | None:
    return HomeAssistantBridge(base_url=base_url, token=token).fetch_entity_state(entity_id)


def execute_home_assistant_ui_action(
    action_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> dict[str, object] | None:
    action_id = str(action_id or "").strip()
    if not canonical_authority:
        return None
    mapping = _canonical_action_mapping(home_assistant_settings, action_id)
    if mapping is None:
        return None
    operation = _mapping_operation(mapping)
    if operation not in DIRECT_HOME_ASSISTANT_ACTION_OPERATIONS:
        return None
    action = _canonical_direct_action(mapping)
    if not _canonical_direct_action_is_compatible(mapping):
        return _canonical_action_unavailable(action_id, action)
    bridge = _canonical_bridge(home_assistant_settings)
    if bridge is None:
        return _canonical_action_unavailable(action_id, action)

    provider_error: HomeAssistantBridgeServiceError | None = None
    try:
        bridge.call_service(
            service_domain=action.service_domain,
            service_name=action.service_name,
            entity_id=action.entity_id,
        )
    except HomeAssistantBridgeServiceError as exc:
        provider_error = exc
        logger.warning(
            "Home Assistant UI action provider request failed action_id=%s detail=%s",
            action_id,
            exc.detail,
        )

    wait_kwargs: dict[str, float] = {}
    if action.verification_timeout_seconds is not None:
        wait_kwargs["timeout_seconds"] = action.verification_timeout_seconds
    latest_state = bridge.wait_for_entity_state(
        action.entity_id,
        action.expected_state,
        **wait_kwargs,
    )
    actual_state = str((latest_state or {}).get("state") or "").strip().lower()
    if actual_state == action.expected_state:
        success_verbs = {
            "locked": "locked",
            "unlocked": "unlocked",
            "on": "turned on",
            "off": "turned off",
        }
        verb = success_verbs.get(
            action.expected_state,
            f"reached {action.expected_state}",
        )
        return _format_result(
            action_id=action_id,
            action=action,
            ok=True,
            status="executed",
            message=f"{action.label} {verb}.",
        )
    if provider_error is not None:
        return _format_result(
            action_id=action_id,
            action=action,
            ok=False,
            status="failed",
            message=f"{action.label} action failed.",
            error="home_assistant_request_failed",
            detail="Home Assistant could not execute the requested action.",
        )
    failure_verbs = {
        "locked": "lock",
        "unlocked": "unlock",
        "on": "turn on",
        "off": "turn off",
    }
    verb = failure_verbs.get(action.expected_state, f"reach {action.expected_state}")
    return _format_result(
        action_id=action_id,
        action=action,
        ok=False,
        status="failed",
        message=f"{action.label} did not {verb}.",
        error="home_assistant_state_verification_failed",
        detail=f"Expected {action.expected_state}, got {actual_state or 'unknown'}.",
    )


def resolve_home_assistant_dynamic_ui_action(
    action_id: str,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings | None = None,
    canonical_authority: bool = False,
) -> dict[str, object] | None:
    action_id = str(action_id or "").strip()
    if not canonical_authority:
        return None
    mapping = _canonical_action_mapping(home_assistant_settings, action_id)
    if mapping is None:
        return None
    operation = _mapping_operation(mapping)
    if operation not in CLIMATE_HOME_ASSISTANT_ACTION_OPERATIONS:
        return None
    bridge = _canonical_bridge(home_assistant_settings)
    if bridge is None:
        raise HTTPException(
            status_code=503,
            detail="Climate controls are temporarily unavailable.",
        )
    try:
        state_payload = bridge.fetch_entity_state(mapping.entity_id)
    except Exception as exc:
        logger.warning(
            "Home Assistant climate state request failed action_id=%s detail=%s",
            action_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Climate controls are temporarily unavailable.",
        ) from exc
    if not isinstance(state_payload, dict):
        raise HTTPException(
            status_code=503,
            detail="Climate controls are temporarily unavailable.",
        )
    attributes = state_payload.get("attributes") or {}
    try:
        target_temperature = (
            float(attributes.get("temperature")) if isinstance(attributes, dict) else None
        )
    except (TypeError, ValueError):
        target_temperature = None
    if target_temperature is None:
        raise HTTPException(
            status_code=409,
            detail="Oracle cannot determine the current target temperature for this climate device.",
        )
    delta = -1 if operation == "cooler" else 1
    next_target = round(target_temperature) + delta
    return {
        "command_text": f"set the {_canonical_action_label(mapping).lower()} to {next_target} degrees",
        "refresh_pages": ["house"],
        "requires_source": False,
    }


def _mapping_operation(mapping: HomeAssistantObjectMapping) -> str:
    return mapping.allowed_operations[0] if len(mapping.allowed_operations) == 1 else ""


def _canonical_action_mapping(
    settings: HomeAssistantRuntimeSettings | None,
    action_id: str,
) -> HomeAssistantObjectMapping | None:
    if settings is None or not settings.enabled:
        return None
    mapping = settings.mapping(action_id)
    if not isinstance(mapping, HomeAssistantObjectMapping) or mapping.kind != "action":
        return None
    return mapping


def _canonical_bridge(
    settings: HomeAssistantRuntimeSettings | None,
) -> HomeAssistantBridge | None:
    if settings is None or not settings.enabled or not settings.base_url or not settings.credential:
        return None
    return HomeAssistantBridge(
        base_url=settings.base_url,
        token=settings.credential,
        timeout_seconds=settings.timeout_seconds,
    )


def _canonical_direct_action(
    mapping: HomeAssistantObjectMapping,
) -> HomeAssistantUiAction:
    operation = _mapping_operation(mapping)
    expected_state = {
        "turn_on": "on",
        "turn_off": "off",
        "lock": "locked",
        "unlock": "unlocked",
    }.get(operation, "")
    entity_domain = mapping.entity_id.split(".", 1)[0] if "." in mapping.entity_id else ""
    return HomeAssistantUiAction(
        entity_id=mapping.entity_id,
        service_domain=entity_domain,
        service_name=operation,
        expected_state=expected_state,
        label=_canonical_action_label(mapping),
        refresh_pages=("home", "house"),
        verification_timeout_seconds=30.0 if operation in {"lock", "unlock"} else None,
    )


def _canonical_direct_action_is_compatible(mapping: HomeAssistantObjectMapping) -> bool:
    operation = _mapping_operation(mapping)
    entity_domain = mapping.entity_id.split(".", 1)[0] if "." in mapping.entity_id else ""
    compatible_domains = {
        "turn_on": {"fan", "light"},
        "turn_off": {"fan", "light"},
        "lock": {"lock"},
        "unlock": {"lock"},
    }.get(operation, set())
    return entity_domain in compatible_domains


def _canonical_action_label(mapping: HomeAssistantObjectMapping) -> str:
    words = str(mapping.oracle_id or "").replace("_", " ").split()
    return " ".join(
        word.upper() if word.lower() in {"ac", "led"} else word.capitalize()
        for word in words
    )


def _canonical_action_unavailable(
    action_id: str,
    action: HomeAssistantUiAction,
) -> dict[str, object]:
    return _format_result(
        action_id=action_id,
        action=action,
        ok=False,
        status="failed",
        message=f"{action.label} action is unavailable.",
        error="home_assistant_action_unconfigured",
        detail="The action is not available in the applied Home Assistant configuration.",
    )


def _format_result(
    *,
    action_id: str,
    action: HomeAssistantUiAction,
    ok: bool,
    status: str,
    message: str,
    error: str | None = None,
    detail: str | None = None,
) -> dict[str, object]:
    output: dict[str, object] = {
        "ok": ok,
        "action_id": action_id,
        "result": {"status": status, "message": message},
        "refresh": {"refresh_pages": list(action.refresh_pages)},
    }
    if not ok:
        output["error"] = error or "action_failed"
        output["detail"] = detail or message
    return output
