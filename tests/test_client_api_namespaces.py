from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType


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

from oracle_app.api import app


class ClientApiNamespaceRouteTests(unittest.TestCase):
    def _registered_http_routes(self) -> set[tuple[str, tuple[str, ...]]]:
        registered: set[tuple[str, tuple[str, ...]]] = set()
        for route in app.routes:
            methods = getattr(route, "methods", None)
            path = getattr(route, "path", None)
            if not methods or not path:
                continue
            registered.add((str(path), tuple(sorted(methods))))
        return registered

    def test_voice_namespace_routes_are_registered(self) -> None:
        registered = self._registered_http_routes()

        expected = {
            ("/api/voice/command", ("POST",)),
            ("/api/voice/route", ("POST",)),
            ("/api/voice/ingest/text", ("POST",)),
            ("/api/voice/session", ("GET",)),
            ("/api/voice/command-events", ("GET",)),
            ("/api/voice/alerts/pending", ("GET",)),
            ("/api/voice/tts", ("POST",)),
            ("/api/voice/stt", ("POST",)),
        }

        for route in expected:
            self.assertIn(route, registered)

    def test_admin_namespace_routes_are_registered(self) -> None:
        registered = self._registered_http_routes()

        expected = {
            ("/api/admin/health", ("GET",)),
            ("/api/admin/health/config", ("GET",)),
            ("/api/admin/health/home-assistant", ("GET",)),
            ("/api/admin/health/audiobook", ("GET",)),
            ("/api/admin/health/calendar", ("GET",)),
            ("/api/admin/health/librenms", ("GET",)),
            ("/api/admin/health/ollama", ("GET",)),
            ("/api/admin/health/music", ("GET",)),
            ("/api/admin/health/news", ("GET",)),
            ("/api/admin/health/tts", ("GET",)),
            ("/api/admin/health/stt", ("GET",)),
            ("/api/admin/hooks", ("GET",)),
            ("/api/admin/playback-authority", ("GET",)),
            ("/api/admin/sources", ("GET",)),
            ("/api/admin/log-targets", ("GET",)),
            ("/api/admin/logs", ("GET",)),
            ("/api/admin/memory/diagnostics/summary", ("GET",)),
            ("/api/admin/network/status", ("GET",)),
            ("/api/admin/network/control/dry-run", ("POST",)),
            ("/api/admin/network/control/confirm", ("POST",)),
            ("/api/admin/orchestrations", ("GET",)),
            ("/api/admin/orchestrations/{orchestration_id}", ("GET",)),
            ("/api/admin/suggestions/openclaw/status", ("GET",)),
            ("/api/admin/suggestions", ("GET",)),
            ("/api/admin/suggestions/runs", ("GET",)),
            ("/api/admin/suggestions/runs/{run_id}", ("GET",)),
            ("/api/admin/suggestions/last-packet", ("GET",)),
            ("/api/admin/suggestions/last-response", ("GET",)),
            ("/api/admin/suggestions/{suggestion_id}", ("GET",)),
            ("/api/admin/suggestions/generate", ("POST",)),
            ("/api/admin/suggestions/{suggestion_id}/review", ("POST",)),
        }

        for route in expected:
            self.assertIn(route, registered)

    def test_ui_namespace_routes_are_registered(self) -> None:
        registered = self._registered_http_routes()

        expected = {
            ("/api/ui/home", ("GET",)),
            ("/api/ui/weather", ("GET",)),
            ("/api/ui/calendar", ("GET",)),
            ("/api/ui/audio", ("GET",)),
            ("/api/ui/house", ("GET",)),
            ("/api/ui/internet", ("GET",)),
            ("/api/ui/orchestrations/{orchestration_id}/preview", ("POST",)),
            ("/api/ui/orchestrations/{orchestration_id}/approve", ("POST",)),
            ("/api/ui/orchestrations/{orchestration_id}/run", ("POST",)),
            ("/api/ui/orchestration-previews/{preview_id}", ("GET",)),
            ("/api/ui/orchestration-runs/{run_id}/cancel", ("POST",)),
            ("/api/ui/action", ("POST",)),
        }

        for route in expected:
            self.assertIn(route, registered)

    def test_provider_integration_route_is_registered(self) -> None:
        registered = self._registered_http_routes()

        self.assertIn(
            ("/api/integrations/home-assistant/events", ("POST",)),
            registered,
        )

    def test_operator_static_aliases_are_mounted(self) -> None:
        mounted_paths = {route.path for route in app.routes if route.__class__.__name__ == "Mount"}

        self.assertIn("/ui", mounted_paths)
        self.assertIn("/admin", mounted_paths)

    def test_retired_operator_deep_links_are_not_registered(self) -> None:
        registered = self._registered_http_routes()

        expected = {
            ("/ui/trace.html", ("GET",)),
            ("/ui/logs.html", ("GET",)),
        }

        for route in expected:
            self.assertNotIn(route, registered)

if __name__ == "__main__":
    unittest.main()
