from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from oracle_app.suggestions.inference_contract import normalize_suggestion_items
from oracle_app.suggestions.redaction import redact_secrets

from ..schemas import OpenClawBridgeOptions, OpenClawBridgeResult


MAX_OPENCLAW_RESPONSE_BYTES = 524288


def generate_suggestions_http(packet: dict[str, Any], options: OpenClawBridgeOptions) -> OpenClawBridgeResult:
    base_url = options.base_url.strip().rstrip("/")
    endpoint_path = options.endpoint_path.strip() or "/"
    if not base_url:
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="configuration", errors=["OpenClaw HTTP base URL is not configured."])
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
            payload = response.read(MAX_OPENCLAW_RESPONSE_BYTES + 1)
            if len(payload) > MAX_OPENCLAW_RESPONSE_BYTES:
                return OpenClawBridgeResult(
                    ok=False,
                    adapter="http",
                    failure_class="response_validation",
                    errors=["OpenClaw response exceeded the 524288-byte limit."],
                )
            raw: Any = json.loads(payload.decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="transport", errors=[f"OpenClaw HTTP {exc.code}."])
    except error.URLError as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="transport", errors=[f"OpenClaw unavailable: {exc.reason}"])
    except TimeoutError:
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="transport", errors=["OpenClaw HTTP transport timed out."])
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="response_validation", errors=[f"OpenClaw returned invalid JSON: {exc}"])

    if not isinstance(raw, dict):
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="response_validation", raw_response={"value": raw}, errors=["OpenClaw response must be a JSON object."])

    suggestions = raw.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = raw.get("items")
    if not isinstance(suggestions, list):
        return OpenClawBridgeResult(ok=False, adapter="http", failure_class="response_validation", raw_response=redact_secrets(raw), errors=["OpenClaw response did not include a suggestions array."])

    normalized, validation_errors = normalize_suggestion_items(suggestions)
    if suggestions and not normalized:
        return OpenClawBridgeResult(
            ok=False,
            adapter="http",
            failure_class="response_validation",
            raw_response=redact_secrets(raw),
            errors=validation_errors,
        )
    return OpenClawBridgeResult(
        ok=True,
        adapter="http",
        raw_response=redact_secrets(raw),
        suggestions=normalized[: options.max_suggestions],
        errors=validation_errors,
    )
