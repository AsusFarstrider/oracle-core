from __future__ import annotations

import json
import logging
import re

from oracle_app.inference import InferenceClient, legacy_inference_client
from oracle_app.schemas import FactsProviderResult


logger = logging.getLogger("oracle-brain.facts")


FACTS_SUMMARIZER_SYSTEM_PROMPT = """You are summarizing a fact-provider payload for Oracle.

Answer the user's question using only the supplied provider answer and evidence.
Do not add facts from your own knowledge.
If the evidence is insufficient, say Oracle could not find a reliable answer.
Keep the response concise and natural for voice.
Answer the exact question asked. Do not return a generic article introduction
unless that introduction directly answers the question.
For specific-property questions such as lifespan, author, date, or location,
state that specific property from the supplied evidence.
For superlative questions like "largest" or "oldest", answer with the named
thing first and omit size, weight, or side details unless the user asks for
them.
Prefer one short sentence. For simple factual questions, keep the answer under
about 18 words. For definition or explanation questions, keep the answer under
about 28 words unless the user explicitly asks for detail.
Do not include extra measurements, history, or side facts unless needed to
answer the question.

Return only valid JSON with this exact schema:
{
  "summary": "concise answer using only the supplied evidence"
}

Do not include markdown, code fences, citations you were not given, or any text outside the JSON object.
"""


def summarize_facts_result(
    result: FactsProviderResult,
    *,
    inference: InferenceClient | None = None,
) -> str | None:
    if result.status not in {"answered", "evidence_only"}:
        logger.info("facts_summarizer_skipped status=%s reason=unsupported_status", result.status)
        return None
    prompt = build_facts_summary_prompt(result)
    if inference is None:
        inference = legacy_inference_client()
    response = inference.generate(prompt, system=FACTS_SUMMARIZER_SYSTEM_PROMPT, format="json")
    summary = parse_facts_summary(str(response.get("response") or ""))
    if summary is None:
        logger.info("facts_summarizer_rejected status=%s reason=invalid_json_or_empty", result.status)
        return None
    if not validate_facts_summary(result, summary):
        logger.info(
            "facts_summarizer_rejected status=%s reason=generic_or_wrong_intent intent=%s",
            result.status,
            _query_intent(result.query),
        )
        return None
    return summary


def build_facts_summary_prompt(result: FactsProviderResult) -> str:
    provider_answer = result.answer.text if result.answer is not None else ""
    evidence_lines = []
    for index, item in enumerate(result.evidence, start=1):
        provenance = item.provenance or {}
        url = str(provenance.get("url") or "").strip()
        suffix = f" url={url}" if url else ""
        evidence_lines.append(
            f"{index}. title={item.title}; source={item.source_name}; snippet={item.snippet}{suffix}"
        )
    evidence_text = "\n".join(evidence_lines) if evidence_lines else "(none)"
    return (
        f"User question:\n{result.query}\n\n"
        f"Provider answer:\n{provider_answer or '(none)'}\n\n"
        f"Evidence:\n{evidence_text}"
    )


def parse_facts_summary(raw_text: str) -> str | None:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    cleaned = _extract_json_object(cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    summary = str(parsed.get("summary") or "").strip()
    return summary or None


def validate_facts_summary(result: FactsProviderResult, summary: str) -> bool:
    normalized_summary = _normalize_text(summary)
    if not normalized_summary:
        return False
    if _is_reliable_no_answer(normalized_summary):
        return True
    query_intent = _query_intent(result.query)
    if not _is_voice_sized_summary(summary, query_intent):
        return False
    if query_intent == "lifespan":
        return _has_any(normalized_summary, ("lifespan", "life span", "life expectancy", "years", "unknown"))
    if query_intent == "authorship":
        return _has_any(normalized_summary, ("wrote", "written by", "author", "by "))
    if query_intent == "date":
        return bool(re.search(r"\b\d{3,4}\b", normalized_summary)) or _has_any(
            normalized_summary,
            (
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ),
        )
    if query_intent == "location":
        return _has_any(normalized_summary, (" in ", " near ", " located", " based", " part of ", " country", " city"))
    if query_intent == "superlative":
        return not _has_any(normalized_summary, ("weigh", "weight", "tonne", "tonnes", "meter", "meters", "metres", "estimated"))
    return True


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


def _query_intent(query: str) -> str:
    normalized = _normalize_text(query)
    if "life span" in normalized or "lifespan" in normalized or "life expectancy" in normalized:
        return "lifespan"
    if re.search(r"\bhow long\b.*\b(live|lives|living)\b", normalized):
        return "lifespan"
    if "who wrote" in normalized or "author of" in normalized:
        return "authorship"
    if re.search(r"\bwhen\b.*\b(built|born|founded|created|published|made|opened|started)\b", normalized):
        return "date"
    if normalized.startswith("where is ") or "where is the" in normalized or "located" in normalized:
        return "location"
    if _has_any(normalized, ("largest", "biggest", "smallest", "oldest", "longest", "fastest")):
        return "superlative"
    if normalized.startswith("what is ") or normalized.startswith("what are "):
        return "definition"
    if normalized.startswith("explain ") or normalized.startswith("how does ") or normalized.startswith("how do "):
        return "explanation"
    return "general"


def _is_reliable_no_answer(normalized_summary: str) -> bool:
    return "could not find" in normalized_summary or "couldn't find" in normalized_summary or "insufficient" in normalized_summary


def _has_any(value: str, terms: tuple[str, ...]) -> bool:
    padded = f" {value} "
    return any(term in padded for term in terms)


def _is_voice_sized_summary(summary: str, query_intent: str) -> bool:
    words = _summary_words(summary)
    sentence_count = _summary_sentence_count(summary)
    if query_intent == "explanation":
        return words <= 32 and sentence_count <= 2
    if query_intent == "definition":
        return words <= 20 and sentence_count <= 1
    if query_intent == "superlative":
        return words <= 14 and sentence_count <= 1
    return words <= 18 and sentence_count <= 1


def _summary_words(summary: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", summary))


def _summary_sentence_count(summary: str) -> int:
    pieces = [piece for piece in re.split(r"[.!?]+(?:\s+|$)", str(summary).strip()) if piece.strip()]
    return max(1, len(pieces)) if str(summary).strip() else 0


def _normalize_text(value: str) -> str:
    normalized = str(value or "").lower().strip()
    normalized = re.sub(r"[\u2018\u2019]", "'", normalized)
    normalized = re.sub(r"[\u201c\u201d]", '"', normalized)
    normalized = re.sub(r"[^a-z0-9' ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized
