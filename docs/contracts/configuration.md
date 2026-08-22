# Oracle V2 Configuration Contract

## Purpose

This contract defines the sole configuration authority for an Oracle V2
deployment. It governs authored configuration, secrets, validation, activation,
runtime adoption, migration, rollback, projections, and configuration clients.

## Authority

Oracle V2 has one fixed, versioned configuration bundle. The selected canonical
generation is the only configuration authority. Physical files divide ownership
and operator concerns; they do not create separate authorities.

Once a process adopts canonical mode:

- environment variables, legacy JSON files, and ordinary CLI flags cannot
  override canonical fields;
- legacy inputs that attempt to define canonical fields are errors;
- bootstrap inputs may select the bundle, runtime instance, command mode, or
  satellite identity, may carry narrowly scoped enrollment authentication, and
  on a satellite may supply the initial projection-service rendezvous URL;
- bootstrap inputs do not configure Oracle behavior; and
- Oracle never blends canonical and legacy values field by field.

Canonical launch performs this rejection before constructing runtime state.
Every populated mapped or retired V1 behavioral input is a startup error;
malformed and unclassified attempts fail closed. Only inputs whose executable
disposition is `bootstrap_not_behavior` may remain. Rejection diagnostics name
the runtime, setting, source kind, locator, and disposition but never the raw
value. Migration tooling may still resolve the same inputs while constructing
and comparing a candidate because it is not canonical runtime authority.

Brain runtime instance identity is bootstrap/deployment metadata. It may scope
locks, applied-generation records, logs, and health, but is not a behavioral
field or profile selector. V2 has one Brain behavior configuration per bundle.

Bootstrap resolves the authored bundle root, installed store root, and local
socket parent before recovery. The authored and installed roots are disjoint:
neither may equal or contain the other after symlink/mount resolution. The live
configuration socket is never created beneath the authored bundle. These rules
preserve packaging, mount, and symlink flexibility while keeping runtime,
transaction, audit, lock, and generation artifacts outside authored input.

Historical precedence is implemented only by migration tooling. An importer may
read legacy CLI, environment, and file inputs using their historical precedence,
produce a candidate bundle and secret candidate, report conflicts and source
mappings, and compare effective behavior before complete cutover.

## Fixed Bundle

The required top-level authored roles are:

- `bundle.yaml`;
- `brain.yaml`;
- `access.yaml`;
- `household.yaml`; and
- `satellites.yaml`.

`brain.yaml` has only fixed `runtime`, `logging`, `storage`, `speech`, and
`inference` sections. It has no generic catch-all or universal provider section.
Listener/trust settings belong to access; household semantics to household;
domain provider selection/policy to domains; instance/store/socket/authoring
locations to bootstrap deployment metadata.

All five Brain sections are present. Within them, `speech.stt`, `speech.tts`,
and `inference.shared_backend` declare `enabled` independently. There is no
broad `speech.enabled` or `inference.enabled` parent switch. An enabled role
selects its provider explicitly; a disabled role may omit operational provider
details.

Oracle defines every optional domain role. Deployments cannot add includes,
wildcard-loaded directories, arbitrary file roles, overlays, host inheritance,
YAML merges, override chains, or custom precedence systems.

A separate file earns a role only when it has a distinct owner, a clear operator
mental model, and meaningful size, risk, or lifecycle. Small deterministic
utilities do not earn dedicated files.

V2 has no `domains/alerts.yaml`. Timer, alarm, and reminder records are
operational state, while interpretation remains code- and contract-owned.
Brain persistence mechanics belong to `brain.yaml:storage.memory`, including
the configurable 90-day terminal alert horizon; satellite claim,
cue assets, and local playback behavior enter the satellite projection; and
notification delivery/suppression policy remains in
`domains/notifications.yaml`. A future alert role requires evidence of
substantial operator-owned policy plus schema review. The retired
`storage.alerts` JSON declaration is rejected.

V2 also has no `domains/playback.yaml`. Shared pause, resume, interruption, and
authority behavior remains code- and contract-owned. Music and audiobook policy
stay in their domain roles; target capabilities, local adapters, cue handling,
and timing stay with satellite configuration and projections; and active
playback/session truth remains operational state. A later playback role requires
substantial operator-owned policy that cannot cleanly belong to a media domain
or satellite.

The reusable core publishes one complete generic example bundle under
`examples/config/`, mirroring canonical relative paths. Example YAML never sits
inside a live bundle root. Examples contain no household-specific identities,
hosts, paths, credentials, or implicitly enabled unsafe behavior.

## Schema And Syntax

The whole authored bundle has one integer `schema_version`. Domain files do not
evolve independently. Oracle versions declare the bundle-schema versions they
can validate and activate.

Stage 5 advances the canonical bundle to `schema_version: 2` and removes the
dormant Memory fields from schema 1 without a dual-schema runtime. This counter
is independent of Oracle product/roadmap versioning. Canonical serialization is
`oracle-config-v2`, and the independently versioned satellite projection
format begins at `projection_schema_version: 1`.

`bundle_id` is the stable configuration-lineage identity bound to an installed
runtime store at initialization. Ordinary activation, migration, and rollback
must preserve it. A different bundle ID blocks activation unless a host-local,
explicitly acknowledged reinitialize/import operation changes the binding and
records an audit event.

Legacy candidate construction requires an explicit `bundle_id`; it never
derives lineage from household identity, a hostname, filesystem path, user, or
runtime observation. For example, a deployment may use bundle ID
`example_home_configuration` independently of household ID `example_home`.

Migration is explicit and staged one version at a time. Startup never rewrites
authored configuration. A migration produces a reviewable candidate and semantic
diff before activation.

Authored files use a restricted YAML 1.2 subset: UTF-8, one document, mappings,
lists, strings, booleans, nulls, ordinary bounded numbers, and comments. Tags,
anchors, aliases, merge keys, duplicate keys, timestamp coercion, binary values,
templates, executable interpolation, and includes are forbidden.

