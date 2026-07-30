from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.provider_bridges.network_observations import (
    NetworkMonitoringObservation,
    NetworkProbeObservation,
)


class NetworkObservationDtoTests(unittest.TestCase):
    def test_probe_observation_has_stable_oracle_native_shape(self) -> None:
        observation = NetworkProbeObservation.from_dict(
            {
                "status": "healthy",
                "checked_at": "2026-07-02T18:00:00-04:00",
                "source": "probe",
                "detail": "Direct network checks succeeded.",
                "problems": [],
                "checks": [{"kind": "dns", "status": "healthy", "detail": "DNS passed."}],
            }
        )

        self.assertEqual(observation["status"], "healthy")
        self.assertEqual(observation["checks"][0]["kind"], "dns")
        self.assertEqual(set(observation), {"status", "checked_at", "source", "detail", "problems", "checks"})

    def test_probe_observation_copies_nested_provider_result(self) -> None:
        raw = {"status": "down", "checked_at": "now", "source": "probe", "detail": "failed", "problems": ["dns"]}
        observation = NetworkProbeObservation.from_dict(raw)
        raw["problems"].append("http")

        self.assertEqual(observation["problems"], ["dns"])

    def test_monitoring_observation_preserves_success_collection_contract(self) -> None:
        observation = NetworkMonitoringObservation.from_dict(
            {
                "status": "healthy",
                "checked_at": "2026-07-02T18:00:00-04:00",
                "source": "librenms",
                "detail": "LibreNMS reports no active alerts.",
                "problems": [],
                "alerts": [],
                "alert_count": 0,
                "devices": [],
                "device_count": 0,
                "devices_error": "",
                "services": [],
                "service_count": 0,
                "services_error": "",
                "interfaces": [],
                "interface_count": 0,
                "interfaces_error": "",
            }
        )

        payload = observation.to_dict()
        self.assertEqual(payload["alerts"], [])
        self.assertEqual(payload["services"], [])
        self.assertEqual(payload["interfaces_error"], "")

    def test_monitoring_observation_omits_unavailable_collection_contract(self) -> None:
        observation = NetworkMonitoringObservation.from_dict(
            {
                "status": "unknown",
                "checked_at": "now",
                "source": "librenms",
                "detail": "LibreNMS not configured.",
                "problems": [],
            }
        )

        self.assertNotIn("alerts", observation)
        self.assertNotIn("devices", observation)

    def test_dto_equality_remains_mapping_compatible(self) -> None:
        payload = {
            "status": "unknown",
            "checked_at": "now",
            "source": "probe",
            "detail": "disabled",
            "problems": [],
        }

        self.assertEqual(NetworkProbeObservation.from_dict(payload), payload)


if __name__ == "__main__":
    unittest.main()
