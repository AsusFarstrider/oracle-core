from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from oracle_app.calendar import (
    CalendarQuery,
    _availability,
    _events_after_anchor,
    _find_location,
    _find_matching_event,
    _list_events,
    _next_event,
)
from oracle_app.calendar_models import CalendarEvent
from oracle_app.configuration.calendar_runtime_settings import CalendarRuntimeSettings
from oracle_app.provider_bridges.nextcloud_calendar import (
    CalendarBridgeConfigurationError,
    CalendarBridgeError,
    NextcloudCalendarBridge,
)
from oracle_app.read_cache import BoundedReadCache, CachedRead


class CalendarReadUnavailableError(RuntimeError):
    """Raised when the configured calendar provider cannot serve a read."""

    def __init__(self, detail: str, *, error_code: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


@dataclass(frozen=True)
class CalendarSourceStatus:
    source_id: str
    source_label: str
    status: str
    freshness: str
    age_seconds: float | None
    retrieved_at: str | None
    stale_reason: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class CalendarReadSnapshot:
    events: list[CalendarEvent]
    sources: tuple[CalendarSourceStatus, ...]
    freshness: str
    age_seconds: float
    complete: bool


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
        snapshot = self.load_calendar(
            scope=scope,
            require_config=require_config,
            force_refresh=force_refresh,
            allow_stale=allow_stale,
        )
        stale_sources = [source for source in snapshot.sources if source.freshness == "stale"]
        return CachedRead(
            value=snapshot.events,
            freshness=snapshot.freshness,
            age_seconds=snapshot.age_seconds,
            stale_reason=stale_sources[0].stale_reason if stale_sources else None,
            refresh_status="failed" if stale_sources else "succeeded",
            failure_kind="provider" if stale_sources else None,
        )

    def load_calendar(
        self,
        *,
        scope: str = "personal",
        person_id: str | None = None,
        calendar_id: str | None = None,
        require_config: bool = False,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> CalendarReadSnapshot:
        kind = "holidays" if scope == "holiday" else "events"
        feeds = self.settings.read.feeds_for_kind(kind) if self.settings.read.enabled else ()
        if kind == "events" and calendar_id:
            feeds = tuple(feed for feed in feeds if feed.id == calendar_id)
        if kind == "events" and person_id:
            feeds = tuple(
                feed for feed in feeds
                if not getattr(feed, "user_ids", ()) or person_id in getattr(feed, "user_ids", ())
            )
        if not feeds:
            if require_config:
                raise RuntimeError(f"{'calendar' if scope == 'personal' else scope} feed is not configured")
            return CalendarReadSnapshot([], (), "fresh", 0.0, True)

        events: list[CalendarEvent] = []
        statuses: list[CalendarSourceStatus] = []
        first_error: CalendarReadUnavailableError | None = None
        for feed in feeds:
            try:
                cached = self._cache.read(
                    f"calendar:{kind}:{self.settings.config_revision}:{feed.id}",
                    ttl_seconds=self.settings.read.fresh_seconds,
                    stale_max_seconds=self.settings.read.stale_if_error_seconds,
                    loader=lambda feed=feed: self._fetch_feed(feed),
                    force_refresh=force_refresh,
                    allow_stale=allow_stale,
                )
            except CalendarReadUnavailableError as exc:
                if kind == "holidays":
                    raise
                first_error = first_error or exc
                statuses.append(CalendarSourceStatus(
                    feed.id, getattr(feed, "label", feed.id), "unavailable", "unavailable", None, None,
                    error_code=exc.error_code,
                ))
                continue
            events.extend(
                replace(
                    event,
                    source_id=feed.id,
                    source_label=getattr(feed, "label", feed.id),
                    user_ids=getattr(feed, "user_ids", ()),
                )
                for event in cached.value
            )
            retrieved_at = (datetime.now(UTC) - timedelta(seconds=cached.age_seconds)).isoformat()
            statuses.append(CalendarSourceStatus(
                feed.id, getattr(feed, "label", feed.id), "available", cached.freshness,
                round(cached.age_seconds, 3), retrieved_at, cached.stale_reason,
            ))
        if not any(source.status == "available" for source in statuses) and first_error is not None:
            raise first_error
        age = max((source.age_seconds or 0.0 for source in statuses), default=0.0)
        freshness = "stale" if any(source.freshness == "stale" for source in statuses) else "fresh"
        return CalendarReadSnapshot(
            events=events,
            sources=tuple(statuses),
            freshness=freshness,
            age_seconds=age,
            complete=all(source.status == "available" for source in statuses),
        )

    def _fetch_feed(self, feed) -> list[CalendarEvent]:
        try:
            return self.bridge.fetch_typed_events(
                feed_url=feed.resolved_url,
                timeout_seconds=self.settings.timeout_seconds or 8,
                timezone_name=self.settings.timezone,
                auth_user=getattr(feed, "read_user", None),
                auth_password=getattr(feed, "read_credential", None),
            )
        except CalendarBridgeConfigurationError:
            raise
        except CalendarBridgeError as exc:
            raise CalendarReadUnavailableError(exc.detail, error_code=exc.error_code) from exc

    def execute(self, query: CalendarQuery) -> dict[str, Any]:
        if not self.settings.enabled:
            raise HTTPException(status_code=500, detail="Calendar feed is not configured")
        if query.intent == "unsupported_mutation":
            return {
                "action": "calendar_unsupported_mutation",
                "events": [],
                "query": query.original_text,
            }
        if not self.settings.read.enabled:
            raise HTTPException(status_code=500, detail="Calendar feed is not configured")
        snapshot = self.load_calendar(
            scope="personal",
            person_id=query.person_id,
            calendar_id=query.calendar_id,
            require_config=True,
            force_refresh=query.force_refresh,
        )
        if query.intent == "find_event":
            result = _find_matching_event(query, snapshot.events, self.settings.timezone)
        elif query.intent == "find_location":
            result = _find_location(query, snapshot.events, self.settings.timezone)
        elif query.intent == "next_event":
            result = _next_event(query, snapshot.events, self.settings.timezone)
        elif query.intent == "availability":
            result = _availability(query, snapshot.events, self.settings.timezone)
        elif query.intent == "after_event":
            result = _events_after_anchor(query, snapshot.events, self.settings.timezone)
        else:
            result = _list_events(query, snapshot.events, self.settings.timezone)
        source_payload = [source.__dict__ for source in snapshot.sources]
        return {
            **result,
            "person_id": query.person_id,
            "person_label": (
                self.settings.people[query.person_id].display_name
                if query.person_id in self.settings.people
                else None
            ),
            "source_availability": source_payload,
            "source_ids": [source.source_id for source in snapshot.sources],
            "requested_calendar_id": query.calendar_id,
            "complete": snapshot.complete,
            "freshness": snapshot.freshness,
            "age_seconds": round(snapshot.age_seconds, 3),
            "provider_retrieved_at": min(
                (source.retrieved_at for source in snapshot.sources if source.retrieved_at),
                default=None,
            ),
            "stale_notice": (
                "I couldn't refresh the calendar, so these are the latest saved events."
                if snapshot.freshness == "stale"
                else None
            ),
            "partial_notice": (
                "I could not reach every relevant calendar, so this answer may be incomplete."
                if not snapshot.complete
                else None
            ),
        }

    def commit_event(self, event_draft: dict[str, Any]) -> dict[str, Any]:
        committed = self.bridge.commit_typed_event(event_draft, settings=self.settings)
        self._cache.invalidate("calendar:events:")
        return committed

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
            snapshot = self.load_calendar(
                scope="personal",
                require_config=True,
                force_refresh=False,
                allow_stale=False,
            )
            return {
                "status": "ok" if snapshot.complete else "degraded",
                "service": "oracle-brain",
                "calendar_configured": True,
                "timezone": self.settings.timezone,
                "detail": (
                    f"Calendar sources available with {len(snapshot.events)} events parsed"
                    if snapshot.complete
                    else f"Calendar is partially available with {len(snapshot.events)} events parsed"
                ),
                "source_availability": [source.__dict__ for source in snapshot.sources],
            }
        except Exception as exc:
            return {
                "status": "failed",
                "service": "oracle-brain",
                "calendar_configured": True,
                "timezone": self.settings.timezone,
                "detail": str(exc),
            }
