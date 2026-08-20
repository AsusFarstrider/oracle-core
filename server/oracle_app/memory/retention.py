from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetentionPolicy:
    successful_raw_transcript_days: int
    failed_raw_transcript_days: int
    transcript_metadata_days: int
    routine_event_days: int
    warning_event_days: int
    error_event_days: int
    critical_event_days: int
    provider_status_event_days: int
    lifecycle_event_days: int
    session_metadata_days: int
    orchestration_history_days: int
    alert_terminal_days: int
    notification_accepted_days: int
    notification_suppressed_days: int
    notification_failed_days: int
    suggestion_raw_evidence_days: int
    suggestion_exchange_days: int
    suggestion_envelope_days: int
    suggestion_mock_days: int


def retention_policy_from_configuration(
    configuration: Any,
) -> RetentionPolicy:
    return RetentionPolicy(**configuration.model_dump())
