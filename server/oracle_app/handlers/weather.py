from __future__ import annotations

from oracle_app.schemas import DispatchPlan
from oracle_app.weather_runtime import CanonicalWeatherExecution
from oracle_app.weather import build_forecast_response, build_weather_response
from oracle_app.weather_forecast import ForecastOutOfRangeError
from oracle_app.weather_history import build_historical_weather_response
from oracle_app.weather_remote import (
    RemoteForecastOutOfRangeError,
    RemoteWeatherError,
    RemoteWeatherLocationError,
    build_remote_current_weather_response,
    build_remote_forecast_response,
)


class WeatherHandler:
    target = "weather"

    def __init__(
        self,
        canonical_execution: CanonicalWeatherExecution | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.canonical_execution = canonical_execution
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        action = str(dispatch.payload.get("action") or "").strip()

        if action == "current_weather":
            try:
                speech, weather = (
                    self.canonical_execution.build_current_response(str(dispatch.payload.get("text", "")))
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                    if self.canonical_authority
                    else build_weather_response(str(dispatch.payload.get("text", "")))
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "weather_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "current_weather",
                "speech": speech,
                "weather": weather,
            }
            return dispatch

        if action == "weather_forecast":
            try:
                speech, forecast = (
                    self.canonical_execution.build_forecast_response(str(dispatch.payload.get("text", "")))
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                    if self.canonical_authority
                    else build_forecast_response(str(dispatch.payload.get("text", "")))
                )
            except ForecastOutOfRangeError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "forecast_out_of_range",
                    "detail": str(exc),
                }
                return dispatch
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "forecast_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "weather_forecast",
                "speech": speech,
                "forecast": forecast,
            }
            return dispatch

        if action == "weather_history":
            try:
                speech, history = (
                    self.canonical_execution.build_history_response(str(dispatch.payload.get("text", "")))
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                    if self.canonical_authority
                    else build_historical_weather_response(str(dispatch.payload.get("text", "")))
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "weather_history_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "weather_history",
                "speech": speech,
                "history": history,
            }
            return dispatch

        if action == "remote_current_weather":
            try:
                speech, weather = (
                    self.canonical_execution.build_remote_current_response(str(dispatch.payload.get("text", "")))
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                    if self.canonical_authority
                    else build_remote_current_weather_response(str(dispatch.payload.get("text", "")))
                )
            except RemoteWeatherLocationError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": exc.error_code,
                    "detail": str(exc),
                }
                return dispatch
            except RemoteWeatherError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": exc.error_code,
                    "detail": str(exc),
                }
                return dispatch
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "remote_weather_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "remote_current_weather",
                "speech": speech,
                "weather": weather,
            }
            return dispatch

        if action == "remote_weather_forecast":
            try:
                speech, forecast = (
                    self.canonical_execution.build_remote_forecast_response(str(dispatch.payload.get("text", "")))
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                    if self.canonical_authority
                    else build_remote_forecast_response(str(dispatch.payload.get("text", "")))
                )
            except RemoteForecastOutOfRangeError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": exc.error_code,
                    "detail": str(exc),
                }
                return dispatch
            except RemoteWeatherLocationError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": exc.error_code,
                    "detail": str(exc),
                }
                return dispatch
            except RemoteWeatherError as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": exc.error_code,
                    "detail": str(exc),
                }
                return dispatch
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {
                    "error": "remote_weather_unavailable",
                    "detail": str(exc),
                }
                return dispatch

            dispatch.status = "executed"
            dispatch.result = {
                "action": "remote_weather_forecast",
                "speech": speech,
                "forecast": forecast,
            }
            return dispatch

        dispatch.status = "failed"
        dispatch.result = {
            "error": "unknown_weather_action",
            "detail": action,
        }
        return dispatch


def _canonical_weather_unavailable():
    raise RuntimeError("Weather capability is not configured")
