from __future__ import annotations

import logging
import time
from collections.abc import Callable

from fastapi import FastAPI


BuildNoArgSnapshot = Callable[[], dict[str, object]]
BuildSatelliteSnapshot = Callable[[str | None], dict[str, object]]
BuildAudioSnapshot = Callable[[str | None, str | None], dict[str, object]]

_build_ui_home_snapshot: BuildNoArgSnapshot | None = None
_build_satellite_ui_config: BuildSatelliteSnapshot | None = None
_build_satellite_ui_home_snapshot: BuildSatelliteSnapshot | None = None
_build_ui_weather_snapshot: BuildNoArgSnapshot | None = None
_build_ui_calendar_page_snapshot: BuildNoArgSnapshot | None = None
_build_ui_audio_snapshot: BuildAudioSnapshot | None = None
_build_ui_house_snapshot: BuildNoArgSnapshot | None = None

logger = logging.getLogger("oracle-brain.ui.snapshots")


def configure_ui_snapshot_routes(
    *,
    build_ui_home_snapshot: BuildNoArgSnapshot,
    build_satellite_ui_config: BuildSatelliteSnapshot,
    build_satellite_ui_home_snapshot: BuildSatelliteSnapshot,
    build_ui_weather_snapshot: BuildNoArgSnapshot,
    build_ui_calendar_page_snapshot: BuildNoArgSnapshot,
    build_ui_audio_snapshot: BuildAudioSnapshot,
    build_ui_house_snapshot: BuildNoArgSnapshot,
) -> None:
    global _build_ui_home_snapshot
    global _build_satellite_ui_config
    global _build_satellite_ui_home_snapshot
    global _build_ui_weather_snapshot
    global _build_ui_calendar_page_snapshot
    global _build_ui_audio_snapshot
    global _build_ui_house_snapshot

    _build_ui_home_snapshot = build_ui_home_snapshot
    _build_satellite_ui_config = build_satellite_ui_config
    _build_satellite_ui_home_snapshot = build_satellite_ui_home_snapshot
    _build_ui_weather_snapshot = build_ui_weather_snapshot
    _build_ui_calendar_page_snapshot = build_ui_calendar_page_snapshot
    _build_ui_audio_snapshot = build_ui_audio_snapshot
    _build_ui_house_snapshot = build_ui_house_snapshot


def _require_builder(builder):
    if builder is None:
        raise RuntimeError("UI snapshot routes are not configured")
    return builder


def _timed_snapshot(name: str, builder, *args):
    started = time.perf_counter()
    try:
        payload = builder(*args)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("ui_snapshot_failed name=%s elapsed_ms=%.1f", name, elapsed_ms)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info("ui_snapshot_completed name=%s elapsed_ms=%.1f", name, elapsed_ms)
    return payload


def ui_home() -> dict[str, object]:
    return _timed_snapshot("home", _require_builder(_build_ui_home_snapshot))


def satellite_ui_config(satellite_id: str | None = None) -> dict[str, object]:
    return _timed_snapshot("satellite_config", _require_builder(_build_satellite_ui_config), satellite_id)


def ui_satellite_home(satellite_id: str | None = None) -> dict[str, object]:
    return _timed_snapshot("satellite_home", _require_builder(_build_satellite_ui_home_snapshot), satellite_id)


def ui_weather() -> dict[str, object]:
    try:
        return _timed_snapshot("weather", _require_builder(_build_ui_weather_snapshot))
    except Exception as exc:
        return {
            "ok": False,
            "error": "weather_unavailable",
            "detail": str(exc),
        }


def ui_calendar() -> dict[str, object]:
    return _timed_snapshot("calendar", _require_builder(_build_ui_calendar_page_snapshot))


def ui_audio(source: str | None = None, user_id: str | None = None) -> dict[str, object]:
    return _timed_snapshot("audio", _require_builder(_build_ui_audio_snapshot), source, user_id)


def ui_house() -> dict[str, object]:
    return _timed_snapshot("house", _require_builder(_build_ui_house_snapshot))


def register_ui_snapshot_routes(app: FastAPI) -> None:
    app.get("/api/ui/home")(ui_home)
    app.get("/api/satellites/config")(satellite_ui_config)
    app.get("/api/ui/satellite/home")(ui_satellite_home)
    app.get("/api/ui/weather")(ui_weather)
    app.get("/api/ui/calendar")(ui_calendar)
    app.get("/api/ui/audio")(ui_audio)
    app.get("/api/ui/house")(ui_house)
