from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


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

from oracle_app import api
from oracle_app.memory import provider_status, schema
from oracle_app.memory.events import list_events
from oracle_app.memory.store import transaction
from oracle_app.schemas import (
    AudiobookHealthResponse,
    CalendarHealthResponse,
    HomeAssistantHealthResponse,
    MusicHealthResponse,
    LibreNmsHealthResponse,
    OllamaHealthResponse,
    SttHealthResponse,
    TtsHealthResponse,
)


class OracleMemoryProviderStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"

    def _insert_snapshot(
        self,
        *,
        projection_id: str,
        observed_at: str,
        provider: str,
        domain: str,
        status: str,
        payload_json: str = "{}",
    ) -> None:
        schema.ensure_schema(self.db_path)
        with transaction(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_current_projections (
                    projection_id, created_at, updated_at, observed_at, projection_type,
                    provider, domain, status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    projection_id,
                    observed_at,
                    observed_at,
                    observed_at,
                    "provider_status",
                    provider,
                    domain,
                    status,
                    payload_json,
                ),
            )

    def test_schema_creates_memory_current_projections(self) -> None:
        schema.ensure_schema(self.db_path)

        self.assertIn("memory_current_projections", schema.table_names(self.db_path))

    def test_normalize_provider_health_maps_ok_and_failed(self) -> None:
        available = provider_status.normalize_provider_health(
            "ollama",
            OllamaHealthResponse(
                status="ok",
                service="oracle-brain",
                ollama_url="http://localhost:11434",
                model="llama",
                detail="ok",
                http_status=200,
            ),
        )
        unavailable = provider_status.normalize_provider_health(
            "home_assistant",
            HomeAssistantHealthResponse(
                status="failed",
                service="oracle-brain",
                home_assistant_url="http://ha.local",
                detail="connection refused",
            ),
        )

        self.assertEqual(available["status"], "available")
        self.assertEqual(available["payload"]["ollama_url_present"], True)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["payload"]["detail_classification"], "connection_error")

    def test_disabled_provider_is_not_normalized_as_an_outage(self) -> None:
        observation = provider_status.normalize_provider_health(
            "ollama",
            OllamaHealthResponse(
                status="disabled",
                service="oracle-brain",
                detail="Inference is intentionally disabled.",
            ),
        )

        self.assertEqual(observation["status"], "disabled")
        self.assertEqual(observation["payload"]["detail_classification"], "disabled")
        result = provider_status.observe_provider_status(
            provider=observation["provider"],
            domain=observation["domain"],
            status=observation["status"],
            payload=observation["payload"],
            db_path=self.db_path,
        )
        self.assertIsNone(result["event_type"])
        snapshot = provider_status.get_provider_status_snapshot(
            observation["provider"],
            observation["domain"],
            db_path=self.db_path,
        )
        self.assertEqual(snapshot["status"], "disabled")

    def test_stt_tts_available_flag_controls_status(self) -> None:
        observation = provider_status.normalize_provider_health(
            "stt",
            SttHealthResponse(
                status="ok",
                service="oracle-brain",
                provider="local",
                configured=True,
                available=False,
                detail="not available",
            ),
        )

        self.assertEqual(observation["status"], "unavailable")
        self.assertEqual(observation["provider"], "local")
        self.assertEqual(observation["domain"], "stt")

    def test_librenms_health_normalizes_without_secrets(self) -> None:
        observation = provider_status.normalize_provider_health(
            "librenms",
            LibreNmsHealthResponse(
                status="ok",
                service="oracle-brain",
                provider="librenms",
                configured=True,
                available=True,
                degraded=True,
                detail="LibreNMS API is reachable.",
                http_status=200,
                active_alert_count=2,
            ),
        )

        self.assertEqual(observation["provider"], "librenms")
        self.assertEqual(observation["domain"], "network")
        self.assertEqual(observation["status"], "degraded")
        self.assertEqual(observation["payload"]["active_alert_count"], 2)
        self.assertNotIn("token", str(observation).lower())

    def test_first_observation_writes_snapshot_and_available_event(self) -> None:
        result = provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            payload={"model": "llama"},
            db_path=self.db_path,
        )

        snapshot = provider_status.get_provider_status_snapshot("ollama", "ollama", db_path=self.db_path)
        [event] = list_events(db_path=self.db_path, event_type="provider_available")
        self.assertEqual(result["event_type"], "provider_available")
        self.assertEqual(snapshot["status"], "available")
        self.assertEqual(snapshot["payload"]["model"], "llama")
        self.assertEqual(event["provider"], "ollama")
        self.assertEqual(event["domain"], "ollama")
        self.assertEqual(event["status"], "available")

    def test_repeated_same_status_updates_snapshot_without_duplicate_event(self) -> None:
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            payload={"model": "first"},
            db_path=self.db_path,
        )
        result = provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            payload={"model": "second"},
            db_path=self.db_path,
        )

        snapshot = provider_status.get_provider_status_snapshot("ollama", "ollama", db_path=self.db_path)
        self.assertIsNone(result["event_type"])
        self.assertEqual(snapshot["payload"]["model"], "second")
        self.assertEqual(len(list_events(db_path=self.db_path, event_type="provider_available")), 1)

    def test_repeated_unavailable_updates_snapshot_without_duplicate_event(self) -> None:
        provider_status.observe_provider_status(
            provider="home_assistant",
            domain="home_assistant",
            status="unavailable",
            payload={"detail_classification": "connection_error"},
            db_path=self.db_path,
        )
        provider_status.observe_provider_status(
            provider="home_assistant",
            domain="home_assistant",
            status="unavailable",
            payload={"detail_classification": "http_error"},
            db_path=self.db_path,
        )

        snapshot = provider_status.get_provider_status_snapshot(
            "home_assistant",
            "home_assistant",
            db_path=self.db_path,
        )
        self.assertEqual(snapshot["payload"]["detail_classification"], "http_error")
        self.assertEqual(len(list_events(db_path=self.db_path, event_type="provider_unavailable")), 1)

    def test_status_transitions_create_events(self) -> None:
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            db_path=self.db_path,
        )
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="unavailable",
            db_path=self.db_path,
        )
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            db_path=self.db_path,
        )

        self.assertEqual(len(list_events(db_path=self.db_path, event_type="provider_available")), 1)
        self.assertEqual(len(list_events(db_path=self.db_path, event_type="provider_unavailable")), 1)
        self.assertEqual(len(list_events(db_path=self.db_path, event_type="provider_recovered")), 1)

    def test_degraded_status_creates_degraded_event(self) -> None:
        provider_status.observe_provider_status(
            provider="plex",
            domain="music",
            status="degraded",
            db_path=self.db_path,
        )

        [event] = list_events(db_path=self.db_path, event_type="provider_degraded")
        self.assertEqual(event["provider"], "plex")
        self.assertEqual(event["status"], "degraded")

    def test_get_latest_provider_status_returns_exact_provider_domain_snapshot(self) -> None:
        provider_status.observe_provider_status(
            provider="ollama",
            domain="ollama",
            status="available",
            payload={"model": "llama"},
            db_path=self.db_path,
        )

        snapshot = provider_status.get_latest_provider_status("ollama", "ollama", db_path=self.db_path)

        self.assertEqual(snapshot["provider"], "ollama")
        self.assertEqual(snapshot["domain"], "ollama")
        self.assertEqual(snapshot["payload"], {"model": "llama"})
        self.assertEqual(
            provider_status.get_provider_status_snapshot("ollama", "ollama", db_path=self.db_path),
            snapshot,
        )

    def test_get_latest_provider_status_without_domain_returns_newest_provider_snapshot(self) -> None:
        self._insert_snapshot(
            projection_id="provider_status:alpha:shared",
            observed_at="2026-04-25T10:00:00+00:00",
            provider="shared",
            domain="alpha",
            status="available",
            payload_json='{"domain": "alpha"}',
        )
        self._insert_snapshot(
            projection_id="provider_status:beta:shared",
            observed_at="2026-04-25T10:01:00+00:00",
            provider="shared",
            domain="beta",
            status="degraded",
            payload_json='{"domain": "beta"}',
        )

        snapshot = provider_status.get_latest_provider_status("shared", db_path=self.db_path)

        self.assertEqual(snapshot["domain"], "beta")
        self.assertEqual(snapshot["status"], "degraded")

    def test_query_provider_status_snapshots_filters_and_orders_newest_first(self) -> None:
        self._insert_snapshot(
            projection_id="provider_status:home_assistant:home_assistant",
            observed_at="2026-04-25T10:00:00+00:00",
            provider="home_assistant",
            domain="home_assistant",
            status="available",
        )
        self._insert_snapshot(
            projection_id="provider_status:ollama:ollama",
            observed_at="2026-04-25T10:02:00+00:00",
            provider="ollama",
            domain="ollama",
            status="unavailable",
        )
        self._insert_snapshot(
            projection_id="provider_status:music:plex",
            observed_at="2026-04-25T10:02:00+00:00",
            provider="plex",
            domain="music",
            status="degraded",
        )

        self.assertEqual(
            [snapshot["projection_id"] for snapshot in provider_status.query_provider_status_snapshots(db_path=self.db_path)],
            [
                "provider_status:music:plex",
                "provider_status:ollama:ollama",
                "provider_status:home_assistant:home_assistant",
            ],
        )
        self.assertEqual(
            [
                snapshot["projection_id"]
                for snapshot in provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(provider="ollama"),
                    db_path=self.db_path,
                )
            ],
            ["provider_status:ollama:ollama"],
        )
        self.assertEqual(
            [
                snapshot["projection_id"]
                for snapshot in provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(domain="music"),
                    db_path=self.db_path,
                )
            ],
            ["provider_status:music:plex"],
        )
        self.assertEqual(
            [
                snapshot["projection_id"]
                for snapshot in provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(status="unavailable"),
                    db_path=self.db_path,
                )
            ],
            ["provider_status:ollama:ollama"],
        )
        self.assertEqual(
            [
                snapshot["projection_id"]
                for snapshot in provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(provider="plex", domain="music", status="degraded"),
                    db_path=self.db_path,
                )
            ],
            ["provider_status:music:plex"],
        )
        self.assertEqual(
            [
                snapshot["projection_id"]
                for snapshot in provider_status.list_provider_status_snapshots(
                    db_path=self.db_path,
                    status="available",
                )
            ],
            ["provider_status:home_assistant:home_assistant"],
        )

    def test_query_provider_status_snapshots_limits_offsets_and_clamps(self) -> None:
        for index in range(3):
            self._insert_snapshot(
                projection_id=f"provider_status:domain-{index}:provider-{index}",
                observed_at=f"2026-04-25T10:0{index}:00+00:00",
                provider=f"provider-{index}",
                domain=f"domain-{index}",
                status="available",
            )

        self.assertEqual(len(provider_status.query_provider_status_snapshots(db_path=self.db_path)), 3)
        self.assertEqual(
            [snapshot["projection_id"] for snapshot in provider_status.query_provider_status_snapshots(
                provider_status.ProviderStatusQuery(limit=1, offset=1),
                db_path=self.db_path,
            )],
            ["provider_status:domain-1:provider-1"],
        )
        self.assertEqual(
            len(
                provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(limit=0),
                    db_path=self.db_path,
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                provider_status.query_provider_status_snapshots(
                    provider_status.ProviderStatusQuery(limit=999),
                    db_path=self.db_path,
                )
            ),
            3,
        )

    def test_query_provider_status_snapshots_handles_malformed_payload_json(self) -> None:
        self._insert_snapshot(
            projection_id="provider_status:ollama:ollama",
            observed_at="2026-04-25T10:00:00+00:00",
            provider="ollama",
            domain="ollama",
            status="available",
            payload_json="{not-json",
        )
        self._insert_snapshot(
            projection_id="provider_status:music:plex",
            observed_at="2026-04-25T10:01:00+00:00",
            provider="plex",
            domain="music",
            status="available",
            payload_json='["not-object"]',
        )

        snapshots = provider_status.query_provider_status_snapshots(db_path=self.db_path)

        self.assertEqual(
            snapshots[0]["payload"],
            {"_payload_parse_error": True, "raw_payload_json": '["not-object"]'},
        )
        self.assertEqual(
            snapshots[1]["payload"],
            {"_payload_parse_error": True, "raw_payload_json": "{not-json"},
        )

    def test_query_provider_status_snapshots_do_not_write_or_modify_rows(self) -> None:
        self._insert_snapshot(
            projection_id="provider_status:ollama:ollama",
            observed_at="2026-04-25T10:00:00+00:00",
            provider="ollama",
            domain="ollama",
            status="available",
            payload_json='{"model": "llama"}',
        )
        with transaction(self.db_path) as conn:
            before = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_current_projections").fetchone()
            event_count_before = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]

        provider_status.query_provider_status_snapshots(db_path=self.db_path)
        provider_status.list_provider_status_snapshots(db_path=self.db_path)
        provider_status.get_latest_provider_status("ollama", "ollama", db_path=self.db_path)
        provider_status.get_provider_status_snapshot("ollama", "ollama", db_path=self.db_path)

        with transaction(self.db_path) as conn:
            after = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM memory_current_projections").fetchone()
            event_count_after = conn.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0]

        self.assertEqual(tuple(after), tuple(before))
        self.assertEqual(event_count_after, event_count_before)

    def test_safe_observe_provider_health_fails_open_and_logs(self) -> None:
        response = OllamaHealthResponse(status="ok", service="oracle-brain", detail="ok")

        with (
            patch(
                "oracle_app.memory.provider_status.observe_provider_status",
                side_effect=RuntimeError("db unavailable"),
            ),
            self.assertLogs("oracle-brain.memory.provider_status", level="WARNING") as captured,
        ):
            result = provider_status.safe_observe_provider_health("ollama", response, db_path=self.db_path)

        self.assertFalse(result)
        self.assertIn("provider_status_observation_failed provider_key=ollama", "\n".join(captured.output))

    def test_health_endpoint_responses_remain_unchanged_when_observation_succeeds(self) -> None:
        response = HomeAssistantHealthResponse(
            status="ok",
            service="oracle-brain",
            home_assistant_url="http://ha.local",
            detail="ok",
            http_status=200,
        )

        with (
            patch("oracle_app.health_routes.check_home_assistant_health", return_value=response) as mock_check,
            patch("oracle_app.health_routes.safe_observe_provider_health", return_value=True) as mock_observe,
        ):
            returned = api.health_home_assistant()

        self.assertIs(returned, response)
        mock_check.assert_called_once_with()
        mock_observe.assert_called_once_with("home_assistant", response)

    def test_health_endpoint_responses_remain_unchanged_when_observation_fails(self) -> None:
        response = OllamaHealthResponse(status="failed", service="oracle-brain", detail="refused")

        with (
            patch("oracle_app.health_routes.check_ollama_health", return_value=response),
            patch("oracle_app.health_routes.safe_observe_provider_health", return_value=False) as mock_observe,
        ):
            returned = api.health_ollama()

        self.assertIs(returned, response)
        mock_observe.assert_called_once_with("ollama", response)

    def test_approved_health_endpoints_are_wrapped(self) -> None:
        cases = (
            (
                "health_audiobook",
                "check_audiobook_health",
                "audiobookshelf",
                AudiobookHealthResponse(
                    status="ok",
                    service="oracle-brain",
                    audiobookshelf_configured=True,
                    configured_satellites=["test_satellite_bravo"],
                    detail="ok",
                ),
            ),
            (
                "health_calendar",
                "check_calendar_health",
                "calendar",
                CalendarHealthResponse(
                    status="ok",
                    service="oracle-brain",
                    calendar_configured=True,
                    timezone="America/New_York",
                    detail="ok",
                ),
            ),
            (
                "health_music",
                "check_music_health",
                "music",
                MusicHealthResponse(
                    status="ok",
                    service="oracle-brain",
                    plex_configured=True,
                    configured_satellites=["test_satellite_bravo"],
                    detail="ok",
                ),
            ),
            (
                "health_tts",
                "check_tts_health",
                "tts",
                TtsHealthResponse(
                    status="ok",
                    service="oracle-brain",
                    provider="piper",
                    configured=True,
                    available=True,
                    detail="ok",
                ),
            ),
            (
                "health_stt",
                "check_stt_health",
                "stt",
                SttHealthResponse(
                    status="ok",
                    service="oracle-brain",
                    provider="whisper",
                    configured=True,
                    available=True,
                    detail="ok",
                ),
            ),
        )

        for endpoint_name, check_name, provider_key, response in cases:
            with self.subTest(endpoint=endpoint_name):
                with (
                    patch(f"oracle_app.health_routes.{check_name}", return_value=response) as mock_check,
                    patch("oracle_app.health_routes.safe_observe_provider_health", return_value=True) as mock_observe,
                ):
                    returned = getattr(api, endpoint_name)()

                self.assertIs(returned, response)
                mock_check.assert_called_once_with()
                mock_observe.assert_called_once_with(provider_key, response)

    def test_aggregate_health_does_not_observe_provider_status(self) -> None:
        composition = type(
            "Composition",
            (),
            {
                "runtime": type(
                    "Runtime",
                    (),
                    {
                        "home_assistant": type("HomeAssistant", (), {"enabled": True})(),
                        "brain": type(
                            "Brain",
                            (),
                            {"inference": type("Inference", (), {"enabled": True})()},
                        )(),
                    },
                )()
            },
        )()
        with patch("oracle_app.health_routes.safe_observe_provider_health") as mock_observe:
            response = api.canonical_health(composition)

        self.assertEqual(response.status, "ok")
        mock_observe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
