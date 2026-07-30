from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

from oracle_app.admin_home_automation_routes import (
    admin_home_automation_runbook_detail,
    admin_home_automation_runbooks,
    register_admin_home_automation_routes,
)
from oracle_app.brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from oracle_app.configuration.domain_models import (
    HomeAssistantAutomation,
    HomeAssistantEventMapping,
    HomeAssistantViews,
)
from oracle_app.configuration.home_assistant_runtime_settings import (
    HomeAssistantAutomationRuntimeSettings,
    HomeAssistantRuntimeSettings,
)
from oracle_app.home_automation import (
    handle_home_assistant_event,
    resume_due_home_automation_runbooks,
)
from oracle_app.home_automation_routes import (
    receive_home_assistant_event,
    receive_home_assistant_event_http,
)
from oracle_app.memory.orchestrations import reconcile_interrupted_orchestration_runs
from oracle_app.runbook_kernel import RunbookRepository
from oracle_app.schemas import HomeAssistantEventIngressRequest


def _canonical_settings(
    *,
    migration_mode: str = "runbook",
    max_notifications: int = 2,
    notification_delivery_enabled: bool = True,
) -> HomeAssistantRuntimeSettings:
    mapping = HomeAssistantEventMapping(
        kind="event",
        event_type="entry_state",
        subject="side_entry",
        entity_id="binary_sensor.side_entry_contact",
        active_state="on",
        inactive_state="off",
    )
    quiet_mapping = HomeAssistantEventMapping(
        kind="event",
        event_type="mode_state",
        subject="quiet",
        entity_id="input_boolean.quiet_mode",
        active_state="on",
        inactive_state="off",
    )
    definition = HomeAssistantAutomation(
        id="side_entry_left_open",
        enabled=True,
        migration_mode=migration_mode,
        event_mapping_id="side_entry_state",
        notification_type="side_entry_open",
        notification_delivery_enabled=notification_delivery_enabled,
        delay_seconds=600,
        repeat_interval_seconds=600,
        max_notifications=max_notifications,
        max_lateness_seconds=120,
        provider_retry_seconds=30,
        max_provider_failures=2,
    )
    return HomeAssistantRuntimeSettings(
        activation_generation_id="activation_11111111111111111111111111111111",
        config_generation_id="config_11111111111111111111111111111111",
        secret_generation_id="secrets_11111111111111111111111111111111",
        selection_operation_id="selection_op_11111111111111111111111111111111",
        selection_revision=1,
        config_revision="oracle-config-v1:sha256:test",
        enabled=True,
        provider_id="primary",
        base_url="http://home-assistant.invalid:8123",
        timeout_seconds=9,
        snapshot_root=None,
        mappings=MappingProxyType(
            {"side_entry_state": mapping, "quiet_mode_state": quiet_mapping}
        ),
        views=HomeAssistantViews(),
        automations=MappingProxyType(
            {
                definition.id: HomeAssistantAutomationRuntimeSettings(
                    definition=definition,
                    event_mapping=mapping,
                )
            }
        ),
        credential_secret="HOME_ASSISTANT_TOKEN",
        event_ingress_secret="HOME_ASSISTANT_EVENT_TOKEN",
        credential="provider-secret",
        event_ingress_credential="event-secret",
    )


class HomeAutomationRunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "memory.sqlite3"
        self.started = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)

    def _event(
        self,
        state: str,
        event_id: str,
        *,
        settings: HomeAssistantRuntimeSettings | None = None,
    ) -> dict:
        return handle_home_assistant_event(
            entity_id="binary_sensor.side_entry_contact",
            state=state,
            event_id=event_id,
            occurred_at=self.started,
            home_assistant_settings=settings or _canonical_settings(),
            db_path=self.db_path,
        )

    def test_direct_notification_migration_mode_does_not_start_run(self) -> None:
        result = self._event(
            "on",
            "event-1",
            settings=_canonical_settings(migration_mode="direct_notification"),
        )

        self.assertEqual(result["status"], "compatibility_active")
        self.assertEqual(RunbookRepository(db_path=self.db_path).list_runs(limit=10), [])

    def test_open_is_correlated_and_close_cancels_waiting_run(self) -> None:
        started = self._event("on", "event-1")
        duplicate = self._event("on", "event-2")
        canceled = self._event("off", "event-3")

        self.assertEqual(started["status"], "started")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["run_id"], started["run_id"])
        self.assertEqual(canceled["status"], "canceled")
        run = RunbookRepository(db_path=self.db_path).require_run(started["run_id"])
        self.assertEqual(run["status"], "canceled")
        self.assertEqual(run["cancellation_reason"], "correlated_entry_closed")

    def test_due_run_verifies_closed_and_completes_without_notification(self) -> None:
        started = self._event("on", "event-1")
        submit = Mock()

        resumed = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "off"},
            notification_submitter=submit,
        )

        self.assertEqual(resumed[0]["status"], "completed")
        submit.assert_not_called()
        run = RunbookRepository(db_path=self.db_path).require_run(started["run_id"])
        self.assertEqual(run["steps"][0]["status"], "completed")

    def test_open_run_submits_notification_and_repeats_with_unique_occurrences(self) -> None:
        started = self._event("on", "event-1")
        submit = Mock(return_value={"status": "queued"})

        first = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "on"},
            notification_submitter=submit,
        )[0]
        second_due = datetime.fromisoformat(first["controller_state"]["next_due_at"])
        second = resume_due_home_automation_runbooks(
            now=second_due,
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "on"},
            notification_submitter=submit,
        )[0]

        self.assertEqual(first["status"], "waiting")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(submit.call_count, 2)
        self.assertEqual(submit.call_args_list[0].args, ("side_entry_open", f"{started['run_id']}:1"))
        self.assertEqual(submit.call_args_list[1].args, ("side_entry_open", f"{started['run_id']}:2"))
        self.assertEqual(submit.call_args_list[0].kwargs["caller"], "home_automation_runbook")

    def test_delivery_disabled_simulates_occurrence_without_submitting(self) -> None:
        started = self._event(
            "on",
            "event-1",
            settings=_canonical_settings(
                max_notifications=1,
                notification_delivery_enabled=False,
            ),
        )
        submit = Mock()

        result = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "on"},
            notification_submitter=submit,
        )[0]

        self.assertEqual(result["status"], "completed")
        submit.assert_not_called()
        run = RunbookRepository(db_path=self.db_path).require_run(started["run_id"])
        self.assertEqual(run["steps"][-1]["target_type"], "notification_simulation")
        self.assertEqual(run["steps"][-1]["verification_status"], "simulated")

    def test_suppressed_occurrence_does_not_exhaust_bound_before_policy_release(self) -> None:
        started = self._event(
            "on",
            "event-1",
            settings=_canonical_settings(max_notifications=1),
        )
        submit = Mock(side_effect=[{"status": "suppressed"}, {"status": "queued"}])

        first = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "on"},
            notification_submitter=submit,
        )[0]
        second = resume_due_home_automation_runbooks(
            now=datetime.fromisoformat(first["controller_state"]["next_due_at"]),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "on"},
            notification_submitter=submit,
        )[0]

        self.assertEqual(first["status"], "waiting")
        self.assertEqual(first["controller_state"]["notification_count"], 0)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["controller_state"]["notification_count"], 1)
        self.assertEqual(submit.call_args_list[0].args[1], f"{started['run_id']}:1")
        self.assertEqual(submit.call_args_list[1].args[1], f"{started['run_id']}:2")

    def test_provider_unavailability_retries_durably(self) -> None:
        started = self._event("on", "event-1")

        result = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: None,
            notification_submitter=Mock(),
        )[0]

        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["controller_state"]["provider_failure_count"], 1)
        self.assertEqual(RunbookRepository(db_path=self.db_path).require_run(started["run_id"])["status"], "waiting")

    def test_waiting_run_survives_restart_reconciliation_and_resumes(self) -> None:
        started = self._event("on", "event-1")

        self.assertEqual(reconcile_interrupted_orchestration_runs(db_path=self.db_path), 0)
        resumed = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            state_fetcher=lambda _entity: {"state": "off"},
            notification_submitter=Mock(),
        )

        self.assertEqual(resumed[0]["status"], "completed")
        self.assertEqual(RunbookRepository(db_path=self.db_path).require_run(started["run_id"])["status"], "completed")

    def test_quiet_mode_is_mapped_but_does_not_start_entry_runbook(self) -> None:
        result = handle_home_assistant_event(
            entity_id="input_boolean.quiet_mode",
            state="on",
            event_id="quiet-1",
            home_assistant_settings=_canonical_settings(),
            db_path=self.db_path,
        )

        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["state"], "active")

    def test_strictly_older_event_cannot_reopen_after_close(self) -> None:
        self._event("off", "close-new")

        stale = handle_home_assistant_event(
            entity_id="binary_sensor.side_entry_contact",
            state="on",
            event_id="open-old",
            occurred_at=self.started - timedelta(seconds=1),
            home_assistant_settings=_canonical_settings(),
            db_path=self.db_path,
        )

        self.assertEqual(stale["status"], "ignored")
        self.assertEqual(stale["reason"], "stale_or_duplicate_event")
        self.assertEqual(RunbookRepository(db_path=self.db_path).list_runs(limit=10), [])

    def test_canonical_event_uses_typed_mapping_and_freezes_definition(self) -> None:
        result = handle_home_assistant_event(
            entity_id="binary_sensor.side_entry_contact",
            state="on",
            event_id="canonical-1",
            occurred_at=self.started,
            home_assistant_settings=_canonical_settings(),
            db_path=self.db_path,
        )

        self.assertEqual(result["status"], "started")
        run = RunbookRepository(db_path=self.db_path).require_run(result["run_id"])
        self.assertEqual(run["payload"]["definition"]["subject"], "side_entry")
        self.assertEqual(run["payload"]["definition"]["entity_id"], "binary_sensor.side_entry_contact")
        self.assertEqual(run["definition_version"], "oracle-config-v1:sha256:test")
        self.assertNotIn("event-secret", repr(run))

    @patch("oracle_app.home_automation.controller.HomeAssistantBridge.fetch_entity_state")
    def test_canonical_resume_uses_typed_provider_and_injected_notification(
        self,
        fetch_state,
    ) -> None:
        settings = _canonical_settings()
        started = handle_home_assistant_event(
            entity_id="binary_sensor.side_entry_contact",
            state="on",
            event_id="canonical-1",
            occurred_at=self.started,
            home_assistant_settings=settings,
            db_path=self.db_path,
        )
        fetch_state.return_value = {"state": "off"}
        submit = Mock()

        resumed = resume_due_home_automation_runbooks(
            now=self.started + timedelta(seconds=600),
            db_path=self.db_path,
            home_assistant_settings=settings,
            notification_submitter=submit,
        )

        self.assertEqual(resumed[0]["run_id"], started["run_id"])
        self.assertEqual(resumed[0]["status"], "completed")
        submit.assert_not_called()

    def test_canonical_resume_requires_injected_notification_capability(self) -> None:
        with self.assertRaisesRegex(ValueError, "injected notification capability"):
            resume_due_home_automation_runbooks(
                home_assistant_settings=_canonical_settings(),
                db_path=self.db_path,
            )