Scalar parsing is explicit: only `true`/`false` are booleans; only `null` is
null; numbers use finite JSON-style decimal integer/float syntax. Hex, octal,
binary, sexagesimal, infinities, NaN, leading `+`, and implicit timestamps are
forbidden. Dates, times, versions, IP addresses, and IDs remain strings unless a
field schema explicitly says otherwise.

One pinned restricted round-trip parser serves validation and managed editing.
It validates the YAML node graph before primitive conversion and preserves
comments/stable formatting where possible. Comment preservation is authoring
quality, not semantic authority, and never affects canonical JSON or hashes.

Unknown fields and unknown configuration-role files are errors. Schema defaults
must be safe, portable, and restrictive. Defaults cannot invent identities,
providers, hosts, secrets, mutating controls, or trust boundaries.

The loader reads only exact registered paths. Unknown `.yaml` or `.yml` files
inside the bundle root are errors even though they are never loaded. Backups,
editor snapshots, migration output, and transaction files live outside the
root. `secrets.env` is the only recognized in-root non-YAML companion. Other
non-configuration files are non-authoritative and may be reported, but never
affect loading or hashing.

The configured bundle root may be a symlink or mount and is resolved once as the
trusted logical root. Schema-known role links are accepted only when their final
regular-file targets remain within that resolved tree. Broken links, cycles,
device files, traversal, and outside targets are invalid. The engine snapshots
resolved known files before parsing so later link or mount changes cannot alter
what was validated. Implementation-owned installed pointers likewise remain
confined to the installed store.

The portable selected pointer is a small versioned manifest naming the Brain
activation, expected config and secret-generation IDs, and a
`satellite_projection_activation_ids` map containing exactly one immutable
activation for every enabled projection-bearing satellite. It also carries the
selection operation ID and a store-local monotonically increasing selection
revision. All immutable
generation content is persisted before an atomic platform replace of that
manifest. Startup verifies the manifest and referenced integrity. Filesystem
symlinks are optional implementation details, not a cross-platform requirement.

## Enablement

File presence never enables behavior.

- A missing optional domain role means unconfigured.
- A present domain declares `enabled` explicitly.
- `information.yaml` has independently enabled fixed capability sections.
- Network enablement is anchored by `network/inventory.yaml`; policy and adapter
  roles cannot exist without it.
- Users, rooms, sources, and satellites declare `enabled` explicitly.

An enabled optional domain cannot transition directly to a missing file.
Retirement first activates `enabled: false`, confirms adoption, then removes the
role in a later candidate. Information sections follow this independently;
network disables through its inventory anchor. Initial absence remains valid.

Disabled configuration remains subject to structural schemas, known-field and
identifier rules, reference validity, and every safety prohibition. Requirements
needed only for operation may remain incomplete until enabled.

Users, rooms, sources, and satellites retire in two steps: disable, then remove.
Removal is blocked while enabled configuration references the ID and requires a
destructive-change acknowledgement. Historical state may retain the typed ID
without blocking removal. Removal never deletes history, revokes secrets, or
decommissions hardware automatically.

Retirement enforcement is a formally separate cross-generation transition-
validation phase. Its activation blockers are persisted in the candidate
inspection report together with the exact selected activation, config
generation/revision, selection operation, and selection revision used as the
baseline. The phase is repeated against that exact selection immediately before
activation; a changed selection requires a fresh review and cannot reuse the
prior result. Rollback is subject to the same transition laws.

The disabling state must exist in the previously selected generation. Disabling
and removing within one candidate does not satisfy retirement. Rename or rekey
is removal plus addition unless a future schema defines and proves stable
identity continuity. Destructive removal acknowledgement remains additionally
required after lawful retirement and can never waive a transition blocker.

## Identity And References

Canonical configuration uses typed Oracle-native IDs. Provider-native IDs may
appear only at mapping or adapter edges. Cross-file references are schema-typed
and validated over the whole bundle.

Canonical IDs are lowercase ASCII, begin with a letter, use only letters,
digits, `_`, and `-`, and have no consecutive or trailing separators. They are
unique within their typed namespace. Display names and aliases remain Unicode.
ID rename is a semantic remove/add operation with reference and state-impact
checks rather than an in-place cosmetic edit.

IDs and aliases are collision-checked within their typed resolution namespace,
including disabled entries. Cross-namespace phrase reuse is allowed when
context disambiguates it; overlapping command grammars produce warnings, and
safety-critical ambiguity clarifies or fails instead of selecting by
precedence.

`household.yaml` owns household identity, semantic timezone, locale, one
optional home-location object, users, rooms, aliases, household defaults, modes,
optional ordered UI escape-hatch links, and
stable request sources. Host operating-system timezone is deployment state and
cannot override household semantics. Canonical source association fields are:

- `associated_user_id`;
- `associated_room_id`; and
- `fixed`.

Household timezone and locale are required authored values. Timezone uses an
IANA identifier and locale uses a supported BCP 47 tag. Host OS, environment,
browser, and inferred geography cannot default or override either value.
Domain-specific provider/account timezone fields are allowed only at their
owning edge and do not replace household semantic time.

Household modes are definitions: IDs, names, aliases, and Oracle policy
semantics. Provider mappings belong to the owning domain, and current mode
values remain operational/provider state. Activation and startup never set,
reset, or toggle a household mode.

Oracle has no household-wide default room for mutating resolution. Room entries
own identity, display, and aliases; domain-specific room policy stays with its
domain. A room-required request resolves only through explicit wording, valid
weak session context, or an authenticated fixed source association, then
clarifies or fails safely.

`associated_room_id` is permitted only when `fixed: true`. Association supplies
context; it does not authenticate a source, authenticate a user, or grant
authorization.

Oracle users are personalization and capability identities, never security
principals. V2 does not introduce Oracle-owned accounts, roles, or per-user
permissions.

Every canonical request has a non-null internal `request_source_id`, serialized
as `source_id`, established by its ingress adapter. A stable source must be
configured and authenticated. Approved ingress may assign an unassociated
ephemeral source identity. A client-supplied string alone never proves
stable-source identity. The current serialized `source` field is a bounded
Stage 3 compatibility alias for deployed clients and is removed after their
characterized fleet/client cutover.

