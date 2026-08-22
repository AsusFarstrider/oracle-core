from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request

from oracle_app.api import _canonical_http_request_source, app
from oracle_app.admin_diagnostics_routes import ui_sources_http
from oracle_app.admin_facts_routes import admin_facts_lookup_http
from oracle_app.admin_network_routes import admin_network_status_http
from oracle_app.admin_notifications_routes import admin_notifications_overview_http
from oracle_app.admin_orchestration_routes import admin_orchestrations_http
from oracle_app.admin_suggestions_routes import admin_openclaw_status_http
from oracle_app.media_routes import ui_audio_music_art_http
from oracle_app.orchestration_routine_routes import run_routine_http
from oracle_app.orchestration_recovery import build_ui_internet_snapshot_http
from oracle_app.schemas import UiRoutineRunRequest


def _routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }


def test_all_six_canonical_families_have_mounted_runtime_routes() -> None:
    paths = {path for _, path in _routes()}
    for prefix in {
        "/api/conversation/",
        "/api/speech/",
        "/api/ui/",
        "/api/admin/",
        "/api/satellite/",
        "/api/integrations/",
    }:
        assert any(path.startswith(prefix) for path in paths), prefix


def test_slice5_canonical_route_inventory_is_exactly_mounted() -> None:
    routes = _routes()
    assert {
        ("POST", "/api/conversation/command"),
        ("POST", "/api/conversation/route"),
        ("GET", "/api/conversation/session"),
        ("GET", "/api/conversation/command-events"),
        ("POST", "/api/speech/stt"),
        ("POST", "/api/speech/tts"),
        ("POST", "/api/satellite/deferred-resume"),
        ("GET", "/api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}"),
        ("POST", "/api/integrations/home-assistant/events"),
    } <= routes


def test_canonical_command_openapi_exposes_no_raw_route_or_dispatch_envelope() -> None:
    document = app.openapi()
    operation = document["paths"]["/api/conversation/command"]["post"]
    schema_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    properties = document["components"]["schemas"][schema_name]["properties"]

    assert set(properties) == {
        "reply_text", "session_id", "source_id", "status", "failure_code",
        "trace_id", "effects",
    }
    assert "route" not in properties
    assert "dispatch" not in properties


def test_registered_admin_edges_fail_closed_without_canonical_composition() -> None:
    request = Request({"type": "http", "app": FastAPI()})
    calls = (
        lambda: admin_facts_lookup_http(request, "test"),
        lambda: admin_network_status_http(request),
        lambda: admin_notifications_overview_http(request),
        lambda: admin_orchestrations_http(request),
        lambda: admin_openclaw_status_http(request),
        lambda: ui_sources_http(request),
    )

    for call in calls:
        with pytest.raises(HTTPException) as raised:
            call()
        assert raised.value.status_code == 503


def test_registered_non_admin_edges_fail_closed_without_canonical_composition() -> None:
    request = Request({"type": "http", "app": FastAPI(), "headers": []})
    calls = (
        lambda: _canonical_http_request_source(None, request),
        lambda: run_routine_http(
            "test_routine",
            UiRoutineRunRequest(client_id="test"),
            request,
        ),
        lambda: ui_audio_music_art_http("/library/metadata/1/thumb", request),
        lambda: build_ui_internet_snapshot_http(request),
    )

    for call in calls:
        with pytest.raises(HTTPException) as raised:
            call()
        assert raised.value.status_code == 503
