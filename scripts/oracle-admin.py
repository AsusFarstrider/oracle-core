#!/usr/bin/env python3
"""Host-local administration entrypoint for standard Oracle installations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import grp
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
SERVER_DIRECTORY = SCRIPT_DIRECTORY.parent / "server"
if str(SERVER_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SERVER_DIRECTORY))

from core_artifact import ArtifactError, _payload_inventory, _tree_identity, verify, verify_pair
from installation_staging import (
    InstallationStagingError,
    build_python_environment,
    stage_artifact_pair,
    validate_python_environment,
)
from oracle_app.installation_identity import environment_directory_name


OUTPUT_FORMAT = "oracle-admin-output-v1"
PLAN_FORMAT = "oracle-operation-plan-v1"
STANDARD_ROOT = Path("/srv/oracle")
SUPPORTED_OS_ID = "debian"
SUPPORTED_OS_MAJOR = "13"
SUPPORTED_ARCHITECTURE = "amd64"
SERVICE_ACCOUNT = "oracle"
SERVICE_GROUP = "oracle"
OPERATOR_GROUP = "oracle-admin"
SERVICE_HOME = "/nonexistent"
SERVICE_SHELL = "/usr/sbin/nologin"
MAINTENANCE_LOCK = Path("/run/lock/oracle-installation.lock")


def inspect_candidate(*args: object, **kwargs: object):
    from oracle_app.configuration import inspect_candidate as implementation

    return implementation(*args, **kwargs)


def snapshot_candidate(*args: object, **kwargs: object):
    from oracle_app.configuration import snapshot_candidate as implementation

    return implementation(*args, **kwargs)


def InstallationLayout(*args: object, **kwargs: object):  # noqa: N802 - lazy class-compatible factory
    from oracle_app.installation import InstallationLayout as implementation

    return implementation(*args, **kwargs)


def load_selected_activation(*args: object, **kwargs: object):
    from oracle_app.installation import load_selected_activation as implementation

    return implementation(*args, **kwargs)


def InitialAssemblyRequest(*args: object, **kwargs: object):  # noqa: N802 - lazy class-compatible factory
    from oracle_app.installation_assembly import InitialAssemblyRequest as implementation

    return implementation(*args, **kwargs)


def assemble_initial_activation(*args: object, **kwargs: object):
    from oracle_app.installation_assembly import assemble_initial_activation as implementation

    return implementation(*args, **kwargs)


def build_systemd_install_plan(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import build_systemd_install_plan as implementation

    return implementation(*args, **kwargs)


def install_systemd_unit(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import install_systemd_unit as implementation

    return implementation(*args, **kwargs)


def build_initial_activation_plan(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import build_initial_activation_plan as implementation

    return implementation(*args, **kwargs)


def prepare_initial_activation(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import prepare_initial_activation as implementation

    return implementation(*args, **kwargs)


def mark_initial_service_started(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import mark_initial_service_started as implementation

    return implementation(*args, **kwargs)


def mark_initial_verification_passed(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import mark_initial_verification_passed as implementation

    return implementation(*args, **kwargs)


def finalize_initial_activation(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import finalize_initial_activation as implementation

    return implementation(*args, **kwargs)


def fail_initial_activation(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import fail_initial_activation as implementation

    return implementation(*args, **kwargs)


def load_initial_activation_transaction(*args: object, **kwargs: object):
    from oracle_app.installation_systemd import load_initial_activation_transaction as implementation

    return implementation(*args, **kwargs)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _command_output(*argv: str) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _debian_architecture() -> str:
    value = _command_output("dpkg", "--print-architecture")
    return value or platform.machine().lower()


def _host_python_candidate() -> Path | None:
    """Discover a host interpreter independently of the CLI launch environment."""

    debian_default = Path("/usr/bin/python3")
    if debian_default.is_file():
        return debian_default
    discovered = shutil.which("python3")
    return Path(discovered) if discovered else None


def _python_capabilities(interpreter: Path | None = None) -> dict[str, object]:
    selected = interpreter or _host_python_candidate()
    if selected is None:
        return {
            "executable": None,
            "implementation": None,
            "version": None,
            "abi": None,
            "venv_module": False,
            "ensurepip_module": False,
        }
    probe = (
        "import importlib.util,json,platform,sysconfig;"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'version':platform.python_version(),'abi':sysconfig.get_config_var('SOABI') or '',"
        "'venv_module':importlib.util.find_spec('venv') is not None,"
        "'ensurepip_module':importlib.util.find_spec('ensurepip') is not None}))"
    )
    try:
        completed = subprocess.run(
            [str(selected), "-c", probe],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        facts = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {
            "executable": str(selected),
            "implementation": None,
            "version": None,
            "abi": None,
            "venv_module": False,
            "ensurepip_module": False,
        }
    return {"executable": str(selected), **facts}


def _storage_probe(root: Path) -> dict[str, object]:
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return {"probe_path": str(probe), "available": False, "error": str(exc)}
    return {
        "probe_path": str(probe),
        "available": True,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
    }


def _identity_probe() -> dict[str, object]:
    try:
        account = pwd.getpwnam(SERVICE_ACCOUNT)
    except KeyError:
        account_record = None
    else:
        supplementary = sorted(
            group.gr_name
            for group in grp.getgrall()
            if SERVICE_ACCOUNT in group.gr_mem and group.gr_gid != account.pw_gid
        )
        account_record = {
            "uid": account.pw_uid,
            "primary_gid": account.pw_gid,
            "home": account.pw_dir,
            "shell": account.pw_shell,
            "supplementary_groups": supplementary,
        }
    groups: dict[str, object] = {}
    for name in (SERVICE_GROUP, OPERATOR_GROUP):
        try:
            group = grp.getgrnam(name)
        except KeyError:
            groups[name] = None
        else:
            groups[name] = {"gid": group.gr_gid, "members": sorted(group.gr_mem)}
    return {
        "service_account": {"name": SERVICE_ACCOUNT, "existing": account_record},
        "groups": groups,
    }


def standard_layout_plan(root: Path = STANDARD_ROOT) -> dict[str, object]:
    """Return the ratified lifecycle ownership map without changing the host."""

    def entry(relative: str, owner: str, group: str, mode: str, authority: str) -> dict[str, str]:
        return {
            "path": str(root / relative),
            "owner": owner,
            "group": group,
            "mode": mode,
            "authority": authority,
        }

    return {
        "root": entry("", "root", SERVICE_GROUP, "0755", "elevated_maintenance"),
        "directories": [
            entry("revisions", "root", SERVICE_GROUP, "0750", "elevated_maintenance"),
            entry("environments", "root", SERVICE_GROUP, "0750", "elevated_maintenance"),
            entry("deployments", "root", SERVICE_GROUP, "0750", "elevated_maintenance"),
            entry("state", "root", SERVICE_GROUP, "0750", "elevated_maintenance"),
            entry("configuration", SERVICE_ACCOUNT, SERVICE_GROUP, "0750", "oracle_control_plane"),
            entry("secrets", SERVICE_ACCOUNT, SERVICE_GROUP, "0700", "oracle_control_plane"),
            entry("activations", SERVICE_ACCOUNT, SERVICE_GROUP, "0750", "oracle_control_plane"),
            entry("selection", SERVICE_ACCOUNT, SERVICE_GROUP, "0750", "oracle_control_plane"),
            entry("state/installation", "root", SERVICE_GROUP, "0750", "elevated_maintenance"),
            entry("state/control", SERVICE_ACCOUNT, SERVICE_GROUP, "0750", "oracle_control_plane"),
            entry("data", SERVICE_ACCOUNT, SERVICE_GROUP, "0700", "oracle_runtime"),
            entry("cache", SERVICE_ACCOUNT, SERVICE_GROUP, "0700", "oracle_runtime_reconstructible"),
            entry("tmp", SERVICE_ACCOUNT, SERVICE_GROUP, "0700", "oracle_runtime_temporary"),
        ],
        "identities": {
            "service_account": SERVICE_ACCOUNT,
            "service_primary_group": SERVICE_GROUP,
            "online_operator_group": OPERATOR_GROUP,
            "system_identity": True,
            "login": False,
            "writable_home": False,
            "implicit_operator_membership": False,
        },
        "host_integrations_outside_root": [
            "systemd unit definitions",
            "system user and group records",
            "host packages and shared libraries",
            "systemd journal",
            "systemd-managed runtime directory and Unix administration socket",
        ],
    }


def _system_uid_limit(path: Path = Path("/etc/login.defs")) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 1000
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "UID_MIN" and fields[1].isdigit():
            return int(fields[1])
    return 1000


def _account_can_write_home(account: dict[str, object]) -> bool:
    home = Path(str(account["home"]))
    if not home.exists():
        return False
    try:
        status = home.stat()
    except OSError:
        return False
    mode = status.st_mode
    if status.st_uid == account["uid"] and mode & 0o200:
        return True
    gids = {int(account["primary_gid"])}
    for name in account["supplementary_groups"]:
        try:
            gids.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            continue
    return bool((status.st_gid in gids and mode & 0o020) or mode & 0o002)


def identity_blockers(identities: dict[str, object] | None = None) -> list[dict[str, str]]:
    """Reject only concrete conflicts with the ratified service-identity model."""

    current = identities or _identity_probe()
    blockers: list[dict[str, str]] = []
    account = current["service_account"]["existing"]
    groups = current["groups"]
    service_group = groups[SERVICE_GROUP]
    if account is None:
        return blockers
    if service_group is None or account["primary_gid"] != service_group["gid"]:
        blockers.append({"code": "oracle_identity_primary_group_conflict", "detail": SERVICE_GROUP})
    if account["uid"] >= _system_uid_limit():
        blockers.append({"code": "oracle_identity_not_system_account", "detail": str(account["uid"])})
    if account["shell"] not in {SERVICE_SHELL, "/bin/false", "/usr/bin/nologin"}:
        blockers.append({"code": "oracle_identity_login_shell_conflict", "detail": account["shell"]})
    if _account_can_write_home(account):
        blockers.append({"code": "oracle_identity_writable_home_conflict", "detail": account["home"]})
    broad = sorted(set(account["supplementary_groups"]) & {"adm", "root", "sudo", "wheel"})
    if broad:
        blockers.append({"code": "oracle_identity_broad_privilege_conflict", "detail": ",".join(broad)})
    return blockers


def _password_status(account: str = SERVICE_ACCOUNT) -> str | None:
    value = _command_output("passwd", "-S", account)
    if not value:
        return None
    fields = value.split()
    return fields[1] if len(fields) >= 2 else None


def ensure_standard_identities() -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("identity creation requires an explicitly elevated oracle-admin invocation")
    before = _identity_probe()
    blockers = identity_blockers(before)
    if blockers:
        raise RuntimeError("existing Oracle identity is incompatible: " + "; ".join(item["code"] for item in blockers))
    created: list[str] = []
    for name in (SERVICE_GROUP, OPERATOR_GROUP):
        if before["groups"][name] is None:
            subprocess.run(["groupadd", "--system", name], check=True)
            created.append(f"group:{name}")
    if before["service_account"]["existing"] is None:
        subprocess.run(
            [
                "useradd", "--system", "--gid", SERVICE_GROUP, "--home-dir", SERVICE_HOME,
                "--no-create-home", "--shell", SERVICE_SHELL, SERVICE_ACCOUNT,
            ],
            check=True,
        )
        created.append(f"account:{SERVICE_ACCOUNT}")
    if _password_status() != "L":
        subprocess.run(["usermod", "--lock", SERVICE_ACCOUNT], check=True)
        created.append(f"password-locked:{SERVICE_ACCOUNT}")
    if _password_status() != "L":
        raise RuntimeError("Oracle service identity password is not locked")
    after = _identity_probe()
    blockers = identity_blockers(after)
    if blockers or after["service_account"]["existing"] is None or after["groups"][OPERATOR_GROUP] is None:
        raise RuntimeError("Oracle identity creation did not produce the required bounded identities")
    return {"created": created, "identities": after, "password_locked": True}


def ensure_standard_layout(root: Path = STANDARD_ROOT) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("protected layout creation requires an explicitly elevated oracle-admin invocation")
    plan = standard_layout_plan(root)
    allowed_top_level = {
        "revisions", "environments", "deployments", "configuration", "secrets", "activations",
        "selection", "state", "data", "cache", "tmp",
    }
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeError(f"standard installation root is unsafe: {root}")
    if root.is_dir():
        unexpected = sorted(path.name for path in root.iterdir() if path.name not in allowed_top_level)
        if unexpected:
            raise RuntimeError("standard installation root contains undeclared entries: " + ", ".join(unexpected))
    account = pwd.getpwnam(SERVICE_ACCOUNT)
    service_group = grp.getgrnam(SERVICE_GROUP)
    owners = {"root": 0, SERVICE_ACCOUNT: account.pw_uid}
    groups = {SERVICE_GROUP: service_group.gr_gid}
    entries = [plan["root"], *plan["directories"]]
    created: list[str] = []
    for item in entries:
        path = Path(item["path"])
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError(f"managed layout entry is unsafe: {path}")
        if not path.exists():
            path.mkdir(mode=int(item["mode"], 8), parents=True, exist_ok=False)
            created.append(str(path))
        os.chown(path, owners[item["owner"]], groups[item["group"]])
        path.chmod(int(item["mode"], 8))
    return {"created": created, "layout": plan}


def _layout_probe(root: Path) -> dict[str, object]:
    allowed = {
        "revisions", "environments", "deployments", "configuration", "secrets", "activations",
        "selection", "state", "data", "cache", "tmp",
    }
    blockers: list[dict[str, str]] = []
    entries: list[dict[str, str]] = []
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        blockers.append({"code": "installation_root_unsafe", "detail": str(root)})
    elif root.is_dir():
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            entry_type = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "other"
            entries.append({"name": path.name, "type": entry_type})
            if path.name not in allowed:
                blockers.append({"code": "installation_root_undeclared_entry", "detail": path.name})
            elif entry_type != "directory":
                blockers.append({"code": "installation_layout_entry_unsafe", "detail": path.name})
    return {"exists": root.exists(), "entries": entries, "blockers": blockers}


def platform_preflight(root: Path = STANDARD_ROOT) -> dict[str, object]:
    release = _os_release()
    version_id = release.get("VERSION_ID", "")
    os_major = version_id.split(".", 1)[0] if version_id else ""
    architecture = _debian_architecture()
    host_python = _host_python_candidate()
    commands = {
        name: shutil.which(name)
        for name in ("apt-get", "dpkg", "python3", "sha256sum", "sudo", "systemctl", "tar")
    }
    commands["python3"] = str(host_python) if host_python is not None else None
    python = _python_capabilities(host_python)
    supported = (
        release.get("ID") == SUPPORTED_OS_ID
        and os_major == SUPPORTED_OS_MAJOR
        and architecture == SUPPORTED_ARCHITECTURE
    )
    findings: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []
    for command in ("python3", "systemctl", "tar"):
        if commands[command] is None:
            blockers.append({"code": "mandatory_command_missing", "detail": command})
    systemd_running = Path("/run/systemd/system").is_dir()
    if commands["systemctl"] is not None and not systemd_running:
        blockers.append({"code": "systemd_not_running", "detail": "systemd is required as the service lifecycle authority"})
    version_parts = str(python["version"] or "").split(".")
    compatible_version = len(version_parts) >= 2 and version_parts[:2] == ["3", "13"]
    if python["implementation"] != "CPython" or not compatible_version:
        blockers.append(
            {
                "code": "compatible_python_missing",
                "detail": f"CPython 3.13 required; running {python['implementation']} {python['version']}",
            }
        )
    if not python["venv_module"]:
        blockers.append({"code": "python_venv_unavailable", "detail": "Python venv module is unavailable"})
    if python["venv_module"] and not python["ensurepip_module"]:
        if commands["apt-get"] is None:
            blockers.append(
                {
                    "code": "python_environment_bootstrap_unavailable",
                    "detail": "ensurepip is unavailable and no supported acquisition tool was found",
                }
            )
        else:
            findings.append(
                {
                    "code": "dependency_acquisition_required",
                    "detail": "python3-venv must be acquired before constructing the production environment",
                }
            )
    if not supported:
        findings.append(
            {
                "code": "unsupported_platform_acknowledgement_required",
                "detail": (
                    f"detected {release.get('ID') or 'unknown'} {os_major or 'unknown'} / {architecture}; "
                    f"validated tuple is {SUPPORTED_OS_ID} {SUPPORTED_OS_MAJOR} / {SUPPORTED_ARCHITECTURE}"
                ),
            }
        )
    elevation_available = os.geteuid() == 0 or commands["sudo"] is not None
    if not elevation_available:
        blockers.append(
            {
                "code": "elevated_maintenance_unavailable",
                "detail": "installation needs root or an available bounded elevation mechanism",
            }
        )
    storage = _storage_probe(root)
    if not storage["available"]:
        blockers.append({"code": "installation_storage_unavailable", "detail": str(storage.get("error") or root)})
    identities = _identity_probe()
    blockers.extend(identity_blockers(identities))
    layout_state = _layout_probe(root)
    blockers.extend(layout_state["blockers"])
    return {
        "support_status": "supported" if supported else "experimental",
        "os": {
            "id": release.get("ID"),
            "version_id": version_id or None,
            "major": os_major or None,
            "pretty_name": release.get("PRETTY_NAME"),
        },
        "architecture": architecture,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "service_manager": {
            "command": commands["systemctl"],
            "running": systemd_running,
        },
        "package_tooling": {"apt_get": commands["apt-get"], "dpkg": commands["dpkg"]},
        "commands": commands,
        "python": python,
        "operator": {
            "effective_uid": os.geteuid(),
            "is_root": os.geteuid() == 0,
            "elevation_available": elevation_available,
        },
        "oracle_identities": identities,
        "installation_root": {
            "path": str(root),
            "exists": root.exists(),
            "storage": storage,
            "layout_state": layout_state,
        },
        "findings": findings,
        "blockers": blockers,
    }


def artifact_preflight(core_archive: Path, household_archive: Path) -> dict[str, object]:
    archives = {
        "core": {"path": str(core_archive), "sha256": None, "valid": False, "error": None},
        "household": {"path": str(household_archive), "sha256": None, "valid": False, "error": None},
    }
    manifests: dict[str, dict[str, object]] = {}
    for kind, path in (("core", core_archive), ("household", household_archive)):
        try:
            archives[kind]["sha256"] = _sha256(path)
            manifests[kind] = verify(path)
            archives[kind]["valid"] = True
        except (OSError, ArtifactError, ValueError, json.JSONDecodeError) as exc:
            archives[kind]["error"] = str(exc)
    pair: dict[str, object] | None = None
    pair_error: str | None = None
    if len(manifests) == 2:
        try:
            verified = verify_pair(core_archive, household_archive)
            core = verified["core"]
            household = verified["household"]
            pair = {
                "core_commit": core["core_commit"],
                "core_git_tree": core["core_git_tree"],
                "core_payload_entries": len(core["inventory"]),
                "deployment_revision": household["deployment_revision"],
                "household_payload_entries": len(household["inventory"]),
                "installation_profiles": household.get("deployment", {}).get("installation_profiles", []),
                "configuration": household.get("deployment", {}).get("configuration"),
                "logical_secret_requirements": household.get("deployment", {}).get("logical_secret_requirements", []),
            }
        except (OSError, ArtifactError, ValueError, json.JSONDecodeError) as exc:
            pair_error = str(exc)
    required_core_paths = {
        "LICENSE", "README.md", "scripts/core_artifact.py", "scripts/installation_staging.py",
        "scripts/oracle-admin.py", "server/oracle_app/installation_assembly.py",
        "server/oracle_app/installation_identity.py", "server/oracle_app/installation_systemd.py",
    }
    if "core" in manifests:
        actual = {str(item.get("path")) for item in manifests["core"].get("inventory", []) if isinstance(item, dict)}
        missing = sorted(required_core_paths - actual)
        if missing:
            pair_error = "core artifact lacks required distribution metadata: " + ", ".join(missing)
            pair = None
    return {"archives": archives, "pair": pair, "pair_error": pair_error}


def build_install_preflight(core_archive: Path, household_archive: Path, *, root: Path = STANDARD_ROOT) -> dict[str, object]:
    host = platform_preflight(root)
    artifacts = artifact_preflight(core_archive, household_archive)
    blockers = list(host["blockers"])
    for kind, record in artifacts["archives"].items():
        if not record["valid"]:
            blockers.append({"code": "artifact_invalid", "detail": f"{kind}: {record['error']}"})
    if artifacts["pair"] is None:
        blockers.append(
            {
                "code": "artifact_pair_invalid",
                "detail": str(artifacts["pair_error"] or "artifact pair could not be verified"),
            }
        )
    plan_basis = {
        "format": PLAN_FORMAT,
        "operation": "install",
        "installation_root": str(root),
        "platform": {
            "support_status": host["support_status"],
            "os": host["os"],
            "architecture": host["architecture"],
            "python": host["python"],
        },
        "artifacts": {
            kind: {"sha256": value["sha256"]}
            for kind, value in artifacts["archives"].items()
        },
        "target": artifacts["pair"],
        "layout": standard_layout_plan(root),
        "preserve": ["household configuration", "secrets", "durable runtime data"],
        "mutations": [
            "acquire declared missing prerequisites",
            "create the dedicated service and operator identities",
            "create protected /srv/oracle lifecycle subtrees",
            "stage exact immutable components",
            "install the fixed systemd service definition",
            "select and verify one complete activation",
        ],
    }
    plan_identity = "oracle-operation-plan-v1:sha256:" + hashlib.sha256(_json_bytes(plan_basis)).hexdigest()
    return {
        "format": OUTPUT_FORMAT,
        "command": "preflight",
        "status": "blocked" if blockers else "ready",
        "mutation_performed": False,
        "platform": host,
        "artifacts": artifacts,
        "plan": {**plan_basis, "identity": plan_identity},
        "blockers": blockers,
    }


def build_staging_preflight(
    core_archive: Path,
    household_archive: Path,
    *,
    root: Path = STANDARD_ROOT,
) -> dict[str, object]:
    """Plan only the bounded protected-layout and immutable-staging operation."""

    result = build_install_preflight(core_archive, household_archive, root=root)
    host = result["platform"]
    artifacts = result["artifacts"]
    blockers = list(result["blockers"])
    requested_profiles = artifacts["pair"].get("installation_profiles", []) if artifacts["pair"] else []
    if requested_profiles != ["minimal-brain"]:
        blockers.append(
            {
                "code": "unsupported_staging_profile_set",
                "detail": "this bounded staging operation requires exactly the minimal-brain profile",
            }
        )
    plan_basis = {
        "format": PLAN_FORMAT,
        "operation": "stage-installation-foundation",
        "installation_root": str(root),
        "platform": {
            "support_status": host["support_status"],
            "os": host["os"],
            "architecture": host["architecture"],
            "python": host["python"],
        },
        "current_identities": host["oracle_identities"],
        "current_layout_state": host["installation_root"]["layout_state"],
        "artifacts": {kind: {"sha256": value["sha256"]} for kind, value in artifacts["archives"].items()},
        "target": artifacts["pair"],
        "layout": standard_layout_plan(root),
        "preserve": ["household configuration", "secrets", "durable runtime data"],
        "mutations": [
            "acquire python3-venv only when validated environment construction requires it",
            "create or validate the oracle service identity and oracle/oracle-admin groups",
            "create or reconcile the protected Oracle lifecycle layout",
            "stage exact immutable application and household deployment revisions",
            "construct or reuse the exact immutable minimal-brain Python environment",
            "record redacted staging evidence",
        ],
        "excluded": ["systemd installation", "activation creation", "service start", "selection change"],
    }
    identity = "oracle-operation-plan-v1:sha256:" + hashlib.sha256(_json_bytes(plan_basis)).hexdigest()
    return {
        **result,
        "command": "stage-plan",
        "status": "blocked" if blockers else "ready",
        "blockers": blockers,
        "plan": {**plan_basis, "identity": identity},
    }


def _apt_repository_identities() -> list[dict[str, str]]:
    candidates = [Path("/etc/apt/sources.list")]
    source_directory = Path("/etc/apt/sources.list.d")
    if source_directory.is_dir():
        candidates.extend(sorted(source_directory.glob("*.list")))
        candidates.extend(sorted(source_directory.glob("*.sources")))
    identities: list[dict[str, str]] = []
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            identities.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return identities


def _ensure_python_environment_support(host: dict[str, object]) -> dict[str, object]:
    python = host["python"]
    if python["venv_module"] and python["ensurepip_module"]:
        return {"disposition": "reused", "package": None, "version": None, "repository_identities": []}
    apt_get = host["package_tooling"]["apt_get"]
    if not python["venv_module"] or apt_get is None:
        raise RuntimeError("a compatible native Python environment cannot be constructed")
    subprocess.run([str(apt_get), "update"], check=True)
    subprocess.run([str(apt_get), "install", "-y", "python3-venv"], check=True)
    version = _command_output("dpkg-query", "-W", "-f=${Version}", "python3-venv")
    if not version:
        raise RuntimeError("python3-venv acquisition could not be validated")
    with tempfile.TemporaryDirectory(prefix="oracle-venv-validation-") as temporary:
        subprocess.run([str(python["executable"]), "-m", "venv", str(Path(temporary) / "environment")], check=True)
    return {
        "disposition": "installed",
        "package": "python3-venv",
        "version": version,
        "repository_identities": _apt_repository_identities(),
    }


def _write_staging_evidence(root: Path, plan_identity: str, result: dict[str, object]) -> Path:
    directory = root / "state" / "installation" / "staging-results"
    directory.mkdir(mode=0o750, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("installation evidence directory is unsafe")
    service_gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    os.chown(directory, 0, service_gid)
    directory.chmod(0o750)
    digest = plan_identity.rsplit(":", 1)[-1]
    destination = directory / f"{digest}.json"
    content = _json_bytes(result)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise RuntimeError("existing staging evidence conflicts with the approved plan")
        return destination
    temporary = directory / f".{destination.name}.tmp-{os.getpid()}"
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o440)
    os.chown(temporary, 0, service_gid)
    os.replace(temporary, destination)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return destination


@contextmanager
def _maintenance_lock(path: Path = MAINTENANCE_LOCK):
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("the standard installation lock requires Linux flock support") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Oracle installation or maintenance operation is active") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def execute_staging(
    core_archive: Path,
    household_archive: Path,
    approved_plan: str,
    *,
    root: Path = STANDARD_ROOT,
    allow_unsupported_platform: bool = False,
    lock_path: Path = MAINTENANCE_LOCK,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("staging requires an explicitly elevated oracle-admin invocation")
    preflight = build_staging_preflight(core_archive, household_archive, root=root)
    if preflight["status"] != "ready":
        raise RuntimeError("staging preflight is blocked")
    plan = preflight["plan"]
    if approved_plan != plan["identity"]:
        raise RuntimeError("approved plan identity is stale or does not match the proposed operation")
    if preflight["platform"]["support_status"] != "supported" and not allow_unsupported_platform:
        raise RuntimeError("experimental platform staging requires explicit acknowledgement")

    with _maintenance_lock(lock_path):
        locked_preflight = build_staging_preflight(core_archive, household_archive, root=root)
        if locked_preflight["status"] != "ready" or locked_preflight["plan"]["identity"] != approved_plan:
            raise RuntimeError("approved plan assumptions changed before the operation lock was acquired")
        dependency = _ensure_python_environment_support(locked_preflight["platform"])
        identities = ensure_standard_identities()
        layout = ensure_standard_layout(root)
        components = stage_artifact_pair(
            core_archive,
            household_archive,
            revisions=root / "revisions",
            deployments=root / "deployments",
        )
        environment = build_python_environment(
            Path(components["application_path"]),
            root / "environments",
            Path(locked_preflight["platform"]["python"]["executable"]),
            profile="minimal-brain",
        )
        evidence = {
            "format": OUTPUT_FORMAT,
            "command": "stage",
            "status": "staged",
            "mutation_performed": True,
            "plan_identity": approved_plan,
            "platform_support_status": locked_preflight["platform"]["support_status"],
            "dependency": dependency,
            "identities_created": identities["created"],
            "layout_paths_created": layout["created"],
            "components": components,
            "environment": environment,
            "activation_created": False,
            "selection_changed": False,
            "service_modified": False,
        }
        evidence_path = _write_staging_evidence(root, approved_plan, evidence)
    return {**evidence, "evidence_path": str(evidence_path)}


def build_initial_assembly_plan(
    core_archive: Path,
    household_archive: Path,
    environment_identity: str,
    *,
    root: Path = STANDARD_ROOT,
) -> dict[str, object]:
    """Inspect one already-staged minimal installation without changing it."""

    artifacts = artifact_preflight(core_archive, household_archive)
    blockers: list[dict[str, str]] = []
    pair = artifacts["pair"]
    if pair is None:
        blockers.append({"code": "artifact_pair_invalid", "detail": str(artifacts["pair_error"] or "invalid pair")})
        target: dict[str, object] = {}
    else:
        target = dict(pair)
        application_identity = "core-" + str(pair["core_commit"])
        deployment_identity = str(pair["deployment_revision"])
        application = root / "revisions" / application_identity
        deployment = root / "deployments" / deployment_identity
        try:
            environment_name = environment_directory_name(environment_identity)
        except ValueError as exc:
            blockers.append({"code": "staged_environment_identity_invalid", "detail": str(exc)})
            environment_name = "invalid-environment-identity"
        environment = root / "environments" / environment_name
        configuration = pair.get("configuration")
        configuration_root = configuration.get("root") if isinstance(configuration, dict) else None
        authored_revision = configuration.get("authored_revision") if isinstance(configuration, dict) else None
        target.update(
            {
                "application_revision_identity": application_identity,
                "household_deployment_revision": deployment_identity,
                "python_environment_identity": environment_identity,
                "configuration_root": configuration_root,
            }
        )
        for code, path in (
            ("staged_application_absent", application),
            ("staged_deployment_absent", deployment),
            ("staged_environment_absent", environment),
        ):
            if path.is_symlink() or not path.is_dir():
                blockers.append({"code": code, "detail": str(path)})
        core_manifest = verify(core_archive)
        household_manifest = verify(household_archive)
        if application.is_dir() and not application.is_symlink():
            if _payload_inventory(application) != core_manifest["inventory"] or _tree_identity(application) != pair["core_git_tree"]:
                blockers.append({"code": "staged_application_drift", "detail": str(application)})
        if deployment.is_dir() and not deployment.is_symlink():
            if _payload_inventory(deployment) != household_manifest["inventory"]:
                blockers.append({"code": "staged_deployment_drift", "detail": str(deployment)})
        if application.is_dir() and environment.is_dir() and not application.is_symlink() and not environment.is_symlink():
            try:
                validate_python_environment(application, environment)
            except (InstallationStagingError, OSError, subprocess.SubprocessError) as exc:
                blockers.append({"code": "staged_environment_invalid", "detail": str(exc)})
        if configuration_root != "configuration":
            blockers.append({"code": "unsupported_configuration_root", "detail": str(configuration_root)})
        elif deployment.is_dir() and not deployment.is_symlink():
            candidate = deployment / configuration_root
            try:
                inspection = inspect_candidate(candidate)
                actual_authored = snapshot_candidate(candidate).authored_revision
            except (OSError, ValueError) as exc:
                blockers.append({"code": "staged_configuration_invalid", "detail": str(exc)})
            else:
                if not inspection.report.activation_eligible:
                    blockers.append({"code": "staged_configuration_ineligible", "detail": str(candidate)})
                if actual_authored != authored_revision:
                    blockers.append({"code": "staged_configuration_revision_mismatch", "detail": actual_authored})
        if pair.get("logical_secret_requirements") != []:
            blockers.append({"code": "initial_secret_material_required", "detail": "minimal assembly supports the empty secret companion"})
        for selection in ("active", "staged", "approved", "previous-known-good"):
            path = root / "selection" / selection
            if path.exists() or path.is_symlink():
                blockers.append({"code": "initial_selection_not_empty", "detail": selection})
    basis = {
        "format": PLAN_FORMAT,
        "operation": "assemble-initial-activation",
        "installation_root": str(root),
        "artifacts": {kind: {"sha256": value["sha256"]} for kind, value in artifacts["archives"].items()},
        "target": target,
        "mutations": [
            "create the initial canonical configuration and empty secret generations",
            "arm canonical-only runtime startup",
            "publish one immutable complete installation activation",
            "select that complete activation as staged",
        ],
        "excluded": ["active selection", "approved selection", "systemd installation", "service start", "target health verification"],
    }
    identity = "oracle-operation-plan-v1:sha256:" + hashlib.sha256(_json_bytes(basis)).hexdigest()
    return {
        "format": OUTPUT_FORMAT,
        "command": "assemble-plan",
        "status": "blocked" if blockers else "ready",
        "mutation_performed": False,
        "artifacts": artifacts,
        "plan": {**basis, "identity": identity},
        "blockers": blockers,
    }


def execute_initial_assembly(
    core_archive: Path,
    household_archive: Path,
    environment_identity: str,
    approved_plan: str,
    *,
    root: Path = STANDARD_ROOT,
    lock_path: Path = MAINTENANCE_LOCK,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("initial assembly requires an explicitly elevated oracle-admin invocation")
    preflight = build_initial_assembly_plan(core_archive, household_archive, environment_identity, root=root)
    if preflight["status"] != "ready" or preflight["plan"]["identity"] != approved_plan:
        raise RuntimeError("initial assembly plan is blocked, stale, or unapproved")
    with _maintenance_lock(lock_path):
        locked = build_initial_assembly_plan(core_archive, household_archive, environment_identity, root=root)
        if locked["status"] != "ready" or locked["plan"]["identity"] != approved_plan:
            raise RuntimeError("initial assembly assumptions changed before the operation lock was acquired")
        target = locked["plan"]["target"]
        with _service_authority():
            complete = assemble_initial_activation(
                InstallationLayout(root),
                InitialAssemblyRequest(
                    core_commit=target["core_commit"],
                    core_git_tree=target["core_git_tree"],
                    application_revision_identity=target["application_revision_identity"],
                    python_environment_identity=target["python_environment_identity"],
                    household_deployment_revision=target["household_deployment_revision"],
                    configuration_root=target["configuration_root"],
                ),
            )
        result = {
            "format": OUTPUT_FORMAT,
            "command": "assemble",
            "status": "staged",
            "mutation_performed": True,
            "plan_identity": approved_plan,
            "activation_id": complete.activation_id,
            "configuration_activation_id": complete.record["configuration_activation_identity"],
            "selection": "staged",
            "active_selection_created": False,
            "approved_selection_created": False,
            "service_modified": False,
            "service_started": False,
        }
        evidence_path = _write_operation_evidence(root, "assembly-results", approved_plan, result)
    return {**result, "evidence_path": str(evidence_path)}


@contextmanager
def _service_authority():
    """Run only Oracle-owned publication with the persistent service identity."""

    account = pwd.getpwnam(SERVICE_ACCOUNT)
    group = grp.getgrnam(SERVICE_GROUP)
    original_euid = os.geteuid()
    original_egid = os.getegid()
    if original_euid != 0:
        raise RuntimeError("service-authority transition requires elevated maintenance authority")
    os.setegid(group.gr_gid)
    os.seteuid(account.pw_uid)
    try:
        yield
    finally:
        os.seteuid(original_euid)
        os.setegid(original_egid)


def _write_operation_evidence(
    root: Path,
    category: str,
    plan_identity: str,
    result: dict[str, object],
) -> Path:
    directory = root / "state" / "installation" / category
    directory.mkdir(mode=0o750, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("installation evidence directory is unsafe")
    service_gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    os.chown(directory, 0, service_gid)
    directory.chmod(0o750)
    destination = directory / f"{plan_identity.rsplit(':', 1)[-1]}.json"
    content = _json_bytes(result)
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != content:
            raise RuntimeError("existing operation evidence conflicts with the approved plan")
        return destination
    temporary = directory / f".{destination.name}.tmp-{os.getpid()}"
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o440)
    os.chown(temporary, 0, service_gid)
    os.replace(temporary, destination)
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return destination


def build_service_install_preflight(*, root: Path = STANDARD_ROOT) -> dict[str, object]:
    blockers: list[dict[str, str]] = []
    plan = None
    active_state = _systemctl_property("is-active", "oracle-brain.service")
    enabled_state = _systemctl_property("is-enabled", "oracle-brain.service")
    if active_state == "active":
        blockers.append({"code": "standard_service_already_active", "detail": "oracle-brain.service"})
    if enabled_state == "enabled":
        blockers.append({"code": "standard_service_already_enabled", "detail": "oracle-brain.service"})
    try:
        exact = build_systemd_install_plan(InstallationLayout(root))
    except (OSError, ValueError, RuntimeError) as exc:
        blockers.append({"code": "systemd_install_plan_invalid", "detail": str(exc)})
    else:
        basis = {
            "format": PLAN_FORMAT,
            "operation": "install-standard-systemd-unit",
            "unit_plan_identity": exact.identity,
            "installation_root": str(root),
            "source": str(exact.source),
            "destination": str(exact.destination),
            "service_definition_identity": exact.service_definition_identity,
            "disposition": exact.disposition,
            "current_systemd_state": {"active": active_state, "enabled": enabled_state},
            "mutations": ["publish or reuse the exact root-owned unit", "run systemd daemon-reload"],
            "excluded": ["systemd enable", "service start", "active selection", "health claim"],
        }
        plan = {
            **basis,
            "identity": "oracle-operation-plan-v1:sha256:" + hashlib.sha256(_json_bytes(basis)).hexdigest(),
        }
    return {
        "format": OUTPUT_FORMAT,
        "command": "service-plan",
        "status": "blocked" if blockers else "ready",
        "mutation_performed": False,
        "plan": plan,
        "blockers": blockers,
    }


def _systemctl_property(operation: str, unit: str) -> str:
    try:
        completed = subprocess.run(
            ["systemctl", operation, unit],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    value = completed.stdout.strip()
    return value or ("not-found" if completed.returncode else "unknown")


def execute_service_install(
    approved_plan: str,
    *,
    root: Path = STANDARD_ROOT,
    lock_path: Path = MAINTENANCE_LOCK,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("systemd unit installation requires an explicitly elevated oracle-admin invocation")
    preflight = build_service_install_preflight(root=root)
    if preflight["status"] != "ready" or preflight["plan"]["identity"] != approved_plan:
        raise RuntimeError("systemd installation plan is blocked, stale, or unapproved")
    with _maintenance_lock(lock_path):
        locked = build_service_install_preflight(root=root)
        if locked["status"] != "ready" or locked["plan"]["identity"] != approved_plan:
            raise RuntimeError("systemd installation assumptions changed before the operation lock was acquired")
        result = {
            "format": OUTPUT_FORMAT,
            "command": "service-install",
            "status": "installed",
            "mutation_performed": True,
            **install_systemd_unit(build_systemd_install_plan(InstallationLayout(root))),
        }
        evidence_path = _write_operation_evidence(root, "service-install-results", approved_plan, result)
    return {**result, "evidence_path": str(evidence_path)}


def build_activation_preflight(*, root: Path = STANDARD_ROOT) -> dict[str, object]:
    blockers: list[dict[str, str]] = []
    plan = None
    try:
        plan = build_initial_activation_plan(InstallationLayout(root))
    except (OSError, ValueError, RuntimeError) as exc:
        blockers.append({"code": "initial_activation_plan_invalid", "detail": str(exc)})
    return {
        "format": OUTPUT_FORMAT,
        "command": "activate-plan",
        "status": "blocked" if blockers else "ready",
        "mutation_performed": False,
        "plan": plan,
        "blockers": blockers,
    }


def _http_json(url: str, *, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else _json_bytes(payload)
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - fixed loopback URL
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"Oracle verification returned a non-object response: {url}")
    return value


def _http_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - fixed loopback URL
        return response.read().decode("utf-8")


def verify_initial_runtime(
    expected_configuration_activation_id: str,
    *,
    base_url: str = "http://127.0.0.1:8011",
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.5,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "Oracle did not become ready."
    while True:
        try:
            if _systemctl_property("is-active", "oracle-brain.service") != "active":
                raise RuntimeError("systemd service is not active")
            health = _http_json(base_url + "/health")
            if health.get("status") != "ok" or health.get("service") != "oracle-brain":
                raise RuntimeError("Oracle health is not ok")
            config = _http_json(base_url + "/health/config")
            applied = config.get("configuration")
            generation = applied.get("applied_generation") if isinstance(applied, dict) else None
            if (
                config.get("ok") is not True
                or not isinstance(applied, dict)
                or applied.get("mode") != "canonical"
                or not isinstance(generation, dict)
                or generation.get("activation_generation_id") != expected_configuration_activation_id
            ):
                raise RuntimeError("Oracle configuration readiness or identity is incorrect")
            command = _http_json(
                base_url + "/command",
                payload={"text": "what time is it", "source": "stage4-install-verifier", "session_id": "stage4-install-verifier"},
            )
            route = command.get("route")
            dispatch = command.get("dispatch")
            result = dispatch.get("result") if isinstance(dispatch, dict) else None
            if (
                not isinstance(route, dict)
                or route.get("target") != "system"
                or not isinstance(dispatch, dict)
                or dispatch.get("status") != "executed"
                or not isinstance(result, dict)
                or result.get("action") != "current_time"
                or not isinstance(command.get("reply_text"), str)
                or not command["reply_text"].strip()
            ):
                raise RuntimeError("Provider-free deterministic interaction failed")
            ui_results = {
                "house_ui": bool(_http_text(base_url + "/ui/").strip()),
                "system_ui": bool(_http_text(base_url + "/admin/").strip()),
                "satellite_ui": bool(_http_text(base_url + "/ui/satellite").strip()),
            }
            if not all(ui_results.values()):
                raise RuntimeError("One or more standard web surfaces did not load")
            return {
                "passed": True,
                "systemd_active": True,
                "readiness": True,
                "health": True,
                "configuration_identity": True,
                "deterministic_interaction": True,
                **ui_results,
            }
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise RuntimeError("Initial Oracle activation verification failed: " + last_error)
        time.sleep(poll_seconds)


def execute_initial_activation(
    approved_plan: str,
    *,
    root: Path = STANDARD_ROOT,
    lock_path: Path = MAINTENANCE_LOCK,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("initial activation requires an explicitly elevated oracle-admin invocation")
    preflight = build_activation_preflight(root=root)
    if preflight["status"] != "ready" or preflight["plan"]["identity"] != approved_plan:
        raise RuntimeError("initial activation plan is blocked, stale, or unapproved")
    prepared = False
    with _maintenance_lock(lock_path):
        locked = build_activation_preflight(root=root)
        if locked["status"] != "ready" or locked["plan"]["identity"] != approved_plan:
            raise RuntimeError("initial activation assumptions changed before the operation lock was acquired")
        layout = InstallationLayout(root)
        try:
            with _service_authority():
                transaction = prepare_initial_activation(layout, locked["plan"])
            prepared = True
            subprocess.run(["systemctl", "enable", "oracle-brain.service"], check=True)
            subprocess.run(["systemctl", "start", "oracle-brain.service"], check=True)
            with _service_authority():
                mark_initial_service_started(layout)
            candidate = load_selected_activation(layout)
            verification = verify_initial_runtime(
                str(candidate.record["configuration_activation_identity"])
            )
            with _service_authority():
                mark_initial_verification_passed(layout, verification)
                final = finalize_initial_activation(layout)
            result = {
                "format": OUTPUT_FORMAT,
                "command": "activate",
                "status": "verified",
                "mutation_performed": True,
                "plan_identity": approved_plan,
                "transaction_id": transaction["transaction_id"],
                "activation_id": final["candidate_activation_id"],
                "verification": verification,
                "systemd_enabled": True,
                "service_started": True,
                "approved": True,
                "previous_known_good": True,
            }
        except BaseException as exc:
            subprocess.run(["systemctl", "stop", "oracle-brain.service"], check=False)
            subprocess.run(["systemctl", "disable", "oracle-brain.service"], check=False)
            if not prepared:
                try:
                    with _service_authority():
                        load_initial_activation_transaction(layout)
                except (OSError, RuntimeError, ValueError):
                    pass
                else:
                    prepared = True
            if prepared:
                with _service_authority():
                    failed = fail_initial_activation(layout, reason=type(exc).__name__)
                result = {
                    "format": OUTPUT_FORMAT,
                    "command": "activate",
                    "status": "recovered_failed",
                    "mutation_performed": True,
                    "plan_identity": approved_plan,
                    "transaction_id": failed["transaction_id"],
                    "activation_id": failed["candidate_activation_id"],
                    "verification": failed["verification"],
                    "systemd_enabled": False,
                    "service_started": False,
                    "approved": False,
                    "previous_known_good": False,
                }
            else:
                raise
        evidence_path = _write_operation_evidence(root, "activation-results", approved_plan, result)
    return {**result, "evidence_path": str(evidence_path)}


def recover_initial_activation(
    *,
    root: Path = STANDARD_ROOT,
    lock_path: Path = MAINTENANCE_LOCK,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise RuntimeError("initial activation recovery requires elevated maintenance authority")
    with _maintenance_lock(lock_path):
        layout = InstallationLayout(root)
        with _service_authority():
            transaction = load_initial_activation_transaction(layout)
        if transaction["state"] == "verification_passed":
            with _service_authority():
                result = finalize_initial_activation(layout)
            outcome = "completed_verified"
        else:
            subprocess.run(["systemctl", "stop", "oracle-brain.service"], check=False)
            subprocess.run(["systemctl", "disable", "oracle-brain.service"], check=False)
            with _service_authority():
                result = fail_initial_activation(layout, reason="interrupted_operation_recovery")
            outcome = "restored_staged_inactive"
    return {
        "format": OUTPUT_FORMAT,
        "command": "activate-recover",
        "status": outcome,
        "mutation_performed": True,
        "transaction_id": result["transaction_id"],
        "activation_id": result["candidate_activation_id"],
    }


def _human(result: dict[str, object]) -> str:
    platform_result = result["platform"]
    artifacts = result["artifacts"]
    plan = result["plan"]
    lines = [
        f"Oracle installation preflight: {result['status']}",
        (
            "Platform: "
            f"{platform_result['os']['pretty_name'] or platform_result['os']['id']} / "
            f"{platform_result['architecture']} ({platform_result['support_status']})"
        ),
        f"Python: {platform_result['python']['implementation']} {platform_result['python']['version']}",
        f"Installation root: {platform_result['installation_root']['path']}",
        f"Core archive SHA-256: {artifacts['archives']['core']['sha256'] or 'unavailable'}",
        f"Household archive SHA-256: {artifacts['archives']['household']['sha256'] or 'unavailable'}",
        f"Plan: {plan['identity']}",
        "No mutation performed.",
    ]
    if platform_result["findings"]:
        lines.append("Findings:")
        lines.extend(f"- {item['code']}: {item['detail']}" for item in platform_result["findings"])
    if result["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {item['code']}: {item['detail']}" for item in result["blockers"])
    return "\n".join(lines)


def _human_staging(result: dict[str, object]) -> str:
    return "\n".join(
        [
            f"Oracle protected staging: {result['status']}",
            f"Plan: {result['plan_identity']}",
            f"Application: {result['components']['application_revision_identity']}",
            f"Deployment: {result['components']['household_deployment_revision']}",
            f"Environment: {result['environment']['environment_identity']}",
            f"Evidence: {result['evidence_path']}",
            "No activation, service, or selection change performed.",
        ]
    )


def _human_assembly(result: dict[str, object]) -> str:
    return "\n".join(
        [
            f"Oracle initial assembly: {result['status']}",
            f"Plan: {result['plan_identity']}",
            f"Activation: {result['activation_id']}",
            f"Configuration activation: {result['configuration_activation_id']}",
            "Selected as staged only; no service or active-selection change performed.",
        ]
    )


def _human_assembly_plan(result: dict[str, object]) -> str:
    lines = [
        f"Oracle initial assembly plan: {result['status']}",
        f"Plan: {result['plan']['identity']}",
        f"Installation root: {result['plan']['installation_root']}",
        "No mutation performed.",
    ]
    if result["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {item['code']}: {item['detail']}" for item in result["blockers"])
    return "\n".join(lines)


def _human_simple_plan(result: dict[str, object]) -> str:
    lines = [f"Oracle {result['command']}: {result['status']}", "No mutation performed."]
    if result["plan"] is not None:
        lines.insert(1, f"Plan: {result['plan']['identity']}")
    if result["blockers"]:
        lines.append("Blockers:")
        lines.extend(f"- {item['code']}: {item['detail']}" for item in result["blockers"])
    return "\n".join(lines)


def _human_service_install(result: dict[str, object]) -> str:
    return "\n".join(
        [
            f"Oracle systemd unit installation: {result['status']}",
            f"Plan: {result['plan_identity']}",
            f"Unit: {result['unit']}",
            f"Disposition: {result['disposition']}",
            "Systemd reloaded; unit not enabled or started.",
        ]
    )


def _human_activation(result: dict[str, object]) -> str:
    lines = [
        f"Oracle initial activation: {result['status']}",
        f"Activation: {result['activation_id']}",
    ]
    if result.get("plan_identity") is not None:
        lines.append(f"Plan: {result['plan_identity']}")
    lines.append(
        "Candidate is approved and known-good."
        if result["status"] in {"verified", "completed_verified"}
        else "Candidate remains staged and inactive; service is disabled."
    )
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--json", action="store_true", help="Emit stable schema-governed machine output")
    commands = root.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight", help="Inspect a host and exact local artifact pair without mutation")
    preflight.add_argument("--core-artifact", type=Path, required=True)
    preflight.add_argument("--household-artifact", type=Path, required=True)
    stage_plan = commands.add_parser("stage-plan", help="Plan protected component staging without mutation")
    stage_plan.add_argument("--core-artifact", type=Path, required=True)
    stage_plan.add_argument("--household-artifact", type=Path, required=True)
    stage = commands.add_parser("stage", help="Apply one exact approved protected-staging plan")
    stage.add_argument("--core-artifact", type=Path, required=True)
    stage.add_argument("--household-artifact", type=Path, required=True)
    stage.add_argument("--approved-plan", required=True)
    stage.add_argument("--allow-unsupported-platform", action="store_true")
    assemble_plan = commands.add_parser("assemble-plan", help="Plan initial staged activation assembly without mutation")
    assemble_plan.add_argument("--core-artifact", type=Path, required=True)
    assemble_plan.add_argument("--household-artifact", type=Path, required=True)
    assemble_plan.add_argument("--environment-identity", required=True)
    assemble = commands.add_parser("assemble", help="Create one initial complete activation and select it as staged")
    assemble.add_argument("--core-artifact", type=Path, required=True)
    assemble.add_argument("--household-artifact", type=Path, required=True)
    assemble.add_argument("--environment-identity", required=True)
    assemble.add_argument("--approved-plan", required=True)
    commands.add_parser("service-plan", help="Plan fixed standard systemd-unit installation without mutation")
    service_install = commands.add_parser("service-install", help="Install the exact approved fixed systemd unit")
    service_install.add_argument("--approved-plan", required=True)
    commands.add_parser("activate-plan", help="Plan the first active selection and verification lifecycle")
    activate = commands.add_parser("activate", help="Execute one approved initial activation and verification plan")
    activate.add_argument("--approved-plan", required=True)
    commands.add_parser("activate-recover", help="Recover one interrupted initial activation transaction")
    return root


_BOOTSTRAP_COMMANDS = frozenset({"preflight", "stage-plan", "stage"})
_ASSEMBLY_COMMANDS = frozenset({"assemble-plan", "assemble"})


def _managed_environment_for_command(args: argparse.Namespace, *, root: Path = STANDARD_ROOT) -> Path | None:
    if args.command in _BOOTSTRAP_COMMANDS:
        return None
    if args.command in _ASSEMBLY_COMMANDS:
        name = environment_directory_name(args.environment_identity)
        environment = root / "environments" / name
    else:
        selected = load_selected_activation(InstallationLayout(root), "staged")
        environment = (selected.directory / "environment").resolve(strict=True)
    try:
        expected_parent = (root / "environments").resolve(strict=True)
        resolved = environment.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("the selected immutable Python environment is unavailable") from exc
    if resolved.parent != expected_parent or resolved != expected_parent / resolved.name:
        raise RuntimeError("the selected immutable Python environment escapes managed storage")
    return resolved


def _reexecute_post_staging_command(
    args: argparse.Namespace,
    argv: list[str],
    *,
    root: Path = STANDARD_ROOT,
) -> None:
    environment = _managed_environment_for_command(args, root=root)
    if environment is None or Path(sys.prefix).resolve() == environment:
        return
    interpreter = environment / "bin" / "python"
    if not interpreter.is_file():
        raise RuntimeError("the selected immutable Python environment interpreter is unavailable")
    os.execv(
        str(interpreter),
        [str(interpreter), str(Path(__file__).resolve()), *argv],
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(raw_argv)
    try:
        _reexecute_post_staging_command(args, raw_argv)
        if args.command == "preflight":
            result = build_install_preflight(args.core_artifact, args.household_artifact)
        elif args.command == "stage-plan":
            result = build_staging_preflight(args.core_artifact, args.household_artifact)
        elif args.command == "stage":
            result = execute_staging(
                args.core_artifact,
                args.household_artifact,
                args.approved_plan,
                allow_unsupported_platform=args.allow_unsupported_platform,
            )
        elif args.command == "assemble-plan":
            result = build_initial_assembly_plan(
                args.core_artifact, args.household_artifact, args.environment_identity
            )
        elif args.command == "assemble":
            result = execute_initial_assembly(
                args.core_artifact,
                args.household_artifact,
                args.environment_identity,
                args.approved_plan,
            )
        elif args.command == "service-plan":
            result = build_service_install_preflight()
        elif args.command == "service-install":
            result = execute_service_install(args.approved_plan)
        elif args.command == "activate-plan":
            result = build_activation_preflight()
        elif args.command == "activate":
            result = execute_initial_activation(args.approved_plan)
        else:
            result = recover_initial_activation()
    except (ArtifactError, InstallationStagingError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        failure = {
            "format": OUTPUT_FORMAT,
            "command": args.command,
            "status": "failed",
            "mutation_performed": False if args.command not in {"stage", "assemble", "service-install", "activate", "activate-recover"} else None,
            "mutation_may_have_occurred": args.command in {"stage", "assemble", "service-install", "activate", "activate-recover"},
            "error": str(exc),
        }
        print(json.dumps(failure, indent=2, sort_keys=True) if args.json else f"Oracle {args.command}: failed\n{exc}")
        return 2
    print(
        json.dumps(result, indent=2, sort_keys=True)
        if args.json
        else (
            _human_staging(result)
            if args.command == "stage"
            else _human_activation(result)
            if args.command in {"activate", "activate-recover"}
            else _human_service_install(result)
            if args.command == "service-install"
            else _human_assembly(result)
            if args.command == "assemble"
            else _human_assembly_plan(result)
            if args.command == "assemble-plan"
            else _human_simple_plan(result)
            if args.command in {"service-plan", "activate-plan"}
            else _human(result)
        )
    )
    return 0 if result["status"] in {"ready", "staged", "installed", "verified", "completed_verified"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
