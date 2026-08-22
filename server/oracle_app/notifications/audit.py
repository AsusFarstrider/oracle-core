from __future__ import annotations

import logging

from oracle_app.memory.runtime import safe_record_event


logger = logging.getLogger("oracle-brain.notifications")


def record_notification_event(
    *,
    notification_type: str,
    occurrence_id: str,
    status: str,
    caller: str,
    target_count: int = 0,
    target_source: str = "",
    correlation_id: str = "",
) -> None:
    is_home_assistant = caller == "home_assistant"
    safe_record_event(
        "ha_notification_received" if is_home_assistant else "notification_emitted",
        source_id="brain",
        provider="home_assistant" if is_home_assistant else "oracle",
        domain="notifications",
        status=status,
        correlation_id=correlation_id or None,
        payload={
            "notification_id": notification_type,
            "provider_event_id": occurrence_id,
            "target_count": target_count,
            "target_source": target_source or None,
            **({} if is_home_assistant else {"caller": caller}),
        },
    )
    logger.info(
        "notification_emitted caller=%s notification_type=%s occurrence_id=%s status=%s target_count=%s",
        caller,
        notification_type,
        occurrence_id,
        status,
        target_count,
    )
