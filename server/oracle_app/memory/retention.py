from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionPolicy:
    successful_raw_transcript_days: int = 14
    failed_raw_transcript_days: int = 30
    transcript_metadata_days: int = 90
    routine_event_days: int = 90
    warning_event_days: int = 180
    error_event_days: int = 365
    critical_event_days: int = 730
    provider_status_event_days: int = 180
    lifecycle_event_days: int = 365
    snapshot_hourly_days: int = 14
    snapshot_daily_days: int = 90
    cache_history_days: int = 30
    rollup_days: int = 365
    evidence_ref_days: int = 90


DEFAULT_RETENTION_POLICY = RetentionPolicy()


def describe_default_policy() -> dict[str, int]:
    return {
        field: int(getattr(DEFAULT_RETENTION_POLICY, field))
        for field in DEFAULT_RETENTION_POLICY.__dataclass_fields__
    }
