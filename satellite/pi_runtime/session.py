from __future__ import annotations

import time
import uuid
from typing import Optional


def get_active_session_id(
    source: str,
    current_session_id: Optional[str],
    last_activity_at: Optional[float],
    timeout_seconds: float,
) -> tuple[str, float]:
    now = time.time()
    if current_session_id is None or last_activity_at is None or now - last_activity_at > timeout_seconds:
        return f"{source}-{uuid.uuid4().hex[:10]}", now
    return current_session_id, now
