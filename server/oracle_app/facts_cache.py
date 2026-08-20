from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
import tempfile
import threading
import time
from datetime import UTC, datetime
from typing import Any

from oracle_app.configuration.domain_models import StaticFactsProvider, WikipediaFactsProvider
from oracle_app.configuration.information_runtime_settings import FactsRuntimeSettings
from oracle_app.schemas import FactsProviderRequest, FactsProviderResult, FactsRetrievalInfo
from oracle_app.runtime_paths import RUNTIME_PATHS
from oracle_app.cache_lifecycle import CacheDiagnostics, CacheMaintenanceResult


logger = logging.getLogger("oracle-brain.facts")

FACTS_CACHE_PATH = RUNTIME_PATHS.facts_cache
FACTS_CACHE_VERSION = 1
FACTS_CACHE_MAX_ENTRIES = 512
_CACHEABLE_STATUSES = {"answered", "evidence_only", "no_result"}
_CACHE_LOCK = threading.RLock()


def load_cached_facts_result(
    request: FactsProviderRequest,
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
    now: float | None = None,
) -> FactsProviderResult | None:
    if not _cache_enabled(settings):
        return None
    with _CACHE_LOCK:
        payload = _read_cache_payload()
        if payload is None or payload.get("version") != FACTS_CACHE_VERSION:
            return None
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return None
        entry = entries.get(_facts_cache_key(request, settings=settings))
        if not isinstance(entry, dict):
            return None
        stored_at = _finite_float(entry.get("stored_at"))
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
    with _CACHE_LOCK:
        payload = _pruned_facts_payload(settings=settings, now=current)[0]
        payload["entries"][_facts_cache_key(request, settings=settings)] = {
            "stored_at": current,
            "result": result.model_dump(),
        }
        payload, _ = _prune_entries(payload, settings=settings, now=current)
        _write_cache_payload(payload)


def maintain_facts_cache(
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
    now: float | None = None,
) -> CacheMaintenanceResult:
    current = time.time() if now is None else now
    with _CACHE_LOCK:
        existed = FACTS_CACHE_PATH.exists()
        payload, counts = _pruned_facts_payload(settings=settings, now=current)
        if existed and counts["changed"]:
            _write_cache_payload(payload)
        diagnostics = facts_cache_diagnostics(settings=settings, now=current)
    return CacheMaintenanceResult(
        cache_id="facts",
        inspected_entries=counts["inspected"],
        removed_expired=counts["expired"],
        removed_malformed=counts["malformed"],
        removed_legacy=counts["legacy"],
        removed_lru=counts["lru"],
        bytes_reclaimed=max(0, counts["bytes_before"] - diagnostics.total_bytes),
        diagnostics=diagnostics,
    )


def facts_cache_diagnostics(
    *,
    settings: dict[str, Any] | FactsRuntimeSettings,
    now: float | None = None,
) -> CacheDiagnostics:
    current = time.time() if now is None else now
    with _CACHE_LOCK:
        payload = _read_cache_payload()
        total_bytes = _path_size(FACTS_CACHE_PATH)
        if payload is None:
            return CacheDiagnostics(
                cache_id="facts", path=str(FACTS_CACHE_PATH),
                exists=FACTS_CACHE_PATH.exists(), healthy=not FACTS_CACHE_PATH.exists(),
                entry_count=0, total_bytes=total_bytes,
                limit_entries=FACTS_CACHE_MAX_ENTRIES, limit_bytes=None,
                malformed_entries=1 if FACTS_CACHE_PATH.exists() else 0,
            )
        if payload.get("version") != FACTS_CACHE_VERSION or not isinstance(payload.get("entries"), dict):
            return CacheDiagnostics(
                cache_id="facts", path=str(FACTS_CACHE_PATH), exists=True, healthy=False,
                entry_count=0, total_bytes=total_bytes,
                limit_entries=FACTS_CACHE_MAX_ENTRIES, limit_bytes=None, legacy_entries=1,
            )
        ttl = max(0, int(_setting(settings, "cache_ttl_seconds") or 0))
        expired = malformed = 0
        valid_times: list[float] = []
        for entry in payload["entries"].values():
            valid, stored_at = _valid_entry(entry)
            if not valid or stored_at is None:
                malformed += 1
            elif ttl <= 0 or current - stored_at > ttl:
                expired += 1
            else:
                valid_times.append(stored_at)
        count = len(payload["entries"])
        return CacheDiagnostics(
            cache_id="facts", path=str(FACTS_CACHE_PATH), exists=True,
            healthy=not malformed and not expired and count <= FACTS_CACHE_MAX_ENTRIES,
            entry_count=count, total_bytes=total_bytes,
            limit_entries=FACTS_CACHE_MAX_ENTRIES, limit_bytes=None,
            expired_entries=expired, malformed_entries=malformed,
            oldest_accessed_at=_format_timestamp(min(valid_times)) if valid_times else None,
        )


