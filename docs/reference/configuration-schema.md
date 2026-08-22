# V2 Configuration Schema Reference

Status: implemented canonical schema 2 and projection schema 1.
Code-owned composed Pydantic v2 models are executable field authority while
remaining subordinate to the configuration contract. All fixed roles have
typed schema leaves and the selected immutable generation is runtime authority.
Generated JSON Schema is versioned/tested System Mode and tooling output, not a
separate hand-maintained authority.

The current checked-in generated output is
`docs/reference/generated/configuration-v2.schema.json`. It is reproduced and
drift-checked from executable models by
`scripts/generate-config-schema.py`; edits to the generated file are never a
schema-authority change by themselves.

This ratification reference fixes file roles, ownership, invariants, and
representative shapes. Stage 3 supplies provider-specific leaf fields, numeric
bounds, and adapter options through the owning Pydantic fragments. Such details
may not introduce a new role, move authority, add precedence, change secret or
trust semantics, or cross another ratified boundary without explicit review.

## Bundle Layout

```text
config/
  bundle.yaml
  brain.yaml
  access.yaml
  household.yaml
  satellites.yaml
  domains/
    information.yaml
    music.yaml
    audiobooks.yaml
    weather.yaml
    calendar.yaml
    home-assistant.yaml
    notifications.yaml
    routines.yaml
    network/
      inventory.yaml
      policy.yaml
      adapters.yaml
  secrets.env
```

The first five YAML roles are required. Domain roles are optional fixed slots.
`secrets.env` is a known deployment-local companion, not a bundle role.
Its logical path is fixed and cannot be redirected by bootstrap. A deployment
may mount that file independently from read-only YAML. Secret mutation is
available only when the companion is writable; immutable installed secret
generations remain in the writable installed store.

The public example mirrors this layout under `examples/config/`; it is not a
runtime bundle root. Its secret template is `secrets.env.example`.

Authoring mode is bootstrap/deployment metadata, not YAML. Supported modes are
`managed_writable` and `external_read_only`; the latter never creates an
installed-store override for authored fields.

An enabled optional role retires through an adopted disabled generation before
file removal. Direct enabled-to-missing transition is an activation blocker;
initial absence is valid unconfigured state.

Every YAML file uses UTF-8, one YAML 1.2 document, and the restricted syntax in
the configuration contract. Paths are relative to the logical bundle root. The
root may itself be a link or mount. Role links are valid only when their final
regular-file targets remain within the once-resolved root. Broken links, cycles,
device files, traversal, and targets outside it are invalid.

Implementation uses one pinned restricted `ruamel.yaml` round-trip parser for
both validation and managed editing, then converts the accepted node graph to
plain primitives for Pydantic validation.

## Common Conventions

- Field names use `snake_case`.
- scalars use explicit `true`/`false`, `null`, and finite JSON-style decimal
  numbers; ambiguous YAML coercions are invalid.
- IDs are typed lowercase ASCII strings beginning with a letter and containing
  letters, digits, `_`, or `-`, with no consecutive or trailing separators.
- IDs are unique within their typed namespace and compared exactly.
- IDs are stable; rename is modeled as an explicit remove/add migration with
  reference and state-impact checks.
- Display names and aliases are Unicode operator text, not IDs.
- `enabled` is explicit where defined and has no implicit `true` default.
- enabled provider roles select one provider explicitly; definition order,
  secret presence, and provider health never create precedence.
- Durations use named numeric fields with explicit units, normally `_seconds`.
- credential-free URLs appear only in provider, adapter, or access edges that
  own and validate them; secret-bearing URLs are whole logical secrets.
- Secret references match `^[A-Z][A-Z0-9_]*$`.
- required machine-specific paths appear only at their owning Brain/satellite
  runtime edge; public examples and core defaults contain no household paths.
- Unknown fields are errors.

## `bundle.yaml`

```yaml
kind: oracle_configuration_bundle
schema_version: 2
bundle_id: example-home
```

`bundle.yaml` contains only bundle identity and schema selection. It does not
list files, include paths, revisions, timestamps, migrations, defaults, or
activation history.

`bundle_id` is a stable non-secret lineage ID, not a revision, hostname, or
display name. The installed store binds it at initialization and rejects an
ordinary candidate carrying another ID.

## `brain.yaml`

`brain.yaml` owns shared Brain runtime behavior rather than household or domain
policy. Its fixed top-level sections are:

```yaml
runtime:
  wake_arbitration:
    window_ms: 1000
    scoring_strategy: audio_level_confidence_recent
    loser_suppression_ms: 10000
  satellite_control_timeout_seconds: 6.0

logging:
  level: INFO

storage:
  memory:
    backend: sqlite
    database_path: data/oracle-memory.sqlite3
    retention: {}

speech:
  stt:
    enabled: false
    providers: {}
  tts:
    enabled: false
    providers: {}

inference:
  shared_backend:
    enabled: false
    providers: {}
    fallback_router: {}
```

Provider-specific connection fields and secret references are admitted only by
the registered schema for that shared provider role. Domain policy cannot be
placed here merely because the Brain executes it.

Configuration schema 2 admits only the Brain implementations Oracle currently owns:
`whisper_cpp` and `fast_whisper` definitions for STT, `piper` for TTS, and
`ollama` for shared inference. Provider maps are closed discriminated unions;
selection must name a present typed definition. Executable/model/database paths
remain at these owning runtime/storage edges. Inference endpoints must be
credential-free HTTP(S) URLs. Shared request defaults and the fallback-router
model/timeout override normalize into the immutable effective snapshot.

`runtime.wake_arbitration` owns Brain-wide arbitration behavior and
`runtime.satellite_control_timeout_seconds` owns the Brain's outbound satellite
control timeout. Listener addresses and the Brain's own public/base URL remain
bootstrap/access concerns and are not admitted here.

`storage.memory` selects the one Memory-owned SQLite store and its bounded
retention policy, including the default 90-day terminal alert horizon.
`storage.alerts` is not accepted; runtime alert authority is Memory-owned. The
checked-in example uses package-relative paths; deployments may
use machine paths at these owning edges, but cannot redirect the configuration
bundle or installed store through these fields.

For the fixed Stage 4 standard Debian layout, the supported values are
`data/oracle-memory.sqlite3`. Standard runtime
binding materializes those logical paths beneath `/srv/oracle/data`; it never
resolves them relative to the immutable application revision. Other machine
paths remain usable by development or custom deployments, but require a later
explicit standard-installation storage/profile contract before they can be
used by the supported Debian installer.

The five top-level sections are present. `speech.stt`, `speech.tts`, and
`inference.shared_backend` each require explicit `enabled`. There is no
`speech.enabled` or `inference.enabled`. When enabled, a role selects its
provider explicitly; when disabled, it may omit operational provider details.

Brain instance identity is bootstrap/deployment metadata, not a `brain.yaml`
field. It may scope locks, applied-generation observations, logs, and health but
cannot select behavioral overrides.

Listener/trust fields, household semantics, domain provider selection/policy,
and bootstrap locations are invalid in `brain.yaml`. No generic catch-all or
universal `providers` section exists.

## `access.yaml`

`access.yaml` declares Oracle-facing trust expectations using code-known modes.
It does not configure the external gateway itself.

```yaml
operator_access:
  mode: trusted_boundary
  boundary_id: oracle_web_gateway
  browser_mutation: true
  csrf_protection: boundary_proof
  host_local_cli: true

trusted_boundary:
  boundary_id: oracle_web_gateway
  enabled: true
  type: authenticated_reverse_proxy
  trusted_proxy_ids:
    - oracle-web-gateway
  accepted_headers:
    - authenticated_request

public_health:
  enabled: false

satellite_authentication:
  enrollment_mode: per_satellite
  directional_credentials_required: true
```

