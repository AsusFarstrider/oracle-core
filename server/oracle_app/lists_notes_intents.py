from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ListsNotesIntent:
    domain: str
    operation: str
    arguments: dict[str, str]


def parse_lists_notes_intent(text: str) -> ListsNotesIntent | None:
    value = " ".join(str(text or "").strip().split())
    lowered = value.casefold()
    patterns = (
        (r"^create (?:a )?list (?:called |named )?(.+)$", "lists", "create_list", ("title",)),
        (r"^(?:what(?:'s| is)|show me) (?:on|in) (?:my |the )?(.+?) list$", "lists", "read", ("list_id",)),
        (r"^add (.+?) to (?:my |the )?(.+?) list$", "lists", "add", ("title", "list_id")),
        (r"^(?:rename|change) (.+?) (?:on|in) (?:my |the )?(.+?) list to (.+)$", "lists", "edit", ("lookup_title", "list_id", "title")),
        (r"^(complete|reopen|delete) (?:everything|all items) (?:on|from) (?:my |the )?(.+?) list$", "lists", "bulk", ("verb", "list_id")),
        (r"^(complete|reopen|delete|remove) (.+?) (?:on|from) (?:my |the )?(.+?) list$", "lists", "item", ("verb", "title", "list_id")),
        (r"^create (?:a )?note (?:called |named )?(.+?)(?: saying (.+))?$", "notes", "create", ("title", "content")),
        (r"^(?:read|show) (?:my |the )?(.+?) note$", "notes", "read", ("title",)),
        (r"^search (?:my )?notes for (.+)$", "notes", "search", ("query",)),
        (r"^append (.+?) to (?:my |the )?(.+?) note$", "notes", "append", ("content", "title")),
        (r"^replace (?:my |the )?(.+?) note with (.+)$", "notes", "replace", ("title", "content")),
        (r"^rename (?:my |the )?(.+?) note to (.+)$", "notes", "rename", ("lookup_title", "title")),
        (r"^delete (?:my |the )?(.+?) note$", "notes", "delete", ("title",)),
    )
    for pattern, domain, operation, keys in patterns:
        match = re.match(pattern, lowered, flags=re.IGNORECASE)
        if not match:
            continue
        arguments = {key: value.strip() for key, value in zip(keys, match.groups()) if value and value.strip()}
        if operation == "bulk":
            operation = {"complete": "complete_all", "reopen": "reopen_all", "delete": "delete_all"}[arguments.pop("verb")]
        elif operation == "item":
            operation = arguments.pop("verb")
        return ListsNotesIntent(domain, operation, arguments)
    if any(token in lowered for token in (" list", "notes", " note")):
        return ListsNotesIntent("notes" if "note" in lowered else "lists", "unsupported", {})
    return None
