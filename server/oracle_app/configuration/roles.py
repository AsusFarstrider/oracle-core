from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REQUIRED_ROLE_PATHS = frozenset(
    {
        "bundle.yaml",
        "brain.yaml",
        "access.yaml",
        "household.yaml",
        "satellites.yaml",
    }
)

OPTIONAL_ROLE_PATHS = frozenset(
    {
        "domains/information.yaml",
        "domains/music.yaml",
        "domains/audiobooks.yaml",
        "domains/weather.yaml",
        "domains/calendar.yaml",
        "domains/home-assistant.yaml",
        "domains/notifications.yaml",
        "domains/routines.yaml",
        "domains/network/inventory.yaml",
        "domains/network/policy.yaml",
        "domains/network/adapters.yaml",
    }
)

KNOWN_ROLE_PATHS = REQUIRED_ROLE_PATHS | OPTIONAL_ROLE_PATHS
SECRET_COMPANION_PATH = "secrets.env"


class BundleRoleError(ValueError):
    def __init__(self, code: str, message: str, *, paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.paths = paths


@dataclass(frozen=True)
class BundleRoleInventory:
    root: Path
    role_paths: tuple[str, ...]
    non_authoritative_paths: tuple[str, ...]


def discover_bundle_roles(root: Path) -> BundleRoleInventory:
    root = Path(root)
    if not root.is_dir():
        raise BundleRoleError("config.bundle.root", "Configuration bundle root must be a directory.")

    discovered_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    yaml_paths = {path for path in discovered_files if Path(path).suffix.lower() in {".yaml", ".yml"}}
    unknown_yaml = tuple(sorted(yaml_paths - KNOWN_ROLE_PATHS))
    if unknown_yaml:
        raise BundleRoleError(
            "config.bundle.unknown_role",
            "Unknown YAML configuration role files are forbidden.",
            paths=unknown_yaml,
        )

    missing_required = tuple(sorted(REQUIRED_ROLE_PATHS - discovered_files))
    if missing_required:
        raise BundleRoleError(
            "config.bundle.missing_role",
            "Required configuration role files are missing.",
            paths=missing_required,
        )

    if "domains/network/inventory.yaml" not in discovered_files:
        orphan_network_roles = tuple(
            path
            for path in ("domains/network/policy.yaml", "domains/network/adapters.yaml")
            if path in discovered_files
        )
        if orphan_network_roles:
            raise BundleRoleError(
                "config.bundle.network_anchor",
                "Network policy and adapter roles require network inventory.",
                paths=orphan_network_roles,
            )

    roles = tuple(sorted(discovered_files & KNOWN_ROLE_PATHS))
    non_authoritative = tuple(sorted(discovered_files - KNOWN_ROLE_PATHS - {SECRET_COMPANION_PATH}))
    return BundleRoleInventory(root=root, role_paths=roles, non_authoritative_paths=non_authoritative)
