from __future__ import annotations

import re


WAKE_WORD_PREFIX_PATTERN = re.compile(
    r"^(?:the\s+)?(?:(?:hey|okay|ok)\s+)?"
    r"(?:(?:oracle)|(?:or go)|(?:article)|(?:circle))"
    r"(?:[\s,.:;!\?-]+|$)",
)
WAKE_WORD_FILLER_PATTERN = re.compile(r"^(?:(?:please|go|hey|okay|ok)\s+)+")
BRACKETED_NONVERBAL_TRANSCRIPT_PATTERN = re.compile(
    r"^\s*(?:[\(\[]\s*[a-z0-9][a-z0-9' ,.-]{0,80}\s*[\)\]]\s*)+$"
)
WAKE_ONLY_JUNK_SEPARATOR_PATTERN = re.compile(r"\s*[.;:!?]+\s*")
WAKE_ONLY_RESIDUE_UNIT_PATTERN = re.compile(
    r"(?:(?:hey|okay|ok|very)\s+)?(?:oracle|article|circle|or\s+go|they\s+oracle|pay\s+oracle|a\s+oracle)"
)
INLINE_COMMAND_PUNCTUATION_PATTERN = re.compile(r"(?<=[a-z])[\.,;:!?]+(?=[a-z])")


def strip_wake_word_prefix(text: str) -> str:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return ""

    cleaned = normalized
    while True:
        next_cleaned = WAKE_WORD_PREFIX_PATTERN.sub("", cleaned, count=1).lstrip()
        if next_cleaned == cleaned:
            break
        cleaned = WAKE_WORD_FILLER_PATTERN.sub("", next_cleaned, count=1).lstrip()

    return cleaned.strip(" ,.:;!?-")


def _is_repeated_wake_only_residue(text: str) -> bool:
    segments = [segment.strip() for segment in WAKE_ONLY_JUNK_SEPARATOR_PATTERN.split(text) if segment.strip()]
    if not segments:
        return False

    for segment in segments:
        remainder = " ".join(re.sub(r"[,]+", " ", segment).split())
        matched_any = False
        while remainder:
            match = WAKE_ONLY_RESIDUE_UNIT_PATTERN.match(remainder)
            if match is None:
                return False
            matched_any = True
            remainder = remainder[match.end() :].lstrip()
        if not matched_any:
            return False

    return True


def _is_wake_only_junk_transcript(text: str) -> bool:
    return bool(BRACKETED_NONVERBAL_TRANSCRIPT_PATTERN.fullmatch(text)) or _is_repeated_wake_only_residue(text)


def normalize_text(text: str) -> str:
    normalized = strip_wake_word_prefix(text)
    normalized = INLINE_COMMAND_PUNCTUATION_PATTERN.sub(" ", normalized)
    normalized = " ".join(normalized.split())
    if _is_wake_only_junk_transcript(normalized):
        return ""
    return normalized