The executable schema will define the bounded supported modes and proof fields.
It cannot accept executable plugins, commands, proxy-native configuration, or
Oracle-managed roles.

The executable access model and semantic safety classifier share one exhaustive
field-disposition table. The current security-relevant transitions are limited
to browser inspection/mutation enablement, trusted-boundary mode/identity/
enablement and additions to its accepted proxies/proof forms, public-health
enablement, newly active credential bindings, and logical credential-role
changes on active bindings. These produce `access_expansion`,
`public_health_enablement`, or `credential_role_change` as declared; restrictive
removals do not produce expansion. Initial activation classifies enabled
surfaces intrinsically. A future access field fails schema-coverage tests until
it receives an explicit disposition.

Non-satellite stable-source authentication uses the named
`credential_bindings` section in `access.yaml`, never a generic
`source_bindings` list:

```yaml
source_authentication:
  credential_bindings:
    - source_id: resident_phone
      credential_secret: RESIDENT_PHONE_SOURCE_CREDENTIAL
```

Every binding references exactly one enabled, type-compatible source from
`household.yaml`. Browser, mobile-application, desktop-application, and kiosk
sources require a binding before ingress may use their stable identity. A
credential is unique, high entropy, scoped to one source, and represented only
by a logical secret reference. Browser credential use requires a supported
HTTP-only cookie or equivalent protected server transport; scripts and local
storage cannot receive it.

Each credential binding accepts exactly one `credential_secret` value at a
time. Replacing that logical secret and activating its new secret generation
atomically revokes the previous value. The schema has no previous/next
credential slots, overlap duration, self-enrollment, or delivery fields.
Disabling the referenced source blocks use immediately; secret removal is a
separate explicit operation.

Every enabled non-satellite source appears exactly once in
`credential_bindings`. A disabled source may retain one inactive binding. Each
`credential_secret` reference is unique across bindings. Satellites do not
appear in this list.

Credential uniqueness is reference-level only. Validation rejects one logical
secret reference used by multiple enabled source bindings. It does not compare,
hash, fingerprint, or persist equality between resolved secret values. Distinct
logical IDs containing the same raw value are operator error outside the V2
schema's enforcement scope.

`AccessRuntimeSettings.from_effective_config` is the frozen consumer-side seam
for this role. It retains the typed operator, trusted-boundary, public-health,
and satellite-authentication policy and resolves raw values only for bindings
whose non-satellite source is enabled in the same adopted activation. Raw
credentials are excluded from representations. Request authentication compares
one presented value in constant time against active bindings and returns a
stable source only for exactly one match. It does not precompare configured
values, fingerprint them, persist equality evidence, or make inactive bindings
operational. An accidental duplicated raw value therefore authenticates no
source rather than selecting one arbitrarily.

This seam does not define an ingress transport. Browser bindings still require
the protected cookie/server boundary promised above, while unproved or unbound
ingress remains ephemeral. Trusted-boundary operator proof never enters the
stable-source credential map.

Each `boundary_id` references a typed enabled trusted-boundary entry in this
file, but V2 boundary authorization does not establish stable-source identity.
Trusted-boundary requests receive ephemeral source identities. User identity,
localhost, VPN/network position, SSH tunnels, and arbitrary headers are not
stable-source proof. `trusted_boundary_bindings` is not an accepted V2 field;
it remains a documented future extension requiring a concrete
installation-level proof mechanism plus schema and contract review.

Satellite request sources are not repeated in these sections. Satellite
credentials authenticate `satellite_id`, and the matching satellite record
supplies `source_id`. Unbound ingress receives a server-controlled ephemeral
identity and cannot use stable-source associations. User and room associations
are never authentication inputs.

At canonical context-bearing HTTP ingress, a payload `source` is compatibility
input only. A satellite claim selects one applied fleet candidate and its
credential is checked against the exact satellite activation bound to the
running Brain revision. A non-satellite credential maps directly through
`credential_bindings`. Invalid presented credentials fail authentication;
missing credentials produce the unassociated server-controlled
`ephemeral_http` source. This runtime behavior adds no further YAML fields.

Initial `operator_access.mode` values are `host_local_only` and
`trusted_boundary`. The first disables browser mutation. The second requires
configured authentication plus CSRF-resistant proof; read-only inspection is a
separate flag. Host-local recovery remains available in both.

`trusted_boundary` is an optional singleton, so V2 can have at most one enabled
remote operator boundary. Its `boundary_id` is stable and its `type` is selected
from a code-known enum; the example's `authenticated_reverse_proxy` is the
current gateway shape rather than a universal requirement.
`operator_access.boundary_id` is required and must match it in
`trusted_boundary` mode. It is absent in `host_local_only` mode. Host-local CLI
access does not traverse this object. Multiple simultaneously enabled remote
boundaries require a later schema revision.

## `household.yaml`

```yaml
household:
  id: example_home
  display_name: Example Home
  timezone: Etc/UTC
  locale: en-US
  home_location:
    locality: Example City
    region: Example Region
    country: US

defaults:
  user_id: resident_one

users:
  - id: resident_one
    enabled: true
    display_name: Resident One
    aliases: []
    capabilities:
      audiobooks:
        enabled: true
        account_id: primary
        credential_secret: AUDIOBOOK_PROVIDER_RESIDENT_ONE_TOKEN

rooms:
  - id: living_room
    enabled: true
    display_name: Living Room
    aliases:
      - lounge

sources:
  - id: living_room_voice
    enabled: true
    type: satellite
    fixed: true
    associated_room_id: living_room
    associated_user_id: resident_one

modes:
  - id: quiet_mode
    enabled: true
    display_name: Quiet Mode
```

Rules:

- one or zero enabled household default users;
- no household-wide default room field;
- required IANA `household.timezone` and supported BCP 47
  `household.locale`, with no host/browser/environment fallback;
- default and association references target declared IDs;
- `associated_room_id` requires `fixed: true`;
- source association is context only;
- aliases are unique within their typed resolution namespace, including
  disabled entries, unless a domain contract defines a stronger ambiguity rule;
- cross-namespace alias reuse is allowed, with warnings for overlapping command
  grammars and safe clarification/failure where ambiguity affects execution;
- disabled identities cannot be new resolution or execution targets; and
- capability secret references never contain raw provider credentials.

`HouseholdRuntimeSettings.from_effective_config` is the frozen consumer-side
lookup seam for this role. It retains every declared typed identity for stable
historical references, while only enabled users, rooms, and modes participate
in new name resolution. Canonical IDs, display names, and aliases are indexed
without producing a parallel registry dictionary. A display-name ambiguity resolves
to no target rather than adding an activation rule not present in schema 2.
Source lookup returns only configured association context from an enabled
source; it does not authenticate ingress, authorize a user, or infer a stable
source from an arbitrary request value.

Initial `sources[].type` values are `satellite`, `mobile_app`, `browser`,
`desktop_app`, and `kiosk`. There is no configured `other` type; new stable
ingress classes extend the schema. Type itself grants no trust or capability.

Identity retirement is disable-then-remove. Removal requires no enabled
configuration references plus destructive-change acknowledgement; historical
state references are retained and do not keep configuration alive.

Household timezone, locale, and the optional single home-location object are
shared semantic truth. Coordinates may be supplied when required; a full street
address should be omitted unless a real capability needs it. Domain files add
provider-specific mappings for that location. Host
operating-system timezone is deployment state and cannot override the household
value.

Mode entries define identity and policy semantics only. Current values are
operational state. Home Assistant-backed mode entity mappings belong in
`domains/home-assistant.yaml`; activation never toggles them.

## `satellites.yaml`