class HomeAutomationRouteTests(unittest.TestCase):
    @staticmethod
    def _request(token: str) -> Request:
        return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})

    @patch("oracle_app.home_automation_routes.handle_home_assistant_event")
    def test_route_authenticates_and_forwards_raw_ha_event(self, handle) -> None:
        handle.return_value = {
            "status": "compatibility_active",
            "event_id": "event-1",
            "event_type": "entry_state",
            "subject": "side_entry",
            "state": "open",
            "run_id": "",
            "reason": "direct_notification_owns_delivery",
        }
        payload = HomeAssistantEventIngressRequest(
            event_id="event-1",
            entity_id="binary_sensor.side_entry_contact",
            state="on",
        )

        response = receive_home_assistant_event(
            payload,
            self._request("event-secret"),
            home_assistant_settings=_canonical_settings(),
        )

        self.assertEqual(response.status, "compatibility_active")
        handle.assert_called_once()

    def test_route_rejects_invalid_token(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            receive_home_assistant_event(
                HomeAssistantEventIngressRequest(
                    event_id="event-1",
                    entity_id="binary_sensor.side_entry_contact",
                    state="on",
                ),
                self._request("wrong"),
                home_assistant_settings=_canonical_settings(),
            )
        self.assertEqual(raised.exception.status_code, 401)

    @patch("oracle_app.home_automation_routes.handle_home_assistant_event")
    def test_http_route_uses_canonical_event_credential_and_runtime(self, handle) -> None:
        from fastapi import FastAPI

        settings = _canonical_settings()
        application = FastAPI()
        composition = CanonicalBrainApplicationComposition(
            runtime=Mock(home_assistant=settings),
            core_consumers=Mock(),
            route_registry=Mock(),
            dispatch_registry=Mock(),
            projection_resolver=Mock(),
            request_source_resolver=Mock(),
            playback_target_resolver=Mock(),
            notification_execution=Mock(),
        )
        setattr(application.state, BRAIN_APPLICATION_COMPOSITION_STATE_KEY, composition)
        request = Request(
            {
                "type": "http",
                "headers": [(b"authorization", b"Bearer event-secret")],
                "app": application,
            }
        )
        handle.return_value = {
            "status": "started",
            "event_id": "canonical-1",
            "event_type": "entry_state",
            "subject": "side_entry",
            "state": "open",
            "run_id": "home-1",
            "reason": "",
        }

        response = receive_home_assistant_event_http(
            HomeAssistantEventIngressRequest(
                event_id="canonical-1",
                entity_id="binary_sensor.side_entry_contact",
                state="on",
            ),
            request,
        )

        self.assertEqual(response.status, "started")
        self.assertIs(handle.call_args.kwargs["home_assistant_settings"], settings)


class HomeAutomationAdminRouteTests(unittest.TestCase):
    def test_admin_home_automation_runbooks_returns_safe_status_summary(self) -> None:
        run = {
            "run_id": "home-1",
            "orchestration_id": "side_entry_left_open",
            "status": "waiting",
            "summary": "Waiting before rechecking side_entry.",
            "started_at": "2026-06-22T12:00:00+00:00",
            "completed_at": "",
            "correlation_key": "home_automation:entry:side_entry",
            "cancellation_reason": "",
            "controller_state": {
                "phase": "repeat_wait",
                "next_due_at": "2026-06-22T12:12:00+00:00",
                "cycle": 2,
                "notification_count": 1,
                "submission_count": 1,
                "provider_failure_count": 0,
            },
            "payload": {"trigger_event_id": "event-1"},
            "steps": [
                {
                    "step_id": "wait-2",
                    "ordinal": 4,
                    "status": "waiting",
                    "target_type": "wait",
                    "target_id": "side_entry",
                    "action_id": "",
                    "summary": "Durable wait scheduled.",
                    "verification_status": "",
                    "started_at": "2026-06-22T12:10:00+00:00",
                    "completed_at": "",
                    "payload": {"due_at": "2026-06-22T12:12:00+00:00"},
                }
            ],
        }
        with (
            patch("oracle_app.admin_home_automation_routes.list_canonical_states", return_value={
                "side_entry": {
                    "subject": "side_entry",
                    "state": "open",
                    "event_id": "event-1",
                    "observed_at": "2026-06-22T12:00:00+00:00",
                }
            }),
            patch("oracle_app.admin_home_automation_routes.RunbookRepository") as repository_class,
        ):
            repository_class.return_value.list_runs.return_value = [run]

            payload = admin_home_automation_runbooks(
                home_assistant_settings=_canonical_settings()
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["active_runs"], 1)
        definition = payload["definitions"][0]
        self.assertEqual(definition["id"], "side_entry_left_open")
        self.assertEqual(definition["latest_state"]["state"], "open")
        self.assertEqual(definition["active_run"]["controller_state"]["phase"], "repeat_wait")
        self.assertEqual(definition["active_run"]["steps"][0]["due_at"], "2026-06-22T12:12:00+00:00")
        self.assertNotIn("payload", definition["active_run"])

    def test_admin_home_automation_runbook_detail_404s_unknown_definition(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            admin_home_automation_runbook_detail(
                "missing", home_assistant_settings=_canonical_settings()
            )
        self.assertEqual(raised.exception.status_code, 404)

    def test_canonical_admin_uses_typed_definitions(self) -> None:
        payload = admin_home_automation_runbooks(
            home_assistant_settings=_canonical_settings(),
        )

        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(payload["definitions"][0]["subject"], "side_entry")

    def test_admin_home_automation_routes_register_expected_paths(self) -> None:
        from fastapi import FastAPI

        app = FastAPI()
        register_admin_home_automation_routes(app)
        paths = {route.path for route in app.routes}

        self.assertIn("/api/admin/home-automation/runbooks", paths)
        self.assertIn("/api/admin/home-automation/runbooks/{runbook_id}", paths)
