from __future__ import annotations

from typing import Any


def music_provider_ref(selection: dict[str, Any]) -> dict[str, Any]:
    existing = selection.get("provider_ref")
    if isinstance(existing, dict):
        return {
            "provider": str(existing.get("provider") or "plex").strip(),
            "item_id": str(existing.get("item_id") or existing.get("rating_key") or "").strip(),
            "item_path": str(existing.get("item_path") or existing.get("plex_key") or "").strip(),
            "parent_path": str(existing.get("parent_path") or existing.get("parent_key") or "").strip(),
        }
    return {
        "provider": "plex",
        "item_id": str(selection.get("provider_item_id") or selection.get("rating_key") or "").strip(),
        "item_path": str(selection.get("provider_item_path") or selection.get("plex_key") or "").strip(),
        "parent_path": str(selection.get("provider_parent_path") or selection.get("parent_key") or "").strip(),
    }


def music_selection_id(selection: dict[str, Any]) -> str:
    existing = str(selection.get("selection_id") or "").strip()
    if existing:
        return existing
    media_type = str(selection.get("media_type") or selection.get("type") or "music").strip().lower() or "music"
    ref = music_provider_ref(selection)
    identity = str(ref.get("item_id") or ref.get("item_path") or "").strip()
    return f"plex:{media_type}:{identity}" if identity else ""


def music_identity_key(selection: dict[str, Any]) -> str:
    return music_selection_id(selection) or str(
        music_provider_ref(selection).get("item_id") or music_provider_ref(selection).get("item_path") or ""
    ).strip()


def music_selection_with_provider_fields(selection: dict[str, Any]) -> dict[str, Any]:
    ref = music_provider_ref(selection)
    return {
        **selection,
        "provider_item_id": ref.get("item_id"),
        "provider_item_path": ref.get("item_path"),
        "provider_parent_path": ref.get("parent_path"),
        # Compatibility aliases are emitted only for the existing satellite transport.
        "plex_key": ref.get("item_path"),
        "parent_key": ref.get("parent_path"),
        "rating_key": ref.get("item_id"),
    }


def music_pending_option(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": music_selection_id(selection),
        "provider_ref": music_provider_ref(selection),
        "title": selection.get("title"),
        "artist": selection.get("artist"),
        "album": selection.get("album"),
        "media_type": selection.get("type") or selection.get("media_type"),
        "score": selection.get("score"),
    }
