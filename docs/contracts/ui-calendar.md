# `/api/ui/calendar` Contract

## Purpose

`GET /api/ui/calendar` returns the Alpha Calendar page snapshot.

This is the stronger upcoming-events view for the household UI.

It is not a full calendar product.

Calendar create now lives on separate dedicated `/api/ui/calendar/*` write endpoints rather than this read snapshot.

Current structured write endpoints:

- `POST /api/ui/calendar/draft`
- `POST /api/ui/calendar/confirm`
- `POST /api/ui/calendar/cancel`
- `POST /api/ui/calendar/mutation/draft`
- `POST /api/ui/calendar/mutation/confirm`

## Request

Method:

- `GET`

Query/body:

- no request body for Alpha

## Required Response Shape

Required top-level fields:

- `generated_at`
- `status`
- `freshness`
- `provider_age_seconds`
- `complete`
- `source_availability`
- `today`
- `upcoming`

When the configured read provider is temporarily unavailable, the endpoint
remains renderable and returns empty `today.events` and `upcoming.events`
arrays with top-level `status: "unavailable"` and app-safe `detail`. The
`create_event` block reports unavailable for that snapshot. Invalid canonical
configuration and unexpected implementation failures remain strict errors.

Recommended Alpha fields:

- `timezone`
- `refresh_after_seconds`

## Field Requirements

### `today`

Required fields:

- `events`

Recommended Alpha fields:

- `date`
- `create_event`

Rules:

- `events` is always an array
- empty is valid

### `upcoming`

Required fields:

- `events`

Rules:

- `events` is always an array
- empty is valid
- items should already be sorted into the order the UI should present

### Event Item Shape

Recommended Alpha event fields:

- `summary`
- `event_ref`
- `start`
- `end`
- `all_day`
- `location`
- `source_id`
- `source_label`

Alpha rule:

- event items must be app-safe summaries only
- `event_ref` is an opaque Oracle selection reference derived from the current
  normalized event and is not a raw provider UID
- the contract must not expose raw provider objects or write-oriented draft fields
- `source_id` and `source_label` identify Oracle's configured feed, not a raw
  provider object or an authorization decision

If `all_day` is `true`:

- clients should treat the event as all-day
- clients should not invent display times from `start` and `end`

### `create_event`

Recommended Alpha fields:

- `available`
- `status`
- `detail`

Rules:

- `available` indicates whether structured House Mode create is currently enabled
- `status` should reflect the current create availability at a high level
- `detail` should remain app-safe and user-facing

## Scope Boundary

`/api/ui/calendar` must not become:

- a full calendar editing API
- a raw Nextcloud provider pass-through
- a generic calendar sync surface

Calendar write behavior remains a separate concern under Oracle’s dedicated UI calendar create flow and voice calendar-write flow.

The UI calendar create path is:

- form-first
- structured
- confirmation-driven
- separate from `/api/voice`

### Stage 8 Bounded Write Target

Slice 8.5 extends the dedicated Calendar write family with bounded existing-
event edit, reschedule, and delete/cancel. The read snapshot may expose only
Oracle-owned stable selection references and app-safe capability state needed
to begin that flow; it remains neither a raw provider browser nor the mutation
authority.

The Brain performs deterministic selection and ambiguity/recurrence-scope
clarification, freezes the selected event/calendar/user/source/scope and result
fields, and requires confirmation under the Calendar write contract.
Delete/cancel is consequential. Clients cannot submit raw provider IDs/payloads,
choose occurrence versus series implicitly, or request delete/recreate
emulation. `POST /api/ui/calendar/mutation/draft` accepts only the opaque stable
`event_ref` already returned by the Oracle read model, its canonical
`calendar_id`, plus bounded ordinary
changes and explicit recurrence scope when needed. It returns a frozen
`draft_id`; `POST /api/ui/calendar/mutation/confirm` is the only endpoint that
executes that draft. Both require the same authenticated stable household
`source_id` and bounded `ui_session_id`; neither `client_id` nor `calendar_id`
authenticates a caller. Both preserve the current structured-versus-voice state
separation.

## Freshness Expectations

`generated_at` is only the time Oracle built this UI payload. It must never be
presented as provider freshness. `provider_age_seconds`, top-level `freshness`,
and each `source_availability` entry's `retrieved_at`, `age_seconds`,
`freshness`, and `status` carry provider-read truth.

`status` is `available` when all relevant feeds are available, `partial` when
at least one relevant feed is missing but useful data remains, and
`unavailable` when no relevant feed can be served. `complete` is false for a
partial result. An empty partial snapshot must be rendered as incomplete, not
as proof that the calendar is empty.

The domain normally fresh-caches each feed for five minutes and may expose a
bounded stale result for up to ten minutes after a provider failure. A client
poll does not force a provider refresh merely because it rebuilt the UI
snapshot. Explicit domain refresh and a successful confirmed write invalidate
the appropriate cached state.

Alpha expectation:

- fetch on page load
- poll while visible
- refresh after actions that materially affect calendar-related UI state if any are added later

Recommended default polling:

- 60 to 120 seconds

## Example

```json
{
  "generated_at": "2026-04-15T13:05:00Z",
  "timezone": "America/New_York",
  "status": "available",
  "freshness": "fresh",
  "provider_age_seconds": 42.3,
  "complete": true,
  "source_availability": [
    {
      "source_id": "household",
      "source_label": "Household",
      "status": "available",
      "freshness": "fresh",
      "age_seconds": 42.3,
      "retrieved_at": "2026-04-15T13:04:18Z"
    }
  ],
  "today": {
    "date": "2026-04-15",
    "events": [
      {
        "summary": "Breakfast",
        "start": "2026-04-15T08:00:00-04:00",
        "end": "2026-04-15T09:00:00-04:00",
        "all_day": false,
        "location": "Kitchen",
        "source_id": "household",
        "source_label": "Household"
      }
    ]
  },
  "upcoming": {
    "events": [
      {
        "summary": "Breakfast",
        "start": "2026-04-15T08:00:00-04:00",
        "end": "2026-04-15T09:00:00-04:00",
        "all_day": false
      },
      {
        "summary": "Mom's Birthday",
        "start": "2026-04-16T00:00:00-04:00",
        "end": "2026-04-17T00:00:00-04:00",
        "all_day": true
      }
    ]
  },
  "create_event": {
    "available": true,
    "status": "available",
    "detail": "Create an event inline, review the normalized draft, and confirm before Oracle commits it."
  },
  "refresh_after_seconds": 120
}
```

## V2 Identity Reconciliation

Canonical calendar UI mutations carry authenticated `source_id` context and a
bounded `ui_session_id` for draft ownership. The current `client_id` field is a
Stage 3 compatibility alias for deployed UI consumers and cannot authenticate
the source, authorize a write, or supply audit actor identity. Calendar draft,
confirmation, cancellation, and single-commit behavior remain unchanged.
