# Calendar Capability

The `calendar` domain is a route target with execution centered in `server/oracle_app/handlers/calendar.py`.

Calendar is split into two domain surfaces:

- calendar read
- calendar write

The contract-level guarantees for write safety and state ownership live in
[calendar-write.md](../../contracts/calendar-write.md).

## Current Domain State

The current implemented calendar domain supports both read and write.

Calendar reads are fresh-cached in process for 60 seconds. If a refresh fails, the last successful
read may be returned for at most 10 minutes with explicit `freshness`, `age_seconds`,
`stale_reason`, and plain-language stale wording. Errors and malformed provider responses are not
cached. Calendar health forces a provider read without stale fallback, and a successful calendar
write invalidates the read cache immediately.

Read and write remain separate surfaces even though they now point at the same backend calendar.

In canonical V2 mode, one immutable calendar execution binds the selected
Nextcloud provider, resolved feed URLs, household timezone, read freshness
policy, and confirmed-write credential tuple. Route parsing, pending-calendar
collision handling, voice and fixed UI reads, health, and confirmed voice/UI
writes consume that dependency directly. Multiple feeds of the same typed kind
are aggregated within that kind; holiday feeds remain separate from ordinary
event replies. The canonical path never rebuilds legacy calendar settings or
falls back to V1 authority when the role or a read/write capability is disabled.

The current split is:

- `calendar_ics_url`: ordinary calendar read feed from the selected Nextcloud
  calendar export
- `holiday_calendar_ics_url`: separate holiday feed used only outside the normal calendar domain, such as holiday-aware date calculations
- `calendar_write_base_url`, `calendar_write_user`, `calendar_write_app_password`, `calendar_write_calendar_uri`: Nextcloud-backed write settings used for event creation and for authenticated ordinary-calendar reads when needed

Holiday events must not be merged into ordinary calendar replies unless they are also present in the personal calendar feed.

## Calendar Read

### Responsibilities

The read surface is responsible for:

- query parsing
- `.ics` fetch and parse
- event normalization
- query execution
- spoken reply shaping

The read path remains brain-owned and deterministic.

### Module Split

The current read implementation is split across:

- `server/oracle_app/calendar.py`
- `server/oracle_app/provider_bridges/nextcloud_calendar.py`
- `server/oracle_app/handlers/calendar.py`

`server/oracle_app/calendar.py` contains core calendar query support.

`server/oracle_app/provider_bridges/nextcloud_calendar.py` contains the active calendar provider bridge and owns Nextcloud-specific fetch, auth, parse, and write mechanics.

`server/oracle_app/handlers/calendar.py` contains the dispatch handler and execution entrypoint for the route target.

### Read Behavior Direction

When the user asks what is on the calendar, Oracle should treat that as a request to understand the day, not as a request for a short preview.

Read replies should:

- reflect the true shape of the requested day
- avoid silently concealing schedule density
- remain concise through phrasing, not omission

For day-summary replies, the intended shape is:

- state the total number of relevant events
- enumerate all relevant events in chronological order
- keep each event phrasing compact

### Today Default Relevance

For `today`, the default read path may omit events that have already fully ended.

By default, keep:

- events currently in progress
- events later today

By default, omit:

- events that fully ended earlier today

If the user explicitly asks for the full day, everything today, or similar, the read path should include all events for the day.

### Ordering And Spoken Representation

Calendar read replies should present events in chronological order by start time.

If multiple events share the same start time, preserve stable ordering from the underlying data source rather than reordering arbitrarily between calls.

Within a single spoken reply, each event should follow the same minimal structure:

- time
- short title

For dense days:

- long titles may be shortened for clarity
- location, notes, and other extra fields should not be injected unless explicitly requested

## Calendar Write

Calendar write is implemented as a first-class write surface, not as an extension of the read path.

The write path should remain brain-owned, session-scoped, and
confirmation-driven in accordance with
[calendar-write.md](../../contracts/calendar-write.md).

### Write Shape

The intended write sequence is:

1. parse event intent
2. collect or clarify missing required fields
3. build `event_draft`
4. produce `event_confirmation`
5. commit through `event_commit` only after explicit confirmation

This sequence should stay explicit in both implementation and state handling.

The current implementation follows that sequence and commits to the selected
Nextcloud calendar only after explicit confirmation.

### First-Pass Write Scope

The initial write surface should support only:

- title
- day/date
- all-day
- for timed events: start time
- for timed events: either end time or duration

The following are deferred:

