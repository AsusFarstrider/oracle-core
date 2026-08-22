from __future__ import annotations

from dataclasses import dataclass, field
import hmac
from types import MappingProxyType
from typing import Mapping

from .effective import EffectiveConfig
from .models import (
    AccessConfiguration,
    OperatorAccess,
    PublicHealth,
    SatelliteAuthentication,
    TrustedBoundary,
)
from .household_runtime_settings import HouseholdRuntimeSettings


@dataclass(frozen=True)
class SourceCredentialBindingSettings:
    source_id: str
    source_type: str
    credential_secret: str
    active: bool
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AccessRuntimeSettings:
    """Frozen V2 access policy and non-satellite source credential seam."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    operator_access: OperatorAccess
    trusted_boundary: TrustedBoundary | None
    public_health: PublicHealth
    satellite_authentication: SatelliteAuthentication
    source_credential_bindings: Mapping[str, SourceCredentialBindingSettings]

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> AccessRuntimeSettings:
        role = effective.role("access.yaml")
        if not isinstance(role, AccessConfiguration):
            raise TypeError("Effective access.yaml role does not use the executable access schema.")
        household = HouseholdRuntimeSettings.from_effective_config(effective)
        bindings: dict[str, SourceCredentialBindingSettings] = {}
        configured = role.source_authentication
        for binding in configured.credential_bindings if configured is not None else ():
            source = household.source(binding.source_id, enabled_only=False)
            if source is None or source.type == "satellite":
                raise ValueError("Canonical source credential binding targets an invalid source.")
            active = source.enabled
            credential = effective.secrets.resolve(binding.credential_secret) if active else None
            if active and credential is None:
                raise ValueError("Active canonical source credential binding has no secret value.")
            bindings[source.id] = SourceCredentialBindingSettings(
                source_id=source.id,
                source_type=source.type,
                credential_secret=binding.credential_secret,
                active=active,
                credential=credential,
            )
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            operator_access=role.operator_access,
            trusted_boundary=role.trusted_boundary,
            public_health=role.public_health,
            satellite_authentication=role.satellite_authentication,
            source_credential_bindings=MappingProxyType(bindings),
        )

    def authenticate_source_credential(self, credential: str | None) -> str | None:
        """Return the one stable source proved by a presented credential.

        This performs request authentication only. It does not compare
        configured values with each other or retain equality evidence.
        """

        if not isinstance(credential, str) or not credential:
            return None
        matches = [
            binding.source_id
            for binding in self.source_credential_bindings.values()
            if binding.active
            and binding.credential is not None
            and hmac.compare_digest(
                credential.encode("utf-8"),
                binding.credential.encode("utf-8"),
            )
        ]
        return matches[0] if len(matches) == 1 else None