`household.yaml` defines what a stable source means and owns its contextual
associations. `access.yaml` proves which authenticated non-satellite ingress may
act as that source. Browser, mobile-application, desktop-application, and kiosk
stable sources require an explicit access binding. Each binding references
exactly one enabled, type-compatible household source. Binding schemas are a
finite set of mechanism-specific structures; an untyped generic binding object
is forbidden, and adding a mechanism requires schema and contract review.

Satellites do not duplicate this binding. They authenticate with their
satellite-specific credentials, and `satellites.yaml` maps the authenticated
satellite identity to its `source_id`. Unbound non-satellite ingress receives a
server-controlled ephemeral source identity. An ephemeral source cannot claim
stable-source associations. Associated users and rooms never participate in
authentication.

Canonical context-bearing HTTP ingress never treats the compatibility
`source` payload field as proof. For satellites, that field may select one
enabled applied satellite candidate, but Oracle must authenticate the
credential against the exact satellite activation bound to the Brain's
immutable applied configuration before assigning its canonical source. A
non-satellite credential resolves its source directly through the active
`access.yaml` binding. A presented but invalid credential fails with
authentication error and cannot fall back to ephemeral access.

An uncredentialed trusted-boundary or browser request receives a
server-controlled ephemeral source and cannot use a payload source's
associations. When it supplies no session identifier, Oracle creates a fresh
server-generated session rather than sharing source-wide fallback context.
Internal callers that do not pass through stable-source authentication likewise
receive an unassociated internal ephemeral source. These rules add no role,
permission, account, enrollment, or generalized identity subsystem.

Authentication credentials remain logical secret references. V2's only
non-satellite stable-source mechanism is `credential_bindings`, using one unique
high-entropy credential scoped to one stable source.

A browser credential binding is valid only through a supported protected
transport such as an HTTP-only cookie; scripts and browser local storage cannot
hold it. Generic mechanism discriminators, arbitrary identity headers, shared
fleet credentials, and implicit authentication fallback are forbidden.

V2 accepts only one credential value per stable non-satellite source at a time.
Replacing its logical secret creates a new secret generation; activation
atomically begins accepting the new value and revokes the old value. Oracle does
not automatically restore or overlap the old credential. Rotation may
temporarily disconnect the source, so the operator provisions the replacement
before or alongside activation. Disabling the source blocks authentication
immediately, while deleting its credential remains a separate explicit secret
operation.

Credential overlap, self-service enrollment, automatic credential delivery,
and per-device rotation windows require a later reviewed lifecycle design.
V2 does not acquire a hidden non-satellite enrollment or
credential-distribution system.

Authentication binding cardinality is one-to-one. Every enabled non-satellite
stable source has exactly one credential binding. A disabled source may retain
one inactive binding.

Each credential logical secret reference belongs to at most one source binding.
Satellites remain outside this cardinality model because their authenticated
satellite record supplies their request-source identity.

Source-binding validation does not compare raw credential values. Oracle checks
that each logical credential reference belongs to at most one enabled source
binding, but it does not compare, fingerprint, hash, or persist credential-value
equality for this purpose. Two logical secret IDs containing the same raw value
are operator error outside V2 enforcement. Stronger value-uniqueness guarantees
require a later reviewed access/security design rather than hidden secret-store
analysis.

`boundary_id` references a typed, enabled trusted-boundary entry in
`access.yaml`. A trusted boundary may authorize operator or System Mode access,
but V2 does not use it to assert a stable household source. Trusted-boundary
requests therefore receive a server-controlled ephemeral source identity.
Localhost, VPN access, network location, an SSH tunnel, user identity, and
arbitrary headers cannot substitute for stable-source proof.

Trusted-boundary stable-source binding is documented only as a future
extension. It requires a concrete installation-level proof mechanism plus
schema and contract review. Oracle cannot accept configuration for a proof
mechanism it does not implement.

Internal origins such as Brain, system, API, UI, or background workers are
actors, not household request sources.

Initial stable source types are `satellite`, `mobile_app`, `browser`,
`desktop_app`, and `kiosk`. Type describes ingress/device class and grants no
trust, capability, or authorization. Provider callbacks/internal workers remain
actors. Ephemeral sources need no configured catch-all type; new stable ingress
classes require a schema extension.

Caller-supplied `client_id` is not a canonical identity. V2 uses authenticated
`source_id`, bounded `ui_session_id` for temporary UI state, operation-specific
idempotency IDs, and separate trusted actor metadata. The current `client_id`
field is a Stage 3 compatibility alias for deployed UI consumers and cannot
establish source association, authorization, or audit identity.

## User And Room Context

User resolution order is:

1. explicit current-request user;
2. active session user;
3. authenticated stable source's associated user;
4. enabled household default user; and
5. safe failure.

Room resolution is branch-aware:

1. explicit current-request room always wins;
2. explicitly local or deictic wording may use an authenticated fixed source's
   associated room and must not borrow unrelated session room context;
3. a valid weak session room may resolve an ordinary non-deictic follow-up;
4. an ordinary non-deictic room-required request may fall back to the fixed
   source association after session context; and
5. unresolved room-required behavior clarifies or fails safely.

Legacy `default_user_id`, `default_room`, `source_default_user`, and equivalent
terms map into this association model during migration. They are not canonical
runtime vocabulary.

## Access Boundary

`access.yaml` declares Oracle's expected ingress and trust boundary. It owns
schema-known ingress modes, trusted proxy expectations, accepted proof, exposure
policy, public-health policy, operator configuration access, and fleet-wide
enrollment/authentication policy. It also owns finite mechanism-specific
authentication bindings for non-satellite stable sources.

Initial operator modes are `host_local_only` and `trusted_boundary`.
`host_local_only` disables browser mutation. `trusted_boundary` permits browser
mutation only with configured authentication and CSRF-resistant proof; read-only
inspection is separately configurable. Host-local recovery remains available in
both modes. New authentication mechanisms require a schema/contract change.