```yaml
satellites:
  - id: living_room_satellite
    enabled: true
    source_id: living_room_voice
    platform: linux
    capabilities:
      voice: true
      display: true
      music_playback: true
      audiobook_playback: true
    brain_client:
      base_url: http://oracle-brain.example.invalid:8011
      credential_secret: LIVING_ROOM_SATELLITE_BRAIN_TOKEN
    control_service:
      base_url: http://living-room-satellite.example.invalid:8021
      local_client_url: http://127.0.0.1:8021
      credential_secret: LIVING_ROOM_CONTROL_SERVICE_TOKEN
    enrollment:
      credential_secret: LIVING_ROOM_ENROLLMENT_TOKEN
    audio:
      input:
        type: system_default
      interaction_output:
        type: system_default
      playback:
        adapter: oracle_native
    ui:
      enabled: true
      touch: true
      profile: living_room_touch
      layout: satellite_landscape_touch_v1
      pages: [home, weather, calendar, audio, house]
      bottom_nav: [home, weather, calendar, audio, house]
    wake:
      enabled: true
      model:
        format: onnx
        asset_id: hey_oracle
```

Rules:

- every enabled satellite references one enabled stable household source;
- one enabled satellite may own a given satellite source;
- satellite and source IDs remain separately typed;
- directional credential references are distinct and satellite-specific;
- the satellite-to-Brain URL and credential are one projected satellite-owned
  edge, and enabled voice satellites require both;
- `brain_bootstrap_url` is installation metadata used only for first contact
  with the projection service; it is not a YAML field, projected field,
  canonical override, discovery mechanism, or fallback list;
- the Brain-facing control-service URL and credential are one satellite-owned
  edge, and playback-capable enabled satellites require both;
- the same edge owns a separate local client URL, shares the control credential,
  and enabled voice- or playback-capable satellites require it;
- non-loopback local client URLs are explicit operator choices and are never
  silently rewritten;
- no deployment-defined inheritance, host overlays, or defaults exist; and
- runtime package version and observed lifecycle state are reported operational
  data unless a later lifecycle schema explicitly declares desired version.

`audio` owns the satellite's PortAudio/default device selection, Linux-only
ALSA capture selection, gains, bounded VAD and follow-up timing, local cue asset
IDs, alert polling, interim acknowledgement timing, and local media
interruption/ducking adapter behavior. It does not own Brain endpoints,
credentials, provider accounts, or raw shell commands.

Projection schema 1 accepts only the `oracle_native` playback adapter. Plexamp
client control is retired; shared Plex provider configuration used by native
playback remains owned by `domains/music.yaml`.

Optional `audio.playback.volume_control` is a discriminated union. Linux may use
`{type: alsa, card: <mixer-card>, control: <mixer-control>}`. Windows may use
`{type: windows_default_endpoint}` and follows the operating system's current
default output endpoint. Cross-platform combinations are invalid, absence means
unsupported, and configured runtime support is checked before activation.

`ui` owns display/touch enablement, a code-known profile/layout ID, and ordered
page/navigation selections. Page order is semantically meaningful. An enabled
UI requires the display capability; a display-capable enabled satellite
requires an enabled UI definition.

`wake` owns the ONNX/TFLite model selection, thresholds, cooldown and
arbitration-client timing, playback suppression, and bounded diagnostic capture
policy. A model uses exactly one package asset ID or machine path and an authored
path must match the declared format. Capture sync policy is projection data; V2
does not admit SSH host/user/key/transport fields as a canonical
transfer mechanism. A satellite must be able to restart from its last valid
projection while the Brain is unavailable.

`SatelliteFleetRuntimeSettings.from_effective_config` is the frozen Brain-side
consumer seam for applied fleet identity and remote control. It retains every
declared satellite for stable references, indexes only enabled satellite/source
ownership, carries the exact applied projection activation ID, and resolves the
Brain-to-control credential only for an enabled playback-capable satellite with
its canonical Brain-facing `control_service.base_url`. Raw control credentials
are excluded from representations.

This seam deliberately excludes `brain_client.base_url`,
`control_service.local_client_url`, enrollment and satellite-to-Brain raw
credentials, and satellite-local audio/UI/wake behavior. Projection pull,
enrollment, and wake-upload authentication continue to use the selected
projection resolver, whose desired-state selection may advance independently
of the Brain process's immutable applied `EffectiveConfig`. The applied fleet
seam is not a second lifecycle-authentication authority.

The audiobook domain has one bounded exception to general fleet consumption.
Its immutable runtime view retains `brain_client.base_url` only for sources
admitted by `domains/audiobooks.yaml:playback.source_ids`, because the Brain
must construct an absolute Brain-hosted stream URL for the selected satellite.
The domain view does not retain the corresponding credential and cannot use the
URL for projection refresh, authentication, or endpoint fallback. This does
not add `brain_client.base_url` to `SatelliteFleetRuntimeSettings`.

## Domain Roles

### `domains/information.yaml`

```yaml
facts:
  enabled: true
  providers: {}

news:
  enabled: true
  providers: {}
  sources: []

suggestions:
  enabled: false
  provider: null
```

Each fixed section is enabled independently. This physical grouping does not
merge the runtime domains.

### `domains/music.yaml`

Owns music provider selection, matching and clarification policy, library
mapping, and Oracle-native playback policy. Playback targets reference source
IDs, never URLs or provider-native host commands.

### `domains/audiobooks.yaml`

Owns the shared audiobook provider connection, library mapping, playback and
sync policy. User credential references remain under user capabilities in
`household.yaml`.

### `domains/weather.yaml`

Owns local, forecast, remote, and history provider roles; location and station
mapping; freshness; cache; and bounded fallback policy.

### `domains/calendar.yaml`

Owns calendar provider roles, shared account/calendar mappings, read/write
policy, and confirmation settings. User-scoped credentials, if introduced,
attach to the owning user capability.

### `domains/home-assistant.yaml`

Owns the Home Assistant bridge, Oracle-to-provider room/entity/action mappings,
camera mappings, finite Home/House/Room view membership, provider-native IDs at
that adapter edge, and HA-owned automation/runbook definitions. Definitions
invoke only registered Oracle operations.

`views` contains only the code-known `home`, `house`, and room-keyed sections.
Entries preserve authored membership/order and may add a household label, but
must reference compatible IDs in `mappings`; raw entity IDs are not repeated.
Room keys reference enabled canonical household rooms. Control entries may bind
typed status/action mappings for the same Oracle object. Camera entries may add
a confined relative `snapshot_ref` only when the selected provider declares an
absolute normalized URL-path `snapshot_root`. Generic dashboard sections,
widgets, layout, icons, themes, presentation rules, and provider discovery are
not schema fields.

Enabled `automations` bind one typed `entry_state` event mapping to one
notification type and bounded delay, repetition, lateness, and provider-retry
policy. Provider event entity IDs are unique, and one enabled event mapping or
canonical subject cannot have multiple enabled lifecycle owners. Canonical
event ingress resolves these definitions from the applied runtime view; active
runs retain the exact applied revision and resolved definition.

### `domains/notifications.yaml`

Owns notification types, bounded templates, audience policy, suppression,
channels, retries, and logical recipient/provider mappings. Callers and routines
cannot provide arbitrary text or destinations.

### `domains/routines.yaml`

Owns composite Oracle routines only. Steps use registered Oracle-native
operations and typed bounded inputs. They must never reference scripts, URLs,
provider-native commands, service names, raw external entity IDs, credentials,
or executable adapter details.

There is no `domains/alerts.yaml`. Alert schedules and records are operational
state; persistence mechanics belong to Brain storage, local polling/cue/playback
settings are projected from satellite configuration, and notification policy
belongs to the notifications role. Adding an alert role requires schema review
and substantial operator-owned policy rather than utility implementation detail.

There is no `domains/playback.yaml`. Shared playback-authority behavior is
code-owned, music and audiobook policy stays in those domain files, target and
local-adapter settings stay in satellite configuration, and current playback
truth is operational state. A new role requires substantial operator-owned
cross-domain policy with no cleaner existing owner.

### Network roles

`domains/network/inventory.yaml` is the anchor:

