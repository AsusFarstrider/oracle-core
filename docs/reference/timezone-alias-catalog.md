# Governed Timezone Alias Catalog

Status: implemented Stage 6 deterministic reference. The executable catalog is
`server/oracle_app/temporal.py`; this page records its product boundary and
review rules.

## Authority And Boundary

Oracle resolves household-local time from the canonical household IANA
timezone. World-time requests accept the bounded aliases below or an explicit
IANA timezone identifier installed with the runtime timezone database. This is
not a place database or geocoder. Unknown places fail honestly, and changing
this catalog is a reviewed product-surface change.

## Supported Alias Groups

| User-facing group | Canonical IANA zone(s) |
| --- | --- |
| UTC / GMT | `UTC` |
| US Eastern; New York; New York City | `America/New_York` |
| US Central; Chicago | `America/Chicago` |
| US Mountain; Denver | `America/Denver` |
| US Pacific; California; Los Angeles; San Francisco; Seattle | `America/Los_Angeles` |
| Arizona; Phoenix | `America/Phoenix` |
| Alaska | `America/Anchorage` |
| Hawaii; Honolulu | `Pacific/Honolulu` |
| London; United Kingdom; UK | `Europe/London` |
| Paris; France | `Europe/Paris` |
| Berlin; Germany | `Europe/Berlin` |
| Tokyo; Japan | `Asia/Tokyo` |
| Beijing; China | `Asia/Shanghai` |
| India; New Delhi | `Asia/Kolkata` |
| Sydney | `Australia/Sydney` |
| Melbourne | `Australia/Melbourne` |
| Auckland; New Zealand | `Pacific/Auckland` |

`here`, `home`, and `local` resolve to the canonical household timezone.

## Required Clarifications

`Washington` clarifies between Washington DC and Washington state. `Georgia`
clarifies between the country and the US state. The selected answer re-enters
the same deterministic temporal owner through the canonical pending-utility
session slot; the clarification does not create another location authority.

Aliases that become materially ambiguous must move to this clarification set
rather than silently retaining a guessed mapping. Expanding beyond common
bounded aliases requires product review; Stage 6 does not add external
geocoding.
