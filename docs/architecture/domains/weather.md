# Weather

Weather is a Brain-owned domain with its own route target and handler path. It
does not execute through the generic system target.

## Capability Surfaces

The reusable domain supports independently configured surfaces for:

- local current observations;
- local forecast;
- bounded local historical weather;
- remote current weather by location;
- remote forecast by location;
- practical coat, umbrella, snow, and temperature answers;
- provider-free sunrise, sunset, civil dawn, and civil dusk; and
- interactive material weather watches and warnings.

Each surface has an explicit provider role and failure behavior. No weather
surface silently falls back to a different surface.

## Responsibility Split

The weather domain owns intent classification, query parsing, provider-neutral
normalization, salience, deterministic reply shaping, freshness policy, and
structured result payloads.

Practical answers lead with the requested decision and cite only available
condition, temperature, or precipitation evidence. Household schedule anchors
must be supplied or clarified; Weather does not invent departure, meal, or
other household times. A bounded informational subject may retain only the
resolved location/window/source needed for a same-session follow-up.

Solar events are local calculations over explicitly configured coordinates and
IANA timezone. They do not invoke inference, remote geocoding, or a weather
provider. Interactive watches and warnings remain read-only provider evidence;
they do not create a proactive worker or notification policy.

Provider bridges own connection, authentication, provider payload parsing, and
translation into Oracle-owned observation and forecast shapes. The retained
WeeWX station bridge supports configured current and static-history sources
plus an optional explicitly configured history fallback. The NWS bridge owns
forecast-provider mechanics. Deployment-specific hosts, paths, credentials,
station locations, and export schedules remain household material.
When the optional WeeWX history fallback uses SSH, it follows the shared strict
SSH host-verification contract and never embeds its password in process
arguments.

## Freshness And Failure

Current observations and forecasts use bounded fresh caches and may expose a
bounded stale-on-error result only with explicit freshness evidence and spoken
wording. Fetch and parse failures are not cached as successful observations.
Historical queries do not silently substitute current weather, and remote
queries do not silently substitute a household's local station.

Remote location resolution fails clearly when a location is ambiguous. A
requested forecast outside the provider window fails rather than selecting a
nearer period.

## Canonical Configuration

`domains/weather.yaml` owns the local, history, forecast, and remote provider
roles; station and location mappings; and cache/freshness policy. Household
timezone, locale, and optional home location remain shared household context.
Observations and provider availability are operational state.

The canonical runtime constructs four distinct capability edges. Disabled
surfaces do not select dormant providers or resolve their secrets. Current,
history, forecast, and remote execution receive only their own typed inputs;
they cannot infer a provider from another enabled surface.

Solar is a fifth provider-free edge over bounded configured locations. Remote
transport may own location resolution, observations, forecasts, active-alert
translation, and its own bounded caches while the domain retains ambiguity,
ranking, follow-up, and final reply policy.

Voice dispatch, bounded caches, provider bridges, and fixed weather UI reads
consume the immutable installed weather execution. The explicit legacy
composition remains limited to migration and bounded characterization.
