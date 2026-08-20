from __future__ import annotations

from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from typing import Any, Mapping

import rfc8785

from .loader import LoadedBundle
from .models import BundleManifest


CONFIG_FORMAT = "oracle-config-v2"
CONFIG_REVISION_PREFIX = f"{CONFIG_FORMAT}:sha256:"


class ConfigurationCanonicalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedBundle:
    format: str
    config_revision: str
    configuration: Mapping[str, Any]
    canonical_bytes: bytes

    def envelope(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "config_revision": self.config_revision,
            "configuration": _thaw(self.configuration),
        }


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _sort_id_list(value: list[Any]) -> list[Any]:
    if all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in value):
        return sorted(value, key=lambda item: item["id"])
    return value


def _apply_semantic_ordering(role_path: str, value: dict[str, Any]) -> dict[str, Any]:
    if role_path == "household.yaml":
        for collection in ("users", "rooms", "sources", "modes"):
            value[collection] = _sort_id_list(value[collection])
        for collection in ("users", "rooms", "modes"):
            for item in value[collection]:
                item["aliases"] = sorted(item["aliases"], key=str.casefold)
    elif role_path == "satellites.yaml":
        value["satellites"] = _sort_id_list(value["satellites"])
    elif role_path == "access.yaml":
        boundary = value.get("trusted_boundary")
        if boundary is not None:
            boundary["trusted_proxy_ids"] = sorted(boundary["trusted_proxy_ids"])
            boundary["accepted_headers"] = sorted(boundary["accepted_headers"])
        source_authentication = value.get("source_authentication")
        if source_authentication is not None:
            source_authentication["credential_bindings"] = sorted(
                source_authentication["credential_bindings"],
                key=lambda item: item["source_id"],
            )
    elif role_path == "domains/information.yaml":
        value["news"]["sources"] = _sort_id_list(value["news"]["sources"])
        for source in value["news"]["sources"]:
            source["aliases"] = sorted(source["aliases"], key=str.casefold)
        for provider in value["facts"]["providers"].values():
            if provider["type"] == "static":
                provider["items"] = _sort_id_list(provider["items"])
                for item in provider["items"]:
                    item["queries"] = sorted(item["queries"], key=str.casefold)
    elif role_path in {"domains/music.yaml", "domains/audiobooks.yaml"}:
        value["playback"]["source_ids"] = sorted(value["playback"]["source_ids"])
    elif role_path == "domains/calendar.yaml":
        for provider in value["providers"].values():
            provider["feeds"] = _sort_id_list(provider["feeds"])
    elif role_path == "domains/home-assistant.yaml":
        value["automations"] = _sort_id_list(value["automations"])
        for mapping in value["mappings"].values():
            if "allowed_operations" in mapping:
                mapping["allowed_operations"] = sorted(mapping["allowed_operations"])
    elif role_path == "domains/notifications.yaml":
        value["types"] = _sort_id_list(value["types"])
        value["recipient_groups"] = _sort_id_list(value["recipient_groups"])
        for notification in value["types"]:
            notification["audience"] = sorted(notification["audience"], key=lambda item: (item["type"], item["id"]))
            notification["suppressed_by"] = sorted(notification["suppressed_by"])
            external = notification.get("external_delivery")
            if external is not None:
                external["recipient_groups"] = sorted(external["recipient_groups"])
    elif role_path == "domains/routines.yaml":
        value["definitions"] = _sort_id_list(value["definitions"])
        for definition in value["definitions"]:
            definition["source_ids"] = sorted(definition["source_ids"])
            definition["triggers"]["source_phrases"] = sorted(definition["triggers"]["source_phrases"], key=str.casefold)
            definition["triggers"]["global_phrases"] = sorted(definition["triggers"]["global_phrases"], key=str.casefold)
    elif role_path == "domains/network/inventory.yaml":
        for collection in ("hosts", "devices", "services", "service_groups", "monitors", "dependencies", "power_targets"):
            value[collection] = _sort_id_list(value[collection])
        for group in value["service_groups"]:
            group["service_ids"] = sorted(group["service_ids"])
        for power_target in value["power_targets"]:
            power_target["capabilities"] = sorted(power_target["capabilities"])
    elif role_path == "domains/network/policy.yaml":
        value["actions"] = _sort_id_list(value["actions"])
        value["recoveries"] = _sort_id_list(value["recoveries"])
        for action in value["actions"]:
            action["required_preconditions"] = sorted(action["required_preconditions"])
        for recovery in value["recoveries"]:
            recovery["triggers"]["global_phrases"] = sorted(recovery["triggers"]["global_phrases"], key=str.casefold)
    return value


def canonicalize_json(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:
        raise ConfigurationCanonicalizationError(str(exc)) from exc


def normalize_bundle(bundle: LoadedBundle) -> NormalizedBundle:
    manifest = bundle.roles["bundle.yaml"]
    if not isinstance(manifest, BundleManifest):
        raise TypeError("bundle.yaml did not resolve to BundleManifest.")

    normalized_roles = {
        role_path: _apply_semantic_ordering(role_path, model.model_dump(mode="json"))
        for role_path, model in bundle.roles.items()
        if role_path != "bundle.yaml"
    }
    configuration: dict[str, Any] = {
        "kind": manifest.kind,
        "schema_version": manifest.schema_version,
        "bundle_id": manifest.bundle_id,
        "roles": normalized_roles,
    }
    canonical_bytes = canonicalize_json(configuration)
    digest = hashlib.sha256(canonical_bytes).hexdigest()
    return NormalizedBundle(
        format=CONFIG_FORMAT,
        config_revision=f"{CONFIG_REVISION_PREFIX}{digest}",
        configuration=_freeze(configuration),
        canonical_bytes=canonical_bytes,
    )
