from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error, request
from urllib.parse import quote
from zoneinfo import ZoneInfo

from oracle_app.calendar_models import CalendarEvent
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings


class CalendarBridgeError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class CalendarBridgeConfigurationError(CalendarBridgeError):
    pass


class NextcloudCalendarBridge:
    provider_name = "nextcloud"

    def fetch_events(
        self,
        *,
        settings: dict[str, Any],
        scope: str,
        require_config: bool,
    ) -> list[CalendarEvent]:
        ics_url = self._calendar_feed_url(settings, scope=scope)
        if not ics_url:
            if require_config:
                label = "calendar" if scope == "personal" else scope
                raise CalendarBridgeConfigurationError("calendar_unconfigured", f"{label} feed is not configured")
            return []

        auth_user, auth_password = self._get_calendar_read_auth(settings, scope=scope)
        payload = self._fetch_ics_payload(
            ics_url,
            timeout_seconds=int(settings["timeout_seconds"]),
            auth_user=auth_user,
            auth_password=auth_password,
        )
        return self._parse_events(payload, str(settings["timezone"]))

    def fetch_typed_events(
        self,
        *,
        feed_url: str,
        timeout_seconds: int,
        timezone_name: str,
        auth_user: str | None = None,
        auth_password: str | None = None,
    ) -> list[CalendarEvent]:
        payload = self._fetch_ics_payload(
            feed_url,
            timeout_seconds=timeout_seconds,
            auth_user=auth_user,
            auth_password=auth_password,
        )
        return self._parse_events(payload, timezone_name)

    def commit_event(self, event_draft: dict[str, Any], *, settings: dict[str, Any]) -> dict[str, Any]:
        backend = self._get_calendar_backend_settings(settings)
        return self._commit_event(
            event_draft,
            base_url=backend["base_url"],
            user=backend["user"],
            app_password=backend["app_password"],
            calendar_uri=backend["calendar_uri"],
            timezone_name=str(settings.get("timezone") or "UTC"),
            timeout_seconds=int(settings.get("timeout_seconds") or 8),
            configured=bool(settings.get("calendar_write_configured")),
        )

    def commit_typed_event(
        self,
        event_draft: dict[str, Any],
        *,
        settings: CalendarRuntimeSettings,
    ) -> dict[str, Any]:
        write = settings.write
        return self._commit_event(
            event_draft,
            base_url=write.base_url or "",
            user=write.user or "",
            app_password=write.credential or "",
            calendar_uri=write.calendar_uri or "",
            timezone_name=settings.timezone,
            timeout_seconds=settings.timeout_seconds or 8,
            configured=write.enabled,
        )

    def _commit_event(
        self,
        event_draft: dict[str, Any],
        *,
        base_url: str,
        user: str,
        app_password: str,
        calendar_uri: str,
        timezone_name: str,
        timeout_seconds: int,
        configured: bool,
    ) -> dict[str, Any]:
        title = str(event_draft.get("title") or "").strip()
        date_value = str(event_draft.get("date") or "").strip()
        all_day = bool(event_draft.get("all_day"))
        start_time = str(event_draft.get("start_time") or "").strip()
        end_time = str(event_draft.get("end_time") or "").strip()
        if not title or not date_value:
            raise ValueError("calendar_write_incomplete")
        if not all_day and not (start_time and end_time):
            raise ValueError("calendar_write_incomplete")
        if not configured:
            raise CalendarBridgeConfigurationError("calendar_write_unconfigured", "calendar_write_unconfigured")
        uid = f"oracle-{uuid.uuid4().hex}@oracle"
        object_name = f"{uid}.ics"
        target_url = self._build_calendar_object_url(
            base_url,
            user,
            calendar_uri,
            object_name,
        )
        payload = self._build_ics_payload(
            event_draft={
                "title": title,
                "date": date_value,
                "all_day": all_day,
                "start_time": start_time,
                "end_time": end_time,
            },
            timezone_name=timezone_name,
            uid=uid,
        )
        auth_header = self._build_basic_auth_header(user, app_password)
        req = request.Request(
            target_url,
            data=payload.encode("utf-8"),
            method="PUT",
            headers={
                "Authorization": auth_header,
                "Content-Type": "text/calendar; charset=utf-8",
                "User-Agent": "oracle-brain-calendar-write/1.0",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                etag = str(response.headers.get("ETag") or "").strip() or None
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CalendarBridgeError("calendar_write_failed", detail or f"Calendar write returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise CalendarBridgeError("calendar_write_failed", str(exc.reason)) from exc

        return {
            "uid": uid,
            "object_name": object_name,
            "calendar_uri": calendar_uri,
            "url": target_url,
            "etag": etag,
            "event_draft": {
                "title": title,
                "date": date_value,
                "all_day": all_day,
                "start_time": start_time,
                "end_time": end_time,
            },
        }

    def _calendar_feed_url(self, settings: dict[str, Any], *, scope: str) -> str:
        if scope == "holiday":
            return str(settings.get("holiday_ics_url") or "").strip()
        return str(settings.get("ics_url") or "").strip()

    def _get_calendar_backend_settings(self, settings: dict[str, Any]) -> dict[str, str]:
        return {
            "base_url": str(settings.get("write_base_url") or "").strip(),
            "user": str(settings.get("write_user") or settings.get("read_user") or "").strip(),
            "app_password": str(settings.get("write_app_password") or settings.get("read_app_password") or "").strip(),
            "calendar_uri": str(settings.get("write_calendar_uri") or "").strip(),
        }

    def _get_calendar_read_auth(self, settings: dict[str, Any], *, scope: str) -> tuple[str | None, str | None]:
        if scope != "personal":
            return None, None
        backend = self._get_calendar_backend_settings(settings)
        if not (backend["user"] and backend["app_password"]):
            return None, None
        return backend["user"], backend["app_password"]

    def _build_basic_auth_header(self, user: str, password: str) -> str:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _build_calendar_object_url(self, base_url: str, user: str, calendar_uri: str, object_name: str) -> str:
        trimmed_base = base_url.rstrip("/")
        return (
            f"{trimmed_base}/remote.php/dav/calendars/"
            f"{quote(user, safe='')}/{quote(calendar_uri, safe='')}/{quote(object_name, safe='')}"
        )

    def _fetch_ics_payload(
        self,
        ics_url: str,
        *,
        timeout_seconds: int,
        auth_user: str | None = None,
        auth_password: str | None = None,
    ) -> str:
        headers = {"User-Agent": "oracle-brain-calendar/1.0"}
        if auth_user and auth_password:
            headers["Authorization"] = self._build_basic_auth_header(auth_user, auth_password)
        req = request.Request(ics_url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CalendarBridgeError("calendar_query_failed", detail or f"Calendar feed returned HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise CalendarBridgeError("calendar_query_failed", str(exc.reason)) from exc

    def _parse_events(self, payload: str, timezone_name: str) -> list[CalendarEvent]:
        lines = self._unfold_ics_lines(payload)
        timezone = ZoneInfo(timezone_name)
        events: list[CalendarEvent] = []
        current: dict[str, tuple[dict[str, str], str]] | None = None

        for line in lines:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT":
                if current is not None:
                    event = self._build_event(current, timezone)
                    if event is not None:
                        events.append(event)
                current = None
                continue
            if current is None or ":" not in line:
                continue
            name_and_params, value = line.split(":", 1)
            parts = name_and_params.split(";")
            name = parts[0].upper()
            params: dict[str, str] = {}
            for chunk in parts[1:]:
                if "=" not in chunk:
                    continue
                key, raw_value = chunk.split("=", 1)
                params[key.upper()] = raw_value
            current[name] = (params, value)

        return events

    def _build_event(self, raw: dict[str, tuple[dict[str, str], str]], default_timezone: ZoneInfo) -> CalendarEvent | None:
        start_raw = raw.get("DTSTART")
        end_raw = raw.get("DTEND")
        summary_raw = raw.get("SUMMARY")
        if start_raw is None or summary_raw is None:
            return None

        start, all_day = self._parse_ics_datetime(start_raw[1], start_raw[0], default_timezone)
        if end_raw is not None:
            end, _ = self._parse_ics_datetime(end_raw[1], end_raw[0], default_timezone)
        elif all_day:
            end = start + timedelta(days=1)
        else:
            end = start + timedelta(hours=1)

        uid = raw.get("UID", ({}, ""))[1].strip()
        summary = self._decode_ics_text(summary_raw[1])
        location = self._decode_ics_text(raw.get("LOCATION", ({}, ""))[1])
        return CalendarEvent(
            uid=uid,
            summary=summary,
            start=start,
            end=end,
            all_day=all_day,
            location=location,
        )

    def _parse_ics_datetime(self, value: str, params: dict[str, str], default_timezone: ZoneInfo) -> tuple[datetime, bool]:
        raw = value.strip()
        if params.get("VALUE", "").upper() == "DATE" or len(raw) == 8:
            parsed_date = datetime.strptime(raw[:8], "%Y%m%d").date()
            return datetime.combine(parsed_date, datetime.min.time(), tzinfo=default_timezone), True
        tzid = params.get("TZID", "").strip()
        if raw.endswith("Z"):
            parsed = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
            return parsed.astimezone(default_timezone), False
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S")
        timezone = self._resolve_ics_timezone(tzid, default_timezone)
        return parsed.replace(tzinfo=timezone).astimezone(default_timezone), False

    def _resolve_ics_timezone(self, tzid: str, default_timezone: ZoneInfo) -> ZoneInfo:
        if not tzid:
            return default_timezone
        aliases = {
            "eastern standard time": "America/New_York",
            "us/eastern": "America/New_York",
        }
        candidate = aliases.get(tzid.strip().lower(), tzid.strip())
        try:
            return ZoneInfo(candidate)
        except Exception:
            return default_timezone

    def _unfold_ics_lines(self, payload: str) -> list[str]:
        lines = payload.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        unfolded: list[str] = []
        for line in lines:
            if line.startswith((" ", "\t")) and unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line)
        return unfolded

    def _decode_ics_text(self, value: str) -> str:
        return (
            value.replace("\\n", " ")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
            .strip()
        )

    def _build_ics_payload(self, *, event_draft: dict[str, Any], timezone_name: str, uid: str) -> str:
        if bool(event_draft.get("all_day")):
            dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            start_date = datetime.strptime(str(event_draft["date"]), "%Y-%m-%d").date()
            end_date = start_date + timedelta(days=1)
            summary = self._escape_ics_text(str(event_draft["title"]))
            return (
                "BEGIN:VCALENDAR\r\n"
                "VERSION:2.0\r\n"
                "PRODID:-//Oracle//EN\r\n"
                "BEGIN:VEVENT\r\n"
                f"UID:{uid}\r\n"
                f"DTSTAMP:{dtstamp}\r\n"
                f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}\r\n"
                f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}\r\n"
                f"SUMMARY:{summary}\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR\r\n"
            )
        local_zone = ZoneInfo(timezone_name)
        start_local = datetime.fromisoformat(f"{event_draft['date']}T{event_draft['start_time']}:00").replace(tzinfo=local_zone)
        end_local = datetime.fromisoformat(f"{event_draft['date']}T{event_draft['end_time']}:00").replace(tzinfo=local_zone)
        if end_local <= start_local:
            end_local = end_local + timedelta(days=1)
        dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        start_utc = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        end_utc = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
        summary = self._escape_ics_text(str(event_draft["title"]))
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Oracle//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:{dtstamp}\r\n"
            f"DTSTART:{start_utc}\r\n"
            f"DTEND:{end_utc}\r\n"
            f"SUMMARY:{summary}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

    def _escape_ics_text(self, value: str) -> str:
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n")
        )


def get_calendar_bridge(settings: dict[str, Any]) -> NextcloudCalendarBridge:
    provider = str(settings.get("calendar_provider") or "nextcloud").strip().lower()
    if provider == "nextcloud":
        return NextcloudCalendarBridge()
    raise CalendarBridgeConfigurationError("calendar_provider_unsupported", f"Unsupported calendar provider: {provider}")