```yaml
enabled: true
hosts: []
devices: []
services: []
monitors: []
```

`policy.yaml` owns Oracle-native observation, action, confirmation, recovery,
and safety policy. `adapters.yaml` owns provider/host translation, including
provider-native service or router identifiers where required at the adapter
edge. Policy cannot contain commands, scripts, credentials, or provider-native
execution details.

`policy.yaml` and `adapters.yaml` cannot exist without `inventory.yaml`. They are
required only when enabled capabilities need them.

### Executable domain leaf inventory

The schema-v1 executable leaf models are closed and provider-specific. A map
named `providers` is a registry of code-known typed definitions; it is not an
arbitrary option bag. A selected provider ID must resolve to a definition even
when its owning role is disabled, and every enabled provider-backed capability
requires an explicit selection.

- `information.yaml:facts` selects a `static` or `wikipedia_api` definition and
  owns acknowledgement, summarization, timeout, and cache policy. Static facts
  use typed fixture IDs, queries, answer/evidence states, and credential-free
  provenance URLs.
- `information.yaml:news` selects an `rss` definition and owns typed source IDs,
  aliases, feed URLs, headline limits, and fresh/stale-on-error bounds.
- `information.yaml:suggestions` selects an implemented `http`, `ssh_cli`, or
  explicit `mock` OpenClaw adapter. The unimplemented websocket stub is not an
  accepted value. HTTP endpoints, SSH/CLI runtime details, optional logical
  secrets, timeout, agent/model selection, and result bounds remain inside this
  provider edge.

`InformationRuntimeSettings.from_effective_config` preserves these as three
independent frozen runtime sections. A disabled section has no operational
provider even when dormant definitions remain authored. Enabled facts resolves
only its explicit typed provider; enabled news resolves its explicit selection
and binds every source to that source's own referenced RSS definition;
enabled suggestions resolves only the selected adapter and its required whole-
URL or password secret. Resolved raw values are excluded from representations.
An absent optional information role has no implicit runtime defaults and is not
constructed.
- `music.yaml` selects a typed `plex` definition containing a credential-free
  base URL, logical credential, library-section identity, timeout, and optional
  machine identity. Matching policy is bounded, and playback targets are
  canonical source IDs.

`MusicRuntimeSettings.from_effective_config` constructs the optional applied
Brain domain only when `music.yaml` is present. Disabled music selects no
dormant Plex definition or playback target. Enabled music resolves exactly its
selected Plex definition and credential from the adopted secret generation,
retains matching/clarification policy, and binds each configured playback
`source_id` to the applied fleet's music-capable Brain-facing control edge. Raw
Plex and control credentials are excluded from representations. The global
satellite-control timeout remains `brain.yaml` ownership, while native-player
and audio behavior remain satellite/control-service ownership.
- `audiobooks.yaml` selects a typed `audiobookshelf` definition containing the
  shared credential-free endpoint, library ID, and timeout. Playback source and
  sleep-timer policy are domain-owned; per-user credentials remain exclusively
  under `household.yaml:users[].capabilities.audiobooks`.

`AudiobookRuntimeSettings.from_effective_config` constructs the optional applied
Brain domain without creating a shared credential. Disabled audiobooks selects
no dormant provider, user account, or playback target. Enabled audiobooks
resolves exactly its selected shared Audiobookshelf definition, retains the
domain sleep-timer policy, resolves credentials only for enabled users whose
typed audiobook capability is enabled, and binds playback source IDs only to
the applied fleet's audiobook-capable Brain-facing control edges. User IDs and
account IDs remain data-driven; no person name or global-token fallback is
encoded. Raw user and control credentials are excluded from representations.
- `weather.yaml` independently selects current, forecast, history, and remote
  roles from typed `weewx` and `nws` definitions. WeeWX owns current/history
  endpoints, freshness, and the bounded typed SSH/archive fallback. NWS owns
  forecast mapping, user-agent, office, coordinates, and timeout. Home forecast
  coordinates may instead come from `household.home_location`.

`WeatherRuntimeSettings.from_effective_config` preserves those four selections
as independent frozen Brain runtime sections. Each disabled capability selects
no dormant definition. Current receives only its WeeWX current endpoint,
timeout, and freshness; history receives only its history endpoint and resolves
the SSH password only when its selected WeeWX definition has that fallback;
forecast receives its selected NWS settings and resolves the narrowly specified
home-coordinate fallback from `household.home_location`; remote receives only
the NWS request identity and timeout it uses for location-driven requests.
Provider-defined coordinates do not become remote defaults, and one capability
never falls back to another. Raw SSH passwords are excluded from
representations. An absent optional weather role creates no runtime defaults.
- `calendar.yaml` selects typed `nextcloud` feeds and separate read/write
  policy. Each feed uses either a credential-free ICS URL or one whole-URL
  logical secret. A write-capable definition is an all-or-none base URL, user,
  logical credential, and calendar URI tuple; confirmation remains mandatory.

`CalendarRuntimeSettings.from_effective_config` constructs separate frozen read
and write execution sections from the one selected provider. Disabled calendar
selects no provider, and each disabled surface resolves none of its dormant
secrets. Read resolves only the configured feed URLs and retains fresh/stale
policy; write resolves only the complete DAV tuple and credential while
retaining mandatory confirmation. The canonical household timezone is supplied
as shared runtime context. Secret-backed feed URLs and write credentials are
excluded from representations, and an absent optional calendar role creates no
runtime defaults.
- `home-assistant.yaml` selects the typed bridge endpoint and logical
  credentials. Its mapping registry admits only room, entity, action, camera,
  mode, and event mappings. Automation definitions reference typed event
  mappings and notification types; provider-native entity IDs remain confined
  to this adapter-owned file.

`HomeAssistantRuntimeSettings.from_effective_config` constructs the optional
applied adapter edge only when the role is enabled. It resolves the selected
provider API credential, freezes the finite mapping registry, and retains only
enabled automations bound to their exact typed event mappings. The separate
event-ingress credential is resolved only when at least one enabled automation
can consume it; disabled automation definitions do not make that credential
operational. Disabled Home Assistant selects no provider, mapping, automation,
or secret, and an absent optional role creates no runtime defaults. Both raw
credentials are excluded from representations. Provider-native IDs remain
inside these adapter-owned mapping records.
- `notifications.yaml` owns typed providers, notification types, satellite-source
  audience entries, household-mode suppression, expiry/audio policy, external
  retry policy, and logical recipient groups. Apprise destinations use either a
  credential-free endpoint or one whole-URL logical secret. V2 currently admits
  only `pause_resume` satellite audio and the implemented quiet-hours policy.

`NotificationRuntimeSettings.from_effective_config` retains only enabled
notification types as operational definitions, including their bounded
configuration-owned message, typed satellite-source audience, suppression, expiry,
audio, and external-delivery policy. External recipient groups and Apprise
providers are bound only when reached through an enabled type's enabled external
policy; dormant types, groups, and providers create neither a fallback nor a
secret requirement. Secret-backed provider URLs are resolved from the adopted
secret generation and excluded from representations. Disabled or absent
notifications create no runtime definitions.

