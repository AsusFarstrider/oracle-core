from __future__ import annotations

import json
from pathlib import Path


CONTRACT_PATH = Path(__file__).with_name("fixtures") / "api_family_contract.json"
CLIENT_API_PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "client-api.md"


def _contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_public_api_families_are_complete_and_purpose_owned() -> None:
    contract = _contract()
    families = contract["families"]

    assert set(families) == {
        "conversation",
        "speech",
        "ui",
        "admin",
        "satellite",
        "integrations",
    }
    assert {name: row["prefix"] for name, row in families.items()} == {
        name: f"/api/{name}" for name in families
    }
    assert len({row["prefix"] for row in families.values()}) == len(families)


def test_characterized_routes_stay_inside_their_semantic_family() -> None:
    families = _contract()["families"]
    routes: list[tuple[str, str]] = []

    for family in families.values():
        prefix = family["prefix"]
        for route in family.get("routes", []):
            assert route["path"].startswith(f"{prefix}/")
            routes.append((route["method"], route["path"]))

    assert len(routes) == len(set(routes))
    assert all(not path.startswith("/api/voice/") for _, path in routes)


def test_conversation_and_speech_are_separate_modal_boundaries() -> None:
    families = _contract()["families"]
    conversation_owners = {
        route["semantic_owner"] for route in families["conversation"]["routes"]
    }
    speech_owners = {route["semantic_owner"] for route in families["speech"]["routes"]}

    assert conversation_owners == {
        "conversation_command",
        "conversation_route",
        "conversation_session",
        "conversation_events",
    }
    assert speech_owners == {"speech_to_text", "text_to_speech"}


def test_satellite_family_owns_delivery_and_local_playback_mechanics() -> None:
    satellite_routes = _contract()["families"]["satellite"]["routes"]
    owners = {route["semantic_owner"] for route in satellite_routes}

    assert owners == {
        "satellite_alert_delivery",
        "satellite_configuration",
        "satellite_playback",
        "satellite_media",
    }
    assert {route["path"] for route in satellite_routes} >= {
        "/api/satellite/alerts/claim",
        "/api/satellite/alerts/{alert_id}/acknowledge",
        "/api/satellite/deferred-resume",
        "/api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}",
    }


def test_conversation_result_has_only_the_ratified_finite_vocabulary() -> None:
    result = _contract()["conversation_result"]

    assert result["required_fields"] == [
        "reply_text",
        "session_id",
        "source_id",
        "status",
        "failure_code",
        "trace_id",
        "effects",
    ]
    assert set(result["statuses"]) == {
        "executed",
        "pending_confirmation",
        "pending_clarification",
        "failed",
        "ignored",
    }
    assert set(result["effect_fields"]) == {
        "follow_up",
        "satellite_playback",
        "deferred_satellite_playback",
        "ui_presentation",
    }
    assert result["reply_text_optional_statuses"] == ["ignored"]


def test_root_health_is_the_only_permanent_root_contract() -> None:
    migration = _contract()["migration"]

    assert migration["permanent_root_routes"] == [
        {"method": "GET", "path": "/health", "semantic_owner": "minimal_brain_liveness"}
    ]
    assert "/health" not in migration["obsolete_root_routes"]
    assert migration["obsolete_prefixes"] == ["/api/voice"]
    assert migration["retired_without_replacement"] == [
        {
            "method": "POST",
            "path": "/api/voice/ingest/text",
            "reason": "duplicate_command_ingress",
        }
    ]


def test_human_contract_matches_the_executable_contract_vocabulary() -> None:
    content = CLIENT_API_PATH.read_text(encoding="utf-8")
    contract = _contract()

    for family in contract["families"].values():
        assert f"`{family['prefix']}`" in content or f"`{family['prefix']}/" in content
    for field in contract["conversation_result"]["required_fields"]:
        assert f"`{field}`" in content
    for status in contract["conversation_result"]["statuses"]:
        assert f"`{status}`" in content
    for effect in contract["conversation_result"]["effect_fields"]:
        assert f"`{effect}`" in content
