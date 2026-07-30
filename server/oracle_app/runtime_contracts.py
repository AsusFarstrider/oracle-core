from __future__ import annotations

from typing import Any

from .schemas import CommandResponse, DispatchPlan, RouteResponse


_ALLOWED_DISPATCH_STATUSES = {
    "planned",
    "pending_integration",
    "pending_confirmation",
    "pending_clarification",
    "executed",
    "failed",
}


class ContractValidationError(ValueError):
    def __init__(
        self,
        *,
        detail: str,
        failure_class: str = "contract_failure",
        owning_component: str,
        error: str,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.failure_class = failure_class
        self.owning_component = owning_component
        self.error = error


def build_failure_result(
    *,
    failure_class: str,
    owning_component: str,
    error: str,
    detail: str,
    action: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "failure_class": failure_class,
        "owning_component": owning_component,
        "error": error,
        "detail": detail,
    }
    if action is not None:
        result["action"] = action
    result.update(extra)
    return result


def validate_fallback_router_decision(decision: Any) -> dict[str, str]:
    if not isinstance(decision, dict):
        raise ContractValidationError(
            detail="Fallback router output must be a JSON object.",
            owning_component="brain.fallback_router",
            error="fallback_router_invalid_output",
        )

    extra_keys = sorted(set(decision.keys()) - {"domain", "normalized_text", "user_id"})
    if extra_keys:
        raise ContractValidationError(
            detail=f"Fallback router output included unsupported fields: {', '.join(extra_keys)}.",
            owning_component="brain.fallback_router",
            error="fallback_router_invalid_output",
        )

    domain = str(decision.get("domain", "")).strip()
    normalized_text = str(decision.get("normalized_text", "")).strip()
    user_id = str(decision.get("user_id", "")).strip().lower()
    allowed_domains = {"facts", "home_assistant", "calendar", "music", "news", "audiobook", "weather", "system"}

    if domain not in allowed_domains:
        raise ContractValidationError(
            detail="Fallback router proposed an unsupported domain.",
            owning_component="brain.fallback_router",
            error="fallback_router_invalid_output",
        )
    if not normalized_text:
        raise ContractValidationError(
            detail="Fallback router omitted normalized_text.",
            owning_component="brain.fallback_router",
            error="fallback_router_invalid_output",
        )

    return {
        "domain": domain,
        "normalized_text": normalized_text,
        "user_id": user_id,
    }


def validate_command_response_contract(
    *,
    route: RouteResponse,
    dispatch: DispatchPlan,
    reply_text: str,
) -> None:
    if route.target != dispatch.target and not (
        route.target == "fallback_router" and dispatch.target != "fallback_router"
    ):
        raise ContractValidationError(
            detail="Dispatch target does not match the resolved route target.",
            owning_component="brain.command_api",
            error="command_response_contract_invalid",
        )

    if not str(dispatch.hook or "").strip():
        raise ContractValidationError(
            detail="Dispatch hook is required before building the command response.",
            owning_component="brain.command_api",
            error="command_response_contract_invalid",
        )

    if not isinstance(dispatch.payload, dict):
        raise ContractValidationError(
            detail="Dispatch payload must be an object.",
            owning_component="brain.command_api",
            error="command_response_contract_invalid",
        )

    if str(dispatch.status or "") not in _ALLOWED_DISPATCH_STATUSES:
        raise ContractValidationError(
            detail="Dispatch status is invalid.",
            owning_component="brain.command_api",
            error="command_response_contract_invalid",
        )

    if dispatch.status in {"pending_confirmation", "pending_clarification", "failed"} and not reply_text.strip():
        raise ContractValidationError(
            detail="Pending and failed command outcomes must include reply_text.",
            owning_component="brain.command_api",
            error="command_response_contract_invalid",
        )


def build_command_contract_failure_response(
    *,
    route: RouteResponse,
    dispatch: DispatchPlan,
    exc: ContractValidationError,
) -> CommandResponse:
    failed_dispatch = dispatch.model_copy(
        update={
            "status": "failed",
            "result": build_failure_result(
                failure_class=exc.failure_class,
                owning_component=exc.owning_component,
                error=exc.error,
                detail=exc.detail,
                action="contract_failure",
            ),
        }
    )
    return CommandResponse(
        route=route,
        dispatch=failed_dispatch,
        reply_text="I couldn't complete that request.",
    )