V2 permits at most one enabled trusted boundary. It is one optional typed object
with a stable `boundary_id` and a code-known boundary type.
`operator_access.boundary_id` must reference that object when operator mode is
`trusted_boundary`; host-local CLI access remains independent. Supporting
multiple simultaneous remote boundaries requires later schema review.

It does not configure external gateways, identity providers, VPNs, tunnels,
firewalls, or proxy-native behavior. Those remain deployment-owned. Operational
readiness may detect mismatch without making the bundle invalid.

Browser/System Mode mutations must cross the configured trusted authentication
boundary. Host-local CLI mutation uses a host-local process boundary. No
unauthenticated configuration mutation is exposed on the ordinary LAN API, and
no user or source ID is itself authorization.

The initial host-local boundary is a Unix-domain socket protected by filesystem
ownership/mode. It calls the same configuration service as HTTP and requires no
shared operator token. When the service is stopped, direct CLI engine use
requires the exclusive installed-store lock. Socket/store paths are bootstrap
deployment settings.

Browser mutation also requires CSRF-resistant request proof supplied by the
supported boundary or a bounded Oracle mechanism. Authentication alone is not
sufficient. If the declared boundary cannot supply the required proof, browser
configuration mutation is disabled while inspection and host-local recovery
remain available.

Access changes are restart-required by default. Trust expansion, weaker
authentication, additional exposure, or public-health enablement requires an
explicit safety acknowledgement. The host-local CLI is the recovery path for a
misconfigured browser boundary.

The current access schema owns a finite semantic safety classifier rather than
scattered generic path heuristics. `access_expansion` covers newly enabled
browser inspection or mutation, trusted-boundary mode or enablement, replacement
of an active boundary identity, additions to accepted trusted proxies/proof
forms, and newly active non-satellite credential bindings. Changing the logical
credential role of an already active binding requires
`credential_role_change`; moving one configured credential role to another
source has the same classification. Public-health false-to-true requires
`public_health_enablement`. Restrictive removals are not mislabeled as
expansion. Initial activation applies the corresponding intrinsic checks.

Every field in the ratified `access.yaml` schema has an explicit classifier
disposition. Adding an access field or mechanism requires updating that finite
table and its coverage; it cannot inherit an invented generic credential or
authorization abstraction.

## Secrets

Authored configuration contains uppercase logical secret references, never raw
secret values. The deployment-local strict `secrets.env` companion is not part
of the authored bundle or its content hash. It is non-shell data and cannot
contain commands, expansion, or executable interpolation.

Its logical location is fixed at `<bundle-root>/secrets.env`; bootstrap cannot
redirect secret lookup to an arbitrary path. A deployment may mount that one
file independently from a read-only YAML tree. System Mode can mutate secrets
only when the companion is writable. When it is read-only, Oracle may validate
and activate an externally supplied secret candidate but cannot replace the
companion in place. Immutable installed secret generations remain in Oracle's
writable installed store.

Provider/adapter roles may contain credential-free endpoint URLs validated by
their owning schema. URLs containing userinfo, tokens, signed parameters,
webhook secrets, or inseparable credential material are forbidden in YAML; the
entire URL becomes one logical secret value. Runbooks and routines cannot
contain URLs under any circumstances.

Machine-specific executable, model, database, device, and storage paths may
appear only in their owning Brain or satellite runtime configuration when
required. Core defaults and public examples cannot contain household/user paths.
Package assets prefer logical package-relative IDs. Routines and domain policy
cannot contain executable paths. Bootstrap paths locate the bundle, installed
store, and host-local service boundary only and cannot override behavior.

User-scoped credential references attach to the owning user capability in
`household.yaml`. Shared provider configuration remains with its domain.
Satellite-specific credential references attach to the owning satellite record;
fleet-wide authentication policy remains in `access.yaml`.

System Mode may create, replace, remove, or rotate a secret but never reads an
existing raw value back. APIs, exports, reports, health, diagnostics, logs,
Memory, diffs, and examples never reveal secret values or value-derived
fingerprints.

Secret backup and disaster recovery are operator-owned outside normal Oracle
exports. Restored values create a new validated secret generation.

A required secret referenced by enabled configuration must be present or
activation is blocked. Disabled configuration may retain an absent reference.
Unreferenced secret entries produce non-blocking hygiene warnings so rotations
can be staged. Secret presence never enables a provider or capability, and
removal is blocked while enabled configuration still requires the ID.

Every enabled provider role selects its provider explicitly. Definition order,
credential presence, environment presence, or current health never selects a
provider. Unselected provider definitions are structurally validated but need
not be operationally complete. Automatic fallback exists only when the owning
domain contract declares a bounded ordered policy; selected-provider
requirements block activation while provider outages remain readiness failures.

## Validation

The central configuration engine composes a fixed registry of code-owned,
domain-owned schema fragments and deterministic validators. Validators do not
call providers or mutate operational state.

Architecture ratification freezes file roles, ownership, authority, lifecycle,
security boundaries, enablement, identity/reference law, normalization,
activation, projections, and representative shapes. It does not pre-design
every provider-specific leaf, numeric bound, or adapter option. Stage 3 may add
those fields only inside the ratified owner and invariants while implementing
the domain-owned Pydantic models. A new file role, authority move, precedence
path, secret-semantic change, or trust-boundary change requires a new explicit
architecture decision.

`domains/home-assistant.yaml:views` is a finite typed configuration surface for
Oracle's existing Home, House, and Room read models. It owns household-specific
membership, ordering, canonical room association, optional labels, and camera
snapshot references. Every item references a compatible typed Home Assistant
mapping; view definitions never duplicate raw entity IDs. Oracle code continues
to own fixed page structures and section types, rendering, icons and default
presentation, state interpretation, supported actions, and serialization. The
surface cannot accept generic widgets, layout, dashboard, or theme definitions.

Camera snapshot references are normalized relative logical paths beneath the
selected provider's deployment-owned `snapshot_root`. Absolute host paths,
traversal, and snapshot references without that root block activation. Under
canonical authority these definitions are exclusive: read paths cannot fall
back to hardcoded household entities or provider discovery.

