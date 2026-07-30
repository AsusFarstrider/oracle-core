from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .request_source_resolution import ResolvedRequestSource
from .satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings


class PlaybackTargetResolutionError(ValueError):
    """A canonical media request has no valid playback-capable target."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedPlaybackTarget:
    source_id: str
    satellite_id: str
    resolution: Literal["explicit", "authenticated_request_source"]


@dataclass(frozen=True)
class CanonicalPlaybackTargetResolver:
    """Resolve media destination without changing request-source identity."""

    fleet: SatelliteFleetRuntimeSettings

    def resolve(
        self,
        *,
        explicit_source_id: str | None,
        request_source: ResolvedRequestSource,
    ) -> ResolvedPlaybackTarget:
        explicit = str(explicit_source_id or "").strip()
        if explicit:
            target = self.fleet.control_target_for_source(explicit)
            if target is None or target.source_id is None:
                raise PlaybackTargetResolutionError("invalid_playback_target")
            return ResolvedPlaybackTarget(
                source_id=target.source_id,
                satellite_id=target.satellite_id,
                resolution="explicit",
            )

        if request_source.stable:
            target = self.fleet.control_target_for_source(
                request_source.request_source_id
            )
            if target is not None and target.source_id is not None:
                return ResolvedPlaybackTarget(
                    source_id=target.source_id,
                    satellite_id=target.satellite_id,
                    resolution="authenticated_request_source",
                )

        raise PlaybackTargetResolutionError("playback_target_required")
