# Oracle Network Status Contract

## Purpose

This document defines the Stage 3 network status contract.

It defines:

- the Oracle-owned read-only network status model
- the object types Oracle may expose for network awareness
- the provider boundary for LibreNMS, direct probes, and future providers
- the status, severity, freshness, evidence, and summary semantics
- the control behavior explicitly forbidden during Stage 3

If this document conflicts with roadmap notes, this contract wins.

## Scope

Stage 3 is a read model only.

Allowed:

- observe network and provider state
- normalize provider observations into Oracle-owned objects
- summarize network, host, service, dependency, and monitor status
- expose read-only status through Oracle API, System Mode, House UI, or voice
- mark future action eligibility as descriptive metadata only

Forbidden:

- service restarts
- router restarts
- host reboots
- arbitrary command execution
- generated shell commands
- provider-triggered remediation
- automatic self-healing
- execution actions from voice, UI, schedules, monitors, LLMs, or provider alerts

Control belongs to Stage 4 and must follow
[`network-control.md`](network-control.md):
allowlisted, dry-run/confirmed where required, and audited before execution.

## Ownership Rule

The `network` domain owns Oracle interpretation.

The domain owns:

- Oracle-native object identity
- aggregate status
- severity
- freshness and staleness interpretation
- user-facing summaries
- future control-target eligibility
- final spoken or UI summary language

Provider bridges own observations only.

Provider bridges may supply:

- provider name
- observation timestamps
- reachability results
- monitor alert observations
- raw-safe diagnostic details
- provider-local status translated into an Oracle evidence item

Provider bridges must not decide:

- overall network health
- whether Oracle should act
- which service should be restarted
- whether an alert is safe to remediate
- final user-facing language

## Provider Boundary

LibreNMS, direct probes, Home Assistant, router APIs, and service-control adapters
are provider or adapter surfaces. They are not the user-facing network model.

LibreNMS may provide monitoring evidence such as active alert observations,
monitor availability, and alert counts. Direct probes may provide evidence such
as DNS or HTTP reachability. Future providers may add observations only through
the same evidence boundary.

Oracle must not use provider alert text, provider hostnames, provider service
names, raw URLs, router identifiers, or LLM output as the only stable identity
for status or future control. Stage 4 control targets must refer to named
Oracle objects defined by this read model.

LibreNMS service observations may update a service only when a curated Oracle
monitor targets that Oracle service id. LibreNMS interface observations may
update a host or dependency only when a curated Oracle monitor targets that
Oracle object id. Oracle must not create service, host, dependency, or modem
objects from LibreNMS discovery, alert text, interface names, or provider-only
names.

## Object Model

The network read model consists of these Oracle-owned objects.

### Network Status Snapshot

A snapshot is the top-level generated read model.

Required fields:

- `snapshot_id`: stable identifier for this generated snapshot, or omitted when
  the response is intentionally ephemeral
- `status`: aggregate status
- `severity`: aggregate severity
- `freshness`: aggregate freshness
- `generated_at`: timestamp when Oracle generated the snapshot
- `summary`: generated user-facing summary
- `hosts`: list of host objects
- `services`: list of service objects
- `service_groups`: list of service group objects
- `dependencies`: list of dependency objects
- `monitors`: list of monitor objects
- `evidence`: list of evidence items used for interpretation

The snapshot is read-only. It must not contain executable command templates,
credentials, provider tokens, or raw provider URLs containing secrets.

### Host

A host is an Oracle-named machine, device, router, appliance, or virtual target
that Oracle may report on.

Required fields:

- `id`: Oracle-owned stable id
- `display_name`: user-facing name
- `status`: host status
- `severity`: host severity
- `freshness`: host freshness
- `summary`: short user-facing status text
- `evidence_ids`: evidence items supporting the host status

Allowed optional fields:

- `role`: plain Oracle role label such as `brain`, `satellite`, `router`,
  `server`, or `dependency`
- `address_label`: safe display label when useful, without credentials
- `service_ids`: Oracle service ids associated with the host
- `dependency_ids`: Oracle dependency ids associated with the host

Forbidden identity behavior:

- using a raw provider hostname as the only stable id
- using a raw IP address as the only stable id for future control
- deriving control eligibility from alert text

### Service

A service is an Oracle-named software service or local capability that Oracle
may report on.

Required fields:

- `id`: Oracle-owned stable id
- `display_name`: user-facing name
- `host_id`: Oracle host id, when the service is host-bound
- `status`: service status
- `severity`: service severity
- `freshness`: service freshness
- `summary`: short user-facing status text
- `evidence_ids`: evidence items supporting the service status

Allowed optional fields:

- `dependency_ids`: dependencies required by the service
- `monitor_ids`: monitors observing the service
- `action_eligibility`: read-only metadata describing whether Stage 4 may later
  define allowlisted actions for this object