def _pruned_facts_payload(*, settings: dict[str, Any] | FactsRuntimeSettings, now: float) -> tuple[dict[str, Any], dict[str, int]]:
    bytes_before = _path_size(FACTS_CACHE_PATH)
    payload = _read_cache_payload()
    if payload is None:
        legacy = 0
        malformed = 1 if FACTS_CACHE_PATH.exists() else 0
        return {"version": FACTS_CACHE_VERSION, "entries": {}}, {
            "inspected": 0, "expired": 0, "malformed": malformed,
            "legacy": legacy, "lru": 0, "changed": malformed,
            "bytes_before": bytes_before,
        }
    if payload.get("version") != FACTS_CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        inspected = len(payload.get("entries", {})) if isinstance(payload.get("entries"), dict) else 0
        return {"version": FACTS_CACHE_VERSION, "entries": {}}, {
            "inspected": inspected, "expired": 0, "malformed": 0,
            "legacy": inspected or 1, "lru": 0, "changed": 1,
            "bytes_before": bytes_before,
        }
    pruned, counts = _prune_entries(payload, settings=settings, now=now)
    counts["bytes_before"] = bytes_before
    return pruned, counts


def _prune_entries(payload: dict[str, Any], *, settings: dict[str, Any] | FactsRuntimeSettings, now: float) -> tuple[dict[str, Any], dict[str, int]]:
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
    ttl = max(0, int(_setting(settings, "cache_ttl_seconds") or 0))
    retained: list[tuple[str, dict[str, Any], float]] = []
    expired = malformed = 0
    for key, entry in entries.items():
        valid, stored_at = _valid_entry(entry)
        if not valid or stored_at is None:
            malformed += 1
        elif ttl <= 0 or now - stored_at > ttl:
            expired += 1
        else:
            retained.append((str(key), entry, stored_at))
    retained.sort(key=lambda item: (item[2], item[0]), reverse=True)
    lru = max(0, len(retained) - FACTS_CACHE_MAX_ENTRIES)
    kept = retained[:FACTS_CACHE_MAX_ENTRIES]
    result = {"version": FACTS_CACHE_VERSION, "entries": {key: entry for key, entry, _ in kept}}
    return result, {
        "inspected": len(entries), "expired": expired, "malformed": malformed,
        "legacy": 0, "lru": lru,
        "changed": int(bool(expired or malformed or lru)), "bytes_before": 0,
    }


def _valid_entry(entry: object) -> tuple[bool, float | None]:
    if not isinstance(entry, dict):
        return False, None
    stored_at = _finite_float(entry.get("stored_at"))
    raw_result = entry.get("result")
    if stored_at is None or not isinstance(raw_result, dict):
        return False, stored_at
    try:
        FactsProviderResult.model_validate(raw_result)
    except Exception:
        return False, stored_at
    return True, stored_at


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
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=FACTS_CACHE_PATH.parent,
            prefix=f".{FACTS_CACHE_PATH.name}.", suffix=".tmp", delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
        tmp_path.replace(FACTS_CACHE_PATH)
    except OSError as exc:
        logger.warning("facts_cache_write_failed error=%s", type(exc).__name__)
        if "tmp_path" in locals():
            tmp_path.unlink(missing_ok=True)


def _float_value(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite_float(value: object) -> float | None:
    parsed = _float_value(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _format_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
