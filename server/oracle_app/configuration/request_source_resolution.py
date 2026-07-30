from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .brain_effective_runtime_settings import BrainEffectiveRuntimeSettings
from .projection_resolution import (
    SatelliteProjectionAuthenticationError,
    SatelliteProjectionResolver,
)


EPHEMERAL_HTTP_SOURCE_ID = "ephemeral_http"


class RequestSourceAuthenticationError(PermissionError):
    """A presented canonical request credential proved no accepted source."""


@dataclass(frozen=True)
class ResolvedRequestSource:
    request_source_id: str
    kind: Literal["stable", "ephemeral"]
    authentication: Literal["satellite_credential", "source_credential", "none"]

    @property
    def stable(self) -> bool:
        return self.kind == "stable"


@dataclass(frozen=True)
class CanonicalRequestSourceResolver:
    """Resolve one request source against one applied Brain snapshot."""

    runtime: BrainEffectiveRuntimeSettings
    projections: SatelliteProjectionResolver

    def resolve(
        self,
        *,
        claimed_source_id: str | None,
        credential: str | None,
    ) -> ResolvedRequestSource:
        claimed = str(claimed_source_id or "").strip()
        presented = str(credential or "").strip()
        if not presented:
            return ResolvedRequestSource(
                request_source_id=EPHEMERAL_HTTP_SOURCE_ID,
                kind="ephemeral",
                authentication="none",
            )

        satellite = self.runtime.satellites.satellite_for_source(claimed)
        if satellite is not None:
            activation_id = satellite.projection_activation_id
            if activation_id is None:
                raise RequestSourceAuthenticationError("Canonical request source authentication failed.")
            try:
                self.projections.authenticate_activation(
                    satellite.satellite_id,
                    activation_id,
                    presented,
                )
            except SatelliteProjectionAuthenticationError as exc:
                raise RequestSourceAuthenticationError(
                    "Canonical request source authentication failed."
                ) from exc
            return ResolvedRequestSource(
                request_source_id=claimed,
                kind="stable",
                authentication="satellite_credential",
            )

        bound_source_id = self.runtime.access.authenticate_source_credential(presented)
        if bound_source_id is None:
            raise RequestSourceAuthenticationError("Canonical request source authentication failed.")
        return ResolvedRequestSource(
            request_source_id=bound_source_id,
            kind="stable",
            authentication="source_credential",
        )