- recurring events
- attendees
- reminders
- descriptions or notes
- locations
- edits
- deletes
- multi-calendar selection

### Clarification Behavior

The write path should not guess when required fields are unclear.

If Oracle cannot confidently resolve:

- title
- day/date
- whether the event is all-day or timed
- for timed events: start time
- for timed events: end time or duration

it should ask a narrow clarification question rather than drafting or committing a write candidate.

Clarification should behave more like structured form completion than open-ended chat.

### Event Draft Shape

`event_draft` is the candidate event Oracle intends to confirm and later commit.

The planned first-pass draft fields are:

- `title`
- `date` as `YYYY-MM-DD`
- `all_day` as boolean
- for timed events: `start_time` as `HH:MM`
- for timed events: `end_time` as `HH:MM`

Additional internal fields may exist for debugging or bookkeeping, but confirmation and commit should be driven from normalized concrete values rather than unresolved natural language.

For timed UI drafts, duration may be accepted as input, but it should be resolved into normalized timed values before confirmation and commit.

Relative dates and spoken times should be resolved before `event_draft` is considered complete.

Examples:

- `tomorrow` -> concrete date
- `next Friday` -> concrete date
- `2pm` -> `14:00`

If a value remains ambiguous, clarification should continue and `event_draft` should not yet be considered complete.

### Confirmation Shape

Confirmation should be generated directly from `event_draft`.

The canonical confirmation form is:

- `I've got '[title]' on [day/date] from [start time] to [end time]. Do you want me to add it?`

Confirmation should include:

- title
- fully resolved day/date
- all-day or timed-event shape
- for timed events: start time
- for timed events: end time or duration

Confirmation should not omit or reorder those fields.

For all-day events, the canonical confirmation shape is:

- `I've got '[title]' on [day/date] as an all-day event. Do you want me to add it?`

## Structured UI Create Path

House Mode now also has a structured UI create path.

That path is:

- form-first
- confirmation-driven
- brain-owned
- separate from the conversational pending state

Current UI endpoints:

- `POST /api/ui/calendar/draft`
- `POST /api/ui/calendar/confirm`
- `POST /api/ui/calendar/cancel`

The structured UI path and the voice path are separate interaction models, but both must honor the same domain rules for normalization, confirmation, and commit integrity.

### Cancellation And Timeout Behavior

While clarification or confirmation is pending:

- explicit cancellation should discard the draft
- unrelated new commands should implicitly cancel the draft
- stale drafts should expire after a reasonable timeout

The write path should not allow a delayed generic `yes` to commit an old draft after session context is no longer clean.

### Commit Integrity

`event_commit` should use the exact values that were confirmed.

There should be no re-interpretation, re-parsing, or silent mutation between:

- `event_draft`
- `event_confirmation`
- `event_commit`

If any field changes, the prior confirmation is no longer valid and the flow must return to clarification and confirmation.

## Backend Direction

The current read domain is built around ICS-fed listing and lookup behavior.

The active calendar provider wiring is now explicitly bridge-scoped:

- `calendar_provider = nextcloud`
- `NextcloudCalendarBridge` is the active provider bridge for both calendar reads and writes

The calendar domain continues to own:

- query parsing
- event matching
- clarification and confirmation flow
- spoken response shaping

The bridge owns:

- provider auth usage
- ICS fetch and parse mechanics
- Nextcloud write URL construction
- provider-specific request and response handling

That remains appropriate for reads.

Calendar writes use a real Nextcloud-capable write path rather than trying to force mutation through an ICS-style read interface.

The write backend is therefore built around actual create semantics, not around stretching the read feed past its shape.

## V2 Configuration Reconciliation

Calendar provider roles, account/calendar mappings, read/write policy, and
confirmation settings belong to `domains/calendar.yaml`. The household timezone
remains shared household identity and is consumed by the calendar domain rather
than duplicated here. Migration preserves the separate read and real write
semantics; provider availability remains readiness rather than deterministic
config validity.

The Stage 3 construction seam maps the optional applied role into frozen
`CalendarRuntimeSettings`. One explicitly selected Nextcloud definition remains
the provider edge, but read and write are independent runtime sections. Read
resolves only its enabled typed feeds and cache policy. Write resolves only its
enabled complete DAV tuple and credential, and the schema-enforced confirmation
requirement remains present in the runtime settings. A disabled surface does
not resolve dormant secrets from the other surface. Household timezone is
consumed as shared canonical context; secret-backed feed URLs and write
credentials remain absent from representations. The canonical application
composition consumes this seam directly.