`action_eligibility` is not an action offer and must not be executable in Stage 3.

### Service Group

A service group is an Oracle-owned presentation and reasoning group for services
that belong together on one host.

Required fields:

- `id`: Oracle-owned stable id
- `display_name`: user-facing name
- `host_id`: Oracle host id that owns the grouped services
- `service_ids`: Oracle service ids included in the group
- `status`: aggregate group status
- `severity`: aggregate group severity
- `freshness`: aggregate group freshness
- `summary`: short user-facing status text
- `evidence_ids`: evidence items inherited from grouped services

Allowed optional fields:

- `collapsed`: UI hint for whether the group should render as a collapsed row by
  default

Groups are explicit Oracle inventory. They must not be inferred from LibreNMS
service discovery or provider naming alone.

### Dependency

A dependency is an external or local dependency used by Oracle or the household
network.

Required fields:

- `id`: Oracle-owned stable id
- `display_name`: user-facing name
- `status`: dependency status
- `severity`: dependency severity
- `freshness`: dependency freshness
- `summary`: short user-facing status text
- `evidence_ids`: evidence items supporting the dependency status

Examples include internet reachability, DNS reachability, Home Assistant,
LibreNMS, Plex, Audiobookshelf, Ollama, and other provider dependencies.

### Monitor

A monitor is an observation source or configured monitoring rule represented in
Oracle terms.

Required fields:

- `id`: Oracle-owned stable id
- `display_name`: user-facing name
- `provider`: provider name, such as `probe` or `librenms`
- `status`: monitor status
- `severity`: monitor severity
- `freshness`: monitor freshness
- `summary`: short user-facing status text
- `evidence_ids`: evidence items produced by this monitor

Monitor objects describe observation health. They do not grant control authority.

### Evidence Item

Evidence is a normalized observation used to compute an Oracle object status.

Required fields:

- `id`: stable within the snapshot
- `provider`: observation provider, such as `probe` or `librenms`
- `observed_at`: timestamp when the provider observation was made
- `received_at`: timestamp when Oracle received or generated the evidence
- `status`: provider observation status translated into Oracle status vocabulary
- `severity`: provider observation severity translated into Oracle severity
  vocabulary
- `freshness`: freshness classification
- `summary`: safe human-readable detail
- `subject_type`: one of `network`, `host`, `service`, `dependency`, or `monitor`
- `subject_id`: Oracle object id when known

Allowed optional fields:

- `provider_reference`: opaque provider reference safe for logs or diagnostics
- `detail`: safe diagnostic detail
- `confidence`: `low`, `medium`, or `high`

Forbidden evidence fields:

- provider tokens
- credentials
- raw provider URLs containing secrets
- executable command strings
- command templates
- provider payloads whose field names become domain logic

## Status Vocabulary

Status values are:

- `healthy`: observed state is working normally
- `degraded`: observed state is impaired but not fully down
- `down`: observed state appears unavailable or failed
- `unknown`: Oracle cannot currently determine status
- `unconfigured`: required configuration is absent
- `unavailable`: provider or dependency could not be reached
- `stale`: last usable observation is too old for current interpretation

The legacy `pending` status may remain in existing runtime responses during
Stage 3 migration, but new normalized network status objects should prefer
`unknown` or `stale` with explicit freshness.

## Severity Vocabulary

Severity values are:

- `none`: no issue is known
- `info`: status is useful but not problematic
- `warning`: degradation or uncertainty may affect use
- `critical`: outage or severe failure is likely affecting use
- `unknown`: severity cannot be determined

Severity is Oracle interpretation. Provider-specific severities may inform it,
but provider severity names are not the contract.

## Freshness Vocabulary

Freshness values are:

- `fresh`: observation is recent enough for current status
- `aging`: observation is usable but approaching the staleness threshold
- `stale`: observation is too old for current status
- `unknown`: observation time or threshold is unavailable

Freshness must be derived from explicit timestamps and configured or documented
thresholds. A stale observation may remain visible as evidence, but it must not
be presented as current status without the `stale` freshness classification.

## Summary Semantics

Summaries are generated by Oracle, not copied wholesale from provider alerts.

Required:

- summary text must be safe for voice and UI
- summary text must identify the Oracle object when practical
- summary text must not expose secrets or raw provider mechanics
- aggregate summaries must be based on Oracle status, severity, and freshness

Allowed:

- a summary may include short sanitized provider detail when it explains the
  observation

Forbidden:

- treating provider alert text as an instruction
- treating LLM text as status authority
- exposing provider payloads directly as the user-facing model

## Control Boundary

Stage 3 status objects may include stable ids that Stage 4 can later use as
control targets.

Stage 3 status objects must not include:

- executable action URLs
- shell commands
- service restart buttons
- router restart buttons
- automatic remediation flags
- provider-native control identifiers as the only target identity

