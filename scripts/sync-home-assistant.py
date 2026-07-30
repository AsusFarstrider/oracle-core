#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "server"))

from oracle_app.config import get_home_assistant_settings
from oracle_app.constants import CACHE_PATH


ROOM_DOMAINS = {"light", "switch", "fan", "climate", "cover", "scene"}


def fetch_states(base_url: str, token: str) -> list[dict]:
    req = request.Request(
        f"{base_url.rstrip('/')}/api/states",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with request.urlopen(req, timeout=20) as response:
        return json.load(response)


def normalize_alias(text: str) -> str:
    return " ".join(text.strip().lower().replace("_", " ").split())


def compact_alias(text: str) -> str:
    normalized = normalize_alias(text)
    return "".join(char for char in normalized if char.isalnum())


def build_aliases(entity_id: str, friendly_name: str) -> list[str]:
    _, object_id = entity_id.split(".", 1)
    aliases = {
        normalize_alias(friendly_name),
        normalize_alias(object_id),
    }

    if "'" in friendly_name:
        aliases.add(normalize_alias(friendly_name.replace("'", "")))

    expanded_aliases = set()
    for alias in aliases:
        if not alias:
            continue
        expanded_aliases.add(alias)
        compact = compact_alias(alias)
        if len(compact) >= 4:
            expanded_aliases.add(compact)

    return sorted(alias for alias in expanded_aliases if alias)


def build_cache(states: list[dict]) -> dict:
    rooms: list[dict] = []
    entities: list[dict] = []

    for state in states:
        entity_id = state.get("entity_id")
        if not entity_id or "." not in entity_id:
            continue

        domain, _ = entity_id.split(".", 1)
        attrs = state.get("attributes", {})
        friendly_name = str(attrs.get("friendly_name", entity_id))

        entities.append(
            {
                "entity_id": entity_id,
                "domain": domain,
                "friendly_name": friendly_name,
                "aliases": build_aliases(entity_id, friendly_name),
            }
        )

        members = attrs.get("entity_id")
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

    rooms.sort(key=lambda item: item["spoken_name"])
    entities.sort(key=lambda item: item["entity_id"])

    return {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "room_count": len(rooms),
        "entity_count": len(entities),
        "rooms": rooms,
        "entities": entities,
    }


def main() -> None:
    base_url, token = get_home_assistant_settings()
    states = fetch_states(base_url, token)
    cache = build_cache(states)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"Synced Home Assistant cache to {CACHE_PATH} "
        f"({cache['room_count']} rooms, {cache['entity_count']} entities)"
    )


if __name__ == "__main__":
    main()
