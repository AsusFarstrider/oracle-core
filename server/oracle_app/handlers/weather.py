from __future__ import annotations

from oracle_app.schemas import DispatchPlan
from oracle_app.weather_runtime import CanonicalWeatherExecution
from oracle_app.weather_forecast import ForecastOutOfRangeError
from oracle_app.weather_context import resolve_weather_context, retain_weather_context
from oracle_app.weather_solar import SolarWeatherError
from oracle_app.weather_remote import (
    RemoteForecastOutOfRangeError,
    RemoteWeatherError,
    RemoteWeatherLocationError,
)


class WeatherHandler:
    target = "weather"

    def __init__(
        self,
        canonical_execution: CanonicalWeatherExecution | None = None,
    ) -> None:
        self.canonical_execution = canonical_execution

    def handle(self, dispatch: DispatchPlan, registry: object) -> DispatchPlan:
        action = str(dispatch.payload.get("action") or "").strip()
        query_text = str(dispatch.payload.get("text", ""))
        resolution = resolve_weather_context(
            query_text,
            action,
            source=dispatch.payload.get("source"),
            session_id=dispatch.payload.get("session_id"),
        )
        if resolution.prompt is not None:
            dispatch.status = "pending_clarification"
            dispatch.result = {"action": resolution.action, "speech": resolution.prompt}
            return dispatch
        action = resolution.action
        query_text = resolution.query

        if action == "current_weather":
            try:
                speech, weather = (
                    self.canonical_execution.build_current_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
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
            self._retain(action, query_text, weather, dispatch)
            return dispatch

        if action == "weather_forecast":
            try:
                speech, forecast = (
                    self.canonical_execution.build_forecast_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
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
            self._retain(action, query_text, forecast, dispatch)
            return dispatch

        if action == "weather_history":
            try:
                speech, history = (
                    self.canonical_execution.build_history_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
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
            self._retain(action, query_text, history, dispatch)
            return dispatch

        if action == "remote_current_weather":
            try:
                speech, weather = (
                    self.canonical_execution.build_remote_current_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
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
            self._retain(action, query_text, weather, dispatch)
            return dispatch

        if action == "remote_weather_forecast":
            try:
                speech, forecast = (
                    self.canonical_execution.build_remote_forecast_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
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
            self._retain(action, query_text, forecast, dispatch)
            return dispatch

        if action == "weather_solar":
            try:
                speech, solar = (
                    self.canonical_execution.build_solar_response(query_text)
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                )
            except SolarWeatherError as exc:
                dispatch.status = "failed"
                dispatch.result = {"action": action, "error": exc.error_code, "detail": str(exc)}
                return dispatch
            dispatch.status = "executed"
            dispatch.result = {"action": action, "speech": speech, "solar": solar}
            self._retain(action, query_text, solar, dispatch)
            return dispatch

        if action == "weather_alerts":
            try:
                speech, alerts = (
                    self.canonical_execution.build_alerts_response()
                    if self.canonical_execution is not None
                    else _canonical_weather_unavailable()
                )
            except Exception as exc:
                dispatch.status = "failed"
                dispatch.result = {"action": action, "error": "weather_alerts_unavailable", "detail": str(exc)}
                return dispatch
            dispatch.status = "executed"
            dispatch.result = {"action": action, "speech": speech, "alerts": alerts}
            return dispatch

        dispatch.status = "failed"
        dispatch.result = {
            "error": "unknown_weather_action",
            "detail": action,
        }
        return dispatch

    @staticmethod
    def _retain(action: str, query_text: str, result: dict[str, object], dispatch: DispatchPlan) -> None:
        retain_weather_context(
            action=action,
            query_text=query_text,
            result=result,
            source=dispatch.payload.get("source"),
            session_id=dispatch.payload.get("session_id"),
        )


def _canonical_weather_unavailable():
    raise RuntimeError("Weather capability is not configured")
