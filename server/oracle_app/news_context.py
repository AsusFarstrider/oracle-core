from __future__ import annotations

from dataclasses import dataclass
import re

from .news import NewsQuery
from .session_state import get_informational_context, set_informational_context


_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
}


@dataclass(frozen=True)
class NewsContextResolution:
    query: NewsQuery | None = None
    result: dict[str, object] | None = None


def is_news_context_followup(text: str, *, source: str | None, session_id: str | None) -> bool:
    if get_informational_context(source, session_id, domain="news") is None:
        return False
    normalized = _normalize(text)
    return bool(
        _ordinal(normalized)
        or normalized in {
            "read that one",
            "read it",
            "tell me more",
            "tell me more about that",
            "when did that happen",
            "when was that",
            "where did that come from",
            "what was the source",
            "any updates on that",
            "is there an update on that",
            "check for updates on that",
        }
    )


def resolve_news_context(
    text: str,
    *,
    source: str | None,
    session_id: str | None,
) -> NewsContextResolution:
    normalized = _normalize(text)
    context = get_informational_context(source, session_id, domain="news")
    subject = context.get("subject") if isinstance(context, dict) else None
    if not isinstance(subject, dict):
        return NewsContextResolution()

    article_ids = tuple(
        str(item) for item in subject.get("article_ids") or [] if str(item).strip()
    )
    selected = str(subject.get("selected_article_id") or "").strip() or None
    source_ids = tuple(
        str(item) for item in subject.get("source_ids") or [] if str(item).strip()
    )
    ordinal = _ordinal(normalized)
    if ordinal is not None:
        if ordinal > len(article_ids):
            return NewsContextResolution(result={
                "action": "news_selection_out_of_range",
                "requested_ordinal": ordinal,
                "available_count": len(article_ids),
            })
        selected = article_ids[ordinal - 1]
        return NewsContextResolution(query=NewsQuery(
            source=None,
            original_text=normalized,
            action="article",
            article_id=selected,
            article_ids=article_ids,
            source_ids=source_ids,
        ))

    if normalized in {"read that one", "read it", "tell me more", "tell me more about that"}:
        if selected is None and len(article_ids) == 1:
            selected = article_ids[0]
        if selected is None:
            return NewsContextResolution(result={
                "action": "news_selection_required",
                "available_count": len(article_ids),
            })
        return NewsContextResolution(query=NewsQuery(
            source=None,
            original_text=normalized,
            action="article",
            article_id=selected,
            article_ids=article_ids,
            source_ids=source_ids,
        ))

    if normalized in {"where did that come from", "what was the source"}:
        return NewsContextResolution(result={
            "action": "news_provenance",
            "source_labels": list(subject.get("source_labels") or []),
        })

    if normalized in {"when did that happen", "when was that"}:
        return NewsContextResolution(result={
            "action": "news_publication_time",
            "published_at": str(subject.get("published_at") or "") or None,
            "subject_text": str(subject.get("subject_text") or ""),
        })

    if normalized in {
        "any updates on that", "is there an update on that", "check for updates on that",
    }:
        return NewsContextResolution(query=NewsQuery(
            source=None,
            original_text=normalized,
            action="updates",
            topic=str(subject.get("topic") or subject.get("subject_text") or "")[:128] or None,
            article_id=selected,
            article_ids=article_ids,
            source_ids=source_ids,
            force_refresh=True,
            after_published_at=str(subject.get("published_at") or "") or None,
        ))
    return NewsContextResolution()


def retain_news_context(
    result: dict[str, object],
    *,
    source: str | None,
    session_id: str | None,
) -> bool:
    headlines = [item for item in result.get("headlines") or [] if isinstance(item, dict)]
    selected_article = result.get("article") if isinstance(result.get("article"), dict) else None
    selected_headline = result.get("selected_headline") if isinstance(result.get("selected_headline"), dict) else None
    evidence = selected_headline or (headlines[0] if headlines else {})
    article_ids = [
        str(item) for item in result.get("article_ids") or [] if str(item).strip()
    ] or [str(item.get("article_id")) for item in headlines if str(item.get("article_id") or "").strip()]
    selected_id = str(result.get("selected_article_id") or "").strip()
    source_ids = [str(item) for item in result.get("source_ids") or [] if str(item).strip()]
    source_labels = [str(item) for item in result.get("source_labels") or [] if str(item).strip()]
    subject_text = str(evidence.get("title") or result.get("topic") or "news")[:512]
    return set_informational_context(
        source,
        session_id,
        domain="news",
        subject={
            "subject_id": f"news:{selected_id or (article_ids[0] if article_ids else 'headlines')}"[:256],
            "subject_text": subject_text,
            "topic": str(result.get("topic") or subject_text)[:512],
            "article_ids": article_ids[:16],
            "selected_article_id": selected_id[:512],
            "source_ids": source_ids[:16],
            "source_labels": source_labels[:16],
            "evidence_ids": article_ids[:16],
            "published_at": str(evidence.get("published_at") or "")[:64],
            "retrieved_at": str(
                (selected_article or {}).get("retrieved_at") or result.get("retrieved_at") or ""
            )[:64],
        },
    )


def _ordinal(text: str) -> int | None:
    for term, value in _ORDINALS.items():
        if re.search(rf"\b{re.escape(term)}\b", text) and re.search(r"\b(?:story|article|one)\b", text):
            return value
    match = re.search(r"\b(?:story|article)\s+(\d)\b", text)
    return int(match.group(1)) if match else None


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().strip(" .?!").split())
