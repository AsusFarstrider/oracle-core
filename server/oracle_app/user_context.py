from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .config import get_source_registry, get_user_registry
from .configuration.household_runtime_settings import HouseholdRuntimeSettings
from .session_state import get_active_context, get_active_user_id


_SWITCH_USER_RE = re.compile(r"^switch to (?P<user>.+?)$")
_DO_THAT_AS_USER_RE = re.compile(r"^(?:do that|do this|do it|run that|run this|play that) as (?P<user>.+?)$")
_AS_USER_SUFFIX_RE = re.compile(r"^(?P<base>.+?) as (?P<user>[a-z0-9][a-z0-9 .'\-]{0,40})$")
_POSSESSIVE_AUDIOBOOK_RE = re.compile(
    r"^(?P<prefix>(?:play|start|queue up|cue up|put on|listen to|read|start reading|resume|continue|pause|stop)\s+)"
    r"(?P<user>[a-z0-9][a-z0-9 .'\-]{0,40})"
    r"(?:'s|s')\s+"
    r"(?P<object>audiobook|book)"
    r"(?P<rest>.*)$"
)


@dataclass(frozen=True)
class UserDirective:
    directive_type: str | None = None
    requested_user_name: str | None = None
    rewritten_text: str | None = None
    error: str | None = None


def analyze_user_directive(
    text: str,
    *,
    source: str | None = None,
    session_id: str | None = None,
) -> UserDirective:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return UserDirective()

    switch_match = _SWITCH_USER_RE.fullmatch(normalized)
    if switch_match is not None:
        return UserDirective(
            directive_type="switch_user",
            requested_user_name=switch_match.group("user").strip(),
        )

    replay_match = _DO_THAT_AS_USER_RE.fullmatch(normalized)
    if replay_match is not None:
        active_context = get_active_context(source, session_id)
        context_text = str((active_context or {}).get("context_text") or "").strip().lower()
        if not context_text:
            return UserDirective(
                directive_type="execute_as",
                requested_user_name=replay_match.group("user").strip(),
                error="no_active_context_for_execute_as",
            )
        return UserDirective(
            directive_type="execute_as",
            requested_user_name=replay_match.group("user").strip(),
            rewritten_text=context_text,
        )

    possessive_match = _POSSESSIVE_AUDIOBOOK_RE.fullmatch(normalized)
    if possessive_match is not None:
        rewritten = (
            f"{possessive_match.group('prefix')}my {possessive_match.group('object')}"
            f"{possessive_match.group('rest')}"
        ).strip()
        return UserDirective(
            directive_type="explicit_request_user",
            requested_user_name=possessive_match.group("user").strip(),
            rewritten_text=" ".join(rewritten.split()),
        )

    suffix_match = _AS_USER_SUFFIX_RE.fullmatch(normalized)
    if suffix_match is not None:
        base = suffix_match.group("base").strip()
        if base:
            return UserDirective(
                directive_type="execute_as",
                requested_user_name=suffix_match.group("user").strip(),
                rewritten_text=base,
            )

    return UserDirective()


def extract_switch_user_name(text: str) -> str | None:
    normalized = " ".join(str(text).strip().lower().split())
    match = _SWITCH_USER_RE.fullmatch(normalized)
    if match is None:
        return None
    return match.group("user").strip() or None


def get_default_user_id(
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    if household_settings is not None:
        user = household_settings.default_user()
        return user.id if user is not None else None
    registry = get_user_registry()
    for user_id, entry in registry.items():
        if bool(entry.get("is_default")):
            return user_id
    return next(iter(registry.keys()), None)


def get_source_default_user_id(source: str | None) -> str | None:
    normalized_source = str(source or "").strip()
    if not normalized_source:
        return None
    entry = get_source_registry().get(normalized_source)
    if not isinstance(entry, dict):
        return None
    ui = entry.get("ui")
    if not isinstance(ui, dict):
        return None
    candidates = []
    for key in ("audiobook", "audio"):
        scoped = ui.get(key)
        if isinstance(scoped, dict):
            candidates.append(scoped)
    candidates.append(ui)
    registry = get_user_registry()
    for candidate in candidates:
        user_id = str(candidate.get("default_user_id") or candidate.get("default_user") or "").strip().lower()
        if user_id and user_id in registry:
            return user_id
    return None


def resolve_user_name(
    raw_name: str | None,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> str | None:
    normalized_name = _normalize_user_phrase(raw_name)
    if not normalized_name:
        return None

    if household_settings is not None:
        return household_settings.resolve_user_id(normalized_name)

    registry = get_user_registry()
    for user_id, entry in registry.items():
        candidates = {user_id}
        display_name = _normalize_user_phrase(entry.get("display_name"))
        if display_name:
            candidates.add(display_name)
        for alias in entry.get("aliases") or []:
            normalized_alias = _normalize_user_phrase(alias)
            if normalized_alias:
                candidates.add(normalized_alias)
        if normalized_name in candidates:
            return user_id
    return None


def resolve_effective_user(
    *,
    source: str | None = None,
    session_id: str | None = None,
    requested_user_name: str | None = None,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> dict[str, Any]:
    requested = _normalize_user_phrase(requested_user_name)
    if requested:
        resolved = resolve_user_name(
            requested,
            household_settings=household_settings,
        )
        if resolved is None:
            return {
                "ok": False,
                "error": "unknown_user",
                "requested_user_name": requested_user_name,
            }
        return {
            "ok": True,
            "user_id": resolved,
            "resolution_source": "explicit_user",
            "requested_user_name": requested_user_name,
        }

    session_user_id = get_active_user_id(source, session_id)
    if session_user_id and (
        household_settings is None
        or household_settings.user(session_user_id) is not None
    ):
        return {
            "ok": True,
            "user_id": session_user_id,
            "resolution_source": "session_user",
            "requested_user_name": None,
        }

    associated_user_id = (
        household_settings.configured_associated_user_id(source)
        if household_settings is not None
        else get_source_default_user_id(source)
    )
    if associated_user_id:
        return {
            "ok": True,
            "user_id": associated_user_id,
            "resolution_source": (
                "source_association"
                if household_settings is not None
                else "source_default_user"
            ),
            "requested_user_name": None,
        }

    default_user_id = get_default_user_id(
        household_settings=household_settings,
    )
    if default_user_id:
        return {
            "ok": True,
            "user_id": default_user_id,
            "resolution_source": (
                "household_default"
                if household_settings is not None
                else "default_user"
            ),
            "requested_user_name": None,
        }

    return {
        "ok": False,
        "error": "no_default_user",
        "requested_user_name": requested_user_name,
    }


def get_user_entry(
    user_id: str | None,
    *,
    household_settings: HouseholdRuntimeSettings | None = None,
) -> dict[str, Any] | None:
    user_key = str(user_id or "").strip().lower()
    if not user_key:
        return None
    if household_settings is not None:
        entry = household_settings.user(user_key)
        if entry is None:
            return None
        return entry.model_dump(mode="python")
    entry = get_user_registry().get(user_key)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def _normalize_user_phrase(value: Any) -> str:
    normalized = " ".join(str(value or "").strip().lower().split())
    if normalized.endswith("'s"):
        normalized = normalized[:-2].strip()
    return normalized
