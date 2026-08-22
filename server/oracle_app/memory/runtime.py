from __future__ import annotations

import logging
from typing import Any

from .correlation import get_correlation_id
from .events import record_event
from .sources import upsert_source


logger = logging.getLogger("oracle-brain.memory")


def safe_record_event(event_type: str, **kwargs: Any) -> bool:
    try:
        if not kwargs.get("correlation_id"):
            kwargs["correlation_id"] = get_correlation_id()
        if not kwargs.get("correlation_id"):
            payload = dict(kwargs.get("payload") or {})
            payload.setdefault("missing_correlation_id", True)
            kwargs["payload"] = payload
        if kwargs.get("source_id") == "brain":
            upsert_source(
                source_id="brain",
                source_type="brain",
                display_name="Oracle Brain",
                db_path=kwargs.get("db_path"),
            )
        record_event(event_type, **kwargs)
    except Exception as exc:
        logger.warning("memory_event_write_failed event_type=%s detail=%s", event_type, exc)
        return False
    return True
