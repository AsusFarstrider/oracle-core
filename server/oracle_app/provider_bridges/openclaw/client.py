from __future__ import annotations

from typing import Any

from .adapters.http import generate_suggestions_http
from .adapters.ssh_cli import generate_suggestions_ssh_cli
from .schemas import OpenClawBridgeOptions, OpenClawBridgeResult


def generate_suggestions(
    packet: dict[str, Any],
    options: OpenClawBridgeOptions | dict[str, Any],
) -> dict[str, Any]:
    parsed = options if isinstance(options, OpenClawBridgeOptions) else OpenClawBridgeOptions(**options)
    if parsed.use_mock or parsed.adapter == "mock":
        return _mock_result(packet, parsed).model_dump()
    if parsed.adapter == "http":
        return generate_suggestions_http(packet, parsed).model_dump()
    if parsed.adapter == "ssh_cli":
        return generate_suggestions_ssh_cli(packet, parsed).model_dump()
    return OpenClawBridgeResult(
        ok=False,
        adapter=parsed.adapter,
        errors=[f"OpenClaw adapter {parsed.adapter} is not implemented yet."],
    ).model_dump()


def _mock_result(packet: dict[str, Any], options: OpenClawBridgeOptions) -> OpenClawBridgeResult:
    run_id = str(packet.get("run_id") or "unknown")
    suggestion = {
        "title": "[MOCK] Review Oracle warning patterns",
        "severity": "low",
        "category": "oracle",
        "source": "oracle",
        "summary": "Mock OpenClaw output generated from the explicit dev/mock path.",
        "evidence": [f"Mock run_id: {run_id}"],
        "suggested_action": "Use this only to verify the suggestion inbox and review workflow.",
        "recommended_oracle_action": None,
        "confidence": 0.1,
        "requires_review": True,
    }
    return OpenClawBridgeResult(
        ok=True,
        adapter="mock",
        raw_response={"mock": True, "suggestions": [suggestion]},
        suggestions=[suggestion][: options.max_suggestions],
        mock=True,
    )