The canonical application composition binds that frozen role to satellite-fleet
and Home Assistant runtime views through
`CanonicalNotificationExecution`. For the spoken channel, source audience
entries must resolve to enabled satellite-backed sources. User audiences are
not admitted, and associated users do not participate in targeting. Each enabled
`suppressed_by` entry requires exactly one Home Assistant `mode_state` event
mapping whose canonical subject is that household mode. Missing, disabled, or
ambiguous evidence blocks activation; unavailable provider state fails silent
at execution. Canonical submission, due-alert decisions, external receipt
processing, and sanitized diagnostics use those typed views and never consult
compatibility notification, Home Assistant, or Apprise settings getters.
- `routines.yaml` owns identity, bounded inputs, triggers, and ordered typed
  steps. Schema-v1 steps are `ui_action`, `audiobook_start`, `sleep_timer`,
  `wait`, `state_check`, `playback_check`, `notification`, and `timer_sound`.
  A timer-sound step targets one of the routine's declared canonical sources
  and queues the existing standard timer alert; it cannot select a sound asset.
  Notification IDs
  resolve to enabled definitions in `domains/notifications.yaml`; message and
  audience remain notification-owned. A step may use a bounded input condition
  (`equals`, `not_equals`, or `greater_than`) to select a path without admitting
  arbitrary expressions. Integer inputs may declare a spoken-duration prompt
  and an in-range `no_timer_value` for the exact `no timer` response. An
  opt-in `confirm_duration` flag selects the code-owned accepted-duration
  confirmation phrase without admitting a configurable reply template.
  Action/check IDs resolve through registered Oracle mappings; scripts, URLs,
  service names, raw entity IDs, commands, credentials, and provider targets
  remain invalid.

`RoutineRuntimeSettings.from_effective_config` retains only enabled composite
definitions and binds each to its enabled owning user, enabled source records,
typed steps, and exact applied capability edges. Home Assistant action/check
references bind to the adapter-owned mapping records; audiobook operations bind
to the canonical user account and admitted applied playback target; notification
steps bind to enabled notification definitions; the bounded
`stop_audiobook` remediation remains a code-known Oracle-native operation.
Global and source-scoped voice phrases are frozen into unambiguous indexes.
Disabled definitions become neither executable entries nor trigger owners.
Construction never creates or resumes a run, and an absent optional routines
role creates no defaults.
- `network/inventory.yaml` owns Oracle hosts, devices, services, service groups,
  monitors, dependencies, and power-target identity. `policy.yaml` owns bounded
  confirmation-required actions, code-known preconditions, execution/recovery
  timing, and plan-approved recovery definitions. `adapters.yaml` owns typed
  direct-probe, LibreNMS, Home Assistant power, service-control, and router-
  control translation, including any native addresses, service targets, paths,
  and logical credentials. Docker service-control adapters may additionally
  carry a bounded ordered list of lifecycle companion targets; this is fixed
  adapter translation, not Oracle service identity or executable workflow.

`NetworkInventoryRuntimeSettings.from_effective_config` constructs only the
enabled Oracle-owned topology anchor. Hosts, hosted devices/services, service
groups, monitor targets, dependency endpoints, and power-target hosts are bound
to their exact declared inventory records. Monitor and power-target adapter IDs
remain unresolved references for the separate adapter seam; construction does
not probe, interpret health, select policy, or grant control authority.
Individually disabled power targets remain declared identity but are excluded by
enabled-only lookup. Disabled or absent inventory creates no operational
topology. Device-host and dependency-endpoint references are activation-time
validation requirements.

`NetworkAdaptersRuntimeSettings.from_effective_config` constructs the finite
typed provider closure reached by enabled inventory monitors, enabled power
targets, and enabled policy actions. Service-control closure follows only its
schema-known readiness and graceful-lifecycle references, including prepare,
client-release, and storage support adapters. Direct probes remain
credential-free; LibreNMS, SSH service control, and router control resolve their
logical credentials with raw values excluded from representations. Home
Assistant power adapters bind to the enabled canonical Home Assistant runtime
edge instead of defining duplicate credentials. Dormant adapters remain
unselected. Construction performs no probe or control operation and grants no
policy authority.

`NetworkPolicyRuntimeSettings.from_effective_config` constructs only enabled
policy definitions. Every enabled action binds its exact inventory target and
already-selected typed adapter; enabled power actions require an individually
enabled power target. Every enabled recovery retains its `plan` approval mode,
diagnostic/remediation profile IDs, and enabled UI/voice trigger policy.
Normalized voice phrases resolve to their single recovery owner. Profile IDs
remain definition identity rather than implying a new executable registry.
Construction performs no control, preview, run, diagnostic, or remediation
operation. Disabled definitions and absent optional network roles create no
implicit authority.

Cross-file validation resolves playback/audience/routine source and user IDs,
household modes and rooms, Home Assistant action/event/check mappings,
notification types, network targets, adapter types, service ownership, and
global routine/recovery trigger collisions. Identity collections normalize in
stable ID order; ordered routine steps remain semantic and are never sorted.

Credential-free URL fields reject userinfo, query strings, and fragments. When
credential material is inseparable from a URL, the entire value uses the
corresponding logical secret-reference field. Unknown provider types, adapter
types, leaf fields, and execution mechanisms are schema errors rather than
readiness warnings.

## Managed Authoring Transaction

Authoring mode and workspace paths are bootstrap metadata, not schema fields.
In `managed_writable` mode, the service accepts a complete external staging
tree containing only fixed YAML roles. It validates and diffs that snapshot
against the selected immutable secret generation before any authored write.
`secrets.env`, unknown files, incomplete required roles, and safety changes
without their exact acknowledgements fail before staging.

Commit holds the installed-store lock, rechecks the expected authored revision,
durably journals prior and candidate role bytes, preserves confined existing
role symlinks, and performs per-file atomic replacement. Optional-role absence
means explicit removal. A selected new activation makes recovery finish the
candidate tree; otherwise recovery restores the complete prior tree. A
semantic no-op may update comments or formatting without installing or
selecting a runtime generation. Transaction artifacts are removed after commit
or recovery and never contain raw secret values.

`external_read_only` rejects this mutation instead of creating an override.
The host-local client now calls this service through a bootstrap-selected,
filesystem-protected Unix socket. Its finite protocol carries fixed-role UTF-8
content rather than caller-selected service-side paths and exposes status,
review, activation, managed apply, rollback, recovery, and write-only secret
mutation. Explicit offline CLI mode validates an existing store, holds the
service-presence lock for the whole command, and invokes the same service under
its ordinary transaction lock; it cannot run beside the socket authority or
initialize a store. The trusted-boundary HTTP client remains a later transport
slice.

## Brain Configuration-Service Bootstrap

Bootstrap metadata is deliberately outside every YAML schema and normalized
configuration revision. Brain recognizes exactly four host inputs for this
service: `ORACLE_CONFIG_BUNDLE_ROOT`, `ORACLE_CONFIG_STORE_ROOT`,
`ORACLE_CONFIG_SOCKET_PATH`, and `ORACLE_CONFIG_AUTHORING_MODE`. They are an
all-or-nothing set. Authoring mode is `managed_writable` or
`external_read_only`; roots must resolve to existing directories, the store
must already have a valid lineage binding, and the socket path is absolute.
Resolved bundle and store roots must be disjoint in both directions, and the
resolved socket location must remain outside the bundle root.

The independent `ORACLE_WAKE_CAPTURE_ARCHIVE_ROOT` host input selects the
deployment-owned filesystem root for accepted diagnostic wake captures. It is
storage placement, not configuration behavior: satellites cannot project or
submit it, and it does not enter the normalized revision. Canonical Brain
startup configures the upload route only when both the selected projection
resolver and this absolute archive root are available.

When the set is absent, the host-local authority is disabled. When it is
present, Brain recovers pending authoring transactions in managed mode and
pending secret transactions in both modes before starting the protected socket.
These fields select the bundle, installed store, local transport, and authoring
command mode only. They cannot supply canonical behavior, override normalized
fields, or initialize or repair a store. Canonical Brain startup uses the
selected generation to construct one immutable `EffectiveConfig`; startup never
falls back to a retired authority if bootstrap is later lost.

The installed-generation adoption loader implements the runtime boundary used
by Brain startup. It resolves `selected.json`
once, verifies the immutable activation/config/secret chain, revalidates the
fixed role inventory through the executable schemas and whole-bundle reference
laws, verifies required-secret metadata, and returns one frozen
`EffectiveConfig`. The object carries exact activation, configuration, secret,
satellite-projection-activation map, selection-operation, and selection-revision
identities. It has no authored
bundle path or reload behavior, so a later selected-pointer change cannot alter
the running snapshot. Brain startup performs this selection and injects all
typed domain dependencies before runtime work begins.

