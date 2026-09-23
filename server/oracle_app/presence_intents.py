from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


PresenceQueryKind = Literal["list", "person"]


@dataclass(frozen=True)
class PresenceQuery:
    kind: PresenceQueryKind
    desired_state: Literal["home", "away"]
    requested_user_name: str | None = None


_LIST_HOME_RE = re.compile(
    r"^(?:who(?: is|'s)(?: currently)?(?: at)? home|is (?:anyone|anybody)(?: currently)? home)$"
)
_LIST_AWAY_RE = re.compile(r"^who(?: is|'s)(?: currently)? (?:away|not home)$")
_PERSON_RE = re.compile(
    r"^is (?P<user>[a-z0-9][a-z0-9 .'-]{0,60}?)(?: currently)? (?P<state>home|at home|away|not home)$"
)


def parse_presence_query(text: str) -> PresenceQuery | None:
    normalized = " ".join(str(text or "").casefold().split())
    if _LIST_HOME_RE.fullmatch(normalized):
        return PresenceQuery(kind="list", desired_state="home")
    if _LIST_AWAY_RE.fullmatch(normalized):
        return PresenceQuery(kind="list", desired_state="away")
    match = _PERSON_RE.fullmatch(normalized)
    if match is None:
        return None
    requested = match.group("user").strip()
    if requested in {"anyone", "anybody"}:
        return PresenceQuery(kind="list", desired_state="home")
    return PresenceQuery(
        kind="person",
        desired_state="away" if match.group("state") in {"away", "not home"} else "home",
        requested_user_name=requested,
    )
