from __future__ import annotations

from dataclasses import dataclass, field

from .domain_models import WeatherConfiguration, WeeWxWeatherProvider, NwsWeatherProvider
from .effective import EffectiveConfig
from .household_runtime_settings import HouseholdRuntimeSettings


@dataclass(frozen=True)
class CurrentWeatherRuntimeSettings:
    enabled: bool
    provider_id: str | None
    current_url: str | None
    timeout_seconds: int | None
    stale_after_seconds: int | None


@dataclass(frozen=True)
class ForecastWeatherRuntimeSettings:
    enabled: bool
    provider_id: str | None
    latitude: float | None
    longitude: float | None
    office: str | None
    user_agent: str | None
    timeout_seconds: int | None


@dataclass(frozen=True)
class WeatherHistorySshRuntimeSettings:
    host: str
    user: str
    password_secret: str
    database_path: str
    timeout_seconds: int
    password: str = field(repr=False)


@dataclass(frozen=True)
class HistoryWeatherRuntimeSettings:
    enabled: bool
    provider_id: str | None
    history_url: str | None
    timeout_seconds: int | None
    ssh_fallback: WeatherHistorySshRuntimeSettings | None


@dataclass(frozen=True)
class RemoteWeatherRuntimeSettings:
    enabled: bool
    provider_id: str | None
    user_agent: str | None
    timeout_seconds: int | None


@dataclass(frozen=True)
class WeatherRuntimeSettings:
    """Frozen Brain execution settings for the optional weather domain role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    enabled: bool
    current: CurrentWeatherRuntimeSettings
    forecast: ForecastWeatherRuntimeSettings
    history: HistoryWeatherRuntimeSettings
    remote: RemoteWeatherRuntimeSettings

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> WeatherRuntimeSettings:
        role = effective.role("domains/weather.yaml")
        if not isinstance(role, WeatherConfiguration):
            raise TypeError("Effective weather role does not use the executable weather schema.")

        current = _current_settings(role)
        forecast = _forecast_settings(effective, role)
        history = _history_settings(effective, role)
        remote = _remote_settings(role)
        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            enabled=role.enabled,
            current=current,
            forecast=forecast,
            history=history,
            remote=remote,
        )


def _current_settings(role: WeatherConfiguration) -> CurrentWeatherRuntimeSettings:
    capability = role.current
    if not capability.enabled:
        return CurrentWeatherRuntimeSettings(False, None, None, None, None)
    provider_id, provider = _selected_weewx(role, capability.provider, "current")
    return CurrentWeatherRuntimeSettings(
        True,
        provider_id,
        str(provider.current_url),
        provider.timeout_seconds,
        provider.stale_after_seconds,
    )


def _forecast_settings(
    effective: EffectiveConfig,
    role: WeatherConfiguration,
) -> ForecastWeatherRuntimeSettings:
    capability = role.forecast
    if not capability.enabled:
        return ForecastWeatherRuntimeSettings(False, None, None, None, None, None, None)
    provider_id, provider = _selected_nws(role, capability.provider, "forecast")
    latitude = provider.latitude
    longitude = provider.longitude
    if latitude is None:
        home_location = HouseholdRuntimeSettings.from_effective_config(effective).household.home_location
        if home_location is not None:
            latitude = home_location.latitude
            longitude = home_location.longitude
    if latitude is None or longitude is None:
        raise ValueError("Enabled canonical home forecast lacks resolved coordinates.")
    return ForecastWeatherRuntimeSettings(
        True,
        provider_id,
        latitude,
        longitude,
        provider.office,
        provider.user_agent,
        provider.timeout_seconds,
    )


def _history_settings(
    effective: EffectiveConfig,
    role: WeatherConfiguration,
) -> HistoryWeatherRuntimeSettings:
    capability = role.history
    if not capability.enabled:
        return HistoryWeatherRuntimeSettings(False, None, None, None, None)
    provider_id, provider = _selected_weewx(role, capability.provider, "history")
    fallback = provider.history_ssh_fallback
    resolved_fallback = None
    if fallback is not None:
        password = effective.secrets.resolve(fallback.password_secret)
        if password is None:
            raise ValueError("Enabled canonical weather history SSH fallback lacks its password.")
        resolved_fallback = WeatherHistorySshRuntimeSettings(
            host=fallback.host,
            user=fallback.user,
            password_secret=fallback.password_secret,
            database_path=str(fallback.database_path),
            timeout_seconds=fallback.timeout_seconds,
            password=password,
        )
    return HistoryWeatherRuntimeSettings(
        True,
        provider_id,
        None if provider.history_url is None else str(provider.history_url),
        provider.timeout_seconds,
        resolved_fallback,
    )


def _remote_settings(role: WeatherConfiguration) -> RemoteWeatherRuntimeSettings:
    capability = role.remote
    if not capability.enabled:
        return RemoteWeatherRuntimeSettings(False, None, None, None)
    provider_id, provider = _selected_nws(role, capability.provider, "remote")
    return RemoteWeatherRuntimeSettings(
        True,
        provider_id,
        provider.user_agent,
        provider.timeout_seconds,
    )


def _selected_weewx(
    role: WeatherConfiguration,
    provider_id: str | None,
    capability: str,
) -> tuple[str, WeeWxWeatherProvider]:
    if provider_id is None:
        raise ValueError(f"Enabled canonical weather {capability} has no selected provider.")
    provider = role.providers[provider_id]
    if not isinstance(provider, WeeWxWeatherProvider):
        raise TypeError(f"Canonical weather {capability} does not select a WeeWX provider.")
    return provider_id, provider


def _selected_nws(
    role: WeatherConfiguration,
    provider_id: str | None,
    capability: str,
) -> tuple[str, NwsWeatherProvider]:
    if provider_id is None:
        raise ValueError(f"Enabled canonical weather {capability} has no selected provider.")
    provider = role.providers[provider_id]
    if not isinstance(provider, NwsWeatherProvider):
        raise TypeError(f"Canonical weather {capability} does not select an NWS provider.")
    return provider_id, provider
