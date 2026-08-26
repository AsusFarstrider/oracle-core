from __future__ import annotations

import subprocess

from fastapi import FastAPI, HTTPException, Request

from .music_runtime.canonical import CanonicalMusicExecution
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .configuration.satellite_fleet_runtime_settings import SatelliteFleetRuntimeSettings
from .memory.diagnostics import DiagnosticsSummaryQuery, build_memory_diagnostics_summary
from .music_runtime.control import ControlPlaneError, serialize_control_plane_error


def build_log_targets(*, fleet_settings: SatelliteFleetRuntimeSettings | None = None) -> list[dict[str, object]]:
    satellites = {
        item.source_id: {}
        for item in (() if fleet_settings is None else fleet_settings.satellites.values())
        if item.enabled and item.playback_capable and item.source_id
    }
    targets: list[dict[str, object]] = [
        {
            "target": "brain",
            "label": "Brain",
            "available": True,
            "detail": "Recent oracle-brain.service journal tail from the current host.",
        }
    ]
    for source_name in sorted(satellites):
        suffix = source_name.split("-")[-1]
        targets.append(
            {
                "target": f"satellite:{source_name}",
                "label": f"Satellite {suffix}",
                "available": False,
                "detail": "Remote satellite journald is not exposed through the brain UI yet.",
            }
        )
        targets.append(
            {
                "target": f"control:{source_name}",
                "label": f"Control Service {suffix}",
                "available": False,
                "detail": "Remote control-service journald is not exposed through the brain UI yet.",
            }
        )
    return targets


def read_brain_log_tail(lines: int) -> dict[str, object]:
    bounded_lines = max(20, min(lines, 400))
    command = [
        "journalctl",
        "-u",
        "oracle-brain.service",
        "-n",
        str(bounded_lines),
        "--no-pager",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "target": "brain",
            "lines": bounded_lines,
            "detail": "Timed out while reading oracle-brain.service logs.",
            "content": "",
        }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "journalctl failed").strip()
        return {
            "ok": False,
            "target": "brain",
            "lines": bounded_lines,
            "detail": detail,
            "content": completed.stdout or "",
        }
    return {
        "ok": True,
        "target": "brain",
        "lines": bounded_lines,
        "detail": "Recent oracle-brain.service log tail.",
        "content": completed.stdout,
    }


def ui_playback_authority(
    source: str | None = None,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    fleet_settings: SatelliteFleetRuntimeSettings | None = None,
) -> dict[str, object]:
    satellites = {
        item.source_id: {"playback_capable": True}
        for item in (() if fleet_settings is None else fleet_settings.satellites.values())
        if item.enabled and item.playback_capable and item.source_id
    }
    def fetch_authority(source_id):
        if music_execution is not None and music_execution.settings.playback_target(source_id) is not None:
            return music_execution.fetch_playback_authority(source_id)
        if audiobook_execution is not None and audiobook_execution.settings.playback_target(source_id) is not None:
            return audiobook_execution.fetch_playback_authority(source_id)
        raise HTTPException(status_code=409, detail="Playback source is not admitted by an enabled media domain.")
    if source is not None:
        requested_source = str(source).strip()
        if not requested_source:
            raise HTTPException(status_code=400, detail="source cannot be empty")
        target = satellites.get(requested_source)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Unknown playback source {requested_source}")
        if not bool(target.get("playback_capable")):
            raise HTTPException(status_code=400, detail=f"Source {requested_source} is not playback-capable")
        try:
            authority = fetch_authority(requested_source)
        except ControlPlaneError as exc:
            return {"ok": False, "source": requested_source, **serialize_control_plane_error(exc)}
        return {"ok": True, "source": requested_source, "authority": authority}

    configured_sources = sorted(
        source_name for source_name, target in satellites.items() if bool(target.get("playback_capable"))
    )
    payloads: list[dict[str, object]] = []
    overall_ok = True
    for configured_source in configured_sources:
        try:
            authority = fetch_authority(configured_source)
            payloads.append({"source": configured_source, "ok": True, "authority": authority})
        except ControlPlaneError as exc:
            overall_ok = False
            payloads.append({"source": configured_source, "ok": False, **serialize_control_plane_error(exc)})
    return {
        "ok": overall_ok,
        "configured_sources": configured_sources,
        "sources": payloads,
    }