`BrainRuntimeSettings.from_effective_config` is the first Brain consumer-side
construction seam. It accepts only that immutable `EffectiveConfig`, requires
the executable typed `brain.yaml` role, carries the exact activation/config/
secret/selection identities, and retains the typed runtime, logging, and
storage leaves. Disabled speech or inference roles produce no operational
provider selection; enabled roles resolve exactly their explicit provider ID
and typed definition. The seam does not duplicate compatibility dictionaries or
consult retired sources. Canonical application composition uses it as the active Brain
process dependency.

`BrainEffectiveRuntimeSettings.from_effective_config` is the complete
construction root for the Brain's applied process snapshot. It retains that
exact immutable `EffectiveConfig`, constructs the required Brain, household,
access, satellite-fleet, and satellite-UI views, and constructs each optional domain or
network view only when its fixed role is present. Absent roles remain `None`
without defaults. Recovery-only network policy does not require an adapter role
when no active inventory or action edge references one. Construction does not
open the selected pointer, configure projection delivery, start providers,
warm processes, schedule work, or execute domain behavior. The store-backed
projection resolver remains a separate lifecycle surface because its selected
satellite activation may advance independently.

`BrainCoreRuntimeConsumers.from_runtime_settings` is the first bounded
execution adapter over that snapshot. It constructs existing disabled,
whisper.cpp, Fast-Whisper, disabled-TTS, or Piper runtime providers directly
from the selected typed definitions, and freezes Ollama request/fallback values
in `InferenceExecutionSettings`. The canonical STT and fallback-router warmup
paths accept these objects explicitly and do not call compatibility configuration
getters. Construction performs neither model loading nor provider I/O.
Canonical lifespan and request dispatch consume these objects.

The matching request-side seams are explicit:
`_synthesize_speech_with_provider` and `_transcribe_audio_with_provider` consume
the constructed speech providers, while `build_dispatch_registry` binds the
immutable inference settings into `FallbackRouterHandler`. Tests prove those
canonical paths do not call compatibility provider/settings getters. Public
route selection and the installed lifespan use these seams.

`CanonicalBrainApplicationComposition.from_startup` closes the construction
boundary without activating it. An incomplete canonical startup is rejected. A
complete result yields the exact applied
`BrainEffectiveRuntimeSettings`, `BrainCoreRuntimeConsumers`, explicitly bound
dispatch registry, and a projection resolver over the installed store. The
resolver remains outside the applied snapshot. Composition performs no route
registration, app-state mutation, socket creation, warmup, scheduling, worker
startup, provider call, or domain execution.

`install_brain_application_composition` places one typed canonical composition
in FastAPI state. The installed lifespan resolves authority once and installs
the complete composition before startup work. Voice
execution and command dispatch read that single state object and use only its
constructed providers and registry, including fail-closed disabled fallback
inference. Canonical installation supplies its store-backed resolver to the
fixed satellite projection/enrollment routes.

Canonical `/health/config` reports `configuration.mode: canonical` plus
`configuration.applied_generation`. The
applied object contains activation/config/secret generation IDs, config
revision, selection operation/revision, and the snapshot's satellite projection
activation map. Text format renders the same safe Brain identities. This is
applied-process reporting, not a fresh selected-pointer read.

## `secrets.env`

The companion format is line-oriented UTF-8 data:

```text
AUDIOBOOK_PROVIDER_RESIDENT_ONE_TOKEN=<value>
LIVING_ROOM_SATELLITE_BRAIN_TOKEN=<value>
LIVING_ROOM_CONTROL_SERVICE_TOKEN=<value>
LIVING_ROOM_ENROLLMENT_TOKEN=<value>
```

The companion is UTF-8 with one entry per physical line and splits only on the
first `=`. Keys are trimmed, unique uppercase logical IDs. The value is the exact
remainder: quotes, backslashes, `$`, spaces, and additional `=` have no special
meaning. Blank lines and lines whose first non-whitespace character is `#` are
comments.

Malformed lines, duplicate keys, `export`, continuations, and multiline values
are errors. An empty value is treated as absent and blocks activation when its
reference is required. Multiline certificates and keys require a future
schema-versioned secret backend if a real integration needs them; the parser
does not acquire shell semantics to support them.

## Normalized Generation Envelope

Installed deterministic JSON is generated, not authored. Its envelope includes
non-secret metadata and the normalized graph:

```json
{
  "format": "oracle-config-v2",
  "config_revision": "oracle-config-v2:sha256:<digest>",
  "configuration": {
    "kind": "oracle_configuration_bundle",
    "schema_version": 2,
    "bundle_id": "example-home",
    "roles": {}
  }
}
```

The digest is SHA-256 over UTF-8 RFC 8785 canonical JSON bytes for the entire
normalized `configuration` object, including kind, schema version, bundle ID,
and every role. It is not over authored YAML or secret values. Generation
envelopes, revision/generation IDs, timestamps, provenance, reports, audit, and
secret metadata remain outside the hashed payload. Golden fixtures define
cross-implementation bytes and digests.

## Selected Pointer Shape

`selected.json` is generated control state, not authored configuration. Its
versioned manifest contains `operation_id`, positive integer
`selection_revision`, `activation_generation_id`, `config_generation_id`,
`secret_generation_id`, and `satellite_projection_activation_ids`, containing
exactly one immutable activation ID per enabled projection-bearing satellite.
The first selection
uses revision 1; every subsequent pointer replacement uses exactly the previous
revision plus one. The operation ID is an opaque `selection_op_` identifier and
must exactly match the pending selection journal that authorized the write.

The journal is a strict `oracle-selection-transaction-v1` envelope. It fixes the
audit event identity/timestamp, operation and actor classes, prior and target
activation/config/secret identities and satellite-activation maps, consecutive revisions, sorted known
acknowledgements, optional candidate-report ID, and bounded secret logical-role
metadata. It never embeds an audit dictionary. Audit storage retains the opaque
event ID and maintains a unique operation-ID index so recovery cannot duplicate
the event. Recovery reloads the durable report and generations and reconstructs
the canonical audit. A pointer committed without its audit is reported as
`selection_committed_audit_pending`; malformed, conflicting, skipped-revision,
or otherwise unprovable journal/pointer state blocks recovery and later writes.

## Satellite Projection Envelope

```json
{
  "kind": "oracle-satellite-projection",
  "projection_schema_version": 1,
  "satellite_id": "living_room_satellite",
  "source_id": "living_room_voice",
  "projection_revision": "oracle-projection-v1:sha256:<digest>",
  "runtime_compatibility": {},
  "configuration": {}
}
```

The projection contains only required local behavior and logical secret roles.
Its revision covers schema version, satellite/source IDs, runtime compatibility,
and normalized configuration. Brain activation metadata is outside that hash.
A separate local activation ID pairs it with its local secret generation and
binds the pair to the exact selected Brain configuration revision.

Projection schema v1 now has an executable generator. `runtime_compatibility`
is an installation-level closed report containing platform, supported
projection-schema versions, and exactly two typed component reports.
`interaction_runtime` has its own version and reports voice capture, Brain
interaction, conversational/TTS audio, cues, input and interaction-output
adapters, wake processing, and wake-model formats. `control_service` has its own
version and reports playback-authority schema support, native music, native
audiobook, and implemented volume-control backends. Generation validates each
projected field against the component that consumes or implements it. Display
and UI capability never establish native playback compatibility.

The accepted report is durable operational state outside the bundle and
configuration revision. One report is required before a satellite's first
projection activation. Its last accepted value remains usable while the
satellite is offline and has no age-only expiry in V2. A newly enrolled runtime
or upgraded runtime must submit and validate its supported-mechanics report;
report fields never provide configuration values or precedence.

