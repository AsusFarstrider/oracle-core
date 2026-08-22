from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from pydantic import ValidationError

from .models import (
    AccessConfiguration,
    ConfigurationModel,
    HouseholdConfiguration,
    SatellitesConfiguration,
    validate_role,
)
from .roles import BundleRoleError, KNOWN_ROLE_PATHS, SECRET_COMPANION_PATH, discover_bundle_roles
from .validation import BundleValidationError, ConfigurationFinding, validate_cross_file_references
from .yaml_parser import ConfigurationSyntaxError, RestrictedYamlParser


AUTHORED_REVISION_PREFIX = "oracle-authored-v1:sha256:"
_CANDIDATE_ID_PATTERN = re.compile(r"^candidate_[0-9a-f]{32}$")


class AuthoredRevisionConflict(ValueError):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__("Authored configuration changed since the candidate revision was read.")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class AuthoredCandidateSnapshot:
    candidate_id: str
    root: Path
    authored_revision: str
    authored_bytes: Mapping[str, bytes]
    non_authoritative_paths: tuple[str, ...]
    snapshot_findings: tuple[ConfigurationFinding, ...] = ()


@dataclass(frozen=True)
class LoadedBundle:
    candidate_id: str
    root: Path
    authored_revision: str
    authored_bytes: Mapping[str, bytes]
    roles: Mapping[str, ConfigurationModel]
    non_authoritative_paths: tuple[str, ...]

    @property
    def household(self) -> HouseholdConfiguration:
        return self.roles["household.yaml"]  # type: ignore[return-value]

    @property
    def access(self) -> AccessConfiguration:
        return self.roles["access.yaml"]  # type: ignore[return-value]

    @property
    def satellites(self) -> SatellitesConfiguration:
        return self.roles["satellites.yaml"]  # type: ignore[return-value]


def _authored_revision(authored_bytes: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(b"oracle-authored-v1\0")
    for role_path in sorted(authored_bytes):
        path_bytes = role_path.encode("utf-8")
        content = authored_bytes[role_path]
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"{AUTHORED_REVISION_PREFIX}{digest.hexdigest()}"


def _confined_role_target(root: Path, role_path: str) -> Path:
    authored_path = root / role_path
    try:
        target = authored_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BundleValidationError(
            [ConfigurationFinding("config.bundle.role_target", role_path, "", f"Role target cannot be resolved: {exc}")]
        ) from exc
    if not target.is_relative_to(root):
        raise BundleValidationError(
            [ConfigurationFinding("config.bundle.path_escape", role_path, "", "Role target escapes the resolved bundle root.")]
        )
    if not target.is_file():
        raise BundleValidationError(
            [ConfigurationFinding("config.bundle.role_target", role_path, "", "Role target must be a regular file.")]
        )
    return target


def snapshot_candidate(root: Path, *, candidate_id: str | None = None) -> AuthoredCandidateSnapshot:
    resolved_root = Path(root).resolve(strict=True)
    candidate_id = candidate_id or f"candidate_{uuid4().hex}"
    if _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
        raise ValueError("candidate_id must be an opaque path-safe candidate identifier.")

    findings: list[ConfigurationFinding] = []
    try:
        inventory = discover_bundle_roles(resolved_root)
        role_paths = inventory.role_paths
        non_authoritative = inventory.non_authoritative_paths
    except BundleRoleError as exc:
        findings.extend(
            ConfigurationFinding(exc.code, "bundle", path, str(exc))
            for path in (exc.paths or ("",))
        )
        discovered = {
            path.relative_to(resolved_root).as_posix()
            for path in resolved_root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        role_paths = tuple(sorted(discovered & KNOWN_ROLE_PATHS))
        non_authoritative = tuple(sorted(discovered - KNOWN_ROLE_PATHS - {SECRET_COMPANION_PATH}))

    authored_bytes: dict[str, bytes] = {}
    for role_path in role_paths:
        try:
            authored_bytes[role_path] = _confined_role_target(resolved_root, role_path).read_bytes()
        except BundleValidationError as exc:
            findings.extend(exc.findings)
    frozen_bytes = MappingProxyType(authored_bytes)
    return AuthoredCandidateSnapshot(
        candidate_id=candidate_id,
        root=resolved_root,
        authored_revision=_authored_revision(frozen_bytes),
        authored_bytes=frozen_bytes,
        non_authoritative_paths=non_authoritative,
        snapshot_findings=tuple(sorted(findings)),
    )


def assert_authored_revision(snapshot: AuthoredCandidateSnapshot, expected_revision: str) -> None:
    if snapshot.authored_revision != expected_revision:
        raise AuthoredRevisionConflict(expected_revision, snapshot.authored_revision)


def load_bundle_snapshot_structure(snapshot: AuthoredCandidateSnapshot) -> LoadedBundle:
    if snapshot.snapshot_findings:
        raise BundleValidationError(snapshot.snapshot_findings)

    parser = RestrictedYamlParser()
    roles: dict[str, ConfigurationModel] = {}
    findings: list[ConfigurationFinding] = []
    for role_path, content in snapshot.authored_bytes.items():
        try:
            text = content.decode("utf-8")
            parsed = parser.parse(text)
            roles[role_path] = validate_role(role_path, parsed.primitive)
        except UnicodeDecodeError as exc:
            findings.append(ConfigurationFinding("config.yaml.utf8", role_path, "", f"Role is not valid UTF-8: {exc}"))
        except ConfigurationSyntaxError as exc:
            location = "" if exc.line is None else f"line {exc.line}, column {exc.column}"
            findings.append(ConfigurationFinding(exc.code, role_path, location, str(exc)))
        except ValidationError as exc:
            for error in exc.errors(include_url=False):
                path = ".".join(str(part) for part in error["loc"])
                findings.append(ConfigurationFinding("config.schema.invalid", role_path, path, error["msg"]))
    if findings:
        raise BundleValidationError(findings)

    return LoadedBundle(
        candidate_id=snapshot.candidate_id,
        root=snapshot.root,
        authored_revision=snapshot.authored_revision,
        authored_bytes=snapshot.authored_bytes,
        roles=MappingProxyType(roles),
        non_authoritative_paths=snapshot.non_authoritative_paths,
    )


def validate_loaded_bundle(bundle: LoadedBundle) -> tuple[ConfigurationFinding, ...]:
    return validate_cross_file_references(
        household=bundle.household,
        access=bundle.access,
        satellites=bundle.satellites,
        roles=bundle.roles,
    )


def load_bundle_snapshot(snapshot: AuthoredCandidateSnapshot) -> LoadedBundle:
    bundle = load_bundle_snapshot_structure(snapshot)
    findings = validate_loaded_bundle(bundle)
    if findings:
        raise BundleValidationError(findings)
    return bundle


def load_bundle(root: Path) -> LoadedBundle:
    return load_bundle_snapshot(snapshot_candidate(root))
