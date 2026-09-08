from __future__ import annotations

from typing import Any

from oracle_app.information_runtime import CanonicalNewsExecution
from oracle_app.news import parse_news_query
from oracle_app.news_context import resolve_news_context, retain_news_context
from oracle_app.schemas import DispatchPlan


class NewsHandler:
    target = "news"

    def __init__(
        self,
        canonical_execution: CanonicalNewsExecution | None = None,
    ) -> None:
        self.canonical_execution = canonical_execution

    def handle(self, dispatch: DispatchPlan, registry: Any) -> DispatchPlan:
        text = str(dispatch.payload.get("text", "")).strip()
        normalized = str(dispatch.payload.get("normalized_text", "")).strip() or text
        source = dispatch.payload.get("source")
        session_id = dispatch.payload.get("session_id")
        if self.canonical_execution is None:
            dispatch.status = "executed"
            dispatch.result = _disabled_news_result(None)
            return dispatch
        context_resolution = resolve_news_context(
            normalized,
            source=source,
            session_id=session_id,
        )
        if context_resolution.result is not None:
            dispatch.status = "executed"
            dispatch.result = context_resolution.result
            return dispatch
        query = context_resolution.query or parse_news_query(
            normalized, runtime_settings=self.canonical_execution.settings
        )
        if query is None:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "news_failed",
                "error": "news_unrecognized",
                "detail": "Oracle could not parse that news request.",
            }
            return dispatch

        try:
            result = self.canonical_execution.execute(query)
        except Exception as exc:
            dispatch.status = "failed"
            dispatch.result = {
                "action": "news_failed",
                "error": "news_fetch_failed",
                "detail": str(exc),
            }
            return dispatch

        dispatch.status = "executed"
        dispatch.result = result
        if result.get("action") in {"headlines", "article", "updates"}:
            retain_news_context(result, source=source, session_id=session_id)
        return dispatch


def _disabled_news_result(query) -> dict[str, object]:
    return {
        "action": "headlines",
        "source": None if query is None else query.source,
        "source_label": "the news",
        "headlines": [],
        "error": "news_source_unavailable",
    }
