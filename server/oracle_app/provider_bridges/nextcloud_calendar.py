from __future__ import annotations

import base64
import copy
import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib import error, request
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo
from xml.etree import ElementTree

from oracle_app.calendar_models import CalendarEvent, CalendarReminderIntent
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings

try:
    from icalendar import Calendar as ICalendar
    import recurring_ical_events
except ImportError:  # Optional dependency: required only by this bridge.
    ICalendar = None
    recurring_ical_events = None


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

    def mutate_typed_event(
        self,
        *,
        uid: str,
        operation: str,
        changes: dict[str, Any],
        recurrence_scope: str,
        recurrence_id: str | None,
        settings: CalendarRuntimeSettings,
    ) -> dict[str, Any]:
        if not settings.write.enabled:
            raise CalendarBridgeConfigurationError("calendar_write_unconfigured", "calendar_write_unconfigured")
        write = settings.write
        object_url, etag, payload = self._find_calendar_object(
            uid,
            base_url=write.base_url or "", user=write.user or "",
            calendar_uri=write.calendar_uri or "", credential=write.credential or "",
            timeout_seconds=settings.timeout_seconds or 8,
        )
        if operation == "delete" and recurrence_scope == "series":
            self._delete_calendar_object(
                object_url, etag=etag, user=write.user or "", credential=write.credential or "",
                timeout_seconds=settings.timeout_seconds or 8,
            )
            self._verify_calendar_object_absent(
                object_url, user=write.user or "", credential=write.credential or "",
                timeout_seconds=settings.timeout_seconds or 8,
            )
            return {"uid": uid, "operation": operation, "recurrence_scope": recurrence_scope, "deleted": True}

        assert ICalendar is not None
        calendar = ICalendar.from_ical(payload)
        components = [item for item in calendar.walk("VEVENT") if str(item.decoded("UID", "")) == uid]
        master = next((item for item in components if item.get("RECURRENCE-ID") is None), None)
        if master is None:
            raise CalendarBridgeError("calendar_event_not_found", "Calendar event master was not found.")
        target = master
        recurrence_value: datetime | date | None = None
        if recurrence_scope == "occurrence":
            if not recurrence_id:
                raise CalendarBridgeError("calendar_recurrence_scope_required", "Occurrence mutation requires a stable occurrence identity.")
            recurrence_value = datetime.fromisoformat(recurrence_id)
            target = next((item for item in components if item.get("RECURRENCE-ID") is not None and self._same_instant(item.decoded("RECURRENCE-ID"), recurrence_value, settings.timezone)), None)
            if operation == "delete":
                master.add("EXDATE", recurrence_value)
            elif target is None:
                target = copy.deepcopy(master)
                for name in ("RRULE", "RDATE", "EXDATE"):
                    if name in target:
                        del target[name]
                target.add("RECURRENCE-ID", recurrence_value)
                calendar.add_component(target)
        if operation == "edit":
            if target is None:
                raise CalendarBridgeError("calendar_mutation_unsupported", "Provider occurrence edit could not be represented safely.")
            if "title" in changes:
                target["SUMMARY"] = str(changes["title"])
            if "start" in changes:
                if "DTSTART" in target:
                    del target["DTSTART"]
                start_value = changes["start"]
                target.add("DTSTART", start_value.date() if changes.get("all_day") else start_value)
            if "end" in changes:
                if "DTEND" in target:
                    del target["DTEND"]
                end_value = changes["end"]
                target.add("DTEND", end_value.date() if changes.get("all_day") else end_value)
        elif operation != "delete":
            raise CalendarBridgeError("calendar_mutation_unsupported", f"Unsupported Calendar mutation {operation!r}.")
        self._put_calendar_object(
            object_url, calendar.to_ical(), etag=etag, user=write.user or "",
            credential=write.credential or "", timeout_seconds=settings.timeout_seconds or 8,
        )
        self._verify_calendar_object(
            object_url, uid=uid, operation=operation, changes=changes,
            recurrence_scope=recurrence_scope, recurrence_id=recurrence_value,
            timezone_name=settings.timezone, user=write.user or "",
            credential=write.credential or "", timeout_seconds=settings.timeout_seconds or 8,
        )
        return {"uid": uid, "operation": operation, "recurrence_scope": recurrence_scope, "deleted": operation == "delete"}

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

    def _calendar_collection_url(self, base_url: str, user: str, calendar_uri: str) -> str:
        return self._build_calendar_object_url(base_url, user, calendar_uri, "").rstrip("/") + "/"

    def _find_calendar_object(self, uid: str, *, base_url: str, user: str, calendar_uri: str, credential: str, timeout_seconds: int) -> tuple[str, str, bytes]:
        collection_url = self._calendar_collection_url(base_url, user, calendar_uri)
        query = f"""<?xml version="1.0" encoding="utf-8" ?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:prop><d:getetag/><c:calendar-data/></d:prop>
 <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
  <c:prop-filter name="UID"><c:text-match collation="i;octet">{self._xml_escape(uid)}</c:text-match></c:prop-filter>
 </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>""".encode()
        req = request.Request(collection_url, data=query, method="REPORT", headers={
            "Authorization": self._build_basic_auth_header(user, credential),
            "Content-Type": "application/xml; charset=utf-8", "Depth": "1",
            "User-Agent": "oracle-brain-calendar-write/1.0",
        })
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                response_payload = response.read()
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            raise CalendarBridgeError("calendar_query_failed", str(exc)) from exc
        root = ElementTree.fromstring(response_payload)
        matches = []
        for response_node in root.findall("{DAV:}response"):
            href = response_node.findtext("{DAV:}href") or ""
            data = response_node.findtext(".//{urn:ietf:params:xml:ns:caldav}calendar-data")
            etag = response_node.findtext(".//{DAV:}getetag") or ""
            if data:
                matches.append((urljoin(collection_url, href), etag.strip(), data.encode()))
        if not matches:
            raise CalendarBridgeError("calendar_event_not_found", "Calendar event was not found in its configured source.")
        if len(matches) != 1:
            raise CalendarBridgeError("calendar_event_ambiguous", "Provider returned multiple objects for the selected event UID.")
        return matches[0]

    def _put_calendar_object(self, url: str, payload: bytes, *, etag: str, user: str, credential: str, timeout_seconds: int) -> None:
        headers = {"Authorization": self._build_basic_auth_header(user, credential), "Content-Type": "text/calendar; charset=utf-8", "User-Agent": "oracle-brain-calendar-write/1.0"}
        if etag:
            headers["If-Match"] = etag
        req = request.Request(url, data=payload, method="PUT", headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds):
                pass
        except error.HTTPError as exc:
            code = "calendar_write_conflict" if exc.code == 412 else "calendar_write_failed"
            raise CalendarBridgeError(code, f"Calendar update returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise CalendarBridgeError("calendar_write_failed", str(exc)) from exc

    def _delete_calendar_object(self, url: str, *, etag: str, user: str, credential: str, timeout_seconds: int) -> None:
        headers = {"Authorization": self._build_basic_auth_header(user, credential), "User-Agent": "oracle-brain-calendar-write/1.0"}
        if etag:
            headers["If-Match"] = etag
        req = request.Request(url, method="DELETE", headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_seconds):
                pass
        except error.HTTPError as exc:
            code = "calendar_write_conflict" if exc.code == 412 else "calendar_write_failed"
            raise CalendarBridgeError(code, f"Calendar delete returned HTTP {exc.code}.") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise CalendarBridgeError("calendar_write_failed", str(exc)) from exc

    def _read_calendar_object(self, url: str, *, user: str, credential: str, timeout_seconds: int) -> bytes:
        req = request.Request(url, method="GET", headers={
            "Authorization": self._build_basic_auth_header(user, credential),
            "Accept": "text/calendar", "User-Agent": "oracle-brain-calendar-write/1.0",
        })
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return response.read()
        except error.HTTPError as exc:
            raise CalendarBridgeError(
                "calendar_event_not_found" if exc.code == 404 else "calendar_write_verification_failed",
                f"Calendar verification returned HTTP {exc.code}.",
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise CalendarBridgeError("calendar_write_verification_failed", str(exc)) from exc

    def _verify_calendar_object_absent(self, url: str, *, user: str, credential: str, timeout_seconds: int) -> None:
        try:
            self._read_calendar_object(
                url, user=user, credential=credential, timeout_seconds=timeout_seconds,
            )
        except CalendarBridgeError as exc:
            if exc.error_code == "calendar_event_not_found":
                return
            raise
        raise CalendarBridgeError(
            "calendar_write_verification_failed",
            "Calendar provider still returned the deleted event.",
        )

    def _verify_calendar_object(
        self, url: str, *, uid: str, operation: str, changes: dict[str, Any],
        recurrence_scope: str, recurrence_id: date | datetime | None,
        timezone_name: str, user: str, credential: str, timeout_seconds: int,
    ) -> None:
        assert ICalendar is not None
        payload = self._read_calendar_object(
            url, user=user, credential=credential, timeout_seconds=timeout_seconds,
        )
        try:
            calendar = ICalendar.from_ical(payload)
        except (TypeError, ValueError) as exc:
            raise CalendarBridgeError(
                "calendar_write_verification_failed",
                "Calendar provider returned invalid data after mutation.",
            ) from exc
        components = [item for item in calendar.walk("VEVENT") if str(item.decoded("UID", "")) == uid]
        master = next((item for item in components if item.get("RECURRENCE-ID") is None), None)
        if master is None:
            raise CalendarBridgeError("calendar_write_verification_failed", "Calendar event identity changed after mutation.")
        if operation == "delete" and recurrence_scope == "occurrence":
            exdates = master.get("EXDATE", [])
            if not isinstance(exdates, list):
                exdates = [exdates]
            excluded = [value.dt for item in exdates for value in item.dts]
            if recurrence_id is None or not any(self._same_instant(value, recurrence_id, timezone_name) for value in excluded):
                raise CalendarBridgeError("calendar_write_verification_failed", "Calendar occurrence exclusion was not retained.")
            return
        target = master
        if recurrence_scope == "occurrence":
            target = next((
                item for item in components
                if item.get("RECURRENCE-ID") is not None
                and recurrence_id is not None
                and self._same_instant(item.decoded("RECURRENCE-ID"), recurrence_id, timezone_name)
            ), None)
        if target is None:
            raise CalendarBridgeError("calendar_write_verification_failed", "Calendar occurrence update was not retained.")
        if "title" in changes and str(target.decoded("SUMMARY", "")) != str(changes["title"]):
            raise CalendarBridgeError("calendar_write_verification_failed", "Calendar title update was not retained.")
        for field, property_name in (("start", "DTSTART"), ("end", "DTEND")):
            if field in changes and not self._same_instant(target.decoded(property_name), changes[field], timezone_name):
                raise CalendarBridgeError("calendar_write_verification_failed", f"Calendar {field} update was not retained.")

    def _same_instant(self, left: date | datetime, right: date | datetime, timezone_name: str) -> bool:
        timezone = ZoneInfo(timezone_name)
        return self._component_datetime(left, timezone)[0].astimezone(UTC) == self._component_datetime(right, timezone)[0].astimezone(UTC)

    def _xml_escape(self, value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

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
        except (TimeoutError, OSError) as exc:
            raise CalendarBridgeError("calendar_query_failed", str(exc)) from exc

    def _parse_events(self, payload: str, timezone_name: str) -> list[CalendarEvent]:
        if ICalendar is None or recurring_ical_events is None:
            raise CalendarBridgeConfigurationError(
                "calendar_bridge_dependency_missing",
                "The Nextcloud Calendar bridge requires its provider dependency profile.",
            )
        timezone = ZoneInfo(timezone_name)
        try:
            calendar = ICalendar.from_ical(payload)
        except (TypeError, ValueError) as exc:
            raise CalendarBridgeError("calendar_query_invalid", "Calendar provider returned invalid iCalendar data.") from exc

        reminder_intents = self._normalize_reminders(calendar, timezone)
        raw_components = list(calendar.walk("VEVENT"))
        recurring_uids = {
            str(component.decoded("UID") if component.get("UID") is not None else "").strip()
            for component in raw_components
            if component.get("RRULE") is not None or component.get("RECURRENCE-ID") is not None
        }
        components = [
            component for component in raw_components
            if str(component.decoded("UID") if component.get("UID") is not None else "").strip()
            not in recurring_uids
        ]
        if recurring_uids:
            now = datetime.now(UTC)
            try:
                components.extend(
                    occurrence for occurrence in recurring_ical_events.of(calendar).between(
                        now - timedelta(days=1), now + timedelta(days=366)
                    )
                    if str(occurrence.decoded("UID") if occurrence.get("UID") is not None else "").strip()
                    in recurring_uids
                )
            except (TypeError, ValueError) as exc:
                raise CalendarBridgeError(
                    "calendar_recurrence_normalization_failed",
                    "Calendar recurrence data could not be normalized safely.",
                ) from exc
        carriers: set[str] = set()
        events: list[CalendarEvent] = []
        for component in components:
            event = self._build_event(component, timezone)
            if event is None:
                continue
            intents: tuple[CalendarReminderIntent, ...] = ()
            if event.uid not in carriers:
                intents = tuple(reminder_intents.get(event.uid, ()))
                carriers.add(event.uid)
            recurring = event.uid in recurring_uids
            recurrence_id = event.recurrence_id
            if recurring and recurrence_id is None:
                recurrence_id = event.start.astimezone(UTC).isoformat()
            events.append(CalendarEvent(**{
                **event.__dict__, "recurring": recurring,
                "recurrence_id": recurrence_id, "reminder_intents": intents,
            }))
        return events

    def _build_event(self, component: Any, default_timezone: ZoneInfo) -> CalendarEvent | None:
        if component.get("DTSTART") is None or component.get("SUMMARY") is None:
            return None
        start, all_day = self._component_datetime(component.decoded("DTSTART"), default_timezone)
        if component.get("DTEND") is not None:
            end, _ = self._component_datetime(component.decoded("DTEND"), default_timezone)
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
        recurrence_value = component.decoded("RECURRENCE-ID") if component.get("RECURRENCE-ID") is not None else None
        recurrence_id = None
        if recurrence_value is not None:
            recurrence_id = self._component_datetime(recurrence_value, default_timezone)[0].astimezone(UTC).isoformat()
        return CalendarEvent(
            uid=str(component.decoded("UID") if component.get("UID") is not None else "").strip(),
            summary=str(component.decoded("SUMMARY")),
            start=start,
            end=end,
            all_day=all_day,
            location=str(component.decoded("LOCATION") if component.get("LOCATION") is not None else ""),
            recurring=component.get("RRULE") is not None,
            recurrence_id=recurrence_id,
        )

    def _normalize_reminders(self, calendar: Any, timezone: ZoneInfo) -> dict[str, list[CalendarReminderIntent]]:
        now = datetime.now(UTC)
        try:
            occurrences = recurring_ical_events.of(calendar).between(
                now - timedelta(days=1), now + timedelta(days=35)
            )
        except (TypeError, ValueError) as exc:
            raise CalendarBridgeError(
                "calendar_reminder_normalization_failed",
                "Calendar recurrence/reminder data could not be normalized safely.",
            ) from exc
        normalized: dict[str, list[CalendarReminderIntent]] = {}
        for occurrence in occurrences:
            uid = str(occurrence.decoded("UID") if occurrence.get("UID") is not None else "").strip()
            if not uid or occurrence.get("DTSTART") is None:
                continue
            start, _ = self._component_datetime(occurrence.decoded("DTSTART"), timezone)
            if occurrence.get("DTEND") is not None:
                end, _ = self._component_datetime(occurrence.decoded("DTEND"), timezone)
            else:
                end = start + timedelta(hours=1)
            occurrence_identity = occurrence.decoded("RECURRENCE-ID") if occurrence.get("RECURRENCE-ID") is not None else start
            occurrence_at = self._component_datetime(occurrence_identity, timezone)[0]
            occurrence_id = self._stable_id("calendar-occurrence", uid, occurrence_at.astimezone(UTC).isoformat())
            summary = str(occurrence.decoded("SUMMARY") if occurrence.get("SUMMARY") is not None else "Calendar event")
            for ordinal, alarm in enumerate(item for item in occurrence.subcomponents if item.name == "VALARM"):
                action = str(alarm.decoded("ACTION") if alarm.get("ACTION") is not None else "").upper()
                # Nextcloud DISPLAY is its ordinary in-app notification reminder.
                # EMAIL/AUDIO and every delivery mechanism remain provider-owned.
                if action != "DISPLAY" or alarm.get("TRIGGER") is None:
                    continue
                trigger = alarm.decoded("TRIGGER")
                if isinstance(trigger, timedelta):
                    related = str(alarm["TRIGGER"].params.get("RELATED", "START")).upper()
                    due_at = (end if related == "END" else start) + trigger
                elif isinstance(trigger, datetime):
                    due_at = trigger if trigger.tzinfo is not None else trigger.replace(tzinfo=timezone)
                else:
                    continue
                alarm_identity = hashlib.sha256(alarm.to_ical()).hexdigest()
                normalized.setdefault(uid, []).append(CalendarReminderIntent(
                    reminder_id=self._stable_id("calendar-reminder", uid, str(ordinal), alarm_identity),
                    event_occurrence_id=occurrence_id,
                    due_at=due_at.astimezone(timezone),
                    event_summary=summary,
                    event_start=start,
                ))
        for intents in normalized.values():
            intents.sort(key=lambda item: (item.due_at, item.event_occurrence_id, item.reminder_id))
        return normalized

    def _component_datetime(self, value: date | datetime, timezone: ZoneInfo) -> tuple[datetime, bool]:
        if isinstance(value, datetime):
            localized = value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
            return localized, False
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone), True

    def _stable_id(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}-{digest}"

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
