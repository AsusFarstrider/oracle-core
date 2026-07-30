from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote


def get_calendar_backend_settings(settings: dict[str, Any]) -> dict[str, str]:
    return {
        "base_url": str(settings.get("write_base_url") or "").strip(),
        "user": str(settings.get("write_user") or settings.get("read_user") or "").strip(),
        "app_password": str(settings.get("write_app_password") or settings.get("read_app_password") or "").strip(),
        "calendar_uri": str(settings.get("write_calendar_uri") or "").strip(),
    }


def build_basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_calendar_object_url(base_url: str, user: str, calendar_uri: str, object_name: str) -> str:
    trimmed_base = base_url.rstrip("/")
    return (
        f"{trimmed_base}/remote.php/dav/calendars/"
        f"{quote(user, safe='')}/{quote(calendar_uri, safe='')}/{quote(object_name, safe='')}"
    )


def get_calendar_read_auth(settings: dict[str, Any], *, scope: str) -> tuple[str | None, str | None]:
    if scope != "personal":
        return None, None
    backend = get_calendar_backend_settings(settings)
    if not (backend["user"] and backend["app_password"]):
        return None, None
    return backend["user"], backend["app_password"]
