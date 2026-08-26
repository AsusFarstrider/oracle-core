from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException, Request

from oracle_app.admin_notifications_routes import (
    admin_notification_deliveries,
    admin_notifications_overview_http,
    register_admin_notifications_routes,
)
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.domain_models import NotificationType


def test_overview_http_uses_sanitized_canonical_view(monkeypatch) -> None:
    definition = NotificationType.model_validate(
        {
            "id": "door_open",
            "enabled": True,
            "message": "Do not expose this message.",
            "audience": [],
            "suppressed_by": [],
            "delivery_ttl_seconds": 90,
            "audio_policy": "pause_resume",
        }
    )
    settings = SimpleNamespace(
        config_revision="oracle-config-v2:sha256:canonical",
        types={"door_open": SimpleNamespace(definition=definition)},
        recipient_groups={},
        providers={},
    )
    application = FastAPI()
    application.state.brain_application_composition = CanonicalBrainApplicationComposition(
        runtime=SimpleNamespace(notifications=settings),  # type: ignore[arg-type]
        core_consumers=Mock(), route_registry=Mock(), dispatch_registry=Mock(),
        projection_resolver=Mock(), request_source_resolver=Mock(),
        playback_target_resolver=Mock(), notification_execution=Mock(),
    )
    monkeypatch.setattr("oracle_app.admin_notifications_routes.list_notification_deliveries", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("oracle_app.admin_notifications_routes.summarize_notification_deliveries", lambda **_kwargs: {"total": 0, "by_status": {}})
    payload = admin_notifications_overview_http(Request({"type": "http", "app": application}))
    assert payload["configuration_revision"] == settings.config_revision
    assert payload["definitions"][0]["id"] == "door_open"
    assert "message" not in payload["definitions"][0]


def test_delivery_history_rejects_unknown_status() -> None:
    try:
        admin_notification_deliveries(status="unknown")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("unknown delivery status was accepted")


def test_routes_register_expected_paths() -> None:
    app = FastAPI()
    register_admin_notifications_routes(app)
    paths = {route.path for route in app.routes}
    assert "/api/admin/notifications" in paths
    assert "/api/admin/notifications/deliveries" in paths