Canonical Home Assistant event ingress is likewise mapping-owned and finite.
One provider entity may identify at most one event mapping, and an enabled
entry-state mapping and canonical subject may each have only one enabled
home-automation lifecycle owner. Ambiguous provider evidence or competing
lifecycle owners block activation. New runs bind the exact applied
configuration revision and freeze their resolved bounded definition under the
runbook contract; later configuration governs new events, not active runs.

Validation is whole-bundle and typed. It checks syntax, schemas, references,
identity uniqueness, enabled-state constraints, secret-reference use, safety
rules, and projection generation.

Reports distinguish:

- configuration validation findings;
- activation blockers; and
- operational-readiness findings.

Severity and `blocks_activation` are separate fields even though every
validation error blocks activation. A provider outage is readiness evidence,
not a configuration validation error. A missing required secret in the selected
secret candidate blocks activation without becoming configuration structure.

## Normalization And Revisions

The engine loads a candidate, normalizes it, validates the normalized graph,
serializes that exact graph as deterministic canonical JSON, computes its hash,
and persists the generation and final report before atomically changing the
selected pointer.

Every snapshot has an opaque `candidate_id` and authored-content revision for
concurrency. Successful normalization produces a
`normalized_candidate_revision` even when semantic errors block activation, so
the report identifies the exact graph reviewed. Parse/normalization failure has
only candidate and authored revisions. Only an activation-eligible candidate is
persisted as an installed config generation with canonical `config_revision`;
invalid candidates and reports can never be selected.

The authored-content revision covers only fixed non-secret YAML role paths and
bytes. Secret edits compare the expected opaque `secret_generation_id`; Oracle
never computes or exposes a secret-content hash. Combined transactions require
both expected revisions and fail before persistence if either changed.

Raw secret submissions exist only in restricted transaction staging. Failed
parse, validation, or activation deletes staged raw values and never copies them
into reports, audit, candidate history, or the authoring workspace. Successful
transactions update the companion and immutable secret generation atomically.
After failure, System Mode requires value re-entry because Oracle never reads it
back.

The revision format is `oracle-config-v2:sha256:<digest>`. Defaults expanded
during normalization are part of the hash. Comments, authored ordering, and
whether a value equal to a default was written explicitly are not semantic.

`oracle-config-v2` canonicalizes the normalized hash payload using RFC 8785 JSON
Canonicalization Scheme semantics, encodes canonical bytes as UTF-8, and hashes
them with SHA-256. Values outside the interoperable JSON data model are invalid.
Golden fixtures fix canonical bytes and digests across implementations. A
canonicalization change requires a new hash-format version and migration.

The payload includes normalized bundle `kind`, `schema_version`, `bundle_id`,
and every normalized configuration role. It excludes only circular/generated
revision and generation IDs, timestamps, reports, provenance, audit, and secret
data. Bundle or schema identity changes therefore produce new revisions.

Identifiers are distinct:

- `config_revision`: deterministic normalized non-secret configuration hash;
- `secret_generation_id`: opaque secret snapshot identifier; and
- `activation_generation_id`: exact activated configuration/secret pairing.

Filesystem generation directories use opaque path-safe IDs. Semantic revisions
remain verified metadata and may deduplicate existing immutable non-secret
content. Directory names never encode household/bundle identity, secret IDs,
parse-dependent timestamps, or raw colon-bearing hash strings.

When both candidate `config_revision` and `secret_generation_id` already match
the selected activation, activation is a semantic no-op: persist the report and
audit outcome, but do not create an activation generation, flip the pointer, or
require restart. Authored/provenance-only differences remain visible separately.
Secret submissions always create a new secret generation without raw-value
comparison or fingerprinting.

## Activation And Runtime Adoption

Immutable installed generations are the runtime input. A runtime never reopens
authored YAML after adoption. It holds an immutable typed `EffectiveConfig` and
the corresponding immutable secret snapshot for its lifetime unless a bounded,
proven whole-snapshot hot reload is explicitly supported.

The Brain constructs one frozen `BrainEffectiveRuntimeSettings` process
snapshot from that exact `EffectiveConfig` before domain startup. It contains
the required Brain-side consumer views and one typed view for every
present optional domain role; absent optional roles remain absent and create no
defaults. Network inventory, adapter, and policy views remain distinct inside
the one process snapshot. Satellite control edges and satellite UI definitions
remain separate immutable views inside that snapshot, without creating separate
configuration authority. Construction does not start providers, warmups,
schedulers, control, recovery, or other domain behavior.

Brain-owned STT, TTS, and shared-inference execution consumers are constructed
directly from the snapshot's typed `BrainRuntimeSettings`. Disabled roles create
explicit disabled speech providers and no inference target. Enabled roles map
only their selected typed provider; model warmup accepts these constructed
consumers explicitly and must not reopen V1 configuration. Provider construction
alone performs no model load or network request.

Voice execution accepts an already constructed STT or TTS provider, and fallback
dispatch accepts an explicitly constructed registry containing the selected
inference settings. Those call paths do not consult compatibility getters.

Before canonical lifespan startup, one
`CanonicalBrainApplicationComposition` is constructed from the already-resolved
canonical `BrainConfigurationStartup`. It requires the complete immutable
applied snapshot and service bootstrap, binds the typed runtime views, core
consumers, and explicit dispatch registry, and separately constructs the
store-backed projection resolver. Composition starts no socket, route, provider,
warmup, scheduler, worker, or domain operation.

FastAPI holds exactly one explicit canonical Brain application composition.
It supplies constructed speech providers and the explicitly bound dispatch
registry. Voice and command execution select through this one application-state
object and cannot fall back to another configuration authority. Installation
attaches its separately store-backed resolver to the fixed projection and
enrollment routes; it does not itself resolve startup authority.

Canonical config health reports the Brain's exact applied activation, config,
secret, revision, selection operation/revision, and applied satellite projection
activation map from its immutable snapshot. It does not reopen another configuration
or claim that the independently advancing selected projection state is already
applied by the Brain.

