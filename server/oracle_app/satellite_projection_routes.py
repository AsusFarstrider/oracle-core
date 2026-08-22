from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, Response, status

from .configuration import (
    GenerationStoreError,
    SatelliteProjectionAuthenticationError,
    SatelliteProjectionResolver,
)


logger = logging.getLogger("oracle-brain.api")

_RESOLVER_STATE_KEY = "satellite_projection_resolver"
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def configure_satellite_projection_routes(
    app: FastAPI,
    resolver: SatelliteProjectionResolver | None,
) -> None:
    setattr(app.state, _RESOLVER_STATE_KEY, resolver)


def satellite_projection_pull(satellite_id: str, request: Request) -> Response:
    credential = _bearer_token(request.headers.get("Authorization"))
    if not credential:
        raise _authentication_error()

    resolver = getattr(request.app.state, _RESOLVER_STATE_KEY, None)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Satellite projection delivery is unavailable.",
            headers=_NO_STORE_HEADERS,
        )
    try:
        envelope = resolver.resolve_pull(satellite_id, credential)
    except SatelliteProjectionAuthenticationError as exc:
        raise _authentication_error() from exc
    except (GenerationStoreError, OSError) as exc:
        logger.error(
            "satellite_projection_pull_unavailable satellite_id=%s error_type=%s",
            satellite_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Satellite projection delivery is unavailable.",
            headers=_NO_STORE_HEADERS,
        ) from exc

    return Response(
        content=envelope.canonical_bytes(),
        media_type="application/json",
        headers=_NO_STORE_HEADERS,
    )


def satellite_projection_enrollment(satellite_id: str, request: Request) -> Response:
    credential = _bearer_token(request.headers.get("Authorization"))
    if not credential:
        raise _authentication_error(enrollment=True)

    resolver = getattr(request.app.state, _RESOLVER_STATE_KEY, None)
    if resolver is None:
        raise _unavailable_error()
    try:
        envelope = resolver.resolve_enrollment_pull(satellite_id, credential)
    except SatelliteProjectionAuthenticationError as exc:
        raise _authentication_error(enrollment=True) from exc
    except (GenerationStoreError, OSError) as exc:
        logger.error(
            "satellite_projection_enrollment_unavailable satellite_id=%s error_type=%s",
            satellite_id,
            type(exc).__name__,
        )
        raise _unavailable_error() from exc

    return Response(
        content=envelope.canonical_bytes(),
        media_type="application/json",
        headers=_NO_STORE_HEADERS,
    )


def register_satellite_projection_routes(app: FastAPI) -> None:
    app.get("/api/satellite/projection/{satellite_id}")(satellite_projection_pull)
    app.post("/api/satellite/enrollment/{satellite_id}")(satellite_projection_enrollment)


def _bearer_token(value: str | None) -> str:
    scheme, separator, token = str(value or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _authentication_error(*, enrollment: bool = False) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "Satellite enrollment authentication failed."
            if enrollment
            else "Satellite projection authentication failed."
        ),
        headers={"WWW-Authenticate": "Bearer", **_NO_STORE_HEADERS},
    )


def _unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Satellite projection delivery is unavailable.",
        headers=_NO_STORE_HEADERS,
    )
