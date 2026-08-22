from __future__ import annotations

import importlib
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def isolate_default_alert_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No pytest path may bootstrap or mutate deployed Memory or alert data."""
    from oracle_app import alerts

    isolated = tmp_path / "oracle-memory.sqlite3"
    monkeypatch.setattr(alerts, "ALERT_DB_PATH", isolated)
    for module_name in (
        "oracle_app.memory.store",
        "oracle_app.memory.schema",
        "oracle_app.memory.events",
        "oracle_app.memory.identities",
        "oracle_app.memory.sources",
        "oracle_app.memory.sessions",
        "oracle_app.memory.transcripts",
        "oracle_app.memory.orchestrations",
        "oracle_app.memory.provider_status",
        "oracle_app.memory.satellite_activity",
        "oracle_app.memory.diagnostics",
        "oracle_app.memory.identity_reconciliation",
        "oracle_app.memory.retention_executor",
        "oracle_app.memory.alerts",
        "oracle_app.notifications.receipts",
        "oracle_app.home_automation.state",
    ):
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "DB_PATH", isolated)

    # Lifespan and cache tests must never inspect, prune, or populate the live
    # development caches.
    facts_cache = importlib.import_module("oracle_app.facts_cache")
    tts = importlib.import_module("tts")
    monkeypatch.setattr(facts_cache, "FACTS_CACHE_PATH", tmp_path / "facts-cache.json")
    monkeypatch.setattr(tts, "PREGENERATED_DIR", tmp_path / "tts-cache")
