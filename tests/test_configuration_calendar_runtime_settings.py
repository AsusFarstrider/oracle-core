from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from oracle_app.configuration import CalendarRuntimeSettings, EffectiveConfig, inspect_candidate
from oracle_app.calendar_models import CalendarEvent
from oracle_app.calendar_runtime import CanonicalCalendarExecution
from oracle_app.dispatch import build_dispatch_plan, build_dispatch_registry, execute_dispatch
from oracle_app.routing import build_route_capability_registry, choose_route
from oracle_app.schemas import CommandRequest, DispatchPlan
from oracle_app.ui_calendar import build_ui_calendar_page_snapshot


EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "examples" / "config"


class CalendarRuntimeSettingsTests(unittest.TestCase):
    def test_disabled_calendar_selects_no_provider_or_execution_surface(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(self._effective_config())

        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.provider_id)
        self.assertIsNone(settings.timeout_seconds)
        self.assertFalse(settings.read.enabled)
        self.assertEqual(dict(settings.read.feeds), {})
        self.assertFalse(settings.write.enabled)
        self.assertIsNone(settings.write.credential)

    def test_read_resolves_only_enabled_feed_edges_and_preserves_policy(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(
            self._effective_config(mode="read", feed_secret=True)
        )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.provider_id, "primary")
        self.assertEqual(settings.provider_type, "nextcloud")
        self.assertEqual(settings.timezone, "Etc/UTC")
        self.assertEqual(settings.timeout_seconds, 9)
        self.assertTrue(settings.read.enabled)
        self.assertEqual(settings.read.fresh_seconds, 45)
        self.assertEqual(settings.read.stale_if_error_seconds, 300)
        self.assertEqual(settings.read.feeds["household"].resolved_url, "https://secret.invalid/events.ics")
        self.assertEqual(settings.read.feeds["holidays"].credential_free_url, "https://calendar.invalid/holidays.ics")
        self.assertEqual(tuple(feed.id for feed in settings.read.feeds_for_kind("holidays")), ("holidays",))
        self.assertFalse(settings.write.enabled)
        self.assertIsNone(settings.write.credential)
        self.assertNotIn("https://secret.invalid/events.ics", repr(settings))
        with self.assertRaises(TypeError):
            settings.read.feeds["other"] = settings.read.feeds["household"]  # type: ignore[index]

    def test_write_resolves_only_complete_write_edge_and_keeps_confirmation(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(
            self._effective_config(mode="write", write_secret=True)
        )

        self.assertTrue(settings.enabled)
        self.assertFalse(settings.read.enabled)
        self.assertEqual(dict(settings.read.feeds), {})
        self.assertTrue(settings.write.enabled)
        self.assertTrue(settings.write.confirmation_required)
        self.assertEqual(settings.write.base_url, "https://nextcloud.invalid")
        self.assertEqual(settings.write.user, "oracle")
        self.assertEqual(settings.write.calendar_uri, "joint")
        self.assertEqual(settings.write.credential, "calendar-write-password")
        self.assertNotIn("calendar-write-password", repr(settings))

    def test_canonical_calendar_route_dispatch_health_and_ui_use_typed_execution(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(
            self._effective_config(mode="read", feed_secret=True)
        )
        execution = CanonicalCalendarExecution(settings)
        event = CalendarEvent(
            uid="oracle-test",
            summary="Oracle test",
            start=datetime(2099, 1, 1, 10, 0, tzinfo=ZoneInfo("Etc/UTC")),
            end=datetime(2099, 1, 1, 11, 0, tzinfo=ZoneInfo("Etc/UTC")),
            all_day=False,
            location=None,
        )

        with patch(
            "oracle_app.calendar.get_calendar_settings",
            side_effect=AssertionError("canonical calendar used V1 settings"),
        ), patch(
            "oracle_app.provider_bridges.nextcloud_calendar.NextcloudCalendarBridge.fetch_typed_events",
            return_value=[event],
        ) as fetch:
            route = choose_route(
                "when is oracle test",
                registry=build_route_capability_registry(
                    calendar_settings=settings,
                    canonical_calendar=True,
                ),
            )
            dispatched = execute_dispatch(
                build_dispatch_plan(
                    CommandRequest(text="when is oracle test", source="test"),
                    route,
                ),
                registry=build_dispatch_registry(
                    canonical_configuration=True,
                    calendar_execution=execution,
                ),
            )
            health = execution.health()
            ui = build_ui_calendar_page_snapshot(
                canonical_execution=execution,
            )

        self.assertEqual(route.target, "calendar")
        self.assertEqual(dispatched.result["events"][0]["summary"], "Oracle test")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(ui["upcoming"]["events"][0]["summary"], "Oracle test")
        self.assertGreaterEqual(fetch.call_count, 2)

    def test_canonical_personal_read_uses_selected_nextcloud_write_auth_when_enabled(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(
            self._effective_config(mode="both", feed_secret=True, write_secret=True)
        )
        execution = CanonicalCalendarExecution(settings)

        with patch(
            "oracle_app.provider_bridges.nextcloud_calendar.NextcloudCalendarBridge.fetch_typed_events",
            return_value=[],
        ) as fetch:
            execution.load_events(scope="personal")

        fetch.assert_called_once_with(
            feed_url="https://secret.invalid/events.ics",
            timeout_seconds=9,
            timezone_name="Etc/UTC",
            auth_user="oracle",
            auth_password="calendar-write-password",
        )

    def test_canonical_calendar_commit_uses_typed_write_edge(self) -> None:
        settings = CalendarRuntimeSettings.from_effective_config(
            self._effective_config(mode="write", write_secret=True)
        )
        execution = CanonicalCalendarExecution(settings)
        committed = {
            "event_draft": {"title": "Dentist"},
            "calendar_uri": "joint",
        }
        dispatch = DispatchPlan(
            target="calendar",
            hook="calendar.commit_event",
            payload={
                "action": "commit_event",
                "event_draft": {
                    "title": "Dentist",
                    "date": "2099-01-01",
                    "all_day": True,
                },
            },
            status="pending_integration",
        )

        with patch(
            "oracle_app.provider_bridges.nextcloud_calendar.NextcloudCalendarBridge.commit_typed_event",
            return_value=committed,
        ) as commit:
            result = execute_dispatch(
                dispatch,
                registry=build_dispatch_registry(
                    canonical_configuration=True,
                    calendar_execution=execution,
                ),
            )

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.result["calendar_event"], committed)
        commit.assert_called_once()

    def test_absent_optional_role_has_no_implicit_runtime_defaults(self) -> None:
        effective = self._effective_config(include_role=False)

        with self.assertRaises(KeyError):
            CalendarRuntimeSettings.from_effective_config(effective)

    def _effective_config(
        self,
        *,
        mode: str | None = None,
        include_role: bool = True,
        feed_secret: bool = False,
        write_secret: bool = False,
    ) -> EffectiveConfig:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            shutil.copytree(EXAMPLE_ROOT, bundle)
            role_path = bundle / "domains" / "calendar.yaml"
            if not include_role:
                role_path.unlink()
            elif mode is not None:
                self._write_enabled_calendar(bundle, mode=mode)
            secret_lines = []
            if feed_secret:
                secret_lines.append("CALENDAR_FEED_URL=https://secret.invalid/events.ics")
            if write_secret:
                secret_lines.append("CALENDAR_WRITE_CREDENTIAL=calendar-write-password")
            if secret_lines:
                (bundle / "secrets.env").write_text("\n".join(secret_lines) + "\n", encoding="utf-8")
            inspection = inspect_candidate(bundle)
            self.assertTrue(inspection.report.activation_eligible, inspection.report)
            self.assertIsNotNone(inspection.bundle)
            self.assertIsNotNone(inspection.normalized_candidate_revision)
            self.assertIsNotNone(inspection.secrets)
            return EffectiveConfig(
                activation_generation_id="activation_11111111111111111111111111111111",
                config_generation_id="config_11111111111111111111111111111111",
                secret_generation_id="secrets_11111111111111111111111111111111",
                selection_operation_id="selection_op_11111111111111111111111111111111",
                selection_revision=1,
                satellite_projection_activation_ids=MappingProxyType({}),
                config_revision=inspection.normalized_candidate_revision,
                bundle_id="example-home",
                schema_version=2,
                roles=inspection.bundle.roles,  # type: ignore[union-attr]
                secrets=inspection.secrets,  # type: ignore[arg-type]
            )

    @staticmethod
    def _write_enabled_calendar(bundle: Path, *, mode: str) -> None:
        calendar = {
            "enabled": True,
            "provider": "primary",
            "providers": {
                "primary": {
                    "type": "nextcloud",
                    "feeds": [
                        {
                            "id": "household",
                            "kind": "events",
                            "ics_url_secret": "CALENDAR_FEED_URL",
                        },
                        {
                            "id": "holidays",
                            "kind": "holidays",
                            "ics_url": "https://calendar.invalid/holidays.ics",
                        },
                    ],
                    "timeout_seconds": 9,
                    "write_base_url": "https://nextcloud.invalid",
                    "write_user": "oracle",
                    "write_credential_secret": "CALENDAR_WRITE_CREDENTIAL",
                    "write_calendar_uri": "joint",
                }
            },
            "policy": {
                "read_enabled": mode in {"read", "both"},
                "write_enabled": mode in {"write", "both"},
                "confirmation_required": True,
                "fresh_seconds": 45,
                "stale_if_error_seconds": 300,
            },
        }
        (bundle / "domains" / "calendar.yaml").write_text(json.dumps(calendar), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
