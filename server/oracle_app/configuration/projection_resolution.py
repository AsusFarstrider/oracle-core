from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hmac
from typing import Any

from .generations import GenerationIntegrityError, GenerationStore
from .normalization import canonicalize_json
from .projection_generations import (
    InstalledSatelliteProjection,
    SatelliteProjectionGenerationStore,
)


SATELLITE_PROJECTION_PULL_FORMAT = "oracle-satellite-projection-pull-v1"


class SatelliteProjectionAuthenticationError(PermissionError):
    """The caller did not prove one selected satellite identity."""


@dataclass(frozen=True)
class ResolvedSatelliteProjection:
    selection_operation_id: str
    selection_revision: int
    installed: InstalledSatelliteProjection


@dataclass(frozen=True, slots=True)
class SatelliteProjectionPullEnvelope:
    """One verified satellite activation serialized as a pull response.

    The envelope retains the verified resolver result rather than copying its
    identities into independently mutable fields. Secret values are exposed
    only by explicit transport serialization and are excluded from repr.
    """

    _resolved: ResolvedSatelliteProjection

    def __post_init__(self) -> None:
        if not isinstance(self._resolved, ResolvedSatelliteProjection):
            raise TypeError("Satellite projection pull envelope requires a resolved projection.")

    def __repr__(self) -> str:
        installed = self._resolved.installed
        return (
            "SatelliteProjectionPullEnvelope("
            f"satellite_id={installed.activation.satellite_id!r}, "
            f"activation_id={installed.activation.generation_id!r}, "
            f"selection_revision={self._resolved.selection_revision!r})"
        )

    def to_payload(self) -> dict[str, Any]:
        installed = self._resolved.installed
        secret_values = {
            logical_id: installed.secrets.snapshot.resolve(logical_id)
            for logical_id in sorted(installed.secrets.snapshot.present_ids)
        }
        if not all(isinstance(value, str) for value in secret_values.values()):
            raise GenerationIntegrityError("Selected satellite projection has an invalid secret snapshot.")
        return {
            "format": SATELLITE_PROJECTION_PULL_FORMAT,
            "satellite_id": installed.activation.satellite_id,
            "selection": {
                "operation_id": self._resolved.selection_operation_id,
                "revision": self._resolved.selection_revision,
            },
            "activation": {
                "activation_id": installed.activation.generation_id,
                "source_config_revision": installed.activation.source_config_revision,
            },
            "projection": {
                "generation_id": installed.projection.generation_id,
                "payload": installed.projection.projection.model_dump(mode="json"),
            },
            "local_secrets": {
                "generation_id": installed.secrets.generation_id,
                "values": secret_values,
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonicalize_json(self.to_payload())


class SatelliteProjectionResolver:
    """Authenticate and resolve one satellite's currently selected projection.

    This is the transport-neutral read boundary. It does not serialize a pull
    response, record delivery state, accept acknowledgements, or mutate selection.
    """

    def __init__(self, store: GenerationStore) -> None:
        store.validate_initialized()
        self.store = store
        self.projections = SatelliteProjectionGenerationStore(store)

    def resolve(
        self,
        satellite_id: str,
        credential: str,
    ) -> ResolvedSatelliteProjection:
        selected = self.store.load_selected()
        activation_id = selected.satellite_projection_activation_ids.get(satellite_id)
        if activation_id is None:
            raise SatelliteProjectionAuthenticationError("Satellite projection authentication failed.")
        installed = self.authenticate_activation(
            satellite_id,
            activation_id,
            credential,
        )
        return self._resolved(selected, installed)

    def authenticate_activation(
        self,
        satellite_id: str,
        activation_id: str,
        credential: str,
    ) -> InstalledSatelliteProjection:
        """Authenticate one exact immutable satellite activation.

        Shared Brain request ingress uses the activation bound to the Brain's
        applied snapshot rather than following a newer desired selection.
        """

        if not isinstance(credential, str) or not credential:
            raise SatelliteProjectionAuthenticationError("Satellite projection authentication failed.")
        installed = self.projections.load_installed(satellite_id, activation_id)
        logical_id = installed.projection.projection.configuration.brain_client.credential_secret
        expected = installed.secrets.snapshot.resolve(logical_id)
        if not isinstance(expected, str):
            raise GenerationIntegrityError("Selected satellite projection lacks its Brain credential.")
        if not hmac.compare_digest(credential.encode("utf-8"), expected.encode("utf-8")):
            raise SatelliteProjectionAuthenticationError("Satellite projection authentication failed.")
        return installed

    def resolve_enrollment(
        self,
        satellite_id: str,
        credential: str,
    ) -> ResolvedSatelliteProjection:
        selected = self.store.load_selected()
        activation_id = selected.satellite_projection_activation_ids.get(satellite_id)
        if activation_id is None or not isinstance(credential, str) or not credential:
            raise SatelliteProjectionAuthenticationError("Satellite enrollment authentication failed.")
        logical_id = self._enrollment_secret_id(selected.config.configuration, satellite_id)
        expected = selected.secrets.snapshot.resolve(logical_id)
        if not isinstance(expected, str):
            raise GenerationIntegrityError("Selected satellite enrollment credential is unavailable.")
        if not hmac.compare_digest(credential.encode("utf-8"), expected.encode("utf-8")):
            raise SatelliteProjectionAuthenticationError("Satellite enrollment authentication failed.")
        installed = self.projections.load_installed(satellite_id, activation_id)
        return self._resolved(selected, installed)

    def resolve_pull(
        self,
        satellite_id: str,
        credential: str,
    ) -> SatelliteProjectionPullEnvelope:
        return SatelliteProjectionPullEnvelope(self.resolve(satellite_id, credential))

    def resolve_enrollment_pull(
        self,
        satellite_id: str,
        credential: str,
    ) -> SatelliteProjectionPullEnvelope:
        return SatelliteProjectionPullEnvelope(
            self.resolve_enrollment(satellite_id, credential)
        )

    @staticmethod
    def _resolved(selected: Any, installed: InstalledSatelliteProjection) -> ResolvedSatelliteProjection:
        if selected.selection_operation_id is None:
            raise GenerationIntegrityError("Selected projection lacks committed selection identity.")
        return ResolvedSatelliteProjection(
            selection_operation_id=selected.selection_operation_id,
            selection_revision=selected.selection_revision,
            installed=installed,
        )

    @staticmethod
    def _enrollment_secret_id(configuration: Any, satellite_id: str) -> str:
        try:
            satellites = configuration["roles"]["satellites.yaml"]["satellites"]
        except (KeyError, TypeError) as exc:
            raise GenerationIntegrityError("Selected satellite enrollment configuration is invalid.") from exc
        if not isinstance(satellites, tuple):
            raise GenerationIntegrityError("Selected satellite enrollment configuration is invalid.")
        matches = [
            item
            for item in satellites
            if isinstance(item, Mapping) and item.get("id") == satellite_id
        ]
        if len(matches) != 1 or matches[0].get("enabled") is not True:
            raise SatelliteProjectionAuthenticationError("Satellite enrollment authentication failed.")
        enrollment = matches[0].get("enrollment")
        logical_id = (
            enrollment.get("credential_secret")
            if isinstance(enrollment, Mapping)
            else None
        )
        if not isinstance(logical_id, str) or not logical_id:
            raise GenerationIntegrityError("Selected satellite enrollment configuration is invalid.")
        return logical_id
