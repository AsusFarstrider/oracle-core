# `/api/ui/weather` Contract

## Purpose

`GET /api/ui/weather` returns the Weather page snapshot. This contract is
implemented through V2 Stage 7 Slice 6.

It is the fuller household weather view built on Oracle-owned weather data.

## Request

Method:

- `GET`

Query/body:

- no request body for Alpha

## Required Response Shape

Required top-level fields:

- `generated_at`
- `current`
- `forecast`
- `solar`
- `alerts`
- `components`

Recommended Alpha success field:

- `ok`

## Field Requirements

### `generated_at`

- ISO-8601 timestamp

### `current`

When current weather is available, required fields are:

- `summary`

Recommended Alpha fields:

- `temperature_f`
- `freshness_class`
- `observation_timestamp`
- `humidity_pct`
- `wind_speed_mph`
- `rain_rate_in_h`

### `forecast`

Required fields:

- `periods`

Rules:

- `periods` is always an array
- empty is acceptable when another useful Weather component remains available
- a missing provider component must be named `unavailable` in `components`

Recommended Alpha period fields:

- `name`
- `start_time`
- `end_time`
- `temperature_f`
- `short_forecast`
- `probability_of_precipitation_pct`

### `solar`

Solar is a provider-free local calculation from configured coordinates, date,
and IANA timezone. When available it contains `date`, `location`, `timezone`,
`source_type`, and `events`. `events` contains `sunrise`, `sunset`,
`civil_dawn`, and `civil_dusk`; a polar no-event is represented by `null`, not
an invented time. Solar remains useful when every external Weather provider is
unavailable.

### `alerts`

Interactive NWS watches and warnings are a read-only Weather component. Alerts
include bounded event/headline/severity/urgency/effective/expiry evidence and
their own cache freshness. This does not authorize proactive delivery.

### `components`

Each of `current`, `forecast`, `solar`, and `alerts` is independently marked
`available` or `unavailable`. One failed provider must not erase useful output
from the other components.

## Optional Fields

Optional Alpha-safe fields may include:

- `ok`
- `location`
- `state`
- `refresh_after_seconds`
- `notice`

## Failure Behavior

Recommended Alpha behavior:

- use a normal success payload when current, forecast, or solar remains usable
- prefer an explicit failure response when Oracle cannot provide a usable page snapshot

Recommended explicit failure shape:

- `ok: false`
- `error`
- `detail`

Recommendation:

- return partial success with explicit component status and `notice` when one or
  more components fail
- return explicit failure only when current, forecast, and solar are all unusable;
  alerts alone are not a useful Weather page

## Freshness Expectations

Alpha expectation:

- fetch on page load
- poll at a slower cadence than Home

Recommended default polling:

- 300 to 600 seconds

## Example

```json
{
  "ok": true,
  "generated_at": "2026-04-15T13:05:00Z",
  "current": {
    "summary": "61 degrees with light rain",
    "temperature_f": 61,
    "freshness_class": "fresh",
    "observation_timestamp": "2026-04-15T09:03:00-04:00",
    "humidity_pct": 84,
    "wind_speed_mph": 8,
    "rain_rate_in_h": 0.03
  },
  "forecast": {
    "periods": [
      {
        "name": "This Afternoon",
        "start_time": "2026-04-15T14:00:00-04:00",
        "end_time": "2026-04-15T18:00:00-04:00",
        "temperature_f": 64,
        "short_forecast": "Chance Rain Showers"
      },
      {
        "name": "Tonight",
        "start_time": "2026-04-15T18:00:00-04:00",
        "end_time": "2026-04-16T06:00:00-04:00",
        "temperature_f": 49,
        "short_forecast": "Mostly Cloudy"
      }
    ]
  },
  "solar": {
    "date": "2026-04-15",
    "location": "Example Home",
    "timezone": "America/New_York",
    "source_type": "astral_local_calculation",
    "events": {
      "civil_dawn": "2026-04-15T05:54:00-04:00",
      "sunrise": "2026-04-15T06:22:00-04:00",
      "sunset": "2026-04-15T19:45:00-04:00",
      "civil_dusk": "2026-04-15T20:13:00-04:00"
    }
  },
  "alerts": {"alerts": [], "freshness": "fresh"},
  "components": {"current": "available", "forecast": "available", "solar": "available", "alerts": "available"},
  "refresh_after_seconds": 300
}
```

## Example Explicit Failure

```json
{
  "ok": false,
  "error": "weather_unavailable",
  "detail": "Oracle could not load current weather, forecast, or solar times."
}
```
