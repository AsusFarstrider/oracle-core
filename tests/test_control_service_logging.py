from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "satellite"))

from control_service_runtime.server import _log_control_event


class ControlServiceLoggingTests(unittest.TestCase):
    def test_control_log_event_includes_correlation_fields(self) -> None:
        with self.assertLogs(level="INFO") as captured:
            _log_control_event(
                "control_command_result",
                command_id="abc123",
                action="pause",
                status="accepted",
                adapter="LocalPlaybackAdapter",
                failure_class="control_service_failure",
                owning_component="satellite.control_service",
                detail="",
            )

        output = "\n".join(captured.output)
        self.assertIn("control_command_result", output)
        self.assertIn("command_id=abc123", output)
        self.assertIn("action=pause", output)
        self.assertIn("status=accepted", output)
        self.assertIn("adapter=LocalPlaybackAdapter", output)
        self.assertIn("failure_class=control_service_failure", output)
        self.assertIn("owning_component=satellite.control_service", output)

    def test_control_log_event_supports_control_command_sent(self) -> None:
        with self.assertLogs(level="INFO") as captured:
            _log_control_event(
                "control_command_sent",
                command_id="xyz789",
                action="play_media",
                status="sent",
                adapter="LocalPlaybackAdapter",
                detail="",
            )

        output = "\n".join(captured.output)
        self.assertIn("control_command_sent", output)
        self.assertIn("command_id=xyz789", output)
        self.assertIn("action=play_media", output)
        self.assertIn("status=sent", output)
        self.assertIn("adapter=LocalPlaybackAdapter", output)


if __name__ == "__main__":
    unittest.main()
