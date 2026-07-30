from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from oracle_app.suggestions.redaction import redact_secrets

from ..schemas import OpenClawBridgeOptions, OpenClawBridgeResult


def generate_suggestions_http(packet: dict[str, Any], options: OpenClawBridgeOptions) -> OpenClawBridgeResult:
    base_url = options.base_url.strip().rstrip("/")
    endpoint_path = options.endpoint_path.strip() or "/"
    if not base_url:
        return OpenClawBridgeResult(ok=False, adapter="http", errors=["OpenClaw HTTP base URL is not configured."])
    url = f"{base_url}{endpoint_path if endpoint_path.startswith('/') else '/' + endpoint_path}"
    body = json.dumps({"packet": redact_secrets(packet), "max_suggestions": options.max_suggestions}).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=options.timeout_seconds) as response:
            raw: Any = json.loads(response.read().decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", errors=[f"OpenClaw HTTP {exc.code}."])
    except error.URLError as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", errors=[f"OpenClaw unavailable: {exc.reason}"])
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", errors=[f"OpenClaw returned invalid JSON: {exc}"])

    if not isinstance(raw, dict):
        return OpenClawBridgeResult(ok=False, adapter="http", raw_response={"value": raw}, errors=["OpenClaw response must be a JSON object."])

    suggestions = raw.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = raw.get("items")
    if not isinstance(suggestions, list):
        return OpenClawBridgeResult(ok=False, adapter="http", raw_response=redact_secrets(raw), errors=["OpenClaw response did not include a suggestions array."])

    normalized = [_normalize_item(item) for item in suggestions if isinstance(item, dict)]
    return OpenClawBridgeResult(
        ok=True,
        adapter="http",
        raw_response=redact_secrets(raw),
        suggestions=normalized[: options.max_suggestions],
    )


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    severity = str(item.get("severity") or "info").strip().lower()
    if severity not in {"info", "low", "medium", "high", "critical"}:
        severity = "info"
    category = str(item.get("category") or "unknown").strip().lower()
    if category not in {"oracle", "home_assistant", "librenms", "network", "server", "automation", "security", "maintenance", "unknown"}:
        category = "unknown"
    source = str(item.get("source") or "mixed").strip().lower()
    if source not in {"oracle", "home_assistant", "librenms", "mixed"}:
        source = "mixed"
    evidence = item.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    return {
        "title": str(item.get("title") or "Untitled OpenClaw suggestion"),
        "severity": severity,
        "category": category,
        "source": source,
        "summary": str(item.get("summary") or ""),
        "evidence": [str(entry) for entry in evidence[:20]],
        "suggested_action": str(item.get("suggested_action") or ""),
        "recommended_oracle_action": item.get("recommended_oracle_action") if item.get("recommended_oracle_action") else None,
        "confidence": max(0.0, min(float(item.get("confidence") or 0.0), 1.0)),
        "requires_review": bool(item.get("requires_review", True)),
    }

