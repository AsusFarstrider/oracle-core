from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib import parse as urlparse

from fastapi import HTTPException

from .admin_diagnostics_routes import serialize_control_plane_error
from .alerts import format_duration, list_alerts
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .music_runtime.canonical import CanonicalMusicExecution
from .music_runtime.control import ControlPlaneError
from .music_runtime.parsing import MusicIntent
from .music_runtime.selection import music_provider_ref, music_selection_id
from .provider_bridges.audiobookshelf_audiobook import normalize_audiobook_item, normalize_audiobook_progress
from .schemas import UiAudioSearchRequest


logger = logging.getLogger("oracle-brain.ui_audio")

SLEEP_TIMER_KIND = "sleep_timer"

_UI_AUDIO_ACTION_DEFINITIONS = [
    {
        "action_id": "resume_audiobook",
        "label": "Resume Audiobook",
        "type": "button",
        "icon": "book-open",
        "requires_confirmation": False,
    },
    {
        "action_id": "pause_audiobook",
        "label": "Pause Audiobook",
        "type": "button",
        "icon": "pause",
        "requires_confirmation": False,
    },
    {
        "action_id": "stop_audiobook",
        "label": "Stop Audiobook",
        "type": "button",
        "icon": "square",
        "requires_confirmation": False,
    },
    {
        "action_id": "resume_music",
        "label": "Resume Music",
        "type": "button",
        "icon": "play",
        "requires_confirmation": False,
    },
    {
        "action_id": "pause_music",
        "label": "Pause Music",
        "type": "button",
        "icon": "pause",
        "requires_confirmation": False,
    },
    {
        "action_id": "stop_music",
        "label": "Stop Music",
        "type": "button",
        "icon": "square",
        "requires_confirmation": False,
    },
]


