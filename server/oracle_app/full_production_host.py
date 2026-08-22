"""Exact host and secret-asset reconciliation for the full-production profile."""

from __future__ import annotations

import grp
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import urllib.request
from typing import Callable


PROFILE = "full-production-brain"
SECRET_ASSET_ROOT = PurePosixPath("secrets/provider-assets")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
GROUP = re.compile(r"^[a-z_][a-z0-9_-]*$")


class FullProductionHostError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def secret_asset_identity(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise FullProductionHostError("required secret provider asset is absent or unsafe")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise FullProductionHostError("required secret provider asset is readable by another host identity")
    return {
        "sha256": _sha256(path),
        "size": path.stat().st_size,
        "mode": f"{mode:04o}",
    }


def _ollama_probe(binary: str, model: str) -> dict[str, str]:
    completed = subprocess.run(
        [binary, "--version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=8) as response:
        payload = json.load(response)
    models = {
        str(item.get("name")): str(item.get("digest"))
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    return {"version_output": completed.stdout.strip(), "model_digest": models.get(model, "")}


def build_host_plan(
    deployment: dict[str, object],
    secret_asset: Path,
    sudoers_source: Path,
    *,
    root: Path = Path("/srv/oracle"),
    provider_probe: Callable[[str, str], dict[str, str]] = _ollama_probe,
) -> dict[str, object]:
    if deployment.get("installation_profiles") != [PROFILE]:
        raise FullProductionHostError("deployment does not select the fixed full-production profile")
    production = deployment.get("full_production_brain")
    if not isinstance(production, dict):
        raise FullProductionHostError("deployment lacks full-production host authority")
    capabilities = production.get("host_capabilities")
    provider_requirements = production.get("external_provider_requirements")
    secret_assets = production.get("secret_assets")
    if not isinstance(capabilities, dict) or not isinstance(provider_requirements, dict) or not isinstance(secret_assets, list):
        raise FullProductionHostError("deployment full-production host authority is malformed")
    if len(secret_assets) != 1 or not isinstance(secret_assets[0], dict):
        raise FullProductionHostError("deployment secret provider asset declaration differs")
    declared_secret = secret_assets[0]
    secret_id = declared_secret.get("id")
    secret_destination_value = declared_secret.get("destination")
    if not isinstance(secret_id, str) or IDENTIFIER.fullmatch(secret_id) is None:
        raise FullProductionHostError("deployment secret provider asset ID is invalid")
    if not isinstance(secret_destination_value, str) or declared_secret.get("mode") != "0600":
        raise FullProductionHostError("deployment secret provider asset declaration differs")
    secret_destination = PurePosixPath(secret_destination_value)
    if (
        secret_destination.is_absolute()
        or any(part in {"", ".", ".."} for part in secret_destination.parts)
        or secret_destination.parent != SECRET_ASSET_ROOT
    ):
        raise FullProductionHostError("deployment secret provider asset destination is unsafe")
    commands = capabilities.get("commands")
    if not isinstance(commands, dict):
        raise FullProductionHostError("deployment host command inventory is malformed")
    command_results = {}
    for name, raw_path in sorted(commands.items()):
        path = Path(str(raw_path))
        command_results[str(name)] = {
            "path": str(path),
            "available": path.exists() and os.access(path, os.X_OK),
        }
    if not all(item["available"] for item in command_results.values()):
        raise FullProductionHostError("one or more required full-production host commands are unavailable")
    groups = capabilities.get("supplementary_groups")
    if (
        not isinstance(groups, list)
        or len(groups) != 2
        or any(not isinstance(name, str) or GROUP.fullmatch(name) is None for name in groups)
        or len(set(groups)) != len(groups)
    ):
        raise FullProductionHostError("full-production supplementary groups are malformed")
    try:
        account = pwd.getpwnam("oracle")
        group_records = {name: grp.getgrnam(name) for name in groups}
    except KeyError as exc:
        raise FullProductionHostError("full-production service identity or host group is absent") from exc
    current_groups = {
        record.gr_name
        for record in grp.getgrall()
        if account.pw_name in record.gr_mem or record.gr_gid == account.pw_gid
    }
    writable = capabilities.get("writable_paths")
    if (
        not isinstance(writable, list)
        or len(writable) != 1
        or not isinstance(writable[0], str)
        or not Path(writable[0]).is_absolute()
        or Path(writable[0]) == Path("/")
        or Path(writable[0]).is_symlink()
        or not Path(writable[0]).is_dir()
    ):
        raise FullProductionHostError("full-production storage capability is unavailable")
    ollama = provider_requirements.get("ollama")
    if not isinstance(ollama, dict):
        raise FullProductionHostError("Ollama requirement is absent")
    observed_provider = provider_probe(str(ollama.get("binary")), str(ollama.get("model")))
    if str(ollama.get("version")) not in observed_provider.get("version_output", ""):
        raise FullProductionHostError("Ollama version differs from the full-production requirement")
    if observed_provider.get("model_digest") != ollama.get("model_digest"):
        raise FullProductionHostError("Ollama model identity differs from the full-production requirement")
    if sudoers_source.is_symlink() or not sudoers_source.is_file():
        raise FullProductionHostError("fixed full-production sudoers source is unavailable")
    secret_identity = secret_asset_identity(secret_asset)
    destination = root.joinpath(*secret_destination.parts)
    destination_state = "absent"
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise FullProductionHostError("installed secret provider asset destination is unsafe")
        destination_state = "reused" if _sha256(destination) == secret_identity["sha256"] else "replace"
    basis = {
        "format": "oracle-full-production-host-plan-v1",
        "profile": PROFILE,
        "commands": command_results,
        "groups": {
            "required": groups,
            "missing_for_oracle": sorted(set(groups) - current_groups),
            "gids": {name: record.gr_gid for name, record in group_records.items()},
        },
        "writable_paths": writable,
        "external_provider": {"ollama": {**ollama, "observed": observed_provider}},
        "secret_asset": {
            **secret_identity,
            "id": secret_id,
            "destination": str(destination),
            "disposition": destination_state,
        },
        "sudoers": {"sha256": _sha256(sudoers_source), "destination": "/etc/sudoers.d/oracle-full-production"},
    }
    identity = "oracle-full-production-host-plan-v1:sha256:" + hashlib.sha256(
        (json.dumps(basis, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    return {**basis, "identity": identity}
