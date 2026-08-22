from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib import request

from .configuration.home_assistant_runtime_settings import HomeAssistantRuntimeSettings
from .constants import CACHE_PATH


ROOM_DOMAINS = {"light", "switch", "fan", "climate", "cover", "scene"}


def refresh_home_assistant_cache(
    settings: HomeAssistantRuntimeSettings | None,
    *,
    cache_path: Path = CACHE_PATH,
) -> dict[str, object]:
    if settings is None or not settings.enabled or not settings.base_url or not settings.credential:
        raise RuntimeError("Home Assistant is disabled in the applied configuration.")
    states = fetch_states(
        settings.base_url,
        settings.credential,
        timeout_seconds=settings.timeout_seconds,
    )
    cache = build_cache(states)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix=f".{cache_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(cache, handle, indent=2, sort_keys=True)
            handle.write("\n")
        temporary_path.replace(cache_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return cache


def fetch_states(
    base_url: str,
    credential: str,
    *,
    timeout_seconds: float | None,
) -> list[dict[str, object]]:
    req = request.Request(
        f"{base_url.rstrip('/')}/api/states",
        headers={"Authorization": f"Bearer {credential}"},
        method="GET",
    )
    with request.urlopen(req, timeout=float(timeout_seconds or 5)) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise RuntimeError("Home Assistant states response is not a list.")
    return [dict(item) for item in payload if isinstance(item, dict)]


def normalize_alias(text: str) -> str:
    return " ".join(text.strip().lower().replace("_", " ").split())


def compact_alias(text: str) -> str:
    return "".join(char for char in normalize_alias(text) if char.isalnum())


def build_aliases(entity_id: str, friendly_name: str) -> list[str]:
    _, object_id = entity_id.split(".", 1)
    aliases = {normalize_alias(friendly_name), normalize_alias(object_id)}
    if "'" in friendly_name:
        aliases.add(normalize_alias(friendly_name.replace("'", "")))
    expanded = set()
    for alias in aliases:
        if not alias:
            continue
        expanded.add(alias)
        compact = compact_alias(alias)
        if len(compact) >= 4:
            expanded.add(compact)
    return sorted(expanded)


def build_cache(states: list[dict[str, object]]) -> dict[str, object]:
    rooms: list[dict[str, object]] = []
    entities: list[dict[str, object]] = []
    for state in states:
        entity_id = str(state.get("entity_id") or "").strip()
        if "." not in entity_id:
            continue
        domain, _ = entity_id.split(".", 1)
        raw_attributes = state.get("attributes")
        attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
        friendly_name = str(attributes.get("friendly_name") or entity_id)
        entities.append(
            {
                "entity_id": entity_id,
                "domain": domain,
                "friendly_name": friendly_name,
                "aliases": build_aliases(entity_id, friendly_name),
            }
        )
        members = attributes.get("entity_id")
        if domain in ROOM_DOMAINS and isinstance(members, list) and members and friendly_name:
            rooms.append(
                {
                    "entity_id": entity_id,
                    "domain": domain,
                    "name": friendly_name,
                    "spoken_name": normalize_alias(friendly_name),
                    "aliases": build_aliases(entity_id, friendly_name),
                    "members": members,
                }
            )
    rooms.sort(key=lambda item: str(item["spoken_name"]))
    entities.sort(key=lambda item: str(item["entity_id"]))
    return {
        "synced_at": datetime.now(UTC).isoformat(),
        "room_count": len(rooms),
        "entity_count": len(entities),
        "rooms": rooms,
        "entities": entities,
    }
