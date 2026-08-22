from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from oracle_app.config import load_home_assistant_cache
from oracle_app.health import (
    check_audiobook_health,
    check_calendar_health,
    check_home_assistant_health,
    check_music_health,
    check_news_health,
    check_ollama_health,
    check_stt_health,
    check_tts_health,
)
from oracle_app.network import build_ui_network_health_snapshot

from .storage import review_history


def collect_sources(
    run_type: str,
    *,
    log_lines: int = 400,
    canonical_composition=None,
    canonical_authority: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _selected_collectors(run_type)
    sections: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    for name, collector in selected:
        try:
            sections[name] = collector(
                log_lines=log_lines,
                canonical_composition=canonical_composition,
                canonical_authority=canonical_authority,
            )
            statuses[name] = {"ok": True}
        except Exception as exc:  # pragma: no cover - defensive collector boundary
            sections[name] = {"ok": False, "error": str(exc)}
            statuses[name] = {"ok": False, "error": str(exc)}
    sections["review_history"] = _collect_review_history()
    statuses["review_history"] = {"ok": True}
    return sections, statuses


def _selected_collectors(run_type: str):
    collectors = {
        "oracle": _collect_oracle,
        "home_assistant": _collect_home_assistant,
        "librenms": _collect_librenms,
    }
    if run_type in collectors:
        return [(run_type, collectors[run_type])]
    return list(collectors.items())


def _collect_oracle(
    *,
    log_lines: int,
    canonical_composition=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    health_checks = {}
    composition = canonical_composition
    checks = {
        "home_assistant": lambda: check_home_assistant_health(
            None if composition is None else composition.runtime.home_assistant,
        ),
        "calendar": lambda: check_calendar_health(
            canonical_execution=None if composition is None else composition.calendar_execution,
        ),
        "music": lambda: check_music_health(
            music_execution=None if composition is None else composition.music_execution,
        ),
        "audiobook": lambda: check_audiobook_health(
            None if composition is None else composition.audiobook_execution,
        ),
        "ollama": lambda: check_ollama_health(
            inference=None if composition is None else composition.core_consumers.inference,
        ),
        "news": lambda: check_news_health(
            canonical_execution=None if composition is None else composition.news_execution,
        ),
        "tts": lambda: check_tts_health(
            provider=None if composition is None else composition.tts_provider(),
        ),
        "stt": lambda: check_stt_health(
            provider=None if composition is None else composition.stt_provider(),
        ),
    }
    for name, func in checks.items():
        try:
            health_checks[name] = _model_or_value(func())
        except Exception as exc:
            health_checks[name] = {"status": "failed", "detail": str(exc)}

    try:
        network_health = build_ui_network_health_snapshot(
            canonical_execution=None if composition is None else composition.network_execution,
            canonical_authority=canonical_authority,
        )
    except Exception as exc:
        network_health = {"status": "failed", "detail": str(exc)}

    try:
        music = None if composition is None else composition.music_execution
        sources = [] if music is None else sorted(music.settings.playback_targets)
    except Exception:
        sources = []

    return {
        "collected_at": datetime.now().astimezone().isoformat(),
        "health": health_checks,
        "network_health": network_health,
        "configured_sources": sources,
        "log_excerpt": _read_brain_logs(log_lines),
    }


def _collect_home_assistant(
    *,
    log_lines: int,
    canonical_composition=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    del log_lines
    del canonical_composition
    del canonical_authority
    try:
        cache = load_home_assistant_cache()
    except HTTPException as exc:
        return {"ok": False, "detail": str(exc.detail)}
    entities = cache.get("entities") if isinstance(cache, dict) else []
    if not isinstance(entities, list):
        entities = []
    unavailable = []
    domains: dict[str, int] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id") or "")
        state = str(entity.get("state") or entity.get("last_state") or "").lower()
        domain = entity_id.split(".", 1)[0] if "." in entity_id else "unknown"
        domains[domain] = domains.get(domain, 0) + 1
        if state in {"unavailable", "unknown"}:
            unavailable.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": entity.get("friendly_name") or entity.get("name"),
                    "state": state,
                }
            )
    return {
        "ok": True,
        "entity_count": len(entities),
        "domain_counts": domains,
        "unavailable_or_unknown": unavailable[:200],
        "cache_keys": sorted(cache.keys()) if isinstance(cache, dict) else [],
    }


def _collect_librenms(
    *,
    log_lines: int,
    canonical_composition=None,
    canonical_authority: bool = False,
) -> dict[str, Any]:
    del log_lines
    if canonical_authority:
        execution = None if canonical_composition is None else canonical_composition.network_execution
        if execution is None:
            return {"enabled": False, "status": {"status": "unconfigured"}}
        return {
            "enabled": True,
            "status": execution.status_snapshot(force_refresh=True),
        }
    from oracle_app.config import get_librenms_settings
    from oracle_app.provider_bridges.librenms import LibreNmsBridge

    settings = get_librenms_settings()
    status = LibreNmsBridge().get_monitoring_status(settings=settings)
    return {
        "enabled": bool(settings.get("enabled")),
        "status": status.to_dict(),
    }


def _collect_review_history() -> dict[str, Any]:
    history = review_history(limit=100)
    return {
        "count": len(history),
        "items": [
            {
                "id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "category": item["category"],
                "source": item["source"],
                "summary": item["summary"],
                "suggested_action": item["suggested_action"],
                "rejection_reason": item["rejection_reason"],
                "correction_text": item["correction_text"],
                "review_notes": item["review_notes"],
                "suppress_if_repeated": item["suppress_if_repeated"],
                "similarity_key": item["similarity_key"],
            }
            for item in history
        ],
    }


def _read_brain_logs(lines: int) -> dict[str, Any]:
    bounded_lines = max(50, min(int(lines), 1000))
    result = subprocess.run(
        ["journalctl", "-u", "oracle-brain.service", "-n", str(bounded_lines), "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    return {
        "ok": result.returncode == 0,
        "lines": bounded_lines,
        "content": result.stdout[-60000:],
        "error": result.stderr[-4000:],
    }


def _model_or_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value