The Brain-side accepted-report store uses one strict
`oracle-satellite-runtime-compatibility-v1` envelope per satellite under the
installed store. It retains the satellite ID, acceptance time, independent
component runtime versions, and the closed compatibility report. Replacement atomically installs the newly
validated last accepted report; no age-based cleanup or history subsystem is
introduced. The host-local protocol operation
`accept_satellite_runtime_compatibility` accepts exactly `satellite_id` plus a
typed `compatibility_report`. It records a sanitized audit request before
atomic replacement and returns only bounded acceptance metadata. The CLI reads
the JSON evidence from a deployment-local file with a 64 KiB limit; the service
never opens a caller-selected path. No network submission endpoint or report-
history subsystem is introduced.

The projected `configuration` contains one common installation-level
`brain_client` block plus `interaction_runtime` and `control_service` blocks.
Every enabled satellite receives the common Brain URL and directional credential
needed for authenticated refresh; this remains outside compatibility component
counting. The interaction block receives its host-local control-service client,
conversational audio, and wake policy. The
control block receives its authentication role, typed native playback and
volume configuration, and only the selected Plex provider fragment needed by
native music. Brain-only `control_service.base_url`, UI policy, enrollment,
unrelated providers, household users/rooms, and audiobook-provider credentials
are not projected; V2 audiobook playback remains Brain-streamed. The local
secret snapshot contains only credentials referenced by the generated blocks.
Raw values never enter projection canonical bytes or revision calculation.

The transport-neutral `SatelliteProjectionResolver` loads only the globally
selected satellite activation, validates the named satellite's directional
`brain_client` credential with a constant-time comparison, and returns the
immutable installed pair with its selection identity. `resolve_pull()` renders
that result as the strict versioned envelope below:

```json
{
  "format": "oracle-satellite-projection-pull-v1",
  "satellite_id": "living_room_satellite",
  "selection": {
    "operation_id": "selection_op_<opaque>",
    "revision": 12
  },
  "activation": {
    "activation_id": "sat_activation_<opaque>",
    "source_config_revision": "oracle-config-v2:sha256:<digest>"
  },
  "projection": {
    "generation_id": "sat_projection_<opaque>",
    "payload": {}
  },
  "local_secrets": {
    "generation_id": "sat_secret_<opaque>",
    "values": {}
  }
}
```

The two nested payloads remain separate generation authorities but are resolved
and transported under one selected activation. The envelope is RFC 8785
canonical JSON when rendered as bytes. Raw values are materialized only by
explicit transport serialization and are excluded from safe representations.
The resolver and envelope do not choose an HTTP route or authentication-header
shape, record delivery/acknowledgement/applied state, perform enrollment, or
mutate selection.

The separate executable transport adapter fixes that choice as LAN-only
`GET /api/satellite/projection/{satellite_id}` with
`Authorization: Bearer <brain_client credential>`. Success returns the exact
canonical envelope bytes with `Cache-Control: no-store`. Authentication
failures are generic `401` responses; unavailable or inconsistent canonical
state is a generic `503`. The adapter is registered in the Brain API but has no
resolver until canonical Brain startup explicitly configures one, so the V1
runtime cannot serve canonical projection secrets accidentally.

`audio.interaction_output` is deliberately narrow: it selects the interaction
runtime's conversational/TTS and cue renderer. It does not select native-media
output. Media output remains deployment-owned behind the control service/player
until a reviewed cross-platform canonical mechanism exists. Speaker arbitration
does not merge those configuration ownership boundaries.

The Brain-side installed store persists the generated pair under
`projections/<satellite_id>/` using three immutable format-v1 collections:

- `oracle-satellite-projection-generation-v1` stores canonical non-secret
  projection bytes and their revision/identity metadata;
- `oracle-satellite-projection-secret-generation-v1` stores exactly the local
  logical secret values in a restricted payload; and
- `oracle-satellite-projection-activation-v1` pairs one projection generation
  with one local secret generation and one exact Brain configuration revision.

Reload recomputes the projection hash, validates canonical encoding and typed
shape, derives required logical secret references from the projection, and
requires the paired secret generation to match that set exactly. Secret-only
rotation may reuse the immutable non-secret generation while creating a new
secret generation and activation. A Brain revision change creates a lightweight
activation that may reuse both unchanged generations.

The global `oracle-selected-v1` pointer contains
`satellite_projection_activation_ids`, an exact satellite-ID-to-activation-ID
map for every enabled projection-bearing satellite. The strict selection journal
carries both previous and target maps, and recovery accepts only an exact prior
or exact committed pointer state. Each referenced satellite activation must bind
the selected Brain configuration revision. This is the desired projection
selection; V2 adds no second desired-state or delivery store here.
Authenticated pull, acknowledgement/applied state, and runtime consumption use
this selected activation map.

## Satellite-Local Projection Store

The platform-neutral local store uses three strict versioned artifacts:

- `oracle-satellite-projection-local-store-v1` binds one resolved store root to
  one `satellite_id`;
- `oracle-satellite-projection-local-activation-v1` records the Brain-issued
  activation ID, source configuration revision, projection/secret generation
  IDs, projection revision, and exact logical-secret IDs beside separate
  canonical `projection.json` and restricted `secrets.json` files; and
- `oracle-satellite-projection-local-selected-v2` atomically selects one
  activation while recording the latest global selection operation ID and
  monotonically observed selection revision. It also contains nullable
  `restart_required_activation_id`, which when present must equal the selected
  activation ID.

The local store does not mint an activation ID. Selection operation/revision is
deliberately absent from immutable activation metadata because a later Brain
rollback may select the same activation again under a newer transaction. A
lower revision, conflicting equal revision, non-canonical response, projection
digest mismatch, wrong lifecycle ID, runtime-compatibility mismatch, incomplete
or extra secret, artifact symlink, or unequal content under an existing
activation ID fails closed. The returned activation view is immutable and its
representation excludes raw values. Component-specific interpretation remains
with the interaction runtime and control service rather than being duplicated
into a second portable copy of every server leaf schema.

The implementation currently provides validation, durable installation,
atomic selection, offline selected-pair loading, and durable restart handoff.
The fixed deployment schedule and installers exist, but fleet enablement waits
for process consumption. Bounded current/previous retention and fallback,
acknowledgement, and applied-state reporting remain open.

The one-shot sync command takes only `--satellite-id`, `--store-root`, and
`--runtime-compatibility`. These select installation state and current
operational compatibility evidence; they cannot supply a Brain URL, credential,
projected value, polling interval, or restart command. Established refresh uses
the selected activation's `brain_client` block. The HTTP response must be
`application/json`, carry `Cache-Control: no-store`, and remain within the
code-owned 2 MiB bound. The code-owned request timeout is 15 seconds.

The command emits one sanitized JSON result and has fixed exit semantics:

- `0`: success with no pending restart latch, including selection-only changes
  and activations that reuse both payload generations;
- `3`: the selected projection or local-secret generation requires installed
  runtime consumers to restart; and
- `1`: transport, bootstrap, compatibility, integrity, or persistence failure.

Deployment invokes the same command with `--mark-restarted` only after all
installed runtime consumers accept restart. That operation atomically clears
the latch and is idempotent. It is retry-state maintenance, not a delivery,
acknowledgement, applied-state, or health assertion.

The Linux `oracle-satellite-projection-sync.timer` and Windows
`OracleSatelliteProjectionSync` task run after startup and every minute without
overlap. Their installers do not accept a polling interval and leave the
schedule disabled unless the operator explicitly enables it. Schedule identity,
cadence, and enablement are deployment metadata and never enter the bundle or
projection. Failure retries at the next fixed interval while preserving the
last valid selection; manual one-shot invocation is also supported.

