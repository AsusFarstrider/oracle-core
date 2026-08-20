from __future__ import annotations


EVENT_TAXONOMY: dict[str, tuple[str, ...]] = {
    "system.lifecycle": (
        "server_started",
        "server_stopped",
        "application_startup_complete",
        "application_shutdown_complete",
    ),
    "system.config": (
        "config_warning",
        "deprecated_config_source",
        "missing_required_config",
    ),
    "routing": (
        "routing_failed",
        "fallback_router_called",
        "fallback_router_unnecessary_candidate",
        "deterministic_parser_miss",
    ),
    "command": (
        "command_attempted",
        "command_succeeded",
        "command_failed",
        "command_rejected",
    ),
    "provider.status": (
        "provider_available",
        "provider_unavailable",
        "provider_degraded",
        "provider_recovered",
    ),
    "satellite.status": (
        "satellite_started",
        "satellite_stopped",
        "satellite_error",
        "wake_detected",
        "audio_capture_failed",
        "stt_upload_failed",
        "tts_playback_failed",
    ),
    "transcript": (
        "transcript_received",
        "transcript_normalized",
        "transcript_rejected_as_noise",
        "transcript_low_confidence",
        "transcript_failed",
    ),
    "external.home_assistant": (
        "ha_snapshot_recorded",
        "ha_entity_unavailable",
        "ha_entity_recovered",
        "ha_cache_refresh_failed",
        "ha_notification_received",
        "home_automation_event_received",
    ),
    "external.librenms": (
        "librenms_snapshot_recorded",
        "librenms_alert_active",
        "librenms_alert_recovered",
        "librenms_device_down",
        "librenms_device_recovered",
    ),
    "network.control": (
        "network_control_dry_run",
        "network_control_started",
        "network_control_confirm",
    ),
    "orchestration": (
        "orchestration_recovery_started",
        "orchestration_recovery_completed",
        "orchestration_recovery_interrupted",
        "orchestration_routine_started",
        "orchestration_routine_waiting",
        "orchestration_routine_resumed",
        "orchestration_routine_completed",
        "orchestration_routine_canceled",
        "orchestration_routine_interrupted",
    ),
    "notification.lifecycle": (
        "notification_emitted",
    ),
    "memory": (
        "retention_pruned",
    ),
}

EVENT_TYPE_TO_CATEGORY: dict[str, str] = {
    event_type: category
    for category, event_types in EVENT_TAXONOMY.items()
    for event_type in event_types
}


def validate_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip()
    if normalized not in EVENT_TYPE_TO_CATEGORY:
        raise ValueError(f"Unknown Oracle Memory event type: {event_type!r}")
    return normalized


def category_for_event_type(event_type: str) -> str:
    return EVENT_TYPE_TO_CATEGORY[validate_event_type(event_type)]
