from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oracle_app.home_assistant_cache import build_cache, refresh_home_assistant_cache
from oracle_app.handlers.system import SystemHandler
from oracle_app.constants import CACHE_PATH
from oracle_app.schemas import DispatchPlan


def test_build_cache_preserves_existing_normalized_shape() -> None:
    cache = build_cache(
        [
            {
                "entity_id": "light.office_lamp",
                "attributes": {"friendly_name": "Office Lamp"},
            },
            {
                "entity_id": "light.office",
                "attributes": {
                    "friendly_name": "Office",
                    "entity_id": ["light.office_lamp"],
                },
            },
        ]
    )

    assert cache["entity_count"] == 2
    assert cache["room_count"] == 1
    assert cache["entities"][0]["entity_id"] == "light.office"
    assert cache["rooms"][0]["spoken_name"] == "office"


def test_refresh_uses_typed_settings_and_atomically_writes_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache_path = tmp_path / "home-assistant-cache.json"
    settings = SimpleNamespace(
        enabled=True,
        base_url="http://ha.invalid",
        credential="secret-token",
        timeout_seconds=9,
    )
    calls = []

    def fake_fetch(base_url, credential, *, timeout_seconds):
        calls.append((base_url, credential, timeout_seconds))
        return [
            {
                "entity_id": "light.office_lamp",
                "attributes": {"friendly_name": "Office Lamp"},
            }
        ]

    monkeypatch.setattr("oracle_app.home_assistant_cache.fetch_states", fake_fetch)
    cache = refresh_home_assistant_cache(settings, cache_path=cache_path)

    assert calls == [("http://ha.invalid", "secret-token", 9)]
    assert cache["entity_count"] == 1
    assert json.loads(cache_path.read_text(encoding="utf-8"))["entity_count"] == 1
    assert list(tmp_path.glob(".home-assistant-cache.json.*.tmp")) == []


def test_refresh_fails_closed_when_home_assistant_is_disabled(tmp_path: Path) -> None:
    settings = SimpleNamespace(enabled=False, base_url=None, credential=None)

    with pytest.raises(RuntimeError, match="disabled"):
        refresh_home_assistant_cache(settings, cache_path=tmp_path / "cache.json")


def test_system_refresh_action_uses_injected_home_assistant_settings(monkeypatch) -> None:
    settings = SimpleNamespace(enabled=True)
    calls = []

    def fake_refresh(received_settings):
        calls.append(received_settings)
        return {"room_count": 3, "entity_count": 12}

    monkeypatch.setattr("oracle_app.handlers.system.refresh_home_assistant_cache", fake_refresh)
    dispatch = DispatchPlan(
        target="system",
        hook="system.refresh_cache",
        payload={"action": "refresh_cache"},
        status="planned",
    )

    result = SystemHandler(home_assistant_settings=settings).handle(dispatch, object())

    assert calls == [settings]
    assert result.status == "executed"
    assert result.result == {
        "action": "refresh_cache",
        "room_count": 3,
        "entity_count": 12,
        "cache_path": str(CACHE_PATH),
    }
