from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import date, datetime, timezone
from typing import Any
from urllib import error, request

from oracle_app.weather_models import WeatherObservation
from oracle_app.provider_bridges.ssh_transport import SshHostVerificationError, strict_ssh_options


class WeatherStationBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class WeatherStationBridgeConfigurationError(WeatherStationBridgeError):
    pass


class WeeWxWeatherStationBridge:
    provider_name = "weewx"

    def fetch_current_observation(self, *, settings: dict[str, Any]) -> WeatherObservation:
        url = str(settings.get("url") or "").strip()
        if not url:
            raise WeatherStationBridgeConfigurationError("weather_unconfigured", "Weather station URL is not configured")
        return self.fetch_typed_current_observation(
            url=url,
            timeout_seconds=int(settings.get("timeout_seconds") or 8),
            stale_after_seconds=int(settings.get("stale_after_seconds") or 1800),
        )

    def fetch_typed_current_observation(
        self,
        *,
        url: str,
        timeout_seconds: int,
        stale_after_seconds: int,
    ) -> WeatherObservation:
        if not url:
            raise WeatherStationBridgeConfigurationError("weather_unconfigured", "Weather station URL is not configured")
        payload = self._fetch_json(url, timeout_seconds=timeout_seconds)
        if not isinstance(payload, dict):
            raise WeatherStationBridgeError("weather_unavailable", "Weather payload has an unexpected shape")

        generation = str((payload.get("generation") or {}).get("time") or "").strip()
        if not generation:
            raise WeatherStationBridgeError("weather_unavailable", "Weather payload missing generation time")

        generated_at = datetime.strptime(generation, "%Y-%m-%dT%H:%M:%S%z")
        now = datetime.now(timezone.utc).astimezone(generated_at.tzinfo)
        age_seconds = (now - generated_at).total_seconds()

        current = payload.get("current", {})
        if not isinstance(current, dict):
            raise WeatherStationBridgeError("weather_unavailable", "Weather payload missing current conditions")
        temperature_f = self._get_metric(current, "temperature")
        if temperature_f is None:
            raise WeatherStationBridgeError("weather_unavailable", "Weather payload missing temperature")

        station = payload.get("station", {})
        if not isinstance(station, dict):
            station = {}
        day = payload.get("day", {})
        if not isinstance(day, dict):
            day = {}

        return WeatherObservation(
            location=str(station.get("location", "Unknown location")),
            generated_at=generated_at,
            age_seconds=age_seconds,
            temperature_f=temperature_f,
            dewpoint_f=self._get_metric(current, "dewpoint"),
            humidity_pct=self._get_metric(current, "humidity"),
            heat_index_f=self._get_metric(current, "heat index"),
            wind_speed_mph=self._get_metric(current, "wind speed"),
            wind_gust_mph=self._get_metric(current, "wind gust"),
            wind_chill_f=self._get_metric(current, "wind chill"),
            rain_rate_in_h=self._get_metric(current, "rain rate"),
            barometer_inhg=self._get_metric(current, "barometer"),
            inside_temperature_f=self._get_metric(current, "inside temperature"),
            inside_humidity_pct=self._get_metric(current, "inside humidity"),
            rain_total_in=self._get_metric(day, "rain total"),
            wind_direction_deg=self._get_metric(current, "wind direction"),
            source_name="WeeWX",
            source_type="weewx",
            freshness_class=self._classify_freshness(age_seconds, stale_after_seconds=stale_after_seconds),
        )

    def load_static_history_entry(self, target_date: date, *, settings: dict[str, Any]) -> dict[str, object] | None:
        json_url = str(settings.get("json_url") or "").strip()
        return self.load_typed_history_entry(
            target_date,
            history_url=json_url or None,
            timeout_seconds=int(settings.get("timeout_seconds") or 8),
        )

    def load_typed_history_entry(
        self,
        target_date: date,
        *,
        history_url: str | None,
        timeout_seconds: int,
    ) -> dict[str, object] | None:
        json_url = str(history_url or "").strip()
        if not json_url:
            return None
        payload = self._fetch_json(json_url, timeout_seconds=timeout_seconds)
        if not isinstance(payload, dict):
            raise WeatherStationBridgeError("weather_history_unavailable", "Historical weather JSON has an unexpected shape")

        days = payload.get("days") or []
        if not isinstance(days, list):
            raise WeatherStationBridgeError("weather_history_unavailable", "Historical weather JSON has an unexpected shape")

        target_key = target_date.isoformat()
        for item in days:
            if not isinstance(item, dict):
                continue
            if str(item.get("date") or "").strip() == target_key:
                return item
        return None

    def query_day_row(
        self,
        table_name: str,
        target_epoch: int,
        *,
        settings: dict[str, Any],
    ) -> dict[str, object] | None:
        return self.query_typed_day_row(
            table_name,
            target_epoch,
            host=str(settings.get("ssh_host") or "").strip(),
            user=str(settings.get("ssh_user") or "").strip(),
            password=str(settings.get("ssh_password") or "").strip(),
            database_path=str(settings.get("db_path") or "").strip(),
            timeout_seconds=int(settings.get("timeout_seconds") or 8),
        )

    def query_typed_day_row(
        self,
        table_name: str,
        target_epoch: int,
        *,
        host: str,
        user: str,
        password: str,
        database_path: str,
        timeout_seconds: int,
    ) -> dict[str, object] | None:
        db_path = database_path
        if not host or not user or not password or not db_path:
            raise WeatherStationBridgeConfigurationError(
                "weather_history_unconfigured",
                "Historical weather is not configured",
            )
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name) is None:
            raise WeatherStationBridgeConfigurationError(
                "weather_history_invalid_table",
                "Historical weather table is invalid",
            )

        sql = (
            f"select dateTime, min, mintime, max, maxtime, sum, count, wsum, sumtime "
            f"from {table_name} where dateTime = {target_epoch};"
        )
        remote_cmd = shlex.join(["sqlite3", "-separator", "|", db_path, sql])
        try:
            command_environment = os.environ.copy()
            command_environment["SSHPASS"] = password
            completed = subprocess.run(
                [
                    "sshpass",
                    "-e",
                    "ssh",
                    *strict_ssh_options(connect_timeout_seconds=timeout_seconds),
                    f"{user}@{host}",
                    remote_cmd,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=command_environment,
            )
        except SshHostVerificationError as exc:
            raise WeatherStationBridgeConfigurationError(
                "weather_history_host_verification_unconfigured",
                "Historical weather SSH host verification is not configured",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WeatherStationBridgeError("weather_history_unavailable", "Historical weather query timed out") from exc
        if completed.returncode != 0:
            raise WeatherStationBridgeError(
                "weather_history_unavailable",
                completed.stderr.strip() or "Historical weather query failed",
            )

        row = completed.stdout.strip()
        if not row:
            return None
        parts = row.split("|")
        if len(parts) != 9:
            raise WeatherStationBridgeError(
                "weather_history_unavailable",
                "Historical weather query returned an unexpected shape",
            )
        return {
            "dateTime": int(parts[0]),
            "min": self._to_float(parts[1]),
            "mintime": self._to_int(parts[2]),
            "max": self._to_float(parts[3]),
            "maxtime": self._to_int(parts[4]),
            "sum": self._to_float(parts[5]),
            "count": self._to_int(parts[6]),
            "wsum": self._to_float(parts[7]),
            "sumtime": self._to_int(parts[8]),
        }

    def _fetch_json(self, url: str, *, timeout_seconds: int) -> dict[str, Any] | list[Any]:
        try:
            with request.urlopen(url, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WeatherStationBridgeError("weather_unavailable", detail or f"WeeWX returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise WeatherStationBridgeError("weather_unavailable", str(exc.reason)) from exc
        except json.JSONDecodeError as exc:
            raise WeatherStationBridgeError("weather_unavailable", "WeeWX returned invalid JSON") from exc

    def _get_metric(self, current: dict[str, Any], key: str) -> float | None:
        metric = current.get(key)
        if not isinstance(metric, dict):
            return None
        value = metric.get("value")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _classify_freshness(self, age_seconds: float, *, stale_after_seconds: int) -> str:
        if age_seconds <= stale_after_seconds / 3:
            return "fresh"
        if age_seconds <= stale_after_seconds:
            return "aging"
        return "stale"

    def _to_float(self, value: str) -> float | None:
        if value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _to_int(self, value: str) -> int | None:
        if value == "":
            return None
        try:
            return int(value)
        except ValueError:
            return None


def get_weather_station_bridge(settings: dict[str, Any]) -> WeeWxWeatherStationBridge:
    provider = str(settings.get("provider") or "weewx").strip().lower()
    if provider == "weewx":
        return WeeWxWeatherStationBridge()
    raise WeatherStationBridgeConfigurationError(
        "weather_unconfigured",
        f"Unsupported weather station provider: {provider}",
    )
