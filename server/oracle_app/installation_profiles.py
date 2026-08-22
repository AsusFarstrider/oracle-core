"""Closed production installation profiles for the standard Brain lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class InstallationProfile:
    profile_id: str
    dependency_lock: Path
    service_definition: Path
    initial_runtime_compatibility_required: bool = False


PROFILES = {
    "minimal-brain": InstallationProfile(
        profile_id="minimal-brain",
        dependency_lock=Path("server/requirements.lock"),
        service_definition=Path("scripts/oracle-brain-standard.service"),
    ),
    "full-production-brain": InstallationProfile(
        profile_id="full-production-brain",
        dependency_lock=Path("server/requirements-full-production.lock"),
        service_definition=Path("scripts/oracle-brain-full-production.service"),
        initial_runtime_compatibility_required=True,
    ),
}


def require_single_profile(values: Sequence[object]) -> InstallationProfile:
    """Resolve the one closed profile declared by a household deployment."""

    if len(values) != 1 or not isinstance(values[0], str) or values[0] not in PROFILES:
        raise ValueError("household deployment must select exactly one supported installation profile")
    return PROFILES[values[0]]
