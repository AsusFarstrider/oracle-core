from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WikipediaSearchPlan:
    search_query: str
    subject: str
    intent: str
    accept_score: int


class WikipediaQuestionPolicy:
    """Facts-domain question shaping and result selection policy."""

    def build_search_plan(self, query: str) -> WikipediaSearchPlan:
        normalized_query = _normalize_words(query)
        authorship_match = re.match(r"^(?:who\s+wrote|who\s+is\s+the\s+author\s+of)\s+(.+?)\??$", normalized_query)
        if authorship_match:
            subject = authorship_match.group(1).strip(" '\"")
            if subject:
                return WikipediaSearchPlan(subject, subject, "authorship", 8)
        lifespan_match = re.match(
            r"^(?:how\s+long\s+(?:do|does|can|could)?\s*)(.+?)\s+(?:live|lives|living)(?:\s+for)?\??$",
            normalized_query,
        )
        if lifespan_match is None:
            lifespan_match = re.match(
                r"^(?:what\s+is\s+the\s+(?:life\s+span|lifespan|life\s+expectancy)\s+of)\s+(.+?)\??$",
                normalized_query,
            )
        if lifespan_match is None:
            lifespan_match = re.match(
                r"^(?:what\s+is\s+)?(.+?)\s+(?:life\s+span|lifespan|life\s+expectancy)\??$",
                normalized_query,
            )
        if lifespan_match:
            subject = _singularize_subject(lifespan_match.group(1).strip(" '\""))
            if subject:
                return WikipediaSearchPlan(f"{subject} lifespan", subject, "lifespan", 7)
        location_match = re.match(r"^(?:where\s+(?:is|are|was|were))\s+(.+?)(?:\s+located)?\??$", normalized_query)
        if location_match:
            subject = location_match.group(1).strip(" '\"")
            if subject:
                return WikipediaSearchPlan(subject, subject, "location", 7)
        date_match = re.match(
            r"^(?:when\s+(?:is|was|were|did))\s+(.+?)\s+(?:built|born|founded|created|published|made|opened|start|started|begin|began|release|released)\??$",
            normalized_query,
        )
        if date_match:
            subject = _strip_leading_article(date_match.group(1).strip(" '\""))
            if subject:
                return WikipediaSearchPlan(subject, subject, "date", 7)
        return WikipediaSearchPlan(query, normalized_query, "general", 4)

    def score_summary(self, *, query: str, summary: dict[str, Any], search_plan: WikipediaSearchPlan) -> int:
        title = _normalize_words(str(summary.get("title") or ""))
        extract = _normalize_words(str(summary.get("extract") or ""))
        subject = search_plan.subject
        score = 8 if subject and subject == title else 5 if subject and subject in title else 3 if subject and subject in extract else 0
        if search_plan.intent == "authorship":
            if "written by" in extract or "author" in extract or "wrote" in extract:
                score += 3
            if title.startswith("the man who wrote") or "authorship question" in extract or "fringe theory" in extract:
                score -= 8
        elif search_plan.intent == "lifespan":
            if "lifespan" in extract or "life span" in extract or "life expectancy" in extract:
                score += 4
            if "years" in extract or "oldest" in extract:
                score += 2
        elif search_plan.intent == "location":
            if "located" in extract or "situated" in extract:
                score += 2
        elif search_plan.intent == "date":
            if any(term in extract for term in ("built", "born", "founded", "created", "published", "opened", "released")):
                score += 2
            if re.search(r"\b\d{3,4}\b", extract):
                score += 2
        else:
            query_words = {word for word in _normalize_words(query).split() if len(word) > 3}
            score += len(query_words & set(title.split()))
        return score

    def needs_lifespan_extract(self, summary: dict[str, Any], search_plan: WikipediaSearchPlan) -> bool:
        return search_plan.intent == "lifespan" and not _has_lifespan_answer(str(summary.get("extract") or ""))

    def enrich_lifespan(self, summary: dict[str, Any], page_extract: str) -> dict[str, Any]:
        sentence = _select_lifespan_sentence(page_extract)
        if not sentence:
            return summary
        enriched = dict(summary)
        enriched["extract"] = sentence
        enriched["_oracle_retrieval_notes"] = ["selected lifespan sentence from wikipedia page extract"]
        return enriched


def _normalize_words(value: str) -> str:
    normalized = value.lower().strip()
    normalized = re.sub(r"[\u2018\u2019]", "'", normalized)
    normalized = re.sub(r"[\u201c\u201d]", '"', normalized)
    normalized = re.sub(r"[^a-z0-9' ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _singularize_subject(subject: str) -> str:
    normalized = re.sub(r"\s+", " ", subject).strip()
    if len(normalized) > 3 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if len(normalized) > 3 and normalized.endswith("s") and not normalized.endswith("ss"):
        return normalized[:-1]
    return normalized


def _strip_leading_article(subject: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", re.sub(r"\s+", " ", subject).strip())


def _has_lifespan_answer(text: str) -> bool:
    normalized = _normalize_words(text)
    return any(term in normalized for term in ("lifespan", "life span", "life expectancy")) or bool(
        re.search(r"\b\d+\s+(?:to\s+\d+\s+)?years\b", normalized)
    )


def _select_lifespan_sentence(text: str) -> str:
    sentences = _split_sentences(text)
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "years" in normalized and any(term in normalized for term in ("life expectancy", "lifespan", "life span")):
            return sentence.strip()
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "years" in normalized and not _is_historical_lifespan_decoy(normalized) and any(
            term in normalized for term in ("live", "lives", "age", "oldest")
        ):
            return sentence.strip()
    for sentence in sentences:
        normalized = _normalize_words(sentence)
        if "lifespan" in normalized or "life span" in normalized or "life expectancy" in normalized:
            return sentence.strip()
    return ""


def _is_historical_lifespan_decoy(normalized_sentence: str) -> bool:
    if "years ago" in normalized_sentence:
        return True
    return any(term in normalized_sentence for term in ("extinct", "extinction", "formerly lived"))


def _split_sentences(text: str) -> list[str]:
    normalized = " ".join(str(text or "").strip().split())
    if not normalized:
        return []
    protected = re.sub(
        r"\b([A-Z])\.",
        lambda match: f"{match.group(1)}__ORACLE_INITIAL__",
        normalized,
    )
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", protected)
    return [piece.replace("__ORACLE_INITIAL__", ".").strip() for piece in pieces if piece.strip()]
