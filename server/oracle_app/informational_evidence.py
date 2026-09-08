from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class InformationAvailability(StrEnum):
    """Shared vocabulary; domains retain their own result shapes."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    NO_RESULT = "no_result"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class InformationFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class InformationFailureKind(StrEnum):
    PROVIDER = "provider"
    TIMEOUT = "timeout"
    CONFIGURATION = "configuration"
    CONTRACT = "contract"
    PROGRAMMING = "programming"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InformationSource:
    """Bounded source identity and source-owned time evidence."""

    source_id: str
    source_label: str
    source_type: str
    observed_at: str | None = None
    published_at: str | None = None
    reference: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("source_id", "source_type"):
            value = str(getattr(self, field_name) or "").strip()
            if _IDENTIFIER_RE.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be a bounded identifier")
        label = str(self.source_label or "").strip()
        if not label or len(label) > 256:
            raise ValueError("source_label must be a bounded non-empty string")
        if self.reference is not None and len(str(self.reference)) > 2048:
            raise ValueError("reference must be at most 2048 characters")
        _validate_timestamp("observed_at", self.observed_at)
        _validate_timestamp("published_at", self.published_at)


@dataclass(frozen=True)
class InformationReadMetadata:
    """Composable read metadata, not a universal informational result DTO."""

    availability: InformationAvailability
    freshness: InformationFreshness
    retrieved_at: str
    sources: tuple[InformationSource, ...] = ()
    failure_kind: InformationFailureKind | None = None
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_timestamp("retrieved_at", self.retrieved_at, required=True)
        if len(self.sources) > 16:
            raise ValueError("informational metadata supports at most 16 sources")
        if self.freshness == InformationFreshness.STALE and self.availability not in {
            InformationAvailability.AVAILABLE,
            InformationAvailability.PARTIAL,
        }:
            raise ValueError("stale freshness requires available or partial data")
        if self.availability in {
            InformationAvailability.NO_RESULT,
            InformationAvailability.DISABLED,
            InformationAvailability.UNAVAILABLE,
        } and self.freshness == InformationFreshness.FRESH:
            raise ValueError("freshness cannot claim fresh data when no data is available")
        if self.stale_reason and self.freshness != InformationFreshness.STALE:
            raise ValueError("stale_reason requires stale freshness")


def _validate_timestamp(field_name: str, value: str | None, *, required: bool = False) -> None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise ValueError(f"{field_name} is required")
        return
    if len(text) > 64:
        raise ValueError(f"{field_name} must be a bounded ISO timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
