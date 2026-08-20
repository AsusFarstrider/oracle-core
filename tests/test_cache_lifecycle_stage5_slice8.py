from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import tts
from oracle_app import api
from oracle_app import facts_cache
from oracle_app.schemas import (
    FactsAnswer,
    FactsProviderInfo,
    FactsProviderRequest,
    FactsProviderResult,
    FactsRetrievalInfo,
)


def _facts_result(query: str) -> FactsProviderResult:
    return FactsProviderResult(
        status="answered",
        query=query,
        answer=FactsAnswer(text=f"answer:{query}"),
        provider=FactsProviderInfo(id="static", name="Static"),
        retrieval=FactsRetrievalInfo(method="fixture"),
    )


def _facts_settings(ttl: int = 60) -> dict[str, object]:
    return {
        "cache_enabled": True,
        "cache_ttl_seconds": ttl,
        "provider": "static",
        "static_items": [],
    }


def test_tts_identity_uses_exact_text_provider_model_configuration_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "tts"
    model = tmp_path / "voice.onnx"
    config = tmp_path / "voice.onnx.json"
    model.write_bytes(b"model")
    config.write_text('{"speaker":1}', encoding="utf-8")
    monkeypatch.setattr(tts, "PREGENERATED_DIR", cache)
    provider = tts.PiperTtsProvider("piper", str(model))

    exact = provider._cache_path_for_text("Done.")
    changed_case = provider._cache_path_for_text("done.")
    changed_space = provider._cache_path_for_text("Done. ")
    assert exact != changed_case
    assert exact != changed_space
    assert exact.name.startswith(f"v{tts.TTS_CACHE_VERSION}-")

    config.write_text('{"speaker":2}', encoding="utf-8")
    assert provider._cache_path_for_text("Done.") != exact
    assert tts.PiperTtsProvider("piper", str(tmp_path / "other.onnx"))._cache_path_for_text("Done.") != exact


def test_tts_maintenance_expires_then_evicts_lru_and_discards_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "tts"
    cache.mkdir()
    monkeypatch.setattr(tts, "PREGENERATED_DIR", cache)
    monkeypatch.setattr(tts, "TTS_CACHE_MAX_IDLE_SECONDS", 100)
    monkeypatch.setattr(tts, "TTS_CACHE_MAX_CLIPS", 2)
    monkeypatch.setattr(tts, "TTS_CACHE_MAX_BYTES", 20)
    prefix = f"v{tts.TTS_CACHE_VERSION}-"

    paths = []
    for index, accessed in enumerate((850.0, 950.0, 960.0, 970.0)):
        path = cache / f"{prefix}{index:064x}.wav"
        path.write_bytes(bytes([index + 1]) * 8)
        os.utime(path, (accessed, accessed))
        paths.append(path)
    legacy = cache / "done.wav"
    legacy.write_bytes(b"legacy")

    result = tts.maintain_tts_cache(now=1000.0)

    assert result.removed_expired == 1
    assert result.removed_legacy == 1
    assert result.removed_lru == 1
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists() and paths[3].exists()
    assert not legacy.exists()
    assert result.diagnostics.entry_count == 2
    assert result.diagnostics.total_bytes == 16
    assert result.diagnostics.healthy


def test_tts_concurrent_atomic_writes_leave_one_complete_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts, "PREGENERATED_DIR", tmp_path / "tts")
    provider = tts.PiperTtsProvider("piper", str(tmp_path / "voice.onnx"))
    payload = b"RIFF" + b"x" * 2048

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: provider._store_cached_clip("exact text", payload), range(32)))

    files = list((tmp_path / "tts").iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == payload
    assert not list((tmp_path / "tts").glob("*.tmp"))


def test_facts_maintenance_prunes_expired_malformed_old_version_and_bounds_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "facts.json"
    monkeypatch.setattr(facts_cache, "FACTS_CACHE_PATH", path)
    monkeypatch.setattr(facts_cache, "FACTS_CACHE_MAX_ENTRIES", 2)
    settings = _facts_settings(ttl=100)
    valid = _facts_result("valid").model_dump()
    path.write_text(json.dumps({
        "version": facts_cache.FACTS_CACHE_VERSION,
        "entries": {
            "expired": {"stored_at": 800.0, "result": valid},
            "malformed": {"stored_at": "never", "result": valid},
            "oldest": {"stored_at": 920.0, "result": valid},
            "middle": {"stored_at": 930.0, "result": valid},
            "newest": {"stored_at": 940.0, "result": valid},
        },
    }), encoding="utf-8")

    result = facts_cache.maintain_facts_cache(settings=settings, now=1000.0)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert result.removed_expired == 1
    assert result.removed_malformed == 1
    assert result.removed_lru == 1
    assert set(payload["entries"]) == {"middle", "newest"}
    assert result.diagnostics.healthy

    path.write_text(json.dumps({"version": 0, "entries": {"old": {}}}), encoding="utf-8")
    old = facts_cache.maintain_facts_cache(settings=settings, now=1000.0)
    assert old.removed_legacy == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "entries": {}, "version": facts_cache.FACTS_CACHE_VERSION,
    }


def test_facts_concurrent_writes_do_not_lose_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "facts.json"
    monkeypatch.setattr(facts_cache, "FACTS_CACHE_PATH", path)
    settings = _facts_settings(ttl=600)

    def store(index: int) -> None:
        query = f"question {index}"
        facts_cache.store_facts_result_in_cache(
            FactsProviderRequest(query=query), _facts_result(query),
            settings=settings, now=1000.0 + index,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(store, range(100)))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == facts_cache.FACTS_CACHE_VERSION
    assert len(payload["entries"]) == 100
    assert facts_cache.facts_cache_diagnostics(settings=settings, now=1100.0).healthy


def test_admin_cache_diagnostics_is_read_only_and_v2_native() -> None:
    facts = facts_cache.CacheDiagnostics(
        cache_id="facts", path="/cache/facts.json", exists=True, healthy=True,
        entry_count=4, total_bytes=500, limit_entries=512, limit_bytes=None,
    )
    speech = facts_cache.CacheDiagnostics(
        cache_id="tts", path="/cache/tts", exists=True, healthy=False,
        entry_count=853, total_bytes=172235420, limit_entries=4096,
        limit_bytes=256 * 1024 * 1024, legacy_entries=853,
    )
    composition = SimpleNamespace(runtime=SimpleNamespace(information=None))
    with (
        patch("oracle_app.api.brain_application_composition", return_value=composition),
        patch("oracle_app.api.facts_cache_diagnostics", return_value=facts),
        patch("oracle_app.api.tts_cache_diagnostics", return_value=speech),
        patch("oracle_app.api.maintain_facts_cache") as facts_maintenance,
        patch("oracle_app.api.maintain_tts_cache") as tts_maintenance,
    ):
        payload = api.admin_cache_diagnostics()

    assert payload["status"] == "degraded"
    assert payload["caches"]["tts"]["legacy_entries"] == 853
    assert payload["cutover_dry_run"] == {
        "destructive": False,
        "tts_discard_entries": 853,
        "tts_discard_bytes": 172235420,
        "facts_prune_candidates": 0,
    }
    facts_maintenance.assert_not_called()
    tts_maintenance.assert_not_called()