The component adoption API returns one of two frozen views over the selected
activation:

- `InteractionRuntimeEffectiveConfig` carries projection identity, the exact
  immutable `interaction_runtime` block, the shared Brain endpoint and resolved
  credential, and the local control-service endpoint and resolved credential;
- `ControlServiceEffectiveConfig` carries projection identity, the exact
  immutable `control_service` block, its resolved inbound credential, and its
  optional resolved music-provider credential.

Neither view retains the selected activation or its complete secret snapshot.
The API fails when the requested component is absent. Interaction and
control-service entrypoint selection and field adaptation consume their views
without recreating legacy precedence.

The satellite-local `canonical-runtime-required.json` marker has exact format
`oracle-satellite-runtime-cutover-v1` and exactly four fields:

- `format`;
- `satellite_id`;
- `activation_id`; and
- `projection_revision`.

It is canonical JSON stored as a restricted regular file in the initialized
local projection store. Symlinks, extra fields, wrong identity, non-canonical
bytes, and malformed lifecycle IDs fail closed. The explicit
`oracle_satellite_runtime_cutover.py` command requires
`--acknowledge-one-way` plus at least one of
`--interaction-runtime-installed` or `--control-service-installed`; every
declared component must load before the marker is exclusively created. The
command accepts only satellite identity, store root, and compatibility-evidence
path as bootstrap selectors and never creates a missing store.

`ControlServiceSettings` is the component's frozen internal execution shape. Its
construction is canonical-only:

- `from_canonical` maps `ControlServiceEffectiveConfig` plus one
  `ControlServiceHostBootstrap` without consulting argv or environment.

The host bootstrap owns bind host/port, logging, reply-audio IPC paths, packaged
native-player resolution, and the code-owned long-form player command adapter.
The canonical snapshot owns the inbound credential, Oracle-native adapter,
provider endpoint/credential/timeout, and volume-control selection. Raw
credentials are excluded from settings representations. Retired shell/Plexamp
external command fields remain empty and disabled in canonical construction;
they are not canonicalized.

`InteractionRuntimeSettings` provides the matching frozen execution shape for
the wake/voice process. `from_canonical` accepts only one
`InteractionRuntimeEffectiveConfig` and one `InteractionRuntimeHostBootstrap`;
it maps every schema-v1 interaction audio and wake leaf, source and endpoint,
and both owned credentials without consulting retired sources. The host bootstrap
owns the diagnostic listener, logging, reply-audio IPC paths, installed logical
asset paths, a default diagnostic-capture directory when canonical configuration
does not pin one, and explicit device-list command mode. Canonical machine paths
remain canonical; logical wake/cue asset IDs must resolve through the finite
installed-asset map or startup fails. Brain and control credentials are excluded
from settings representations.

The interaction process entrypoint resolves canonical authority before parsing
behavior. It requires the existing store identity, projection-store root, and
compatibility-evidence path together, constructs the typed host bootstrap, and
selects only `from_canonical`. Known behavioral CLI/environment inputs fail startup rather than being
ignored or blended. The interaction HTTP client accepts the projected Brain
credential and sends it as a Bearer
header for every owned STT, command, TTS, arbitration, polling, activity, and
Brain-routed playback-control request. Wake-capture sync scheduling/transport remains code/deployment-owned
rather than becoming an arbitrary projected transport adapter. The standalone
helper uses the installation authority resolver and loads projected enablement,
cadence, deletion, and retention before uploading complete pairs through the
fixed Brain route. It adds no third compatibility component.

The canonical constructor also retains `satellite_id` separately from
`source_id`. The current wake-arbitration request uses `satellite_id`; source-
scoped commands, alerts, and activity retain `source_id`.

For Stage 3, credential enforcement is limited to projection pull and the fixed
wake-capture upload surface. The upload proves one selected satellite and
validates the projected source in the bounded capture sidecar; it does not make
the credential a general source assertion. The other interaction requests carry
the credential so the native client boundary is complete, but their shared Brain
endpoints do not validate it, derive a
source from it, or treat it as authorization. Browser/UI ingress and stable-
source proof must be designed together before enforcement expands; a supplied
header, claimed source, localhost, or network location is not sufficient proof.

`volume_control.type` has exactly two executable values:

- `alsa` requires non-empty `card` and `control` and invokes that mixer;
- `windows_default_endpoint` has no additional fields and controls the current
  Windows default render endpoint's scalar master volume through
  `pycaw==20251023`, installed only when `sys_platform == "win32"`.

The Windows readiness check requires the Windows platform, importable dependency,
resolvable default speakers, and a successful scalar read. Failure blocks
startup. No endpoint selection, enumeration, per-application volume, or backend
fallback is accepted.

The command records no delivery, acknowledgement, applied, or enrollment state.
With no selected local activation it produces `projection_bootstrap_required`
unless the operator supplies both `brain_bootstrap_url` and an absolute
restricted enrollment-credential file path. That disjoint one-shot mode calls
the dedicated enrollment route and installs the returned ordinary envelope.
The timer supplies neither input, and established refresh rejects enrollment as
a replacement for its projected operational edge.

## Candidate Report Identity

Every service review persists an `oracle-candidate-report-v1` document with
`validation_version: oracle-configuration-validation-v1`, candidate ID,
authored revision, normalized candidate revision when available, all finding
categories, semantic changes, required acknowledgements, and the exact selected
baseline. A non-null baseline contains activation/config/secret generation IDs,
the satellite projection activation map, config revision, selection operation
ID, and selection revision. Transition
context is retained separately because it identifies the baseline used by that
validation phase.

Every review invocation also creates its own `review_candidate` audit event with
sanitized actor and `reviewed` or `review_blocked` outcome. Reports may be
deduplicated only by all complete validation inputs; audit requests are never
deduplicated. Review does not create a selection operation ID or transaction
journal.

## Transition Validation Shape

Candidate inspection reports persist `transition_blockers` independently from
standalone `validation_findings`, secret/readiness `activation_blockers`, and
`readiness_findings`. Each transition blocker remains category `activation` and
blocks installation and selection. `transition_validation_context` contains the
exact baseline `activation_generation_id`, `config_generation_id`,
`config_revision`, `selection_operation_id`, and `selection_revision`.

The phase compares the selected normalized graph with the candidate normalized
graph. It blocks direct removal or rekeying of an enabled user, room, source, or
satellite; direct removal of an enabled ordinary optional role; removal of an
information file containing any enabled capability; and removal of configured
network roles while the previously selected inventory anchor is enabled. The
candidate may first select those entries disabled, but removal becomes eligible
only from a later generation where that disabled state is already selected.
Rollback uses the same comparison in reverse historical direction. Removal
acknowledgements remain required after, and only after, this phase permits the
transition.

## Validation Finding Shape

Every finding has stable machine-readable fields:

```json
{
  "code": "config.reference.unknown_user",
  "severity": "error",
  "blocks_activation": true,
  "file_role": "household",
  "path": "sources[0].associated_user_id",
  "message": "Associated user does not exist",
  "owner": "household"
}
```

Activation blockers that are not structural validation errors use their own
codes and category. Operational-readiness findings are reported separately.
Secret fields report only presence and logical usage.

Required references from enabled configuration block activation when absent.
References used only by disabled configuration may be absent. Unreferenced
secret IDs are non-blocking hygiene warnings, not implicit configuration.

## Provenance And Diff

Provenance records file role and relative path plus one of `authored`,
`defaulted`, `derived`, or `projected`. It is report metadata rather than hash
input. Semantic diffs compare normalized typed values and classify restart and
safety impact. Access weakening, identity removal, credential-role change, and
mutating-control enablement require explicit acknowledgement.

## Migration Mapping Requirements

A supported canonical schema migration records source and destination schema
identities, deterministic transforms, secret handling, validation evidence,
compatibility and rollback requirements, and any explicitly retired fields.
