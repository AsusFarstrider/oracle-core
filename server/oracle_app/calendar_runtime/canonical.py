from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from oracle_app.calendar import CalendarQuery, _find_matching_event, _list_events
from oracle_app.calendar_models import CalendarEvent
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings
from oracle_app.provider_bridges.nextcloud_calendar import CalendarBridgeError, NextcloudCalendarBridge
from oracle_app.read_cache import BoundedReadCache, CachedRead


class CanonicalCalendarExecution:
    """Calendar read/write behavior bound to one applied configuration snapshot."""

    def __init__(self, settings: CalendarRuntimeSettings) -> None:
        self.settings = settings
        self.bridge = NextcloudCalendarBridge()
        self._cache: BoundedReadCache[list[CalendarEvent]] = BoundedReadCache()

    def load_events(
        self,
        *,
        scope: str = "personal",
        require_config: bool = False,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> CachedRead[list[CalendarEvent]]:
        kind = "holidays" if scope == "holiday" else "events"
        feeds = self.settings.read.feeds_for_kind(kind) if self.settings.read.enabled else ()
        if not feeds:
            if require_config:
                raise RuntimeError(f"{'calendar' if scope == 'personal' else scope} feed is not configured")
            return CachedRead(value=[], freshness="fresh", age_seconds=0.0, stale_reason=None)

        def load() -> list[CalendarEvent]:
            events: list[CalendarEvent] = []
            for feed in feeds:
                auth_user = None
                auth_password = None
                if kind == "events" and self.settings.write.enabled:
                    auth_user = self.settings.write.user
                    auth_password = self.settings.write.credential
                try:
                    events.extend(
                        self.bridge.fetch_typed_events(
                            feed_url=feed.resolved_url,
                            timeout_seconds=self.settings.timeout_seconds or 8,
                            timezone_name=self.settings.timezone,
                            auth_user=auth_user,
                            auth_password=auth_password,
                        )
                    )
                except CalendarBridgeError as exc:
                    raise RuntimeError(exc.detail) from exc
            return events

        feed_identity = ",".join(f"{feed.id}:{feed.resolved_url}" for feed in feeds)
        return self._cache.read(
            f"calendar:{kind}:{self.settings.config_revision}:{feed_identity}",
            ttl_seconds=self.settings.read.fresh_seconds,
            stale_max_seconds=self.settings.read.stale_if_error_seconds,
            loader=load,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
        )

    def execute(self, query: CalendarQuery) -> dict[str, Any]:
        if not self.settings.enabled or not self.settings.read.enabled:
            raise HTTPException(status_code=500, detail="Calendar feed is not configured")
        cached = self.load_events(scope="personal", require_config=True)
        if query.intent == "find_event":
            result = _find_matching_event(query, cached.value, self.settings.timezone)
        else:
            result = _list_events(query, cached.value, self.settings.timezone)
        return {
            **result,
            "freshness": cached.freshness,
            "age_seconds": round(cached.age_seconds, 3),
            "stale_reason": cached.stale_reason,
            "stale_notice": (
                "I couldn't refresh the calendar, so these are the latest saved events."
                if cached.freshness == "stale"
                else None
            ),
        }

    def commit_event(self, event_draft: dict[str, Any]) -> dict[str, Any]:
        return self.bridge.commit_typed_event(event_draft, settings=self.settings)

    def health(self) -> dict[str, Any]:
        configured = self.settings.enabled and self.settings.read.enabled and bool(self.settings.read.feeds)
        if not configured:
            return {
                "status": "disabled",
                "service": "oracle-brain",
                "calendar_configured": False,
                "timezone": self.settings.timezone,
                "detail": "Calendar feed is not configured",
            }
        try:
            count = len(
                self.load_events(
                    scope="personal",
                    require_config=True,
                    force_refresh=True,
                    allow_stale=False,
                ).value
            )
            return {
                "status": "ok",
                "service": "oracle-brain",
                "calendar_configured": True,
                "timezone": self.settings.timezone,
                "detail": f"Calendar feed reachable with {count} events parsed",
            }
        except Exception as exc:
            return {
                "status": "failed",
                "service": "oracle-brain",
                "calendar_configured": True,
                "timezone": self.settings.timezone,
                "detail": str(exc),
            }
