from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Literal

from oracle_app.schemas import FactsProviderResult
from oracle_app.session_state import (
    clear_pending_state,
    get_informational_context,
    get_pending_state,
    set_informational_context,
    set_pending_state,
)


_REFERENCE_RE = re.compile(r"\b(?:he|she|they|it|him|her|them|that|this)\b", re.IGNORECASE)
_PROVENANCE_PHRASES = {
    "where did you get that",
    "where did that come from",
    "what is your source",
    "what was your source",
    "what source is that from",
    "what source was that from",
}
_AGE_PHRASES = {
    "how old is that",
    "how old was that",
    "how old is that answer",
    "when did you look that up",
}


@dataclass(frozen=True)
class FactsContextResolution:
    kind: Literal["lookup", "provenance", "no_provenance", "age", "no_age", "clarification"]
    query: str
    prompt: str | None = None
    source_names: tuple[str, ...] = ()
    context_used: bool = False
    age_seconds: int | None = None


def resolve_facts_context(
    query: str,
    *,
    source: str | None,
    session_id: str | None,
    now: datetime | None = None,
) -> FactsContextResolution:
    normalized = _normalize(query)
    pending = get_pending_state(source, session_id, domain="informational")
    if isinstance(pending, dict) and pending.get("target_domain") == "facts":
        resolved = _resolve_pending_subject(query, pending)
        if resolved is not None:
            clear_pending_state(source, session_id, domain="informational", reason="facts_subject_supplied")
            return FactsContextResolution("lookup", resolved, context_used=True)
        return FactsContextResolution(
            "clarification",
            query,
            prompt="Please name the person or subject you mean.",
        )

    context = get_informational_context(source, session_id, domain="facts")
    subject = _context_subject(context)
    if normalized in _PROVENANCE_PHRASES:
        sources = _context_sources(context)
        if subject and sources:
            return FactsContextResolution("provenance", query, source_names=sources, context_used=True)
        return FactsContextResolution("no_provenance", query)
    if normalized in _AGE_PHRASES:
        retrieved_at = _context_retrieved_at(context)
        if subject and retrieved_at is not None:
            current = now or datetime.now(UTC)
            return FactsContextResolution(
                "age",
                query,
                context_used=True,
                age_seconds=max(0, int((current.astimezone(UTC) - retrieved_at).total_seconds())),
            )
        return FactsContextResolution("no_age", query)

    if _REFERENCE_RE.search(query):
        if subject:
            return FactsContextResolution(
                "lookup",
                _REFERENCE_RE.sub(subject, query),
                context_used=True,
            )
        stored = set_pending_state(
            source,
            session_id,
            pending_type="clarification",
            domain="informational",
            payload={
                "target_domain": "facts",
                "clarification_kind": "facts_subject",
                "prompt": "Who or what are you asking about?",
                "options": [],
                "original_text": query,
                "subject_id": "facts_subject",
            },
        )
        return FactsContextResolution(
            "clarification",
            query,
            prompt="Who or what are you asking about?" if stored else "I need the subject of that question.",
        )
    return FactsContextResolution("lookup", query)


def retain_facts_context(
    result: FactsProviderResult,
    *,
    source: str | None,
    session_id: str | None,
    now: datetime | None = None,
) -> bool:
    if result.status not in {"answered", "evidence_only"}:
        return False
    subject_text = _result_subject(result)
    if not subject_text:
        return False
    evidence_sources = [item.source_name for item in result.evidence]
    source_ids = _unique_bounded(evidence_sources or [result.provider.name])
    evidence_ids = _unique_bounded(
        [
            str(item.provenance.get("url") or item.provenance.get("page_title") or item.title)
            for item in result.evidence
        ]
    )
    return set_informational_context(
        source,
        session_id,
        domain="facts",
        subject={
            "subject_id": _subject_id(result, subject_text),
            "subject_text": subject_text,
            "query_text": result.query,
            "source_ids": source_ids,
            "evidence_ids": evidence_ids,
            "retrieved_at": _result_retrieved_at(result, now=now).isoformat(),
        },
    )


def _resolve_pending_subject(answer: str, pending: dict[str, object]) -> str | None:
    if str(pending.get("clarification_kind") or "") != "facts_subject":
        return None
    subject = str(answer or "").strip(" .?!\"'")
    if not subject or len(subject) > 96 or len(subject.split()) > 10:
        return None
    normalized = _normalize(subject)
    if normalized.startswith(("what ", "when ", "where ", "who ", "how ", "why ", "tell ")):
        return None
    original = str(pending.get("original_text") or "").strip()
    if not original or _REFERENCE_RE.search(original) is None:
        return None
    return _REFERENCE_RE.sub(subject, original)


def _result_subject(result: FactsProviderResult) -> str:
    for evidence in result.evidence:
        title = str(evidence.provenance.get("page_title") or evidence.title or "").strip()
        if title and title.casefold() not in {"wikipedia", "static fact"}:
            return title[:512]
    return ""


def _subject_id(result: FactsProviderResult, subject_text: str) -> str:
    for evidence in result.evidence:
        value = str(evidence.provenance.get("url") or evidence.provenance.get("page_title") or "").strip()
        if value:
            return value[:512]
    return f"{result.provider.id}:{_normalize(subject_text)}"[:512]


def _context_subject(context: dict[str, object] | None) -> str:
    subject = context.get("subject") if isinstance(context, dict) else None
    return str(subject.get("subject_text") or "").strip() if isinstance(subject, dict) else ""


def _context_sources(context: dict[str, object] | None) -> tuple[str, ...]:
    subject = context.get("subject") if isinstance(context, dict) else None
    values = subject.get("source_ids") if isinstance(subject, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def _context_retrieved_at(context: dict[str, object] | None) -> datetime | None:
    subject = context.get("subject") if isinstance(context, dict) else None
    raw = str(subject.get("retrieved_at") or "").strip() if isinstance(subject, dict) else ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _result_retrieved_at(result: FactsProviderResult, *, now: datetime | None) -> datetime:
    for note in result.retrieval.notes:
        if not str(note).startswith("cached_at="):
            continue
        try:
            parsed = datetime.fromisoformat(str(note).split("=", 1)[1].replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return (now or datetime.now(UTC)).astimezone(UTC)


def _unique_bounded(values) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()[:256]
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output[:16]


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().strip(" .?!").split())
