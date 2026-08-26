from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

python_multipart_stub = ModuleType("python_multipart")
python_multipart_stub.__version__ = "0.0.13"
python_multipart_stub.__all__ = []
python_multipart_stub.__author__ = ""
python_multipart_stub.__copyright__ = ""
python_multipart_stub.__license__ = ""
python_multipart_multipart_stub = ModuleType("python_multipart.multipart")
python_multipart_multipart_stub.parse_options_header = lambda value: (value, {})
sys.modules.setdefault("python_multipart", python_multipart_stub)
sys.modules.setdefault("python_multipart.multipart", python_multipart_multipart_stub)

from fastapi import HTTPException

from oracle_app.admin_diagnostics_routes import (
    admin_memory_diagnostics_summary,
    ui_log_targets,
    ui_logs,
    ui_playback_authority,
    ui_sources,
)
from oracle_app.admin_facts_routes import admin_facts_lookup
from oracle_app.api import app
from oracle_app.health_routes import health_librenms
from oracle_app.command_events import clear_command_interim_events, list_command_interim_events
from oracle_app.facts import lookup_facts
from oracle_app.memory.diagnostics import DiagnosticsSummaryQuery
from oracle_app.music_runtime.control import ControlPlaneError
from oracle_app.schemas import LibreNmsHealthResponse


FACTS_ADMIN_CONFIG = {
    "enabled": True,
    "provider": "static",
    "summarizer_enabled": True,
    "ack_enabled": True,
    "static_items": [
        {
            "queries": ["what is the largest animal"],
            "answer": {"text": "The blue whale is the largest animal known to have ever existed."},
            "evidence": [
                {
                    "title": "Blue whale",
                    "snippet": "The blue whale is the largest animal known to have ever existed.",
                    "source_name": "Static Fixture",
                    "source_type": "static",
                    "provenance": {
                        "url": "https://example.invalid/blue-whale",
                        "api_token": "do-not-return",
                    },
                }
            ],
        },
        {
            "queries": ["what is flurble dust"],
            "status": "no_result",
            "detail": "No fixture result.",
        },
        {
            "queries": ["trigger static facts error"],
            "status": "provider_error",
            "detail": "Static provider failed.",
        },
    ],
}


def _facts_execution():
    return SimpleNamespace(
        settings=SimpleNamespace(summarizer_enabled=True),
        inference=object(),
        lookup=lambda request: lookup_facts(request, settings=FACTS_ADMIN_CONFIG),
    )


def _fleet(*source_ids: str):
    return SimpleNamespace(
        satellites={
            source_id: SimpleNamespace(
                enabled=True,
                playback_capable=True,
                source_id=source_id,
            )
            for source_id in source_ids
        }
    )


def _music_execution(fetch):
    admitted = object()
    return SimpleNamespace(
        settings=SimpleNamespace(playback_target=lambda source_id: admitted),
        fetch_playback_authority=fetch,
    )


class UiApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_command_interim_events()

    @patch("oracle_app.admin_facts_routes.summarize_facts_result", return_value="The largest animal is the blue whale.")
    def test_admin_facts_lookup_returns_normalized_payload_with_summary(self, mock_summary) -> None:
        payload = admin_facts_lookup("What is the largest animal", canonical_execution=_facts_execution())

        self.assertTrue(payload["ok"])
        facts = payload["facts"]
        self.assertEqual(facts["facts_status"], "answered")
        self.assertEqual(facts["provider"]["id"], "static")
        self.assertEqual(facts["retrieval"]["method"], "static_fixture")
        self.assertEqual(facts["summary"], "The largest animal is the blue whale.")
        self.assertTrue(facts["summarized_by_model"])
        self.assertEqual(payload["summarizer"]["attempted"], True)
        self.assertEqual(payload["summarizer"]["reason"], "summarized")
        self.assertEqual(list_command_interim_events(source=None, session_id=None), [])
        mock_summary.assert_called_once()

    @patch("oracle_app.admin_facts_routes.summarize_facts_result")
    def test_admin_facts_lookup_can_skip_summarizer(self, mock_summary) -> None:
        payload = admin_facts_lookup(
            "What is the largest animal",
            summarize=False,
            canonical_execution=_facts_execution(),
        )

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["facts"]["summarized_by_model"])
        self.assertFalse(payload["summarizer"]["attempted"])
        self.assertEqual(payload["summarizer"]["reason"], "not_requested")
        mock_summary.assert_not_called()

    def test_admin_facts_lookup_reports_no_result_without_summarizer(self) -> None:
        payload = admin_facts_lookup("What is flurble dust", canonical_execution=_facts_execution())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["facts"]["facts_status"], "no_result")
        self.assertEqual(payload["summarizer"]["reason"], "unsupported_status")

    def test_admin_facts_lookup_reports_provider_error(self) -> None:
        payload = admin_facts_lookup("Trigger static facts error", canonical_execution=_facts_execution())

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["facts"]["facts_status"], "provider_error")
        self.assertEqual(payload["facts"]["detail"], "Static provider failed.")

    @patch("oracle_app.admin_facts_routes.summarize_facts_result", return_value="The largest animal is the blue whale.")
    def test_admin_facts_lookup_redacts_secret_like_fields(self, _mock_summary) -> None:
        payload = admin_facts_lookup("What is the largest animal", canonical_execution=_facts_execution())

        provenance = payload["facts"]["evidence"][0]["provenance"]
        self.assertEqual(provenance["api_token"], "[redacted]")
        self.assertEqual(provenance["url"], "https://example.invalid/blue-whale")

    def test_admin_facts_lookup_rejects_empty_query(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            admin_facts_lookup("   ")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_admin_facts_lookup_route_is_registered(self) -> None:
        paths = {route.path for route in app.routes}

        self.assertIn("/api/admin/facts/lookup", paths)

    def test_admin_memory_diagnostics_summary_maps_query_params(self) -> None:
        expected = {"ok": True, "events": {"recent": []}, "providers": {"latest": []}, "sources": {"items": []}}
        with patch("oracle_app.admin_diagnostics_routes.build_memory_diagnostics_summary", return_value=expected) as mock_summary:
            payload = admin_memory_diagnostics_summary(
                observed_after="2026-04-24T12:00:00+00:00",
                observed_before="2026-04-25T12:00:00+00:00",
                event_limit=25,
                provider_limit=10,
                source_limit=5,
                satellite_limit=7,
                event_type="provider_unavailable",
                severity="error",
                status="unavailable",
                domain="ollama",
                provider="ollama",
                source_type="brain",
                satellite_source_id="test_satellite_alpha",
                satellite_status="degraded",
            )

        self.assertIs(payload, expected)
        mock_summary.assert_called_once()
        [query] = mock_summary.call_args.args
        self.assertIsInstance(query, DiagnosticsSummaryQuery)
        self.assertEqual(query.observed_after, "2026-04-24T12:00:00+00:00")
        self.assertEqual(query.observed_before, "2026-04-25T12:00:00+00:00")
        self.assertEqual(query.event_limit, 25)
        self.assertEqual(query.provider_limit, 10)
        self.assertEqual(query.source_limit, 5)
        self.assertEqual(query.satellite_limit, 7)
        self.assertEqual(query.event_type, "provider_unavailable")
        self.assertEqual(query.severity, "error")
        self.assertEqual(query.status, "unavailable")
        self.assertEqual(query.domain, "ollama")
        self.assertEqual(query.provider, "ollama")
        self.assertEqual(query.source_type, "brain")
        self.assertEqual(query.satellite_source_id, "test_satellite_alpha")
        self.assertEqual(query.satellite_status, "degraded")

    def test_admin_memory_diagnostics_summary_does_not_read_logs_or_health(self) -> None:
        with (
            patch("oracle_app.admin_diagnostics_routes.build_memory_diagnostics_summary", return_value={"ok": True}) as mock_summary,
            patch("oracle_app.admin_diagnostics_routes.read_brain_log_tail") as mock_logs,
            patch("oracle_app.health_routes.check_home_assistant_health") as mock_ha,
            patch("oracle_app.health_routes.check_ollama_health") as mock_ollama,
            patch("oracle_app.health_routes.check_stt_health") as mock_stt,
            patch("oracle_app.health_routes.check_tts_health") as mock_tts,
            patch("oracle_app.health_routes.check_audiobook_health") as mock_audiobook,
            patch("oracle_app.health_routes.check_calendar_health") as mock_calendar,
            patch("oracle_app.health_routes.check_librenms_health") as mock_librenms,
            patch("oracle_app.health_routes.check_music_health") as mock_music,
            patch("oracle_app.health_routes.safe_observe_provider_health") as mock_observe,
        ):
            payload = admin_memory_diagnostics_summary()

        self.assertEqual(payload, {"ok": True})
        mock_summary.assert_called_once()
        mock_logs.assert_not_called()
        mock_ha.assert_not_called()
        mock_ollama.assert_not_called()
        mock_stt.assert_not_called()
        mock_tts.assert_not_called()
        mock_audiobook.assert_not_called()
        mock_calendar.assert_not_called()
        mock_librenms.assert_not_called()
        mock_music.assert_not_called()
        mock_observe.assert_not_called()

    def test_librenms_health_records_provider_observation(self) -> None:
        response = LibreNmsHealthResponse(
            status="ok",
            service="oracle-brain",
            provider="librenms",
            configured=True,
            available=True,
            detail="LibreNMS API is reachable.",
            http_status=200,
        )
        with (
            patch("oracle_app.health_routes.check_librenms_health", return_value=response) as mock_health,
            patch("oracle_app.health_routes.safe_observe_provider_health") as mock_observe,
        ):
            payload = health_librenms()

        self.assertIs(payload, response)
        mock_health.assert_called_once_with()
        mock_observe.assert_called_once_with("librenms", response)

    def test_ui_log_targets_marks_only_brain_available(self) -> None:
        payload = ui_log_targets()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["targets"][0]["available"])
        self.assertEqual(payload["targets"][0]["target"], "brain")
        self.assertEqual(len(payload["targets"]), 1)

    @patch("oracle_app.admin_diagnostics_routes.subprocess.run")
    def test_ui_logs_returns_brain_journal_tail(self, mock_run) -> None:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "line-a\nline-b\n"
        mock_run.return_value.stderr = ""

        payload = ui_logs("brain", 80)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["target"], "brain")
        self.assertIn("line-a", payload["content"])
        mock_run.assert_called_once()

    def test_ui_logs_reports_unavailable_remote_target(self) -> None:
        payload = ui_logs(
            "satellite:pi-satellite-102",
            120,
            fleet_settings=_fleet("pi-satellite-102"),
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["target"], "satellite:pi-satellite-102")
        self.assertIn("not available", payload["detail"])

    def test_ui_sources_returns_sorted_configured_sources(self) -> None:
        payload = ui_sources(fleet_settings=_fleet("server-satellite-105", "pi-satellite-102"))

        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["sources"],
            [
                {
                    "source": "pi-satellite-102",
                    "playback_capable": True,
                    "supports_oracle_native_music": True,
                    "supports_plexamp": False,
                },
                {
                    "source": "server-satellite-105",
                    "playback_capable": True,
                    "supports_oracle_native_music": True,
                    "supports_plexamp": False,
                },
            ],
        )

    def test_canonical_sources_use_typed_fleet(self) -> None:
        from oracle_app.admin_diagnostics_routes import ui_sources as build_sources

        fleet = SimpleNamespace(
            satellites={
                "living_room_satellite": SimpleNamespace(
                    enabled=True,
                    playback_capable=True,
                    source_id="living_room_voice",
                )
            }
        )

        payload = build_sources(fleet_settings=fleet)

        self.assertEqual(payload["sources"][0]["source"], "living_room_voice")
        self.assertTrue(payload["sources"][0]["supports_oracle_native_music"])
        self.assertFalse(payload["sources"][0]["supports_plexamp"])

    def test_ui_playback_authority_returns_single_source(self) -> None:
        mock_fetch = Mock(
            return_value={"ok": True, "output_owner": {"backend_type": "reply_audio"}}
        )

        payload = ui_playback_authority(
            "pi-satellite-102",
            music_execution=_music_execution(mock_fetch),
            fleet_settings=_fleet("pi-satellite-101", "pi-satellite-102"),
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "pi-satellite-102")
        self.assertEqual(payload["authority"]["output_owner"]["backend_type"], "reply_audio")
        mock_fetch.assert_called_once_with("pi-satellite-102")

    def test_ui_playback_authority_aggregates_errors(self) -> None:
        def fake_fetch(source: str):
            if source == "pi-satellite-101":
                raise ControlPlaneError(
                    "timeout",
                    failure_class="transport_failure",
                    owning_component="brain.control_plane_client",
                    error_code="control_unreachable",
                )
            return {"ok": True, "playback_active": False}

        payload = ui_playback_authority(
            music_execution=_music_execution(fake_fetch),
            fleet_settings=_fleet("pi-satellite-101", "pi-satellite-102"),
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["configured_sources"], ["pi-satellite-101", "pi-satellite-102"])
        self.assertEqual(len(payload["sources"]), 2)
        failed = next(item for item in payload["sources"] if item["source"] == "pi-satellite-101")
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["failure_class"], "transport_failure")
        self.assertEqual(failed["control_error"], "control_unreachable")

    def test_ui_playback_authority_rejects_non_playback_source(self) -> None:
        with self.assertRaises(HTTPException) as captured:
            ui_playback_authority("desk", fleet_settings=_fleet())

        self.assertEqual(captured.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
