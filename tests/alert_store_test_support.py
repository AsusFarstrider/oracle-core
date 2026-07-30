from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oracle_app.alerts as alerts_module


class IsolatedAlertStoreTestCase(unittest.TestCase):
    """Give each alert-writing test its own empty persistent store."""

    alert_state_path: Path

    def setUp(self) -> None:
        super().setUp()
        self._alert_tempdir = tempfile.TemporaryDirectory()
        self.alert_state_path = Path(self._alert_tempdir.name) / "alerts-state.json"
        self._alert_path_patch = patch.object(
            alerts_module,
            "ALERTS_STATE_PATH",
            self.alert_state_path,
        )
        self._alert_path_patch.start()
        with alerts_module._LOCK:
            self._original_alerts = list(alerts_module._ALERTS)
            alerts_module._ALERTS.clear()

    def tearDown(self) -> None:
        with alerts_module._LOCK:
            alerts_module._ALERTS[:] = self._original_alerts
        self._alert_path_patch.stop()
        self._alert_tempdir.cleanup()
        super().tearDown()
