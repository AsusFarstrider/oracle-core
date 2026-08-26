from __future__ import annotations

from fastapi import HTTPException, Request

from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .configuration.generations import GenerationStoreError
from .configuration.request_source_resolution import RequestSourceAuthenticationError


def authenticate_satellite_source(
    request: Request,
    *,
    claimed_source_id: str,
) -> tuple[CanonicalBrainApplicationComposition, str]:
    """Authenticate one managed satellite and return its credential-bound source."""

    composition = getattr(
        request.app.state,
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        None,
    )
    if not isinstance(composition, CanonicalBrainApplicationComposition):
        raise HTTPException(status_code=503, detail="Canonical satellite authentication is unavailable.")
    authorization = str(request.headers.get("Authorization") or "")
    scheme, separator, token = authorization.partition(" ")
    credential = token.strip() if separator and scheme.casefold() == "bearer" else ""
    if not credential:
        raise HTTPException(
            status_code=401,
            detail="Satellite authentication failed.",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        )
    try:
        resolved = composition.request_source_resolver.resolve(
            claimed_source_id=claimed_source_id,
            credential=credential,
            peer_address=request.client.host if request.client is not None else None,
        )
    except RequestSourceAuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Satellite authentication failed.",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
        ) from exc
    except (GenerationStoreError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Canonical satellite authentication is unavailable.",
            headers={"Cache-Control": "no-store"},
        ) from exc
    satellite = composition.runtime.satellites.satellite_for_source(
        resolved.request_source_id
    )
    if resolved.authentication != "satellite_credential" or satellite is None:
        raise HTTPException(status_code=403, detail="Source is not a managed satellite.")
    return composition, resolved.request_source_id
