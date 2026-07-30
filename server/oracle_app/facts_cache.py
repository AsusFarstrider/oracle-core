from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oracle_app.configuration.domain_models import StaticFactsProvider, WikipediaFactsProvider
from oracle_app.configuration.information_runtime_settings import FactsRuntimeSettings
from oracle_app.schemas import FactsProviderRequest, FactsProviderResult, FactsRetrievalInfo


logger = logging.getLogger("oracle-brain.facts")

FACTS_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "facts-cache.json"
FACTS_CACHE_VERSION = 1
_CACHEABLE_STATUSES = {"answered", "evidence_only", "no_result"}


def load_cached_facts_result(
    request: FactsProviderRequest,
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
    now: float | None = None,
) -> FactsProviderResult | None:
    if not _cache_enabled(settings):
        return None
    payload = _read_cache_payload()
    if payload is None:
        return None
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return None
    entry = entries.get(_facts_cache_key(request, settings=settings))
    if not isinstance(entry, dict):
        return None
    stored_at = _float_value(entry.get("stored_at"))
    if stored_at is None:
        return None
    ttl_seconds = max(0, int(_setting(settings, "cache_ttl_seconds") or 0))
    current = time.time() if now is None else now
    if ttl_seconds <= 0 or current - stored_at > ttl_seconds:
        return None
    raw_result = entry.get("result")
    if not isinstance(raw_result, dict):
        return None
    try:
        result = FactsProviderResult.model_validate(raw_result)
    except Exception as exc:
        logger.warning("facts_cache_invalid_entry error=%s", type(exc).__name__)
        return None
    cached_at = _format_timestamp(stored_at)
    notes = list(result.retrieval.notes)
    notes.append("cache_hit")
    notes.append(f"cached_at={cached_at}")
    return result.model_copy(update={"retrieval": FactsRetrievalInfo(method=result.retrieval.method, notes=notes)})


def store_facts_result_in_cache(
    request: FactsProviderRequest,
    result: FactsProviderResult,
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
    now: float | None = None,
) -> None:
    if not _cache_enabled(settings) or result.status not in _CACHEABLE_STATUSES:
        return
    current = time.time() if now is None else now
    payload = _read_cache_payload() or {"version": FACTS_CACHE_VERSION, "entries": {}}
    if payload.get("version") != FACTS_CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        payload = {"version": FACTS_CACHE_VERSION, "entries": {}}
    payload["entries"][_facts_cache_key(request, settings=settings)] = {
        "stored_at": current,
        "result": result.model_dump(),
    }
    _write_cache_payload(payload)


def _cache_enabled(settings: dict[str, Any] | FactsRuntimeSettings) -> bool:
    return bool(_setting(settings, "cache_enabled", False))


def _facts_cache_key(
    request: FactsProviderRequest,
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
) -> str:
    provider = str(_setting(settings, "provider_id") or _setting(settings, "provider") or "static").strip().lower()
    key_payload = {
        "version": FACTS_CACHE_VERSION,
        "provider": provider,
        "query": _normalize_cache_query(request.query),
        "options": {
            "include_evidence": bool(request.options.include_evidence),
            "max_evidence_items": int(request.options.max_evidence_items),
        },
        "provider_settings": _provider_settings_fingerprint(provider, settings),
    }
    encoded = json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provider_settings_fingerprint(
    provider: str,
    settings: dict[str, Any] | FactsRuntimeSettings,
) -> dict[str, Any]:
    typed_provider = settings.provider if isinstance(settings, FactsRuntimeSettings) else None
    if isinstance(typed_provider, WikipediaFactsProvider):
        return {"wikipedia_language": typed_provider.language}
    if isinstance(typed_provider, StaticFactsProvider):
        return {"static_items_hash": _stable_hash([item.model_dump() for item in typed_provider.items])}
    if provider == "wikipedia_api":
        return {"wikipedia_language": str(_setting(settings, "wikipedia_language") or "en").strip().lower() or "en"}
    if provider == "static":
        return {"static_items_hash": _stable_hash(_setting(settings, "static_items") or [])}
    return {}


def _setting(settings: dict[str, Any] | FactsRuntimeSettings, name: str, default: object = None) -> object:
    return settings.get(name, default) if isinstance(settings, dict) else getattr(settings, name, default)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_cache_query(query: str) -> str:
    return " ".join(str(query or "").strip().lower().split())


def _read_cache_payload() -> dict[str, Any] | None:
    try:
        raw = FACTS_CACHE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("facts_cache_read_failed error=%s", type(exc).__name__)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("facts_cache_corrupt path=%s", FACTS_CACHE_PATH)
        return None
    return payload if isinstance(payload, dict) else None


def _write_cache_payload(payload: dict[str, Any]) -> None:
    try:
        FACTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = FACTS_CACHE_PATH.with_suffix(f"{FACTS_CACHE_PATH.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(FACTS_CACHE_PATH)
    except OSError as exc:
        logger.warning("facts_cache_write_failed error=%s", type(exc).__name__)


def _float_value(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