Satellite pull/enrollment resolution is deliberately not frozen into that
applied process snapshot. It remains a separately configured store-backed
lifecycle surface because globally selected satellite desired state may advance
independently of the Brain process's applied generation.

Restart is required by default. Reports distinguish the atomically
`selected_generation` from each process's `applied_generation`. A process never
blends them. Startup fails closed when the selected generation cannot be adopted.

Canonical Brain and satellite startup fail closed unless their complete
selected activation can be validated. Normal configuration selection and
rollback choose only compatible canonical generations. A Brain-side desired
projection does not prove satellite adoption; the satellite must durably
validate and select the complete projection itself.

Activation never executes domain behavior, triggers a routine, mutates provider
state, or rewrites operational state. Active runbook definitions remain frozen
under the runbook contract; new configuration governs new decisions after a
runtime adopts it.

## Rollback

Schema and runtime compatibility rules apply equally to activation and rollback.
A retained generation is activatable only by a compatible Oracle version.

Normal rollback selects a previous configuration generation, validates it
against the current secret generation, and creates a new activation generation.
Historical secret generations are never silently reactivated. Secret recovery
is a separate explicit, validated, audited operation.

Non-secret configuration generations may be retained indefinitely. Secret
generations use bounded retention: keep the current generation and only the
transition generations needed for rotation or offline satellite convergence.
Revoked generations are permanently non-activatable. Obsolete raw values are
pruned after transition/acknowledgement gates while sanitized historical
activation and audit metadata remains.

## Satellite Projections

The Brain owns the full bundle. Satellites receive only minimal generated
projections and the secret values required by their own projection. Non-secret
projection JSON and satellite-local secret generations remain physically and
logically separate and activate atomically as a local pair.

`satellite_id` identifies a managed installation and lifecycle. `source_id`
identifies request, context, alert, and playback ownership. Each enabled
satellite references one enabled stable source, and one active satellite owns a
given satellite source. The IDs may be equal by convention but are not the same
type.

Satellite-to-Brain and Brain-to-control-service credentials are separate,
unique per satellite, and directionally scoped. A bootstrap enrollment
credential proves enrollment for one declared satellite and cannot claim
another identity.

`satellites.yaml` also owns the satellite-to-Brain endpoint together with its
directional `brain_client` credential reference. The endpoint is projected and
persisted with the satellite's last valid local configuration; environment,
ordinary CLI, network inventory, and discovery observations cannot override an
applied projection. Every enabled projection-bearing satellite requires the
complete endpoint and credential so voice, display, playback-only, and future
installation shapes can authenticate projection refresh. `brain_client` is
common installation-level projected configuration, not a third runtime-
compatibility component. The interaction runtime consumes it when that process
talks to the Brain but does not exclusively own it.

A fresh satellite cannot obtain that projected endpoint without first locating
the projection service. Installation therefore supplies one
`brain_bootstrap_url` as bootstrap/deployment metadata outside the bundle and
projection. It is a rendezvous locator only: the satellite may use it to enroll
or fetch a projection when no valid local pair exists, but it never enters
`EffectiveConfig`, overrides `brain_client.base_url`, or forms a fallback
precedence list. After validation, the projected `brain_client.base_url` is the
sole runtime endpoint authority. A restart uses the last valid local pair while
the Brain is unavailable; a fresh installation with neither a reachable
bootstrap locator nor a valid local pair fails closed. V2 does not add mDNS,
broadcast discovery, alternate URL lists, or automatic endpoint rewriting.

Brain-hosted audiobook manifests use the same authored endpoint without
creating another authority. The immutable audiobook-domain runtime may retain
`brain_client.base_url` only for an audiobook-admitted playback source and only
to construct absolute Brain stream URLs for that target. It does not receive
the satellite-to-Brain credential, cannot authenticate or refresh a projection,
and does not place the endpoint in the general Brain fleet view.

`satellites.yaml` owns the Brain-facing control-service endpoint together with
its directional credential reference. The endpoint is an absolute credential-
free HTTP(S) URL. Household source identity, network inventory, enrollment
observations, and runtime reachability may reference or report the same managed
installation but cannot override this control edge. Enabled playback-capable
satellites require the complete endpoint and credential.

The same satellite-owned control edge may declare a separate `local_client_url`
used by co-resident voice and playback runtimes. It shares the directional
control-service credential; it is not a second authority or credential scope.
Enabled voice- or playback-capable satellites require this local client URL,
which is included in the applied projection and cannot be overridden by process
environment or ordinary CLI. A non-loopback value must be authored explicitly;
validation may warn but must not silently rewrite it.

The control-service listener bind host and port are deployment/bootstrap
metadata, not canonical behavior fields. They cannot derive, replace, or
override `base_url` or `local_client_url`. Migration and deployment validation
may report an obvious listener/endpoint mismatch, but neither activation nor
runtime silently rewrites an authored canonical endpoint to match a listener.

Reply-audio state and stop-file locations are co-resident process IPC bootstrap
metadata, not canonical behavior. The satellite and control-service launchers
must resolve the same host-local locations. Migration compares their effective
paths and blocks a mismatch; the paths are not added to the bundle or satellite
projection.

Process log verbosity is deployment diagnostic metadata and does not contribute
to the effective configuration revision. One-shot utilities such as satellite
audio-device listing are explicit command modes. They do not activate the normal
runtime, persist in a projection, or provide a channel for behavioral CLI or
environment overrides.

Projection format has its own code-owned schema version. A satellite accepts
only supported, validated projections. It retains current and previous valid
local pairs and must restart from its last valid projection while the Brain is
unavailable. Fallback cannot restore server-side authority for revoked
credentials.

The satellite-local selected pointer also owns a durable restart-required
latch. Selecting a projection or local-secret generation different from the
previous local selection sets the latch to the newly selected activation ID in
the same atomic pointer replacement. A lightweight activation that reuses both
generations does not set it. If a latch is already pending, a later selection
carries it forward to that selection's activation ID. Deployment may clear the
latch only after every installed interaction-runtime and control-service target
has restarted successfully. Missing targets are skipped; the satellite UI is
not a runtime consumer and is never restarted by this handoff.

