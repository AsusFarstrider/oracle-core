from __future__ import annotations

import secrets

from fastapi import FastAPI, HTTPException, Request, status

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .home_automation import handle_home_assistant_event
from .schemas import HomeAssistantEventIngressRequest, HomeAssistantEventIngressResponse


def receive_home_assistant_event(
    payload: HomeAssistantEventIngressRequest,
    request: Request,
    *,
    home_assistant_settings: HomeAssistantRuntimeSettings,
) -> HomeAssistantEventIngressResponse:
    configured_token = home_assistant_settings.event_ingress_credential
    if not configured_token:
        raise HTTPException(status_code=503, detail="Home Assistant event ingress is not configured.")
    provided_token = _bearer_token(request.headers.get("Authorization"))
    if not provided_token or not secrets.compare_digest(provided_token, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Home Assistant event ingress credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return HomeAssistantEventIngressResponse(
        **handle_home_assistant_event(
            entity_id=payload.entity_id,
            state=payload.state,
            event_id=payload.event_id,
            occurred_at=payload.occurred_at,
            home_assistant_settings=home_assistant_settings,
        )
    )


def receive_home_assistant_event_http(
    payload: HomeAssistantEventIngressRequest,
    request: Request,
) -> HomeAssistantEventIngressResponse:
    composition = getattr(
        getattr(request.scope.get("app"), "state", None),
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical configuration is unavailable.")
    return receive_home_assistant_event(
        payload,
        request,
        home_assistant_settings=composition.runtime.home_assistant,
    )


def register_home_automation_routes(app: FastAPI) -> None:
    app.post(
        "/api/integrations/home-assistant/events",
        response_model=HomeAssistantEventIngressResponse,
    )(receive_home_assistant_event_http)


def _bearer_token(value: str | None) -> str:
    scheme, separator, token = str(value or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()
