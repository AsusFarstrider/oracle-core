from __future__ import annotations

from typing import Any

from .collectors import collect_sources
from .redaction import redact_secrets
from .storage import utc_now_iso


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
    canonical_authority: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sections, collector_status = collect_sources(
        run_type,
        canonical_composition=canonical_composition,
        canonical_authority=canonical_authority,
    )
    packet = {
        "run_id": run_id,
        "generated_at": utc_now_iso(),
        "data_window_start": window_start,
        "data_window_end": window_end,
        "reason": reason or "Manual System Mode suggestion generation.",
        "run_type": run_type,
        "custom_prompt": custom_prompt,
        "max_suggestions": max_suggestions,
        "instructions": {
            "role": "OpenClaw is an external advisory analyst only.",
            "output": "Return structured JSON suggestions only.",
            "do_not_execute": True,
            "avoid_repeats": "Use review_history to avoid repeating rejected, corrected, ignored, or false-positive suggestions unless new evidence exists.",
            "recommended_oracle_action": "Use null unless a future allowlist action name is clearly known. It will not be executed.",
        },
        "source_sections": sections,
    }
    return redact_secrets(packet), collector_status
