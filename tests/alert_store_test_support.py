from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oracle_app.alerts as alerts_module
from oracle_app.memory.schema import ensure_schema
from oracle_app.memory.store import transaction


class IsolatedAlertStoreTestCase(unittest.TestCase):
    """Give each alert-writing test its own empty persistent store."""

    alert_db_path: Path

    def setUp(self) -> None:
        super().setUp()
        self._alert_tempdir = tempfile.TemporaryDirectory()
        self.alert_db_path = Path(self._alert_tempdir.name) / "oracle-memory.sqlite3"
        self.alert_state_path = self.alert_db_path
        self._alert_path_patch = patch.object(
            alerts_module,
            "ALERT_DB_PATH",
            self.alert_db_path,
        )
        self._alert_path_patch.start()
        ensure_schema(self.alert_db_path)
        with transaction(self.alert_db_path) as conn:
            for source_id in (
                "child-room",
                "kitchen_display",
                "living_room_satellite",
                "living_room_voice",
                "pi-satellite-102",
                "source-a",
                "source-b",
                "test",
                "test-source",
                "test_satellite_alpha",
                "test_satellite_bravo",
            ):
                conn.execute(
                    """INSERT INTO memory_sources (
                           source_id, created_at, updated_at, source_type,
                           display_name, status, payload_json
                       ) VALUES (?, '2026-01-01T00:00:00+00:00',
                                 '2026-01-01T00:00:00+00:00', 'test', ?, 'active', '{}')""",
                    (source_id, source_id),
                )

    def tearDown(self) -> None:
        self._alert_path_patch.stop()
        self._alert_tempdir.cleanup()
        super().tearDown()
