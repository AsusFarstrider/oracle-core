from __future__ import annotations

from oracle_app.api import app


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
