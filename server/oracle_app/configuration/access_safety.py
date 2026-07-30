from __future__ import annotations

from typing import Mapping

from .models import (
    AccessConfiguration,
    CredentialBinding,
    OperatorAccess,
    PublicHealth,
    SatelliteAuthentication,
    SourceAuthentication,
    TrustedBoundary,
)


ACCESS_SCHEMA_FIELD_DISPOSITIONS = {
    "operator_access": "container",
    "operator_access.mode": "access_expansion",
    "operator_access.boundary_id": "access_expansion",
    "operator_access.browser_inspection": "access_expansion",
    "operator_access.browser_mutation": "access_expansion",
    "operator_access.csrf_protection": "proof_requirement",
    "operator_access.host_local_cli": "fixed_recovery_boundary",
    "trusted_boundary": "access_expansion_when_enabled",
    "trusted_boundary.boundary_id": "access_expansion_when_enabled",
    "trusted_boundary.enabled": "access_expansion",
    "trusted_boundary.type": "fixed_mechanism",
    "trusted_boundary.trusted_proxy_ids": "access_expansion_on_addition",
    "trusted_boundary.accepted_headers": "access_expansion_on_addition",
    "public_health": "container",
    "public_health.enabled": "public_health_enablement",
    "satellite_authentication": "container",
    "satellite_authentication.enrollment_mode": "fixed_mechanism",
    "satellite_authentication.directional_credentials_required": "fixed_safety_requirement",
    "source_authentication": "credential_binding_container",
    "source_authentication.credential_bindings": "active_binding_expansion",
    "source_authentication.credential_bindings[].source_id": "credential_binding_identity",
    "source_authentication.credential_bindings[].credential_secret": "credential_role_change",
}


def access_schema_field_paths() -> frozenset[str]:
    paths: set[str] = set(AccessConfiguration.model_fields)
    nested = {
        "operator_access": OperatorAccess,
        "trusted_boundary": TrustedBoundary,
        "public_health": PublicHealth,
        "satellite_authentication": SatelliteAuthentication,
        "source_authentication": SourceAuthentication,
    }
    for prefix, model in nested.items():
        paths.update(f"{prefix}.{name}" for name in model.model_fields)
    paths.update(
        f"source_authentication.credential_bindings[].{name}"
        for name in CredentialBinding.model_fields
    )
    return frozenset(paths)


def _roles(configuration: Mapping[str, object]) -> Mapping[str, object]:
    roles = configuration["roles"]
    if not isinstance(roles, Mapping):
        raise TypeError("Normalized configuration roles must be a mapping.")
    return roles


def _active_bindings(configuration: Mapping[str, object]) -> dict[str, str]:
    roles = _roles(configuration)
    household = roles["household.yaml"]
    access = roles["access.yaml"]
    if not isinstance(household, Mapping) or not isinstance(access, Mapping):
        raise TypeError("Normalized household and access roles must be mappings.")
    enabled_sources = {
        source["id"]
        for source in household["sources"]
        if source["enabled"] is True and source["type"] != "satellite"
    }
    authentication = access.get("source_authentication")
    if not isinstance(authentication, Mapping):
        return {}
    return {
        binding["source_id"]: binding["credential_secret"]
        for binding in authentication["credential_bindings"]
        if binding["source_id"] in enabled_sources
    }


def classify_access_safety(
    before_configuration: Mapping[str, object],
    after_configuration: Mapping[str, object],
) -> dict[str, frozenset[str]]:
    before_access = _roles(before_configuration)["access.yaml"]
    after_access = _roles(after_configuration)["access.yaml"]
    if not isinstance(before_access, Mapping) or not isinstance(after_access, Mapping):
        raise TypeError("Normalized access roles must be mappings.")
    classified: dict[str, set[str]] = {}

    def require(path: str, acknowledgement: str) -> None:
        classified.setdefault(f"roles.access.yaml.{path}", set()).add(acknowledgement)

    before_operator = before_access["operator_access"]
    after_operator = after_access["operator_access"]
    if before_operator["mode"] != "trusted_boundary" and after_operator["mode"] == "trusted_boundary":
        require("operator_access.mode", "access_expansion")
    for field in ("browser_inspection", "browser_mutation"):
        if before_operator[field] is False and after_operator[field] is True:
            require(f"operator_access.{field}", "access_expansion")
    if before_operator["boundary_id"] != after_operator["boundary_id"] and after_operator["boundary_id"] is not None:
        require("operator_access.boundary_id", "access_expansion")

    before_boundary = before_access.get("trusted_boundary")
    after_boundary = after_access.get("trusted_boundary")
    if isinstance(after_boundary, Mapping) and after_boundary["enabled"] is True:
        if not isinstance(before_boundary, Mapping):
            require("trusted_boundary", "access_expansion")
        else:
            if before_boundary["enabled"] is False:
                require("trusted_boundary.enabled", "access_expansion")
            if before_boundary["boundary_id"] != after_boundary["boundary_id"]:
                require("trusted_boundary.boundary_id", "access_expansion")
            if set(after_boundary["trusted_proxy_ids"]) - set(before_boundary["trusted_proxy_ids"]):
                require("trusted_boundary.trusted_proxy_ids", "access_expansion")
            if set(after_boundary["accepted_headers"]) - set(before_boundary["accepted_headers"]):
                require("trusted_boundary.accepted_headers", "access_expansion")

    if before_access["public_health"]["enabled"] is False and after_access["public_health"]["enabled"] is True:
        require("public_health.enabled", "public_health_enablement")

    before_bindings = _active_bindings(before_configuration)
    after_bindings = _active_bindings(after_configuration)
    binding_path = (
        "source_authentication.credential_bindings"
        if isinstance(before_access.get("source_authentication"), Mapping)
        and isinstance(after_access.get("source_authentication"), Mapping)
        else "source_authentication"
    )
    if set(after_bindings) - set(before_bindings):
        require(binding_path, "access_expansion")
    if any(
        source_id in before_bindings and before_bindings[source_id] != secret
        for source_id, secret in after_bindings.items()
    ):
        require(binding_path, "credential_role_change")
    before_secret_owners = {secret: source for source, secret in before_bindings.items()}
    if any(
        secret in before_secret_owners and before_secret_owners[secret] != source
        for source, secret in after_bindings.items()
    ):
        require(binding_path, "credential_role_change")

    return {path: frozenset(values) for path, values in classified.items()}


def intrinsic_access_acknowledgements(configuration: Mapping[str, object]) -> frozenset[str]:
    access = _roles(configuration)["access.yaml"]
    if not isinstance(access, Mapping):
        raise TypeError("Normalized access role must be a mapping.")
    required: set[str] = set()
    operator = access["operator_access"]
    boundary = access.get("trusted_boundary")
    if (
        operator["mode"] == "trusted_boundary"
        or operator["browser_inspection"] is True
        or operator["browser_mutation"] is True
        or (isinstance(boundary, Mapping) and boundary["enabled"] is True)
        or _active_bindings(configuration)
    ):
        required.add("access_expansion")
    if access["public_health"]["enabled"] is True:
        required.add("public_health_enablement")
    return frozenset(required)
