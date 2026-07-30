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

## Request

Method:

- `GET`

Query/body:

- no request body for Alpha

## Required Response Shape

Required top-level fields:

- `generated_at`
- `today`
- `upcoming`

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
- `start`
- `end`
- `all_day`

Alpha rule:

- event items must be app-safe summaries only
- the contract must not expose raw provider objects or write-oriented draft fields

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

## Freshness Expectations

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
  "today": {
    "date": "2026-04-15",
    "events": [
      {
        "summary": "Breakfast",
        "start": "2026-04-15T08:00:00-04:00",
        "end": "2026-04-15T09:00:00-04:00",
        "all_day": false
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
