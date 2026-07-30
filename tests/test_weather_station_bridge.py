from __future__ import annotations

import json
import shlex
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.provider_bridges.weewx_weather_station import (
    WeatherStationBridgeConfigurationError,
    WeeWxWeatherStationBridge,
    get_weather_station_bridge,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class WeatherStationBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._ssh_tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._ssh_tempdir.cleanup)
        self.known_hosts_path = Path(self._ssh_tempdir.name) / "known_hosts"
        self.known_hosts_path.write_text("weather.local ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n")
        self.known_hosts_path.chmod(0o600)
        ssh_environment = patch.dict(
            "os.environ", {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(self.known_hosts_path)}
        )
        ssh_environment.start()
        self.addCleanup(ssh_environment.stop)

    def _current_settings(self) -> dict:
        return {
            "provider": "weewx",
            "url": "http://weather.local/weewx.json",
            "timeout_seconds": 8,
            "stale_after_seconds": 1800,
        }

    def _history_settings(self) -> dict:
        return {
            "provider": "weewx",
            "json_url": "http://weather.local/oracle_history.json",
            "ssh_host": "weather.local",
            "ssh_user": "oracle",
            "ssh_password": "secret",
            "db_path": "/var/lib/weewx/archive.sdb",
            "timeout_seconds": 8,
        }

    @patch("oracle_app.provider_bridges.weewx_weather_station.request.urlopen")
    def test_fetch_current_observation_returns_oracle_weather_observation(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "station": {"location": "Home"},
                "generation": {"time": "2026-05-06T10:00:00+0000"},
                "current": {
                    "temperature": {"value": 61.2},
                    "dewpoint": {"value": 55.0},
                    "humidity": {"value": 80.0},
                    "wind speed": {"value": 5.5},
                    "wind gust": {"value": 12.0},
                    "wind direction": {"value": 90.0},
                    "rain rate": {"value": 0.01},
                    "barometer": {"value": 29.91},
                    "inside temperature": {"value": 69.0},
                    "inside humidity": {"value": 48.0},
                },
                "day": {"rain total": {"value": 0.57}},
            }
        )

        observation = WeeWxWeatherStationBridge().fetch_current_observation(settings=self._current_settings())

        self.assertEqual(observation.location, "Home")
        self.assertEqual(observation.temperature_f, 61.2)
        self.assertEqual(observation.source_type, "weewx")
        self.assertEqual(observation.rain_total_in, 0.57)

    @patch("oracle_app.provider_bridges.weewx_weather_station.request.urlopen")
    def test_load_static_history_entry_returns_matching_day(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _Response(
            {
                "days": [
                    {"date": "2026-05-05", "temperature_max_f": 63.0},
                    {"date": "2026-05-06", "temperature_max_f": 70.0},
                ]
            }
        )

        entry = WeeWxWeatherStationBridge().load_static_history_entry(
            date(2026, 5, 6),
            settings=self._history_settings(),
        )

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["temperature_max_f"], 70.0)

    @patch("oracle_app.provider_bridges.weewx_weather_station.subprocess.run")
    def test_query_day_row_parses_weewx_archive_row(self, mock_run) -> None:
        mock_run.return_value = Mock(
            returncode=0,
            stdout="1777939200|42.4|1|63.3|2|0.26|3|4358751.0|85800\n",
            stderr="",
        )

        row = WeeWxWeatherStationBridge().query_day_row(
            "archive_day_outTemp",
            1777939200,
            settings=self._history_settings(),
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["min"], 42.4)
        self.assertEqual(row["sum"], 0.26)
        self.assertEqual(row["sumtime"], 85800)
        command = mock_run.call_args.args[0]
        self.assertEqual(command[:3], ["sshpass", "-e", "ssh"])
        self.assertNotIn("secret", command)
        self.assertEqual(mock_run.call_args.kwargs["env"]["SSHPASS"], "secret")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(f"UserKnownHostsFile={self.known_hosts_path}", command)
        self.assertEqual(
            shlex.split(command[-1]),
            [
                "sqlite3",
                "-separator",
                "|",
                "/var/lib/weewx/archive.sdb",
                (
                    "select dateTime, min, mintime, max, maxtime, sum, count, wsum, sumtime "
                    "from archive_day_outTemp where dateTime = 1777939200;"
                ),
            ],
        )

    def test_query_day_row_rejects_unbounded_table_name(self) -> None:
        with self.assertRaises(WeatherStationBridgeConfigurationError):
            WeeWxWeatherStationBridge().query_typed_day_row(
                "archive_day_outTemp; reboot",
                1777939200,
                host="weather.local",
                user="oracle",
                password="secret",
                database_path="/var/lib/weewx/archive.sdb",
                timeout_seconds=8,
            )

    def test_get_weather_station_bridge_rejects_unsupported_provider(self) -> None:
        settings = self._current_settings()
        settings["provider"] = "other"

        with self.assertRaises(WeatherStationBridgeConfigurationError):
            get_weather_station_bridge(settings)


if __name__ == "__main__":
    unittest.main()
