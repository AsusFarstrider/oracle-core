from __future__ import annotations

from typing import Any

from .interaction_synchronization import synchronized_interaction


MAX_TURNS = 6
FOLLOW_UP_PREFIXES = (
    "what about",
    "how about",
    "and ",
    "also ",
    "what else",
    "tell me more",
    "more about",
    "why is",
    "why does",
    "how does",
    "can you explain",
    "explain that",
)
FOLLOW_UP_EXACT = {
    "and",
    "also",
    "why",
    "how",
    "what else",
    "tell me more",
    "go on",
}


_CONVERSATIONS: dict[str, dict[str, Any]] = {}


def _conversation_key(source: str | None, session_id: str | None) -> str | None:
    source_key = str(source or "").strip()
    session_key = str(session_id or "").strip()
    if not source_key or not session_key:
        return None
    return f"{source_key}:{session_key}"


def _copy_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    copied = dict(conversation)
    copied["history"] = [dict(item) for item in conversation.get("history", [])]
    return copied


@synchronized_interaction
def get_conversation(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = _conversation_key(source, session_id)
    if not key:
        return None
    conversation = _CONVERSATIONS.get(key)
    if conversation is None:
        return None
    return _copy_conversation(conversation)


@synchronized_interaction
def get_or_create_conversation(source: str | None, session_id: str | None) -> dict[str, Any] | None:
    key = _conversation_key(source, session_id)
    if not key:
        return None
    conversation = _CONVERSATIONS.setdefault(
        key,
        {
            "history": [],
            "home_assistant_conversation_id": None,
            "last_target": None,
            "last_action": None,
        },
    )
    return _copy_conversation(conversation)


@synchronized_interaction
def clear_conversation(source: str | None, session_id: str | None) -> bool:
    key = _conversation_key(source, session_id)
    if not key:
        return False
    return _CONVERSATIONS.pop(key, None) is not None


@synchronized_interaction
def clear_all_conversations() -> None:
    _CONVERSATIONS.clear()


@synchronized_interaction
def append_turn(source: str | None, session_id: str | None, role: str, text: str) -> None:
    key = _conversation_key(source, session_id)
    if not key:
        return
    conversation = _CONVERSATIONS.setdefault(
        key,
        {
            "history": [],
            "home_assistant_conversation_id": None,
            "last_target": None,
            "last_action": None,
        },
    )
    if conversation is None:
        return
    cleaned = str(text).strip()
    if not cleaned:
        return
    history = list(conversation.get("history", []))
    history.append({"role": role, "text": cleaned})
    conversation["history"] = history[-MAX_TURNS:]


@synchronized_interaction
def set_dispatch_context(
    source: str | None,
    session_id: str | None,
    *,
    target: str | None = None,
    action: str | None = None,
) -> None:
    key = _conversation_key(source, session_id)
    if not key:
        return
    conversation = _CONVERSATIONS.setdefault(
        key,
        {
            "history": [],
            "home_assistant_conversation_id": None,
            "last_target": None,
            "last_action": None,
        },
    )
    if conversation is None:
        return
    if target is not None:
        conversation["last_target"] = target
    if action is not None:
        conversation["last_action"] = action


@synchronized_interaction
def get_home_assistant_conversation_id(source: str | None, session_id: str | None) -> str | None:
    conversation = get_conversation(source, session_id)
    if conversation is None:
        return None
    value = conversation.get("home_assistant_conversation_id")
    return str(value) if value else None


@synchronized_interaction
def set_home_assistant_conversation_id(source: str | None, session_id: str | None, conversation_id: str | None) -> None:
    key = _conversation_key(source, session_id)
    if not key:
        return
    conversation = _CONVERSATIONS.setdefault(
        key,
        {
            "history": [],
            "home_assistant_conversation_id": None,
            "last_target": None,
            "last_action": None,
        },
    )
    if conversation is None:
        return
    conversation["home_assistant_conversation_id"] = conversation_id


def should_include_ollama_history(prompt: str) -> bool:
    current = str(prompt).strip().lower()
    if not current:
        return False
    if current in FOLLOW_UP_EXACT:
        return True
    if any(current.startswith(prefix) for prefix in FOLLOW_UP_PREFIXES):
        return True
    referential_tokens = {"it", "that", "they", "them", "those", "these", "he", "she", "him", "her"}
    words = current.replace("?", " ").replace(",", " ").split()
    return any(word in referential_tokens for word in words[:4])


@synchronized_interaction
def build_ollama_prompt(source: str | None, session_id: str | None, prompt: str) -> str:
    conversation = get_conversation(source, session_id)
    current = str(prompt).strip()
    if conversation is None:
        return current
    if not should_include_ollama_history(current):
        return current

    history = list(conversation.get("history", []))
    if not history:
        return current

    lines = [
        "Recent conversation:",
    ]
    for item in history[-MAX_TURNS:]:
        role = "User" if item.get("role") == "user" else "Oracle"
        text = str(item.get("text", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    lines.append(f"User: {current}")
    lines.append("Use the conversation context when interpreting the latest user message.")
    return "\n".join(lines)