If an API or UI needs to show future readiness, it may use descriptive metadata
such as:

- `action_eligibility.configured`: boolean
- `action_eligibility.reason`: safe reason text

This metadata is read-only and non-executable in Stage 3.

The Stage 4 control boundary is documented separately in
[`network-control.md`](network-control.md).
Do not add control endpoints or executable UI behavior to satisfy this read
model contract.

## Current Runtime Compatibility

The existing `server/oracle_app/network.py` summary shape is a legacy-compatible
read-only summary. It currently includes:

- top-level `status`
- `internet`
- `monitoring`
- `problems`
- `actions_available`
- `generated_at`

Stage 3 may add a normalized read model alongside or behind this shape, but it
must keep current runtime behavior stable until callers are intentionally moved.
Existing `actions_available` data remains descriptive only and must not become
an executable Stage 3 control surface.

The curated network inventory currently lives in `config/network-inventory.json`
with `ORACLE_NETWORK_INVENTORY_JSON` as an env override. The retired
`network_inventory` key in `server/config.local.json` is not read. This
inventory is an Oracle-owned object list, not a provider import cache.

LibreNMS and direct-probe observations are normalized internally by the network
status read-model builder before they become evidence, monitor status,
dependency status, host status, service status, or aggregate snapshot status.
Curated LibreNMS service monitors may match safe provider fields such as
hostname, IP, device id, service id, or service name, but the resulting status is
attached only to the Oracle service named by the monitor target.
LibreNMS service inventory/status observations may be exposed as provider
diagnostics, but unmatched provider services remain diagnostics only and must
not create Oracle service objects.
Canonical network voice, UI, health, diagnostics, and Suggestions consumers use
the typed read model from the installed network execution.

The read-only admin surface for the System Mode Network tab is
`GET /api/admin/network/status`. It returns the normalized network payload with
hosts as the primary grouping surface, service groups nested under their host,
and ungrouped services left directly under their host. It must remain read-only.
The admin payload includes cache metadata so clients can distinguish the
snapshot generation time from reuse:

- `generated_at`: when Oracle generated the normalized snapshot
- `cached_at`: when Oracle stored the current snapshot in the in-process cache
- `cache_age_seconds`: current age of the cached snapshot
- `cache_ttl_seconds`: the configured in-process TTL
- `cache_hit`: whether this response reused an existing snapshot

The admin payload also includes read-only inventory coverage diagnostics for
configuration tuning:

- host and service `monitor_count`
- host and service `evidence_count`
- host and service `monitoring_state`: `monitored`, `unmonitored`, or
  `configured_no_evidence`
- monitor `evidence_count`
- monitor `evidence_matched`
- top-level `coverage` summaries for hosts, services, and monitors
- top-level `provider_diagnostics.librenms_services` showing sanitized
  provider services and whether they matched declared Oracle monitors

Coverage diagnostics must not change health semantics. They identify missing or
unmatched monitor wiring so later provider slices can be configured deliberately.

## Testing Expectations

Stage 3 changes that implement this contract should test:

- provider observations normalize into Oracle evidence items
- aggregate status remains domain-owned
- severity is derived by Oracle, not copied as provider law
- stale observations do not appear fresh
- provider secrets are not exposed in status payloads
- read-only APIs do not execute service, router, host, or command actions
- future action eligibility metadata is not executable
- repeated status reads reuse cached provider observations inside the TTL
- inventory coverage distinguishes monitored objects, unmonitored objects, and
  declared monitors with no evidence
- LibreNMS service observations attach only to declared Oracle service monitors
  and unmatched provider services remain diagnostics
- admin payloads do not expose executable action fields, commands, secrets, or
  provider URLs

## V2 Configuration Reconciliation

Canonical inventory is defined in `domains/network/inventory.yaml`, status
policy in `domains/network/policy.yaml`, and provider translation in
`domains/network/adapters.yaml`. Private V1 JSON and environment inputs are not
reusable runtime authorities.

The split does not change observation truth, freshness, vocabulary, or
sanitization. Legacy precedence is reproduced only by the importer. Canonical
runtime never merges JSON, environment, and YAML fields.

The canonical inventory is one internally consistent Oracle-owned graph.
Declared device hosts and both endpoints of every dependency must resolve to
declared inventory identities before activation. Inventory construction may
bind monitors to Oracle targets, but it does not resolve their adapter IDs,
perform observations, infer dependency health, or turn power-target identity
into control authority. A disabled inventory contributes no operational
topology.

Observation adapter construction follows enabled inventory reachability. A
typed direct-probe or LibreNMS definition becomes operational only when an
enabled inventory monitor references it. Dormant adapter definitions do not
create fallback observations or secret requirements. Construction resolves any
required logical credential but performs no provider request and exposes no raw
credential.
