from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from oracle_app.alert_scheduler import process_due_audiobook_sleep_timers
from oracle_app.api import _apply_canonical_alert_target
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.request_source_resolution import ResolvedRequestSource
from oracle_app.memory.alerts import AlertRecord
from oracle_app.schemas import CommandRequest, SatelliteAlertClaimRequest
from oracle_app.satellite_alert_routes import satellite_alert_claim


UTC = timezone.utc
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


class _Fleet:
    def __init__(self) -> None:
        self.targets = {
            "satellite-source": SimpleNamespace(alert_capable=True),
            "display-source": SimpleNamespace(alert_capable=True),
            "disabled-source": SimpleNamespace(alert_capable=False),
        }

    def satellite_for_source(self, source_id):
        return self.targets.get(source_id)


class Stage5Slice7AlertDeliveryTests(unittest.TestCase):
    @patch("oracle_app.api.brain_application_composition")
    def test_alert_target_authority_rejects_ephemeral_and_validates_explicit_target(
        self, composition
    ) -> None:
        composition.return_value.runtime.satellites = _Fleet()
        payload = CommandRequest(
            text="set a timer for five minutes",
            source="ephemeral_http",
            alert_delivery_target_source_id="display-source",
        )
        _resolved, error = _apply_canonical_alert_target(
            payload,
            route_target="system",
            request_source=ResolvedRequestSource(
                request_source_id="ephemeral_http", kind="ephemeral", authentication="none"
            ),
        )
        self.assertEqual(error, "ephemeral_alert_creation_forbidden")

        stable = ResolvedRequestSource(
            request_source_id="wall-ui", kind="stable", authentication="source_credential"
        )
        resolved, error = _apply_canonical_alert_target(
            payload.model_copy(update={"source": "wall-ui"}),
            route_target="system",
            request_source=stable,
        )
        self.assertIsNone(error)
        self.assertEqual(resolved.alert_delivery_target_source_id, "display-source")
        _resolved, error = _apply_canonical_alert_target(
            payload.model_copy(update={
                "source": "wall-ui",
                "alert_delivery_target_source_id": "disabled-source",
            }),
            route_target="system",
            request_source=stable,
        )
        self.assertEqual(error, "invalid_alert_delivery_target")

    @patch("oracle_app.api.brain_application_composition")
    def test_authenticated_satellite_defaults_alert_target_to_itself(self, composition) -> None:
        composition.return_value.runtime.satellites = _Fleet()
        payload = CommandRequest(text="set an alarm for 6 am", source="satellite-source")
        resolved, error = _apply_canonical_alert_target(
            payload,
            route_target="system",
            request_source=ResolvedRequestSource(
                request_source_id="satellite-source",
                kind="stable",
                authentication="satellite_credential",
            ),
        )
        self.assertIsNone(error)
        self.assertEqual(resolved.alert_delivery_target_source_id, "satellite-source")

    @patch("oracle_app.api.brain_application_composition")
    def test_ephemeral_audiobook_sleep_timer_is_rejected_even_with_explicit_playback_target(
        self, composition
    ) -> None:
        composition.return_value.runtime.satellites = _Fleet()
        payload = CommandRequest(
            text="resume my audiobook with a sleep timer for 20 minutes",
            source="ephemeral_http",
            playback_target_source_id="satellite-source",
        )
        _resolved, error = _apply_canonical_alert_target(
            payload,
            route_target="audiobook",
            request_source=ResolvedRequestSource(
                request_source_id="ephemeral_http", kind="ephemeral", authentication="none"
            ),
        )
        self.assertEqual(error, "ephemeral_alert_creation_forbidden")

    @patch("oracle_app.satellite_alert_routes.reconcile_satellite_receipts")
    @patch("oracle_app.satellite_alert_routes.claim_due_alerts", return_value=[])
    def test_claim_derives_source_from_bearer_credential(
        self, claim_due, reconcile
    ) -> None:
        resolver = Mock()
        resolver.resolve.return_value = ResolvedRequestSource(
            request_source_id="satellite-source",
            kind="stable",
            authentication="satellite_credential",
        )
        composition = object.__new__(CanonicalBrainApplicationComposition)
        object.__setattr__(composition, "request_source_resolver", resolver)
        object.__setattr__(composition, "runtime", SimpleNamespace(satellites=_Fleet()))
        object.__setattr__(
            composition,
            "notification_execution",
            SimpleNamespace(build_delivery_decisions=lambda _source: {}),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(
                brain_application_composition=composition
            )),
            headers={"Authorization": "Bearer secret"},
            client=SimpleNamespace(host="192.0.2.4"),
        )
        response = satellite_alert_claim(
            SatelliteAlertClaimRequest(source_id="claimed-value"), request
        )
        self.assertEqual(response.alerts, [])
        self.assertEqual(claim_due.call_args.kwargs["source_id"], "satellite-source")
        resolver.resolve.assert_called_once_with(
            claimed_source_id="claimed-value", credential="secret", peer_address="192.0.2.4"
        )
        reconcile.assert_called_once_with("satellite-source")

    @patch("oracle_app.alert_scheduler.acknowledge_alert")
    @patch("oracle_app.alert_scheduler.sync_then_control", return_value=("executed", {}))
    @patch("oracle_app.alert_scheduler.claim_due_alerts")
    def test_brain_scheduler_executes_typed_audiobook_stop_and_acknowledges(
        self, claim_due, sync_then_control_mock, acknowledge
    ) -> None:
        claim_due.return_value = [
            AlertRecord(
                alert_id="sleep",
                kind="sleep_timer",
                source_id="satellite-source",
                session_id="session",
                due_at=NOW,
                created_at=NOW - timedelta(minutes=1),
                message="Sleep timer finished.",
                lease_id="lease-one",
                lease_expires_at=NOW + timedelta(seconds=30),
                status="leased",
            )
        ]
        execution = SimpleNamespace(
            execute_satellite_command=Mock(), close_session=Mock(), sync_session=Mock()
        )
        completed = process_due_audiobook_sleep_timers(
            audiobook_execution=execution,
            satellites=SimpleNamespace(
                enabled_satellite_ids_by_source={"satellite-source": "satellite-one"}
            ),
            now=NOW,
        )
        self.assertEqual(completed, 1)
        self.assertEqual(sync_then_control_mock.call_args.kwargs["action"], "stop_longform_audio")
        acknowledge.assert_called_once_with(
            alert_id="sleep",
            source_id="satellite-source",
            lease_id="lease-one",
            now=NOW,
            completed=True,
            db_path=ANY,
        )


if __name__ == "__main__":
    unittest.main()
