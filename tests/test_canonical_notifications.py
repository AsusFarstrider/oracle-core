from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from oracle_app.configuration.domain_models import (
    HomeAssistantEventMapping,
    NotificationType,
)
from oracle_app.configuration.notification_runtime_settings import NotificationTypeRuntimeSettings
from oracle_app.notifications.canonical import CanonicalNotificationExecution
from oracle_app.notifications.external_worker import process_due_external_deliveries
from oracle_app.notifications.receipts import reserve_notification_delivery


class _CanonicalBridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_to(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "accepted"}


class CanonicalNotificationExecutionTests(unittest.TestCase):
    def test_submission_uses_typed_policy_and_satellite_source_audience(self) -> None:
        execution, runtime = self._execution(suppressed_by=[])
        with (
            patch(
                "oracle_app.notifications.canonical.dispatch_satellite_announcement_values",
                return_value={
                    "status": "queued",
                    "queued_targets": ["living_room_voice"],
                    "target_count": 1,
                },
            ) as dispatch,
            patch("oracle_app.notifications.canonical.record_notification_event"),
            patch("oracle_app.notifications.catalog.get_notification_settings") as legacy_catalog,
            patch("oracle_app.notifications.policy.get_home_assistant_settings") as legacy_ha,
        ):
            result = execution.submit("door_open", "event-1", caller="home_automation_runbook")

        self.assertEqual(result["queued_targets"], ["living_room_voice"])
        self.assertEqual(
            dispatch.call_args.kwargs["targets"],
            ("living_room_voice",),
        )
        self.assertEqual(dispatch.call_args.kwargs["message"], runtime.definition.message)
        legacy_catalog.assert_not_called()
        legacy_ha.assert_not_called()

    def test_suppression_uses_exact_canonical_mode_mapping(self) -> None:
        execution, runtime = self._execution(suppressed_by=["quiet"])
        bridge = SimpleNamespace(fetch_entity_state=lambda entity_id: {"state": "on"})
        with (
            patch("oracle_app.notifications.canonical.HomeAssistantBridge", return_value=bridge) as bridge_class,
            patch("oracle_app.notifications.policy.get_home_assistant_settings") as legacy_ha,
        ):
            status = execution.evaluate_suppression(runtime)

        self.assertEqual(status, "active")
        bridge_class.assert_called_once_with(
            base_url="http://ha.invalid",
            token="token",
            timeout_seconds=8,
        )
        legacy_ha.assert_not_called()

    def test_external_worker_resolves_current_typed_definition_and_provider(self) -> None:
        execution, runtime = self._execution(external=True, suppressed_by=[])
        bridge = _CanonicalBridge()
        now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "memory.sqlite3"
            reserve_notification_delivery(
                notification_type="door_open",
                occurrence_id="run-1:1",
                correlation_id="run-1",
                channel="external",
                destination_id="primary",
                provider="apprise",
                max_attempts=3,
                retry_seconds=30,
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                failure_policy="best_effort",
                repeat_policy="first_per_correlation",
                db_path=db_path,
            )
            with (
                patch("oracle_app.notifications.external_worker.get_notification_settings") as legacy_notifications,
                patch("oracle_app.notifications.external_worker.get_apprise_settings") as legacy_apprise,
            ):
                outcomes = process_due_external_deliveries(
                    now=now,
                    db_path=db_path,
                    bridge=bridge,  # type: ignore[arg-type]
                    canonical_execution=execution,
                )

        self.assertEqual(outcomes[0]["status"], "accepted")
        self.assertEqual(bridge.calls[0]["base_url"], "http://apprise.invalid")
        self.assertEqual(bridge.calls[0]["config_key"], "oracle")
        self.assertEqual(bridge.calls[0]["body"], runtime.definition.message)
        legacy_notifications.assert_not_called()
        legacy_apprise.assert_not_called()

    def test_external_worker_rejects_mixed_canonical_and_legacy_settings(self) -> None:
        execution, _runtime = self._execution(suppressed_by=[])

        with self.assertRaisesRegex(ValueError, "cannot accept legacy settings"):
            process_due_external_deliveries(
                canonical_execution=execution,
                notification_settings={},
            )

    @staticmethod
    def _execution(
        *,
        suppressed_by: list[str],
        external: bool = False,
    ) -> tuple[CanonicalNotificationExecution, NotificationTypeRuntimeSettings]:
        external_policy = None
        groups = {}
        if external:
            external_policy = {
                "enabled": True,
                "recipient_groups": ["primary"],
                "delivery_ttl_seconds": 300,
                "max_attempts": 3,
                "retry_seconds": 30,
                "quiet_hours_policy": "bypass",
                "repeat_policy": "first_per_correlation",
                "failure_policy": "best_effort",
            }
            provider = SimpleNamespace(
                provider_id="apprise_primary",
                type="apprise",
                resolved_base_url="http://apprise.invalid",
                timeout_seconds=8,
            )
            group = SimpleNamespace(
                definition=SimpleNamespace(
                    id="primary",
                    enabled=True,
                    configuration_key="oracle",
                    routing_tag="primary",
                ),
                provider=provider,
            )
            groups = {"primary": group}
        definition = NotificationType.model_validate(
            {
                "id": "door_open",
                "enabled": True,
                "message": "The door is open.",
                "audience": [
                    {"type": "source", "id": "living_room_voice"},
                ],
                "suppressed_by": suppressed_by,
                "delivery_ttl_seconds": 90,
                "audio_policy": "pause_resume",
                **({} if external_policy is None else {"external_delivery": external_policy}),
            }
        )
        runtime = NotificationTypeRuntimeSettings(
            definition=definition,
            external_recipient_groups=MappingProxyType(groups),
        )
        settings = SimpleNamespace(
            enabled=True,
            config_revision="revision-1",
            notification_type=lambda notification_id: runtime if notification_id == "door_open" else None,
        )
        fleet = SimpleNamespace(
            config_revision="revision-1",
            satellite_for_source=lambda source_id: (
                SimpleNamespace(source_id=source_id)
                if source_id == "living_room_voice"
                else None
            )
        )
        mapping = HomeAssistantEventMapping(
            kind="event",
            event_type="mode_state",
            subject="quiet",
            entity_id="input_boolean.quiet_mode",
            active_state="on",
        )
        home_assistant = SimpleNamespace(
            enabled=True,
            base_url="http://ha.invalid",
            credential="token",
            timeout_seconds=8,
            mappings={"quiet_mode_state": mapping},
        )
        return (
            CanonicalNotificationExecution(
                settings=settings,  # type: ignore[arg-type]
                home_assistant=home_assistant,  # type: ignore[arg-type]
                satellites=fleet,  # type: ignore[arg-type]
            ),
            runtime,
        )


if __name__ == "__main__":
    unittest.main()