def _build_ui_generated_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_ui_client_id(client_id: str) -> str:
    normalized = str(client_id or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="client_id cannot be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
    if normalized.lower() != normalized or any(char not in allowed for char in normalized):
        raise HTTPException(
            status_code=400,
            detail="client_id must be lowercase, hyphen-separated, and contain only letters, numbers, and hyphens",
        )
    return normalized


def resolve_ui_audio_source(
    source: str | None,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> tuple[str | None, list[str]]:
    configured_sources = sorted(
        set(() if music_execution is None else music_execution.settings.playback_targets)
        | set(() if audiobook_execution is None else audiobook_execution.settings.playback_targets)
    )
    if source is None:
        return (configured_sources[0] if configured_sources else None), configured_sources

    requested_source = str(source).strip()
    if not requested_source:
        raise HTTPException(status_code=400, detail="source cannot be empty")
    if requested_source not in configured_sources:
        raise HTTPException(status_code=404, detail=f"Unknown playback source {requested_source}")
    return requested_source, configured_sources


def _build_ui_audio_sources(
    configured_sources: list[str],
    *,
    household_settings: HouseholdRuntimeSettings | None,
) -> list[dict[str, object]]:
    output = []
    for source_id in configured_sources:
        source = None if household_settings is None else household_settings.source(source_id)
        room = None if source is None or household_settings is None else household_settings.room(source.associated_room_id)
        output.append({
            "source": source_id,
            "label": room.display_name if room is not None else source_id.replace("_", " ").title(),
            "default_room": None if room is None else room.id,
        })
    return sorted(output, key=lambda item: (str(item["label"]).casefold(), str(item["source"])))


def _infer_audio_source_from_client_id(
    client_id: str | None,
    *,
    household_settings: HouseholdRuntimeSettings | None,
) -> str | None:
    normalized = str(client_id or "").strip()
    if not normalized:
        return None
    source_ids = set(() if household_settings is None else household_settings.sources)
    if normalized in source_ids:
        return normalized
    satellite_prefix = "satellite-ui-"
    if normalized.startswith(satellite_prefix):
        candidate = normalized[len(satellite_prefix) :]
        if candidate in source_ids:
            return candidate
    return None


def build_ui_audio_users(
    source: str | None = None,
    *,
    household_settings: HouseholdRuntimeSettings | None,
) -> tuple[list[dict[str, object]], str | None]:
    if household_settings is None:
        return [], None
    users = [
        {
            "user_id": user.id,
            "label": user.display_name,
            "is_default": user.id == household_settings.default_user_id,
            "audiobook_enabled": bool(user.capabilities.audiobooks and user.capabilities.audiobooks.enabled),
        }
        for user in household_settings.users.values()
        if user.enabled
    ]
    return users, (
        household_settings.configured_associated_user_id(source)
        or household_settings.default_user_id
    )


def resolve_ui_audio_user(user_id: str | None, users: list[dict[str, object]], default_user_id: str | None) -> str | None:
    available = {str(item.get("user_id") or "") for item in users}
    requested = str(user_id or "").strip().lower()
    if requested:
        if requested not in available:
            raise HTTPException(status_code=404, detail=f"Unknown audio user {requested}")
        return requested
    if default_user_id in available:
        return default_user_id
    return next(iter(available), None)


def _build_ui_audio_results(audiobook_payload: dict[str, object], *, user_id: str | None = None) -> list[dict[str, object]]:
    current = audiobook_payload.get("current")
    if not isinstance(current, dict):
        return []
    library_item_id = str(current.get("library_item_id") or "").strip()
    if not library_item_id:
        return []
    return [
        {
            "result_id": f"audiobook:{library_item_id}",
            "type": "audiobook",
            "title": current.get("title"),
            "subtitle": current.get("author"),
            "source": "current_audiobook",
            "library_item_id": library_item_id,
            "position_seconds": current.get("current_time_seconds"),
            "duration_seconds": current.get("duration_seconds"),
            "art_url": _build_ui_audiobook_art_url(library_item_id, user_id),
        }
    ]


def _build_ui_audio_sleep_timer_status(source: str | None) -> dict[str, object]:
    timers = list_alerts(source, SLEEP_TIMER_KIND) if source else []
    status: dict[str, object] = {
        "supported": True,
        "selected_minutes": 0,
        "options_minutes": [0, 15, 20, 30, 60],
        "active": False,
        "count": len(timers),
        "remaining_seconds": None,
        "remaining_label": None,
        "due_at": None,
        "alert_id": None,
    }
    if not timers:
        return status
    current = timers[0]
    remaining_seconds = max(0.0, (current.due_at - datetime.now().astimezone()).total_seconds())
    status.update(
        {
            "active": True,
            "alert_id": current.alert_id,
            "due_at": current.due_at.isoformat(),
            "remaining_seconds": remaining_seconds,
            "remaining_label": format_duration(remaining_seconds),
        }
    )
    return status


def ui_audio_search_session_id(client_id: str) -> str:
    return f"ui-audio:{client_id}"


def ui_audio_target_session_id(client_id: str, target: str) -> str:
    return f"ui-audio:{client_id}:{target}"


def _build_ui_audiobook_art_url(library_item_id: str, user_id: str | None) -> str | None:
    normalized_id = str(library_item_id or "").strip()
    if not normalized_id:
        return None
    query = f"?user_id={urlparse.quote(user_id)}" if user_id else ""
    return f"/api/ui/audio/art/audiobook/{urlparse.quote(normalized_id)}{query}"


def _build_ui_music_art_url(art_path: object) -> str | None:
    normalized_path = str(art_path or "").strip()
    if not normalized_path or not normalized_path.startswith("/") or "://" in normalized_path:
        return None
    return f"/api/ui/audio/art/music?path={urlparse.quote(normalized_path, safe='')}"


def _normalize_ui_audiobook_result(
    candidate: dict[str, object],
    *,
    source: str = "audiobook_search",
    user_id: str | None = None,
) -> dict[str, object] | None:
    library_item_id = str(candidate.get("library_item_id") or "").strip()
    if not library_item_id:
        return None
    author = str(candidate.get("author") or candidate.get("subtitle") or "").strip()
    narrator = str(candidate.get("narrator") or "").strip()
    subtitle_parts = [part for part in [author, f"Narrated by {narrator}" if narrator else ""] if part]
    duration = candidate.get("duration_seconds", candidate.get("duration"))
    return {
        "result_id": f"audiobook:{library_item_id}",
        "type": "audiobook",
        "title": str(candidate.get("title") or "Untitled audiobook").strip(),
        "subtitle": " • ".join(subtitle_parts) if subtitle_parts else "Audiobook",
        "source": source,
        "library_item_id": library_item_id,
        "author": author,
        "narrator": narrator,
        "duration_seconds": duration,
        "art_url": candidate.get("art_url") or _build_ui_audiobook_art_url(library_item_id, user_id),
    }


def _normalize_ui_music_result(candidate: dict[str, object]) -> dict[str, object] | None:
    media_type = str(candidate.get("media_type") or candidate.get("type") or "").strip().lower()
    if media_type not in {"track", "album"}:
        return None
    selection_id = music_selection_id(candidate)
    provider_ref = music_provider_ref(candidate)
    if not selection_id:
        return None
    title = str(candidate.get("title") or candidate.get("album") or "Untitled music").strip()
    artist = str(candidate.get("artist") or "").strip()
    album = str(candidate.get("album") or "").strip()
    subtitle = artist if media_type == "album" else " • ".join(part for part in [artist, album] if part)
    return {
        "result_id": selection_id,
        "type": "music",
        "media_type": media_type,
        "title": title,
        "subtitle": subtitle or media_type.title(),
        "source": "music_search",
        "selection_id": selection_id,
        "provider_ref": provider_ref,
        "artist": artist,
        "album": album,
        "duration_seconds": candidate.get("duration_seconds"),
        "art_url": candidate.get("art_url") or _build_ui_music_art_url(candidate.get("art_path")),
    }


def _build_ui_music_search_intent(query: str, media_type: str) -> MusicIntent:
    normalized = " ".join(str(query or "").strip().split())
    return MusicIntent(
        intent="play",
        media_type=media_type,
        title=normalized if media_type == "track" else None,
        artist=None,
        album=normalized if media_type == "album" else None,
        playlist=None,
        genre=None,
        qualifiers=[],
        mode="replace",
        original_text=normalized,
    )


def _search_ui_audio_audiobooks(
    *,
    query: str,
    user_id: str | None,
    limit: int,
    audiobook_execution: CanonicalAudiobookExecution,
) -> list[dict[str, object]]:
    candidates = audiobook_execution.search_audiobooks(query, user_id=user_id)
    results = [
        result
        for result in (_normalize_ui_audiobook_result(candidate, user_id=user_id) for candidate in candidates[:limit])
        if result is not None
    ]
    return results[:limit]


def _search_ui_audio_music(
    *,
    query: str,
    limit: int,
    music_execution: CanonicalMusicExecution,
) -> list[dict[str, object]]:
    normalized_results: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for media_type in ("track", "album"):
        for candidate in music_execution.search(_build_ui_music_search_intent(query, media_type)):
            normalized = _normalize_ui_music_result(candidate)
            if normalized is None:
                continue
            result_id = str(normalized.get("result_id") or "").strip()
            if not result_id or result_id in seen_ids:
                continue
            seen_ids.add(result_id)
            normalized_results.append(normalized)
            if len(normalized_results) >= limit:
                return normalized_results
    return normalized_results


def build_ui_audio_actions(output_owner: dict[str, object] | None) -> list[dict[str, object]]:
    media_kind = str((output_owner or {}).get("media_kind") or "").strip().lower()
    if media_kind == "audiobook":
        action_ids = ["resume_audiobook", "pause_audiobook", "stop_audiobook"]
    elif media_kind == "music":
        action_ids = ["resume_music", "pause_music", "stop_music"]
    else:
        action_ids = ["resume_audiobook", "resume_music"]
    allowed = set(action_ids)
    return [dict(item) for item in _UI_AUDIO_ACTION_DEFINITIONS if str(item.get("action_id")) in allowed]


def summarize_ui_playback_session(session: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(session, dict):
        return None
    return {
        "backend_type": session.get("backend_type"),
        "media_kind": session.get("media_kind"),
        "state": session.get("state"),
        "title": session.get("title"),
        "artist_or_author": session.get("artist_or_author"),
        "position_seconds": session.get("position_seconds"),
        "duration_seconds": session.get("duration_seconds"),
        "resumable": bool(session.get("resumable")),
    }


def _summarize_ui_audiobook_progress(progress: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(progress, dict):
        return None
    progress = normalize_audiobook_progress(progress)
    if progress is None:
        return None
    return {
        "library_item_id": str(progress.get("library_item_id", "")).strip(),
        "title": str(progress.get("title", "")).strip(),
        "author": str(progress.get("author", "")).strip(),
        "current_time_seconds": float(progress.get("current_time_seconds") or 0),
        "duration_seconds": float(progress.get("duration_seconds") or 0),
    }


def _extract_ui_audiobook_item_metadata(item: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(item, dict):
        return {}
    item = normalize_audiobook_item(item)
    return {
        "title": str(item.get("title") or "").strip(),
        "author": str(item.get("author") or "").strip(),
        "duration_seconds": item.get("duration_seconds"),
    }


def _enrich_ui_audiobook_current(
    current: dict[str, object] | None,
    *,
    user_id: str | None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
) -> dict[str, object] | None:
    if not isinstance(current, dict):
        return None
    if str(current.get("title") or "").strip() and str(current.get("author") or "").strip():
        return current
    library_item_id = str(current.get("library_item_id") or "").strip()
    if not library_item_id:
        return current
    try:
        if audiobook_execution is None:
            return current
        metadata = _extract_ui_audiobook_item_metadata(audiobook_execution.fetch_item(library_item_id, user_id=user_id))
    except Exception as exc:
        logger.info("Unable to enrich UI audiobook progress metadata for %s: %s", library_item_id, exc)
        return current
    enriched = dict(current)
    if not str(enriched.get("title") or "").strip() and metadata.get("title"):
        enriched["title"] = metadata["title"]
    if not str(enriched.get("author") or "").strip() and metadata.get("author"):
        enriched["author"] = metadata["author"]
    try:
        if not float(enriched.get("duration_seconds") or 0) and metadata.get("duration_seconds") is not None:
            enriched["duration_seconds"] = float(metadata["duration_seconds"] or 0)
    except (TypeError, ValueError):
        pass
    return enriched


def build_ui_audio_snapshot(
    source: str | None = None,
    user_id: str | None = None,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    selected_source, configured_sources = resolve_ui_audio_source(
        source,
        music_execution=music_execution,
        audiobook_execution=audiobook_execution,
    )
    users, default_user_id = build_ui_audio_users(
        selected_source,
        household_settings=household_settings,
    )
    selected_user = resolve_ui_audio_user(user_id, users, default_user_id)
    playback_payload: dict[str, object] = {
        "ok": False,
        "active": False,
        "active_sessions": [],
        "output_owner": None,
    }
    if selected_source is None:
        playback_payload["detail"] = "No playback-capable sources are configured."
    else:
        try:
            fetch_authority = (
                music_execution.fetch_playback_authority
                if music_execution is not None and music_execution.settings.playback_target(selected_source) is not None
                else audiobook_execution.fetch_playback_authority
                if audiobook_execution is not None
                else None
            )
            if fetch_authority is None:
                raise RuntimeError("No canonical playback provider is configured.")
            authority = fetch_authority(selected_source)
            sessions = authority.get("active_sessions")
            if not isinstance(sessions, list):
                sessions = []
            playback_payload = {
                "ok": True,
                "active": bool(authority.get("playback_active")) or bool(sessions),
                "active_sessions": [
                    summary
                    for summary in (summarize_ui_playback_session(session) for session in sessions)
                    if summary is not None
                ],
                "output_owner": summarize_ui_playback_session(authority.get("output_owner")),
                "degraded_state": bool(authority.get("degraded_state")),
                "degraded_reasons": list(authority.get("degraded_reasons") or []),
            }
        except ControlPlaneError as exc:
            playback_payload = {
                "ok": False,
                "active": False,
                "active_sessions": [],
                "output_owner": None,
                **serialize_control_plane_error(exc),
            }

    try:
        if audiobook_execution is None:
            raise RuntimeError("Audiobooks are disabled in canonical configuration.")
        progress = audiobook_execution.fetch_current_progress(user_id=selected_user)
        current_audiobook = _enrich_ui_audiobook_current(
            _summarize_ui_audiobook_progress(progress),
            user_id=selected_user,
            audiobook_execution=audiobook_execution,
        )
        audiobook_payload: dict[str, object] = {
            "ok": True,
            "resume_available": isinstance(progress, dict),
            "current": current_audiobook,
        }
    except Exception as exc:
        audiobook_payload = {
            "ok": False,
            "resume_available": False,
            "current": None,
            "detail": str(exc),
        }

    return {
        "generated_at": _build_ui_generated_at(),
        "users": users,
        "selected_user": selected_user,
        "source": selected_source,
        "selected_target": selected_source,
        "available_sources": _build_ui_audio_sources(configured_sources, household_settings=household_settings),
        "targets": _build_ui_audio_sources(configured_sources, household_settings=household_settings),
        "playback": playback_payload,
        "now_playing": playback_payload.get("output_owner"),
        "audiobook": audiobook_payload,
        "current_audiobook": audiobook_payload.get("current"),
        "results": _build_ui_audio_results(audiobook_payload, user_id=selected_user),
        "selected_result": None,
        "sleep_timer": _build_ui_audio_sleep_timer_status(selected_source),
        "actions": build_ui_audio_actions(playback_payload.get("output_owner")),
        "capabilities": {
            "library_search": True,
            "music_search": True,
            "current_audiobook": True,
            "target_selection": bool(configured_sources),
            "sleep_timer": True,
            "structured_play": True,
        },
        "refresh_after_seconds": 5,
    }


def ui_audio_search_impl(
    payload: UiAudioSearchRequest,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    household_settings: HouseholdRuntimeSettings | None,
) -> dict[str, object]:
    client_id = _normalize_ui_client_id(payload.client_id)
    query = " ".join(str(payload.query or "").strip().split())
    if not query:
        raise HTTPException(status_code=400, detail="Audio search query cannot be empty")
    default_source = str(payload.source or "").strip() or _infer_audio_source_from_client_id(
        client_id,
        household_settings=household_settings,
    )
    if default_source:
        resolve_ui_audio_source(default_source, music_execution=music_execution, audiobook_execution=audiobook_execution)
    users, default_user_id = build_ui_audio_users(default_source, household_settings=household_settings)
    selected_user = resolve_ui_audio_user(payload.user_id, users, default_user_id)
    try:
        if payload.kind == "audiobook":
            if audiobook_execution is None:
                raise HTTPException(status_code=409, detail="Audiobooks are disabled in canonical configuration.")
            results = _search_ui_audio_audiobooks(query=query, user_id=selected_user, limit=payload.limit, audiobook_execution=audiobook_execution)
        else:
            if music_execution is None:
                raise HTTPException(status_code=409, detail="Music is disabled in canonical configuration.")
            results = _search_ui_audio_music(query=query, limit=payload.limit, music_execution=music_execution)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Audio search failed: {exc}") from exc
    return {
        "ok": True,
        "generated_at": _build_ui_generated_at(),
        "client_id": client_id,
        "kind": payload.kind,
        "query": query,
        "selected_user": selected_user,
        "results": results,
        "result_count": len(results),
    }
