from __future__ import annotations

import logging


logger = logging.getLogger("oracle-brain.trace")


def log_pending_event(
    event: str,
    *,
    pending_kind: str,
    source: str | None,
    session_id: str | None,
) -> None:
    logger.info(
        "%s pending_kind=%s source=%s session_id=%s",
        event,
        pending_kind,
        source or "-",
        session_id or "-",
    )


def log_fallback_event(
    *,
    source: str | None,
    session_id: str | None,
    from_target: str,
    to_target: str,
    detail: str,
) -> None:
    logger.info(
        "fallback_invoked source=%s session_id=%s from_target=%s to_target=%s detail=%s",
        source or "-",
        session_id or "-",
        from_target,
        to_target,
        detail,
    )


def log_session_event(
    event: str,
    *,
    source: str | None,
    session_id: str | None,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    logger.info(
        "%s source=%s session_id=%s reason=%s detail=%s",
        event,
        source or "-",
        session_id or "-",
        reason or "-",
        detail or "-",
    )


def log_followup_event(
    event: str,
    *,
    source: str | None,
    session_id: str | None,
    order: str,
    route_target: str | None = None,
    pending_domain: str | None = None,
    detail: str | None = None,
) -> None:
    logger.info(
        "%s source=%s session_id=%s order=%s route_target=%s pending_domain=%s detail=%s",
        event,
        source or "-",
        session_id or "-",
        order or "-",
        route_target or "-",
        pending_domain or "-",
        detail or "-",
    )
