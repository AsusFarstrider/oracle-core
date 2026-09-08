from __future__ import annotations

import json
from typing import Any

from .collectors import collect_sources
from .redaction import redact_secrets
from .storage import utc_now_iso


MAX_SUGGESTIONS_PACKET_BYTES = 131072
MAX_SUGGESTIONS_SECTION_BYTES = 20480
MAX_PACKET_STRING_CHARACTERS = 4096
MAX_PACKET_COLLECTION_ITEMS = 100
MAX_PACKET_OBJECT_FIELDS = 100
MAX_PACKET_DEPTH = 8


def build_packet(
    *,
    run_id: str,
    run_type: str,
    window_start: str,
    window_end: str,
    reason: str | None,
    custom_prompt: str | None,
    max_suggestions: int,
    canonical_composition=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sections, collector_status = collect_sources(
        run_type,
        canonical_composition=canonical_composition,
        window_start=window_start,
        window_end=window_end,
    )
    requested = [name for name in collector_status if name != "review_history"]
    available = [
        name for name in requested if collector_status[name].get("status") == "available"
    ]
    partial = [
        name for name in requested if collector_status[name].get("status") == "partial"
    ]
    unavailable = [
        name for name in requested if collector_status[name].get("status") == "unavailable"
    ]
    collection_status = (
        "unavailable"
        if requested and len(unavailable) == len(requested)
        else "partial"
        if partial or unavailable
        else "available"
    )
    bounded_sections: dict[str, Any] = {}
    omissions: list[dict[str, Any]] = []
    for name, section in sections.items():
        bounded_sections[name], section_omissions = _bounded_json_value(
            section,
            max_bytes=MAX_SUGGESTIONS_SECTION_BYTES,
            path=f"source_sections.{name}",
        )
        omissions.extend(section_omissions)

    packet = {
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "data_window_start": window_start,
        "data_window_end": window_end,
        "reason": reason or "Manual System Mode suggestion generation.",
        "run_type": run_type,
        "custom_prompt": custom_prompt,
        "max_suggestions": max_suggestions,
        "collection": {
            "status": collection_status,
            "requested_sources": requested,
            "available_sources": available,
            "partial_sources": partial,
            "unavailable_sources": unavailable,
            "collector_status": collector_status,
            "bounds": {
                "brain_log_characters": 30000,
                "home_assistant_unavailable_items": 100,
                "review_history_items": 50,
                "memory_events": 200,
                "section_bytes": MAX_SUGGESTIONS_SECTION_BYTES,
                "packet_bytes": MAX_SUGGESTIONS_PACKET_BYTES,
            },
            "omissions": omissions[:100],
        },
        "instructions": {
            "role": "OpenClaw is an external advisory analyst only.",
            "output": "Return structured JSON suggestions only.",
            "do_not_execute": True,
            "avoid_repeats": "Use review_history to avoid repeating rejected, corrected, ignored, or false-positive suggestions unless new evidence exists.",
            "recommended_oracle_action": "Use null unless a future allowlist action name is clearly known. It will not be executed.",
        },
        "source_sections": bounded_sections,
    }
    redacted_packet = redact_secrets(packet)
    redacted_packet = _enforce_packet_limit(redacted_packet)
    redacted_packet["collection"]["serialized_bytes"] = _json_size(redacted_packet)
    redacted_packet = _enforce_packet_limit(redacted_packet)
    redacted_packet["collection"]["serialized_bytes"] = _json_size(redacted_packet)
    return redacted_packet, redacted_packet["collection"]["collector_status"]


def _bounded_json_value(
    value: Any,
    *,
    max_bytes: int,
    path: str,
    depth: int = 0,
) -> tuple[Any, list[dict[str, Any]]]:
    omissions: list[dict[str, Any]] = []
    if depth >= MAX_PACKET_DEPTH:
        return {"_omitted": "maximum_depth"}, [{"path": path, "reason": "maximum_depth"}]
    if value is None or isinstance(value, (bool, int, float)):
        return value, omissions
    if isinstance(value, str):
        bounded = value[:MAX_PACKET_STRING_CHARACTERS]
        if len(bounded) != len(value):
            omissions.append(
                {
                    "path": path,
                    "reason": "string_characters",
                    "omitted": len(value) - len(bounded),
                }
            )
        while _json_size(bounded) > max_bytes and bounded:
            bounded = bounded[: max(0, len(bounded) // 2)]
        return bounded, omissions
    if isinstance(value, list):
        result: list[Any] = []
        candidates = value[:MAX_PACKET_COLLECTION_ITEMS]
        for index, item in enumerate(candidates):
            bounded, nested = _bounded_json_value(
                item,
                max_bytes=max_bytes,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            if _json_size([*result, bounded]) > max_bytes:
                omissions.append(
                    {"path": path, "reason": "section_bytes", "omitted": len(value) - index}
                )
                break
            result.append(bounded)
            omissions.extend(nested)
        else:
            if len(value) > len(candidates):
                omissions.append(
                    {
                        "path": path,
                        "reason": "collection_items",
                        "omitted": len(value) - len(candidates),
                    }
                )
        return result, omissions
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        candidates = list(value.items())[:MAX_PACKET_OBJECT_FIELDS]
        for index, (key, item) in enumerate(candidates):
            text_key = str(key)[:240]
            bounded, nested = _bounded_json_value(
                item,
                max_bytes=max_bytes,
                path=f"{path}.{text_key}",
                depth=depth + 1,
            )
            if _json_size({**result, text_key: bounded}) > max_bytes:
                omissions.append(
                    {"path": path, "reason": "section_bytes", "omitted": len(value) - index}
                )
                break
            result[text_key] = bounded
            omissions.extend(nested)
        else:
            if len(value) > len(candidates):
                omissions.append(
                    {
                        "path": path,
                        "reason": "object_fields",
                        "omitted": len(value) - len(candidates),
                    }
                )
        return result, omissions
    return _bounded_json_value(str(value), max_bytes=max_bytes, path=path, depth=depth)


def _enforce_packet_limit(packet: dict[str, Any]) -> dict[str, Any]:
    if _json_size(packet) <= MAX_SUGGESTIONS_PACKET_BYTES:
        return packet
    sections = packet.get("source_sections")
    collection = packet.get("collection")
    if not isinstance(sections, dict) or not isinstance(collection, dict):
        raise ValueError("Suggestions packet cannot be bounded safely.")
    omissions = collection.setdefault("omissions", [])
    for name in sorted(sections, key=lambda key: _json_size(sections[key]), reverse=True):
        sections[name] = {"_omitted": "packet_bytes"}
        if isinstance(omissions, list):
            omissions.append({"path": f"source_sections.{name}", "reason": "packet_bytes"})
            del omissions[100:]
        if _json_size(packet) <= MAX_SUGGESTIONS_PACKET_BYTES:
            return packet
    raise ValueError("Suggestions packet metadata exceeds its serialized byte limit.")


def _json_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
