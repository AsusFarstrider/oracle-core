from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib import error, parse, request

from oracle_app.read_cache import BoundedReadCache
from oracle_app.weather_models import ResolvedRemoteLocation
from oracle_app.provider_bridges.nws_weather_forecast import (
    NwsWeatherForecastBridge,
    WeatherForecastBridgeError,
)


class RemoteWeatherBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class RemoteWeatherBridgeLocationError(RemoteWeatherBridgeError):
    pass


class NominatimNwsRemoteWeatherBridge:
    """Bounded location and NWS current/alert mechanics for remote Weather."""

    provider_name = "nominatim_nws"

    def __init__(self) -> None:
        self._location_cache: BoundedReadCache[ResolvedRemoteLocation] = BoundedReadCache()
        self._current_cache: BoundedReadCache[dict[str, Any]] = BoundedReadCache()
        self._forecast_cache: BoundedReadCache[dict[str, Any]] = BoundedReadCache()
        self._alert_cache: BoundedReadCache[list[dict[str, Any]]] = BoundedReadCache()

    def resolve_location(
        self, location_text: str, *, user_agent: str, timeout_seconds: int
    ) -> ResolvedRemoteLocation:
        stripped = " ".join(location_text.strip().split())
        if stripped.isalpha() and len(stripped) <= 3 and " " not in stripped:
            raise RemoteWeatherBridgeLocationError(
                "remote_weather_location_unresolved",
                f"{stripped.upper()} is too ambiguous. Please say the city and state.",
            )
        return self._location_cache.read(
            f"location:{stripped.casefold()}",
            ttl_seconds=24 * 60 * 60,
            stale_max_seconds=24 * 60 * 60,
            allow_stale=False,
            loader=lambda: self._resolve_uncached(
                stripped, user_agent=user_agent, timeout_seconds=timeout_seconds
            ),
        ).value

    def fetch_current(
        self,
        location: ResolvedRemoteLocation,
        *,
        user_agent: str,
        timeout_seconds: int,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        cached = self._current_cache.read(
            f"current:{location.latitude:.4f}:{location.longitude:.4f}",
            ttl_seconds=5 * 60,
            stale_max_seconds=30 * 60,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
            loader=lambda: self._fetch_current_uncached(
                location, user_agent=user_agent, timeout_seconds=timeout_seconds
            ),
        )
        return {
            **cached.value,
            "freshness": cached.freshness,
            "cache_age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    def fetch_alerts(
        self,
        *,
        latitude: float,
        longitude: float,
        user_agent: str,
        timeout_seconds: int,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        cached = self._alert_cache.read(
            f"alerts:{latitude:.4f}:{longitude:.4f}",
            ttl_seconds=2 * 60,
            stale_max_seconds=15 * 60,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
            loader=lambda: self._fetch_alerts_uncached(
                latitude, longitude, user_agent=user_agent, timeout_seconds=timeout_seconds
            ),
        )
        return {
            "alerts": cached.value,
            "freshness": cached.freshness,
            "age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    def fetch_forecast(
        self,
        location: ResolvedRemoteLocation,
        *,
        user_agent: str,
        timeout_seconds: int,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> dict[str, Any]:
        cached = self._forecast_cache.read(
            f"forecast:{location.latitude:.4f}:{location.longitude:.4f}",
            ttl_seconds=10 * 60,
            stale_max_seconds=2 * 60 * 60,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
            loader=lambda: self._load_forecast(
                location, user_agent=user_agent, timeout_seconds=timeout_seconds
            ),
        )
        return {
            **cached.value,
            "freshness": cached.freshness,
            "age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "refresh_status": cached.refresh_status,
            "failure_kind": cached.failure_kind,
        }

    @staticmethod
    def _load_forecast(
        location: ResolvedRemoteLocation, *, user_agent: str, timeout_seconds: int
    ) -> dict[str, Any]:
        try:
            return NwsWeatherForecastBridge().fetch_typed_forecast_for_coordinates(
                latitude=location.latitude,
                longitude=location.longitude,
                user_agent=user_agent,
                timeout_seconds=timeout_seconds,
            )
        except WeatherForecastBridgeError as exc:
            if exc.error_code == "forecast_location_out_of_range":
                raise RemoteWeatherBridgeLocationError(
                    "remote_weather_location_unresolved",
                    "That location is outside the current remote forecast coverage.",
                ) from exc
            raise RemoteWeatherBridgeError("remote_weather_unavailable", exc.detail) from exc

    def _resolve_uncached(
        self, location_text: str, *, user_agent: str, timeout_seconds: int
    ) -> ResolvedRemoteLocation:
        url = "https://nominatim.openstreetmap.org/search?" + parse.urlencode(
            {"q": location_text, "format": "jsonv2", "addressdetails": 1, "limit": 3}
        )
        payload = self._get_json(url, user_agent=user_agent, timeout_seconds=timeout_seconds)
        matches = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        if not matches:
            raise RemoteWeatherBridgeLocationError(
                "remote_weather_location_unresolved", f"I couldn't resolve the location {location_text}."
            )
        top = matches[0]
        top_name = str(top.get("name") or "").strip().casefold()
        top_importance = float(top.get("importance") or 0.0)
        for candidate in matches[1:]:
            if (
                str(candidate.get("name") or "").strip().casefold() == top_name
                and abs(top_importance - float(candidate.get("importance") or 0.0)) <= 0.05
            ):
                raise RemoteWeatherBridgeLocationError(
                    "remote_weather_location_unresolved",
                    f"{location_text.title()} is ambiguous. Please say the city and state.",
                )
        address = top.get("address") if isinstance(top.get("address"), dict) else {}
        city = next(
            (str(address.get(key) or "").strip() for key in ("city", "town", "village") if address.get(key)),
            str(top.get("name") or "").strip(),
        )
        state = str(address.get("state") or "").strip() or None
        country = str(address.get("country") or "").strip() or None
        label = ", ".join(part for part in (city, state) if part) or str(top.get("display_name") or location_text)
        try:
            latitude, longitude = float(top["lat"]), float(top["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RemoteWeatherBridgeLocationError(
                "remote_weather_location_unresolved", f"I couldn't resolve the location {location_text}."
            ) from exc
        return ResolvedRemoteLocation(
            query_text=location_text,
            label=label,
            latitude=latitude,
            longitude=longitude,
            city=city or None,
            state=state,
            country=country,
        )

    def _fetch_current_uncached(
        self,
        location: ResolvedRemoteLocation,
        *,
        user_agent: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        point = self._get_json(
            f"https://api.weather.gov/points/{location.latitude:.4f},{location.longitude:.4f}",
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
        )
        point_props = point.get("properties") if isinstance(point, dict) else {}
        point_props = point_props if isinstance(point_props, dict) else {}
        stations_url = str(point_props.get("observationStations") or "").strip()
        if not stations_url:
            raise RemoteWeatherBridgeLocationError(
                "remote_weather_location_unresolved", "That location is outside the current remote weather coverage."
            )
        stations = self._get_json(stations_url, user_agent=user_agent, timeout_seconds=timeout_seconds)
        features = stations.get("features") if isinstance(stations, dict) else []
        if not isinstance(features, list) or not features:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", "No observation stations were returned.")
        station_props = features[0].get("properties") if isinstance(features[0], dict) else {}
        station_id = str((station_props or {}).get("stationIdentifier") or "").strip()
        if not station_id:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", "No remote station identifier was available.")
        observation = self._get_json(
            f"https://api.weather.gov/stations/{station_id}/observations/latest",
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
        )
        return self._normalize_current(location, point_props, observation)

    def _fetch_alerts_uncached(
        self,
        latitude: float,
        longitude: float,
        *,
        user_agent: str,
        timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        payload = self._get_json(
            f"https://api.weather.gov/alerts/active?point={latitude:.4f},{longitude:.4f}",
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
        )
        features = payload.get("features") if isinstance(payload, dict) else []
        output: list[dict[str, Any]] = []
        for feature in features if isinstance(features, list) else []:
            props = feature.get("properties") if isinstance(feature, dict) else None
            if not isinstance(props, dict):
                continue
            output.append({
                "id": str(feature.get("id") or props.get("id") or "").strip(),
                "event": str(props.get("event") or "").strip(),
                "headline": str(props.get("headline") or "").strip(),
                "severity": str(props.get("severity") or "Unknown").strip(),
                "urgency": str(props.get("urgency") or "Unknown").strip(),
                "effective": str(props.get("effective") or "").strip() or None,
                "expires": str(props.get("expires") or "").strip() or None,
                "source_name": "National Weather Service",
            })
        return output[:8]

    def _normalize_current(
        self, location: ResolvedRemoteLocation, point_props: dict[str, Any], observation: Any
    ) -> dict[str, Any]:
        obs_props = observation.get("properties") if isinstance(observation, dict) else {}
        obs_props = obs_props if isinstance(obs_props, dict) else {}
        timestamp = str(obs_props.get("timestamp") or "").strip()
        if not timestamp:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", "Remote observation missing timestamp.")
        generated_at = datetime.fromisoformat(timestamp)
        age_seconds = max(0.0, (datetime.now(timezone.utc).astimezone(generated_at.tzinfo) - generated_at).total_seconds())
        temperature_f = _c_to_f(_metric(obs_props, "temperature"))
        if temperature_f is None:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", "Remote observation missing temperature.")
        relative = ((point_props.get("relativeLocation") or {}).get("properties") or {})
        label = ", ".join(
            part for part in (
                str(relative.get("city") or "").strip() or location.city,
                str(relative.get("state") or "").strip() or location.state,
            ) if part
        ) or location.label
        return {
            "location": label,
            "requested_location": location.query_text,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "observation_timestamp": generated_at.isoformat(),
            "age_seconds": age_seconds,
            "freshness_class": "fresh" if age_seconds <= 1200 else "aging" if age_seconds <= 3600 else "stale",
            "source_name": "National Weather Service",
            "source_type": "nws_observation",
            "temperature_f": temperature_f,
            "humidity_pct": _metric(obs_props, "relativeHumidity"),
            "barometer_inhg": _pa_to_inhg(_metric(obs_props, "barometricPressure")),
            "wind_speed_mph": _kmh_to_mph(_metric(obs_props, "windSpeed")),
            "wind_gust_mph": _kmh_to_mph(_metric(obs_props, "windGust")),
            "wind_direction_deg": _metric(obs_props, "windDirection"),
            "rain_rate_in_h": None,
            "station_id": str(obs_props.get("stationId") or "").strip(),
            "station_name": str(obs_props.get("stationName") or "").strip(),
            "text_description": str(obs_props.get("textDescription") or "").strip(),
            "provider_location_label": location.label,
        }

    @staticmethod
    def _get_json(url: str, *, user_agent: str, timeout_seconds: int) -> dict[str, Any] | list[Any]:
        req = request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/geo+json"})
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RemoteWeatherBridgeError("remote_weather_unavailable", detail or f"Provider returned HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError) as exc:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise RemoteWeatherBridgeError("remote_weather_unavailable", "Provider returned invalid JSON.") from exc


def _metric(properties: dict[str, Any], key: str) -> float | None:
    item = properties.get(key)
    value = item.get("value") if isinstance(item, dict) else None
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _c_to_f(value: float | None) -> float | None:
    return None if value is None else value * 9.0 / 5.0 + 32.0


def _kmh_to_mph(value: float | None) -> float | None:
    return None if value is None else value * 0.621371


def _pa_to_inhg(value: float | None) -> float | None:
    return None if value is None else value * 0.000295299830714