Satellite alarm and timer cues use logical asset IDs in the projection, never
arbitrary host filesystem paths. Migration maps Oracle's packaged default alarm
and timer files to the code-owned `alarm` and `timer` asset IDs. A legacy custom
path requires an explicit supported replacement asset ID and remains report
evidence only; Stage 3 does not create a general asset registry or upload
subsystem. Failure to play a selected cue remains operational failure and may
use the contract-preserved spoken fallback.

V2 satellite playback execution uses the Oracle native player. Plexamp client
control is retired behavior and is not an accepted canonical adapter. Migration
may preserve shared Plex library/provider configuration needed by the native
player, but it never copies a Plexamp client URL or raw Plexamp control command.
An installation still using Plexamp control requires explicit native-player
replacement before its canonical candidate can complete.

Raw pause, resume, stop, navigation, volume, media, now-playing, and long-form
command strings are forbidden canonical configuration. Migration records only
sanitized presence and requires typed native-player coverage for every effective
non-empty command before cutover. Empty historical defaults need no replacement.
Canonical launch rejects rather than ignores any retained legacy command input.

System output-volume control is optional typed satellite behavior. Schema v1
supports Linux `alsa` with required mixer `card` and `control`, and Windows
`windows_default_endpoint` with no operator-supplied endpoint identifier or
command. The types are platform-specific. Absence means Oracle does not claim
system-mixer control. Windows default-endpoint support follows the operating
system's current default output; an explicitly pinned playback device may
produce a validation warning because playback and mixer targets can diverge.
Activation blocks a configured backend that the target runtime does not report
as compatible. New backend types require schema and contract review.

`audio.interaction_output` configures only the interaction runtime's
conversational/TTS and cue renderer. It is not a canonical native-media output
selector. Native media output remains a control-service/player deployment
concern until Oracle implements and reviews a concrete cross-platform selection
and validation mechanism. Local speaker arbitration still coordinates the two
processes, but that coordination does not create shared ownership of output-
device configuration.

Shared music-provider URL, credential, machine identity, and HTTP timeout remain
domain-owned even when a satellite native player consumes their minimal
projection. Schema v1 has no per-satellite Plex timeout override. Migration
compares the Brain and control-service effective provider tuples. Credential
disagreement blocks completion; timeout disagreement deterministically selects
the Brain historical default of 8 seconds and reports the satellite-side
behavior change.

Wake-capture collection, local storage, interval, deletion, and retention policy
are typed satellite projection fields. Raw sync hosts, users, SSH key paths,
server paths, and transport selectors are forbidden projection content and are
retired migration inputs. Enabled sync requires a runtime-compatible code-owned
mechanism; migration never infers one from the retired fields. Until one exists
for the target runtime, enabled sync is an activation blocker rather than a
reason to preserve legacy transport authority.

Each non-secret projection has a deterministic `projection_revision` covering
its projection schema, satellite/source IDs, runtime compatibility, and
normalized projected configuration. A satellite-local activation ID atomically
pairs one immutable projection generation with one local secret generation and
binds both to the exact selected Brain configuration revision. Secret-only
rotation preserves projection revision but creates a new local activation. A
Brain revision change also creates a new lightweight local activation even when
it reuses unchanged projection and secret generations.

Before Brain activation, every declared enabled satellite projection is
generated and validated. The complete satellite activation map participates in
the same global pointer transaction, rollback, journal recovery, and audit as
the Brain activation; an incomplete map or revision mismatch blocks selection.
Generation failure or known runtime incompatibility blocks activation. Delivery
is asynchronous and satellite-pull: an authenticated satellite requests only
its own selected projection pair from the Brain. One versioned pull response
binds the selected activation and global selection identity to two distinct
payloads: canonical non-secret projection JSON and its exact minimal local-
secret snapshot. Projection and secret authority remain separate even though
they travel together; V2 does not split them into independently resolved
requests that could observe different selections. Serializing the response is
an explicit secret-bearing transport boundary and must not expose values in
logs, representations, or error output. An
offline satellite keeps its last applied projection while reports distinguish
desired, delivered, acknowledged, and applied generations. Oracle never claims
distributed atomic activation.

The V2 pull surface is
`GET /api/satellite/projection/{satellite_id}` with the directional
`brain_client` value in the standard Bearer authorization header. The path is a
selector, never proof by itself. Authentication failures are deliberately
indistinguishable, secret-bearing success responses are non-cacheable, and
canonical-store unavailability fails closed without returning internal detail.
This LAN-only lifecycle surface is not the browser authentication boundary and
is distinct from the browser UI configuration endpoint.

After validation, a satellite persists the projection and secret payloads as
separate restricted immutable artifacts and atomically selects them using the
Brain-issued `sat_activation_` ID. It must not create a second local activation
identity for the same pair. Global selection operation/revision belongs to the
local selected pointer, not the immutable activation metadata: a later rollback
may lawfully reselect the same activation under a newer global selection
revision. A satellite rejects an older selection revision, a conflicting reuse
of the current revision, or unequal content under an existing activation ID.
The selected pointer changes only after both payloads are durable.

Projection refresh has one installation-level writer. Neither the interaction
runtime nor control service independently pulls or selects configuration. The
one-shot sync operation derives the Brain URL and directional credential only
from the previously selected local activation; ordinary CLI/environment cannot
replace them. `satellite_id`, local store root, and the current typed runtime-
compatibility evidence path are bootstrap inputs, not behavior precedence.
A store without a selected activation requires explicit first-contact
provisioning. The one-shot command accepts exactly one credential-free
`brain_bootstrap_url` and one absolute path to a locally provisioned restricted
enrollment-credential file. It posts to the dedicated satellite enrollment
route, which authenticates only the enabled selected satellite's
`enrollment.credential_secret` and returns the existing immutable projection
envelope without mutating selection or enrollment state. Successful local
installation ends first contact; every later refresh derives its endpoint and
operational credential only from the selected local activation. The enrollment
credential is reusable until explicitly replaced, disabled, or deleted; Oracle
does not consume, rotate, deliver, persist into the projection, or expose it in
argv, environment, reports, or logs. Scheduled refresh never receives either
first-contact input.

