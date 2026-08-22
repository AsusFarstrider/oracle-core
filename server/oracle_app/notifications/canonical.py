from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from oracle_app.alerts import list_due_alerts
from oracle_app.configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from oracle_app.configuration.notification_runtime_settings import (
    NotificationRuntimeSettings,
    NotificationTypeRuntimeSettings,
)
from oracle_app.configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from oracle_app.provider_bridges.home_assistant import HomeAssistantBridge

from .audit import record_notification_event
from .channels.external import reserve_canonical_external_deliveries
from .channels.satellite_announcement import (
    dispatch_satellite_announcement_values,
    reserve_suppressed_satellite_receipts,
)
from .errors import (
    NotificationContextNotSupportedError,
    NotificationDefinitionNotFoundError,
    NotificationRequestError,
    NotificationSuppressionUnavailableError,
)
from .policy import SuppressionStatus


@dataclass(frozen=True)
class CanonicalNotificationExecution:
    """Typed notification capability bound to one immutable effective revision."""

    settings: NotificationRuntimeSettings | None
    home_assistant: HomeAssistantRuntimeSettings | None
    satellites: SatelliteFleetRuntimeSettings

    @property
    def config_revision(self) -> str:
        return self.satellites.config_revision

    def submit(
        self,
        notification_type: str,
        occurrence_id: str,
        *,
        context: dict[str, Any] | None = None,
        caller: str = "oracle",
        correlation_id: str = "",
    ) -> dict[str, Any]:
        clean_occurrence_id = str(occurrence_id or "").strip()
        clean_caller = str(caller or "").strip().lower()
        if not clean_occurrence_id:
            raise NotificationRequestError("occurrence_id is required")
        if not clean_caller:
            raise NotificationRequestError("caller is required")
        if context:
            raise NotificationContextNotSupportedError(
                "Current notification definitions do not declare context fields."
            )

        runtime = self._notification_type(notification_type)
        clean_type = runtime.definition.id
        suppression = self.evaluate_suppression(runtime)
        if suppression == "unavailable":
            record_notification_event(
                notification_type=clean_type,
                occurrence_id=clean_occurrence_id,
                status="suppression_unavailable",
                caller=clean_caller,
                correlation_id=correlation_id,
            )
            raise NotificationSuppressionUnavailableError(clean_type)
        if suppression == "active":
            created = reserve_suppressed_satellite_receipts(
                notification_type=clean_type,
                occurrence_id=clean_occurrence_id,
                targets=self.satellite_targets(runtime),
                expires_at=_now_local()
                + timedelta(seconds=runtime.definition.delivery_ttl_seconds),
            )
            status = "suppressed" if created else "duplicate"
            record_notification_event(
                notification_type=clean_type,
                occurrence_id=clean_occurrence_id,
                status=status,
                caller=clean_caller,
                correlation_id=correlation_id,
            )
            return _result(clean_type, clean_occurrence_id, status=status)

        definition = runtime.definition
        satellite_result = dispatch_satellite_announcement_values(
            notification_type=clean_type,
            occurrence_id=clean_occurrence_id,
            targets=self.satellite_targets(runtime),
            message=definition.message,
            audio_policy=definition.audio_policy,
            delivery_ttl_seconds=definition.delivery_ttl_seconds,
            caller=clean_caller,
            now=_now_local(),
        )
        satellite_status = str(satellite_result["status"])
        channel_results: dict[str, dict[str, Any]] = {
            "satellite_announcement": {
                "channel": "satellite_announcement",
                "status": satellite_status,
                "queued_targets": list(satellite_result["queued_targets"]),
                "target_count": int(satellite_result["target_count"]),
            }
        }
        status = satellite_status
        external = definition.external_delivery
        if external is not None and external.enabled:
            try:
                external_result = reserve_canonical_external_deliveries(
                    notification_type=runtime,
                    occurrence_id=clean_occurrence_id,
                    correlation_id=correlation_id,
                )
            except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                external_result = {
                    "channel": "external",
                    "status": "failed",
                    "error_class": type(exc).__name__,
                }
                if external.failure_policy == "required":
                    record_notification_event(
                        notification_type=clean_type,
                        occurrence_id=clean_occurrence_id,
                        status="failed",
                        caller=clean_caller,
                        target_count=int(satellite_result["target_count"]),
                        correlation_id=correlation_id,
                    )
                    raise NotificationRequestError(
                        "Required external notification work could not be accepted."
                    ) from exc
                status = "partial"
            else:
                if satellite_status == "duplicate" and external_result["status"] == "queued":
                    status = "queued"
            channel_results["external"] = external_result
        record_notification_event(
            notification_type=clean_type,
            occurrence_id=clean_occurrence_id,
            status=status,
            caller=clean_caller,
            target_count=int(satellite_result["target_count"]),
            correlation_id=correlation_id,
        )
        return _result(
            clean_type,
            clean_occurrence_id,
            status=status,
            queued_targets=list(satellite_result["queued_targets"]),
            channel_results=channel_results,
        )

    def build_delivery_decisions(self, source: str | None) -> dict[str, str]:
        due = list_due_alerts(source, kind="notification")
        if not due:
            return {}
        status_by_notification: dict[str, SuppressionStatus] = {}
        decisions: dict[str, str] = {}
        now = _now_local()
        for alert in due:
            notification_type = str(alert.metadata.get("notification_id") or "").strip()
            occurrence_id = str(alert.metadata.get("event_id") or "").strip()
            caller = str(alert.metadata.get("caller") or "home_assistant").strip() or "home_assistant"
            if alert.expires_at is not None and alert.expires_at <= now:
                decisions[alert.alert_id] = "suppress"
                record_notification_event(
                    notification_type=notification_type,
                    occurrence_id=occurrence_id,
                    status="expired",
                    caller=caller,
                    target_source=str(alert.source or ""),
                )
                continue
            try:
                runtime = self._notification_type(notification_type)
            except NotificationDefinitionNotFoundError:
                decisions[alert.alert_id] = "suppress"
                continue
            if notification_type not in status_by_notification:
                status_by_notification[notification_type] = self.evaluate_suppression(runtime)
            suppression = status_by_notification[notification_type]
            if suppression == "active":
                decisions[alert.alert_id] = "suppress"
                record_notification_event(
                    notification_type=notification_type,
                    occurrence_id=occurrence_id,
                    status="suppressed_before_delivery",
                    caller=caller,
                    target_source=str(alert.source or ""),
                )
            elif suppression == "inactive":
                decisions[alert.alert_id] = "deliver"
            else:
                decisions[alert.alert_id] = "defer"
        return decisions

    def evaluate_suppression(self, runtime: NotificationTypeRuntimeSettings) -> SuppressionStatus:
        mode_ids = runtime.definition.suppressed_by
        if not mode_ids:
            return "inactive"
        home_assistant = self.home_assistant
        if (
            home_assistant is None
            or not home_assistant.enabled
            or home_assistant.base_url is None
            or home_assistant.credential is None
        ):
            return "unavailable"
        bridge = HomeAssistantBridge(
            base_url=home_assistant.base_url,
            token=home_assistant.credential,
            timeout_seconds=home_assistant.timeout_seconds,
        )
        for mode_id in mode_ids:
            mappings = [
                mapping
                for mapping in home_assistant.mappings.values()
                if mapping.kind == "event"
                and mapping.event_type == "mode_state"
                and mapping.subject == mode_id
            ]
            if len(mappings) != 1:
                return "unavailable"
            mapping = mappings[0]
            state = bridge.fetch_entity_state(mapping.entity_id)
            normalized = str((state or {}).get("state") or "").strip().lower()
            if normalized in {"", "unknown", "unavailable"}:
                return "unavailable"
            if normalized == mapping.active_state.strip().lower():
                return "active"
        return "inactive"

    def satellite_targets(self, runtime: NotificationTypeRuntimeSettings) -> tuple[str, ...]:
        target_ids: list[str] = []
        for source_id in runtime.source_audience_ids:
            if self.satellites.satellite_for_source(source_id) is not None:
                target_ids.append(source_id)
        return tuple(dict.fromkeys(target_ids))

    def _notification_type(self, value: str) -> NotificationTypeRuntimeSettings:
        settings = self.settings
        clean_type = str(value or "").strip().lower()
        runtime = None if settings is None or not settings.enabled else settings.notification_type(clean_type)
        if runtime is None:
            raise NotificationDefinitionNotFoundError(clean_type)
        return runtime


def _result(
    notification_type: str,
    occurrence_id: str,
    *,
    status: str,
    queued_targets: list[str] | None = None,
    channel_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "notification_id": notification_type,
        "event_id": occurrence_id,
        "queued_targets": list(queued_targets or []),
        "channel_results": dict(channel_results or {}),
    }


def _now_local() -> datetime:
    from oracle_app.alerts import _now_local as alerts_now_local

    return alerts_now_local()
