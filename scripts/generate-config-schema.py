#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from oracle_app.configuration.models import ROLE_MODELS, SecretMutationInput  # noqa: E402
from oracle_app.configuration.roles import OPTIONAL_ROLE_PATHS, REQUIRED_ROLE_PATHS  # noqa: E402


OUTPUT_PATH = REPO_ROOT / "docs" / "reference" / "generated" / "configuration-v2.schema.json"

ROLE_OWNERS = {
    "bundle.yaml": "configuration",
    "brain.yaml": "brain",
    "access.yaml": "access",
    "household.yaml": "household",
    "satellites.yaml": "satellite-fleet",
    "domains/information.yaml": "information",
    "domains/music.yaml": "music",
    "domains/audiobooks.yaml": "audiobooks",
    "domains/weather.yaml": "weather",
    "domains/calendar.yaml": "calendar",
    "domains/home-assistant.yaml": "home-assistant",
    "domains/notifications.yaml": "notifications",
    "domains/routines.yaml": "routines",
    "domains/network/inventory.yaml": "network-inventory",
    "domains/network/policy.yaml": "network-policy",
    "domains/network/adapters.yaml": "network-adapters",
}

ROLE_SAFETY = {
    "access.yaml": ["access_expansion", "public_health_enablement"],
    "household.yaml": ["identity_removal"],
    "satellites.yaml": ["identity_removal", "credential_role_change"],
    "domains/home-assistant.yaml": ["mutating_control_enablement"],
    "domains/routines.yaml": ["mutating_control_enablement"],
    "domains/network/inventory.yaml": ["mutating_control_enablement"],
}


def _close_typed_maps(value: object) -> None:
    if isinstance(value, dict):
        if "patternProperties" in value:
            value["additionalProperties"] = False
        for child in value.values():
            _close_typed_maps(child)
    elif isinstance(value, list):
        for child in value:
            _close_typed_maps(child)


def build_schema() -> dict[str, object]:
    expected_roles = set(REQUIRED_ROLE_PATHS | OPTIONAL_ROLE_PATHS)
    if set(ROLE_MODELS) != expected_roles or set(ROLE_OWNERS) != expected_roles:
        raise RuntimeError("Generated schema role metadata does not match the fixed role registry.")

    properties: dict[str, object] = {}
    for role_path in sorted(ROLE_MODELS):
        role_schema = ROLE_MODELS[role_path].model_json_schema(mode="validation")
        _close_typed_maps(role_schema)
        role_schema["x-oracle-file-role"] = role_path
        role_schema["x-oracle-owner"] = ROLE_OWNERS[role_path]
        role_schema["x-oracle-required-role"] = role_path in REQUIRED_ROLE_PATHS
        role_schema["x-oracle-restart-impact"] = "restart_required"
        role_schema["x-oracle-safety-classifications"] = ROLE_SAFETY.get(role_path, [])
        properties[role_path] = role_schema

    secret_mutation = SecretMutationInput.model_json_schema(mode="validation")
    secret_mutation["x-oracle-owner"] = "secrets"
    secret_mutation["x-oracle-restart-impact"] = "restart_required"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:oracle:configuration-schema:v2",
        "title": "Oracle V2 Configuration Bundle Roles",
        "description": "Generated tooling output; executable Pydantic models remain field authority.",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(REQUIRED_ROLE_PATHS),
        "properties": properties,
        "x-oracle-format": "oracle-configuration-schema-v1",
        "x-oracle-bundle-schema-version": 1,
        "x-oracle-service-inputs": {"secret_mutation": secret_mutation},
    }


def serialized_schema() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify Oracle configuration JSON Schema output.")
    parser.add_argument("--write", action="store_true", help="Rewrite the checked-in generated schema.")
    args = parser.parse_args()
    expected = serialized_schema()
    if args.write:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(expected, encoding="utf-8")
        return 0
    actual = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
    if actual == expected:
        return 0
    print(
        "Generated configuration schema is stale; run scripts/generate-config-schema.py --write",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