def ui_sources(*, fleet_settings: SatelliteFleetRuntimeSettings | None = None) -> dict[str, object]:
    sources = [
        {
            "source": source_id,
            "playback_capable": True,
            "supports_oracle_native_music": True,
            "supports_plexamp": False,
        }
        for source_id in sorted(
            item.source_id
            for item in (() if fleet_settings is None else fleet_settings.satellites.values())
            if item.enabled and item.playback_capable and item.source_id
        )
    ]
    return {"ok": True, "sources": sources}


def ui_log_targets() -> dict[str, object]:
    return {"ok": True, "targets": build_log_targets()}


def ui_logs(
    target: str = "brain",
    lines: int = 120,
    *,
    fleet_settings: SatelliteFleetRuntimeSettings | None = None,
) -> dict[str, object]:
    normalized_target = str(target).strip().lower() or "brain"
    if normalized_target == "brain":
        return read_brain_log_tail(lines)

    known_targets = {
        str(item["target"])
        for item in build_log_targets(fleet_settings=fleet_settings)
    }
    if normalized_target not in known_targets:
        raise HTTPException(status_code=404, detail=f"Unknown log target {target}")
    return {
        "ok": False,
        "target": normalized_target,
        "lines": max(20, min(lines, 400)),
        "detail": "This log target is not available from the brain UI yet.",
        "content": "",
    }


def admin_memory_diagnostics_summary(
    observed_after: str | None = None,
    observed_before: str | None = None,
    event_limit: int = 100,
    provider_limit: int = 100,
    source_limit: int = 100,
    satellite_limit: int = 100,
    event_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    domain: str | None = None,
    provider: str | None = None,
    source_type: str | None = None,
    satellite_source_id: str | None = None,
    satellite_status: str | None = None,
) -> dict[str, object]:
    return build_memory_diagnostics_summary(
        DiagnosticsSummaryQuery(
            observed_after=observed_after,
            observed_before=observed_before,
            event_limit=event_limit,
            provider_limit=provider_limit,
            source_limit=source_limit,
            satellite_limit=satellite_limit,
            event_type=event_type,
            severity=severity,
            status=status,
            domain=domain,
            provider=provider,
            source_type=source_type,
            satellite_source_id=satellite_source_id,
            satellite_status=satellite_status,
        )
    )


def register_admin_diagnostics_routes(app: FastAPI) -> None:
    app.get("/api/admin/playback-authority")(ui_playback_authority_http)
    app.get("/api/admin/sources")(ui_sources_http)
    app.get("/api/admin/log-targets")(ui_log_targets_http)
    app.get("/api/admin/logs")(ui_logs_http)
    app.get("/api/admin/memory/diagnostics/summary")(admin_memory_diagnostics_summary)


def _canonical_music(request: Request):
    from .brain_application_composition import (
        BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
        CanonicalBrainApplicationComposition,
    )

    composition = getattr(getattr(request.scope.get("app"), "state", None), BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    canonical = isinstance(composition, CanonicalBrainApplicationComposition)
    if not canonical:
        raise HTTPException(status_code=503, detail="Canonical application composition is unavailable.")
    return composition.music_execution, composition.audiobook_execution, composition.runtime.satellites


def ui_playback_authority_http(request: Request, source: str | None = None) -> dict[str, object]:
    music, audiobooks, fleet = _canonical_music(request)
    return ui_playback_authority(source, music_execution=music, audiobook_execution=audiobooks, fleet_settings=fleet)


def ui_sources_http(request: Request) -> dict[str, object]:
    _music, _audiobooks, fleet = _canonical_music(request)
    return ui_sources(fleet_settings=fleet)


def ui_log_targets_http(request: Request) -> dict[str, object]:
    _music, _audiobooks, fleet = _canonical_music(request)
    return {"ok": True, "targets": build_log_targets(fleet_settings=fleet)}


def ui_logs_http(request: Request, target: str = "brain", lines: int = 120) -> dict[str, object]:
    _music, _audiobooks, fleet = _canonical_music(request)
    return ui_logs(target, lines, fleet_settings=fleet)
