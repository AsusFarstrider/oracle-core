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


ROOT = Path(__file__).resolve().parents[1]


class UiBrowserSurfaceTests(unittest.TestCase):
    def test_ui_mounts_are_registered(self) -> None:
        mounts = {route.path for route in app.routes if route.__class__.__name__ == "Mount"}

        self.assertIn("/ui", mounts)
        self.assertIn("/admin", mounts)

    def test_household_ui_index_exists_with_expected_copy(self) -> None:
        content = (ROOT / "house_ui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Oracle House Alpha", content)
        self.assertIn("data-page-panel=\"home\"", content)
        self.assertIn("data-page-panel=\"audio\"", content)
        self.assertIn("data-page-panel=\"internet\"", content)
        app_script = (ROOT / "house_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Find and fix internet problems", app_script)
        self.assertIn("Approve these fixes", app_script)
        self.assertIn("Possible visible effects", app_script)
        self.assertIn("House Mode", content)

    def test_household_escape_hatches_are_configuration_owned_and_fail_safe(self) -> None:
        script = (ROOT / "house_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("escapeHatches: {}", script)
        self.assertIn("normalizeEscapeHatches(homePayload.escape_hatches)", script)
        self.assertIn('return ESCAPE_HATCH_ICONS.has(normalized) ? normalized : "open_in_new"', script)
        self.assertIn('classList.toggle("is-hidden", hatches.length === 0)', script)
        self.assertIn('href="${escapeHtml(item.url)}"', script)
        normalization = script[
            script.index("function normalizeEscapeHatchIcon"):
            script.index("function toggleMobileNav")
        ]
        self.assertNotIn("http://", normalization)
        self.assertNotIn("https://", normalization)
        self.assertNotIn(".includes(", normalization)
        self.assertNotIn("hostname", normalization)

    def test_operator_ui_index_exists_with_expected_copy(self) -> None:
        content = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("System Oracle", content)
        self.assertIn("data-admin-panel=\"overview\"", content)
        self.assertIn("data-admin-panel=\"activity\"", content)
        self.assertIn("data-admin-panel=\"orchestration\"", content)
        self.assertIn("Structured runtime", content)
        self.assertIn("Switch to House Mode", content)
        script = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")
        self.assertIn("Prepare run", script)
        self.assertIn("Run routine", script)
        self.assertIn("Routine active", script)
        self.assertIn("Cancel routine", script)
        self.assertIn("data-orchestration-input", script)
        self.assertIn("Routine definitions remain file-based", script)
        self.assertIn("Configure later", script)
        self.assertIn("Approve these fixes", script)

    def test_system_mode_assets_exist(self) -> None:
        self.assertTrue((ROOT / "ui" / "system.js").exists())
        self.assertTrue((ROOT / "ui" / "system.css").exists())

    def test_activity_uses_memory_diagnostics_not_log_scraping(self) -> None:
        content = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")

        self.assertIn("/api/admin/memory/diagnostics/summary", content)
        self.assertIn('satellite_limit: "50"', content)
        self.assertIn("renderActivityEvents", content)
        self.assertIn("renderActivityProviders", content)
        self.assertIn("renderActivitySatellites", content)
        self.assertIn("renderActivitySources", content)
        self.assertIn("Network command result", content)
        self.assertIn("Network command started", content)
        self.assertIn("network_control_confirm", content)
        self.assertIn("network_control_started", content)
        self.assertIn("orchestration_routine_started", content)
        self.assertIn("Routine waiting", content)
        self.assertIn("Verification", content)
        self.assertIn("post-action cooldown", content)
        self.assertIn("blocked_by_active", content)
        self.assertIn("Use <a href=\"./logs.html\">Logs</a>", content)
        self.assertNotIn("/api/admin/logs?target=brain&lines=120", content)
        self.assertNotIn("buildActivityItems", content)
        self.assertNotIn("shouldSuppressActivityLine", content)
        self.assertNotIn("Filtered from admin logs", content)

    def test_activity_renders_satellite_status_without_offline_language(self) -> None:
        content = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")

        self.assertIn("Satellite status", content)
        self.assertIn("payload?.satellites?.latest", content)
        self.assertIn("Stale observation", content)
        self.assertIn("Last seen", content)
        self.assertIn("Last wake", content)
        self.assertIn("Last error", content)
        self.assertNotIn("Stale offline", content)

    def test_network_page_renders_control_verification_coverage(self) -> None:
        content = (ROOT / "ui" / "system.js").read_text(encoding="utf-8")

        self.assertIn("/api/admin/network/control/actions", content)
        self.assertIn("Control Verification", content)
        self.assertIn("Enabled, unverified", content)
        self.assertIn("All control actions", content)
        self.assertIn("enabled_unverified", content)

    def test_satellite_ui_schedules_page_snapshot_refreshes(self) -> None:
        content = (ROOT / "satellite_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("refresh_after_seconds", content)
        self.assertIn("schedulePageRefresh", content)
        self.assertIn("data-routine-id", content)
        self.assertIn("/api/ui/orchestrations/", content)
        self.assertIn("DEFAULT_PAGE_REFRESH_SECONDS", content)
        self.assertIn("home: 30", content)
        self.assertIn("calendar: 120", content)
        self.assertIn("weather: 300", content)

    def test_satellite_ui_prioritizes_source_bound_routine_over_calendar_card(self) -> None:
        content = (ROOT / "satellite_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("routineActions.length > 0,", content)
        self.assertIn('selected[calendarIndex] = "routine_actions"', content)
        self.assertIn('const calendarIndex = selected.indexOf("calendar")', content)

    def test_satellite_ui_prioritizes_room_environment_over_calendar_fallback(self) -> None:
        content = (ROOT / "satellite_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("hasRoomEnvironment,", content)
        self.assertIn("hasRoomEnvironment && !selected.includes(\"room_environment\")", content)
        self.assertIn('selected[calendarIndex] = "room_environment"', content)
        self.assertIn("} else if (hasRoomEnvironment", content)

        index = (ROOT / "satellite_ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("app.js?v=27", index)

    def test_browser_surface_paths_remain_repo_local(self) -> None:
        api_text = (ROOT / "server" / "oracle_app" / "api.py").read_text(encoding="utf-8")
        browser_routes_text = (ROOT / "server" / "oracle_app" / "browser_routes.py").read_text(encoding="utf-8")

        self.assertIn("register_browser_routes(app)", api_text)
        self.assertIn('app.mount("/ui", StaticFiles(directory=HOUSE_UI_DIR, html=True), name="oracle-ui")', browser_routes_text)
        self.assertIn(
            'app.mount("/admin", StaticFiles(directory=OPERATOR_UI_DIR, html=True), name="oracle-admin")',
            browser_routes_text,
        )


if __name__ == "__main__":
    unittest.main()
