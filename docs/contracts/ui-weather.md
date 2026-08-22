# `/api/ui/weather` Contract

## Purpose

`GET /api/ui/weather` returns the Alpha Weather page snapshot.

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

Recommended Alpha success field:

- `ok`

## Field Requirements

### `generated_at`

- ISO-8601 timestamp

### `current`

Required fields:

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
- empty is acceptable only when Oracle can still return a usable current-weather snapshot and the forecast portion is temporarily unavailable or intentionally omitted
- if Oracle cannot assemble a usable weather page snapshot at all, an explicit failure response is preferred over returning an empty-but-ambiguous success payload

Recommended Alpha period fields:

- `name`
- `start_time`
- `end_time`
- `temperature_f`
- `short_forecast`

## Optional Fields

Optional Alpha-safe fields may include:

- `ok`
- `location`
- `state`
- `refresh_after_seconds`
- `notice`

## Failure Behavior

Recommended Alpha behavior:

- use a normal success payload when Oracle can still return a usable weather page snapshot
- prefer an explicit failure response when Oracle cannot provide a usable page snapshot

Recommended explicit failure shape:

- `ok: false`
- `error`
- `detail`

Recommendation:

- if current conditions are available but forecast periods are temporarily unavailable, return success with `forecast.periods: []` and a `notice` or similar explanatory field
- if both current and forecast fail such that the page would be unusable, return an explicit failure response instead of an ambiguous empty success payload

This is a contract recommendation for Alpha consistency, not a statement that the current code already implements this response shape.

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
  "refresh_after_seconds": 300
}
```

## Example Explicit Failure

```json
{
  "ok": false,
  "error": "weather_unavailable",
  "detail": "Oracle could not load current weather or forecast data."
}
```