Runtime-compatibility reports are operational evidence, not configuration.
Every enabled satellite requires one accepted report before its first active
projection. Oracle may use the last accepted report while that satellite is
offline; V2 does not expire reports solely by age or require the fleet to be
simultaneously online for Brain activation. A report describes supported
installation platform mechanics and projection schemas plus two typed component
reports: `interaction_runtime` and `control_service`. The interaction component
reports voice capture, Brain interaction, conversational audio, cues, audio
adapters, wake processing, and wake formats. The control component separately
reports playback-authority schema support, native music, native audiobook, and
implemented volume backends. Each component has its own runtime version. UI is
not a compatibility component, and display/UI behavior never proves native-
player capability. A report cannot select or override projected fields.
A runtime upgrade replaces the accepted report only after validation, and an
unsupported required mechanic blocks projection generation.

The existing host-local configuration service accepts this operational
evidence through one finite typed operation. The request supplies exactly one
configured-style satellite ID and one closed compatibility report; the service
validates the existing executable report schema and atomically replaces only
that satellite's last accepted report. Acceptance records a sanitized actor-
attributed audit request and returns only acceptance time, platform, supported
projection schemas, and component runtime versions. It cannot change the
bundle, select a generation, project a configuration value, or make arbitrary
runtime evidence fields executable. There is no LAN compatibility-submission
endpoint in V2.

The V1 satellite configuration listener and its `config_bind_host` and
`config_bind_port` inputs are bounded compatibility/diagnostic infrastructure,
not canonical projection delivery. Their bind values remain deployment
bootstrap metadata and are never projected. They may be removed only after the
satellite-pull transport, acknowledgement, local installation, and offline
restart path replace their required uses.

Disabling a satellite prevents enrollment, refresh, and ordinary participation
after the Brain adopts the change, but does not remotely stop or erase it.
Decommissioning and credential revocation are separate audited lifecycle
operations.

## Configuration Clients And Authoring

The Brain-side configuration service is the only mutation authority. Offline
tools use the same engine and may activate only while the service is stopped and
an exclusive lock is held.

System Mode and CLI are configuration clients, not authorities. They create or
edit candidates, validate, show semantic diffs and restart impact, request
required safety acknowledgements, and activate through the service.

Service-level candidate review is durable and audited even when no mutation
follows. Each complete report is bound to its candidate authored/normalized
revisions, the exact selected activation/config/secret/selection baseline, and
one code-owned validation version. Identical complete validation inputs may
reuse report content, but every operator review request records a distinct
sanitized actor-attributed audit event. Pure in-process inspection helpers remain
side-effect-free. Review creates no generations, pointer writes, or mutation
recovery journal.

Each deployment has one authoring workspace, normally its private deployment
repository checkout. The service may update authored files and local backups but
never commits, pushes, or resolves Git conflicts. Manual edits do not affect
runtime until validation and activation. Rollback does not rewrite authored
files; promoting an old generation into a new authored candidate is explicit.

Managed multi-file edits use a complete external staging tree, exclusive lock,
transaction journal, per-file atomic replacement, and recoverable backups. The
service completes or restores an interrupted authored transaction before new
work. The runtime pointer changes only after authored and installed artifacts
are durable; partial authored writes can never become partial runtime config.
Non-secret authoring backups may be retained under policy. Any raw-secret
rollback copy is restricted transaction material and is deleted immediately
after commit or recovery; it is never a normal backup/export surface.

Bootstrap declares one authoring mode. In `managed_writable`, the service may
edit the workspace. In `external_read_only`, external packaging/GitOps owns YAML;
System Mode may inspect, validate, diff, and report but cannot edit YAML or
create a hidden writable override. A complete externally supplied candidate
still follows normal activation. The fixed `secrets.env` path may be mounted
independently writable and retain write-only secret operations; otherwise
secret mutation is unavailable. Authoring mode is deployment state, not a
bundle field.

## Audit And Provenance

Configuration validation, migration, activation, rollback, secret rotation,
projection delivery/adoption, and lifecycle operations produce durable,
sanitized audit records.

Every selected-pointer replacement is a recoverable selection transaction. One
opaque operation ID is persisted in both its durable journal and the selected
pointer, and its selection revision is exactly one greater than the prior
pointer revision. Pending selection recovery completes before another selection
write may begin. Audit persistence is idempotent and deduplicated by operation
ID. If pointer replacement commits but audit persistence does not, the caller
receives an explicit committed-but-audit-pending failure and recovery retains
the journal until that exact audit is durable. A journal/pointer combination
that cannot prove either the prior or target state is ambiguous corruption and
fails closed; Oracle never guesses, advances the revision, or starts another
selection transaction from it.

The journal does not embed an arbitrary or duplicated audit dictionary. Its
minimal versioned envelope contains only the operation/audit identities,
sanitized actor class, prior and target activation/config/secret identities,
consecutive revisions, acknowledged safety classes, optional durable candidate-
report identity, and secret logical-role metadata needed for secret operations.
Recovery validates those facts against the pointer, immutable generations, and
durable report, recomputes semantic changes, and constructs the event through
the canonical audit builder. Broader journal-schema registries, signatures, and
hostile-tamper machinery are outside V2's personal-deployment threat model.

Reports may show file role, relative path, authored/defaulted/derived/projected
provenance, owning schema fragment, restart impact, and secret presence. Public
health remains shallow. Absolute private paths, raw secrets, provider-native
credentials, and sensitive payloads are forbidden.

## Forbidden Behavior

Oracle V2 must never:

- use field-level runtime precedence;
- load wildcard configuration directories or deployment-defined includes;
- permit YAML execution, templates, merges, or inheritance;
- let domains, System Mode, or satellites read configuration files directly;
- use source association as authentication or authorization;
- accept arbitrary client source strings as trusted identities;
- expose or silently restore secret values;
- activate configuration by mutating provider or operational state;
- claim fleet-wide atomicity; or
- preserve legacy vocabulary or compatibility solely because existing tests
  encode it.
