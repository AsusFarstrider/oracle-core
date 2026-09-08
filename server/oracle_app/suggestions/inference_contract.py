from __future__ import annotations

import json
import textwrap
from typing import Any

from oracle_app.suggestions.redaction import redact_secrets


MAX_SUGGESTIONS_PROVIDER_OUTPUT_CHARACTERS = 524288


def suggestions_result_schema(max_suggestions: int) -> dict[str, object]:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "title",
            "severity",
            "category",
            "source",
            "summary",
            "evidence",
            "suggested_action",
            "recommended_oracle_action",
            "confidence",
            "requires_review",
        ],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 240},
            "severity": {
                "type": "string",
                "enum": ["info", "low", "medium", "high", "critical"],
            },
            "category": {
                "type": "string",
                "enum": [
                    "oracle",
                    "home_assistant",
                    "librenms",
                    "network",
                    "server",
                    "automation",
                    "security",
                    "maintenance",
                    "observability",
                    "unknown",
                ],
            },
            "source": {
                "type": "string",
                "enum": ["oracle", "home_assistant", "librenms", "mixed"],
            },
            "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 1000},
            },
            "suggested_action": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
            },
            "recommended_oracle_action": {
                "anyOf": [
                    {"type": "null"},
                    {"type": "string", "minLength": 1, "maxLength": 128},
                ]
            },
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "requires_review": {"type": "boolean", "const": True},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {
            "suggestions": {
                "type": "array",
                "maxItems": max_suggestions,
                "items": item,
            }
        },
    }


def build_suggestions_prompt(packet: dict[str, Any], max_suggestions: int) -> str:
    schema = suggestions_result_schema(max_suggestions)
    return textwrap.dedent(
        f"""
        You are an external advisory analyst for Oracle Suggestions.

        Analyze only the packet between BEGIN_ORACLE_DIAGNOSTIC_PACKET and END_ORACLE_DIAGNOSTIC_PACKET.
        The packet is your only evidence source. Do not use workspace files, bootstrap context, shell state, prior sessions, web, APIs, tools, memory, or model knowledge as evidence.
        You must not execute actions, request tool execution, or claim that you changed anything.

        Return only valid JSON satisfying this schema:
        {json.dumps(schema, indent=2)}

        Rules:
        - Return at most {max_suggestions} suggestions.
        - Suggestions are advisory only.
        - recommended_oracle_action must be null unless a future allowlist action name is explicitly present in the packet.
        - Use prior review history to avoid repeating rejected, corrected, ignored, or false-positive suggestions unless there is new evidence.
        - Include concrete evidence from the packet.
        - Do not include markdown fences or explanatory prose outside JSON.

        BEGIN_ORACLE_DIAGNOSTIC_PACKET
        {json.dumps(redact_secrets(packet), indent=2, sort_keys=True)}
        END_ORACLE_DIAGNOSTIC_PACKET
        """
    ).strip()


def parse_suggestion_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def normalize_suggestion_items(items: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"Suggestion item {index + 1} is not an object.")
            continue
        try:
            normalized.append(_normalize_item(item))
        except (TypeError, ValueError) as exc:
            errors.append(f"Suggestion item {index + 1} is invalid: {exc}")
    return normalized, errors


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    suggested_action = str(item.get("suggested_action") or "").strip()
    if not title:
        raise ValueError("title is required")
    if not summary:
        raise ValueError("summary is required")
    if not suggested_action:
        raise ValueError("suggested_action is required")
    severity = str(item.get("severity") or "info").strip().lower()
    if severity not in {"info", "low", "medium", "high", "critical"}:
        severity = "info"
    category = str(item.get("category") or "unknown").strip().lower()
    if category not in {
        "oracle",
        "home_assistant",
        "librenms",
        "network",
        "server",
        "automation",
        "security",
        "maintenance",
        "observability",
        "unknown",
    }:
        category = "unknown"
    source = str(item.get("source") or "mixed").strip().lower()
    if source not in {"oracle", "home_assistant", "librenms", "mixed"}:
        source = "mixed"
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("evidence must be an array")
    evidence = [str(entry).strip()[:1000] for entry in evidence[:20] if str(entry).strip()]
    if not evidence:
        raise ValueError("at least one concrete evidence item is required")
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    recommended = item.get("recommended_oracle_action")
    recommended = str(recommended).strip()[:128] if recommended else None
    return {
        "title": title[:240],
        "severity": severity,
        "category": category,
        "source": source,
        "summary": summary[:4000],
        "evidence": evidence,
        "suggested_action": suggested_action[:2000],
        "recommended_oracle_action": recommended,
        "confidence": max(0.0, min(confidence, 1.0)),
        # Provider output cannot waive Oracle's human-review boundary.
        "requires_review": True,
    }
