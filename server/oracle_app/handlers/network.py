from __future__ import annotations

from typing import Any

from oracle_app.network import build_network_response, parse_network_query
from oracle_app.network_runtime import CanonicalNetworkExecution
from oracle_app.schemas import DispatchPlan


class NetworkHandler:
    target = "network"

    def __init__(
        self,
        canonical_execution: CanonicalNetworkExecution | None = None,
        *,
        canonical_authority: bool = False,
    ) -> None:
        self.canonical_execution = canonical_execution
        self.canonical_authority = canonical_authority

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        del registry
        text = str(dispatch.payload.get("text", "")).strip()
        normalized = str(dispatch.payload.get("normalized_text", "")).strip() or text
        query = parse_network_query(normalized)
        if query is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "network_failed",
                "error": "network_unrecognized",
                "detail": "Oracle could not parse that network request.",
            }
            return dispatch

        speech, summary = build_network_response(
            normalized,
            canonical_execution=self.canonical_execution,
            canonical_authority=self.canonical_authority,
        )
        dispatch.status = "executed"
        dispatch.result = {
            "action": query.action,
            "speech": speech,
            "summary": summary,
        }
        return dispatch
