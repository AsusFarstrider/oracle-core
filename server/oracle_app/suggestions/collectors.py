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
from oracle_app.memory.events import EventQuery, query_events

from .storage import review_history


_REVIEW_HISTORY_LIMIT = 50
_HOME_ASSISTANT_ITEM_LIMIT = 100
_LOG_CHARACTER_LIMIT = 30000
_MEMORY_EVENT_LIMIT = 200


def collect_sources(
    run_type: str,
    *,
    log_lines: int = 400,
    canonical_composition=None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _selected_collectors(run_type)
    sections: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    for name, collector in selected:
        observed_at = datetime.now().astimezone().isoformat()
        try:
            sections[name] = collector(
                log_lines=log_lines,
                canonical_composition=canonical_composition,
                window_start=window_start,
                window_end=window_end,
            )
            statuses[name] = _collector_status(name, sections[name], observed_at=observed_at)
        except Exception as exc:  # pragma: no cover - defensive collector boundary
            sections[name] = {"ok": False, "error": str(exc)}
            statuses[name] = _collector_status(
                name,
                sections[name],
                observed_at=observed_at,
                unavailable=True,
            )
        if isinstance(sections[name], dict):
            sections[name]["_availability"] = statuses[name]["status"]
            sections[name]["_provenance"] = statuses[name]["provenance"]
    review_observed_at = datetime.now().astimezone().isoformat()
    try:
        sections["review_history"] = _collect_review_history()
        statuses["review_history"] = _collector_status(
            "review_history", sections["review_history"], observed_at=review_observed_at
        )
    except Exception as exc:  # pragma: no cover - defensive collector boundary
        sections["review_history"] = {"ok": False, "error": str(exc)}
        statuses["review_history"] = _collector_status(
            "review_history", sections["review_history"], observed_at=review_observed_at, unavailable=True
        )
    sections["review_history"]["_availability"] = statuses["review_history"]["status"]
    sections["review_history"]["_provenance"] = statuses["review_history"]["provenance"]
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
    window_start: str | None = None,
    window_end: str | None = None,
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
        )
    except Exception as exc:
        network_health = {"status": "failed", "detail": str(exc)}

    try:
        music = None if composition is None else composition.music_execution
        sources = [] if music is None else sorted(music.settings.playback_targets)
    except Exception:
        sources = []

    events = query_events(
        EventQuery(
            observed_after=window_start,
            observed_before=window_end,
            limit=_MEMORY_EVENT_LIMIT + 1,
        )
    )
    bounded_events = events[:_MEMORY_EVENT_LIMIT]

    return {
        "collected_at": datetime.now().astimezone().isoformat(),
        "health": health_checks,
        "network_health": network_health,
        "configured_sources": sources,
        "memory_events": {
            "window_start": window_start,
            "window_end": window_end,
            "count": len(bounded_events),
            "truncated": len(events) > len(bounded_events),
            "items": [
                {
                    key: event.get(key)
                    for key in (
                        "event_id",
                        "observed_at",
                        "event_type",
                        "category",
                        "severity",
                        "source_id",
                        "provider",
                        "domain",
                        "status",
                        "correlation_id",
                    )
                }
                for event in bounded_events
            ],
        },
        "log_excerpt": _read_brain_logs(log_lines),
    }


def _collect_home_assistant(
    *,
    log_lines: int,
    canonical_composition=None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    del log_lines
    del canonical_composition
    del window_start, window_end
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
        "unavailable_or_unknown": unavailable[:_HOME_ASSISTANT_ITEM_LIMIT],
        "unavailable_or_unknown_total": len(unavailable),
        "unavailable_or_unknown_omitted": max(0, len(unavailable) - _HOME_ASSISTANT_ITEM_LIMIT),
        "cache_keys": sorted(cache.keys()) if isinstance(cache, dict) else [],
    }


def _collect_librenms(
    *,
    log_lines: int,
    canonical_composition=None,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    del log_lines
    del window_start, window_end
    execution = None if canonical_composition is None else canonical_composition.network_execution
    if execution is None:
        return {"enabled": False, "status": {"status": "unconfigured"}}
    return {
        "enabled": True,
        "status": execution.status_snapshot(force_refresh=True),
    }


def _collect_review_history() -> dict[str, Any]:
    history = review_history(limit=_REVIEW_HISTORY_LIMIT + 1)
    visible = history[:_REVIEW_HISTORY_LIMIT]
    return {
        "count": len(visible),
        "omitted": max(0, len(history) - len(visible)),
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
            for item in visible
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
        "content": result.stdout[-_LOG_CHARACTER_LIMIT:],
        "content_characters": min(len(result.stdout), _LOG_CHARACTER_LIMIT),
        "omitted_characters": max(0, len(result.stdout) - _LOG_CHARACTER_LIMIT),
        "error": result.stderr[-4000:],
    }


def _collector_status(
    name: str,
    section: Any,
    *,
    observed_at: str,
    unavailable: bool = False,
) -> dict[str, Any]:
    issues: list[str] = []
    if isinstance(section, dict):
        if section.get("enabled") is False:
            unavailable = True
            issues.append("collector disabled or unconfigured")
        if section.get("ok") is False:
            unavailable = True
            issues.append(str(section.get("error") or section.get("detail") or "collector unavailable"))
        if name == "oracle":
            for component, value in (section.get("health") or {}).items():
                status = str((value or {}).get("status") or "").casefold() if isinstance(value, dict) else ""
                if status in {"failed", "error", "unavailable"}:
                    issues.append(f"{component}:{status}")
            network = section.get("network_health")
            if isinstance(network, dict) and str(network.get("status") or "").casefold() in {
                "failed", "error", "unavailable"
            }:
                issues.append(f"network:{network.get('status')}")
            logs = section.get("log_excerpt")
            if isinstance(logs, dict) and logs.get("ok") is False:
                issues.append("brain_logs:unavailable")
        nested_status = section.get("status")
        if isinstance(nested_status, dict) and str(nested_status.get("status") or "").casefold() in {
            "disabled", "failed", "error", "unavailable", "unconfigured"
        }:
            unavailable = True
            issues.append(f"status:{nested_status.get('status')}")
    status = "unavailable" if unavailable else "partial" if issues else "available"
    return {
        "ok": status != "unavailable",
        "status": status,
        "issues": issues[:20],
        "observed_at": observed_at,
        "provenance": {
            "collector": name,
            "authority": {
                "oracle": "canonical_brain_composition_and_brain_journal",
                "home_assistant": "home_assistant_operational_cache",
                "librenms": "canonical_network_status_snapshot",
                "review_history": "oracle_memory_suggestion_reviews",
            }.get(name, "oracle_suggestions_collector"),
            "coverage": {
                "oracle": "requested_window_memory_events_plus_current_snapshot_and_recent_journal",
                "home_assistant": "current_operational_cache_snapshot",
                "librenms": "current_network_status_snapshot",
                "review_history": "bounded_prior_review_history",
            }.get(name, "current_snapshot"),
        },
    }


def _model_or_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value
