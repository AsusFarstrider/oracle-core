# V2 Configuration Bundle Architecture

## Role

This document describes how Oracle implements the configuration law in
[`configuration.md`](../contracts/configuration.md). Field-level rules and
examples belong in [`configuration-schema.md`](../reference/configuration-schema.md).

## Physical Model

```text
household-authored configuration bundle
├── bundle.yaml
├── brain.yaml
├── access.yaml
├── household.yaml
├── satellites.yaml
├── domains/
│   ├── information.yaml
│   ├── music.yaml
│   ├── audiobooks.yaml
│   ├── weather.yaml
│   ├── calendar.yaml
│   ├── home-assistant.yaml
│   ├── notifications.yaml
│   ├── routines.yaml
│   └── network/
│       ├── inventory.yaml
│       ├── policy.yaml
│       └── adapters.yaml

separately protected secret companion
└── secrets.env

installed runtime store
├── candidates/
├── config-generations/
├── secret-generations/
├── activations/
├── projections/<satellite_id>/
│   ├── projection-generations/
│   ├── secret-generations/
│   └── activations/
├── reports/
├── audit/
└── selected.json                   revisioned, operation-bound atomic pointer
```

The reusable core owns schemas, validators, migrations, examples, and tooling.
Its complete generic authored example is `examples/config/` and mirrors the
fixed role layout without becoming a runtime root.
[`examples/deployment/minimal/template.json`](../../examples/deployment/minimal/template.json)
wraps that exact role set with the standard minimal profile, host-local ingress,
core-pin shape, placeholder, validation, and separate-secret boundaries needed
to initialize an isolated household deployment definition.
The private deployment authority owns one or more explicitly isolated household
definitions. A standard target receives only its own exact materialized
deployment revision and separately supplied secrets; it does not require the
authoring repository. The installed store owns immutable runtime artifacts.
Runtime processes consume only installed generations.

Pointer manifests are portable across Linux and Windows. They name the exact
activation and expected config/projection plus secret-generation IDs. Writers
fully persist immutable generations, write/fsync a new manifest, then use the
platform's atomic replace operation. Startup verifies every reference before
adoption; symlinks are not required by contract.

Each replacement first persists one selection journal containing its operation
ID, audit event identity/timestamp, actor, previous and target activation/config/
secret identities, consecutive selection revisions, acknowledgements, and an
optional durable candidate-report reference. It contains no audit payload. The
same operation ID and new revision are embedded
in `selected.json`. Recovery runs under the store lock before any later
selection write and accepts only a pointer proving the exact prior state or the
exact committed target state. Audit records use an operation-ID index for
idempotent deduplication. A committed pointer whose audit write is pending is an
explicit client-visible outcome, while any other journal/pointer combination
fails closed as ambiguous.

Recovery reloads the referenced immutable generations and candidate report,
recomputes semantic changes, and calls the ordinary audit builder with the
journal's bounded facts. This keeps recovery's trust boundary small without
adding a general journal-schema or cryptographic verification subsystem.

Generation directories use opaque path-safe IDs. Content identities such as
`config_revision` and `projection_revision` live in verified metadata rather
than directory names. Existing immutable config content may be reused by
revision after integrity verification. Names never embed household/bundle names,
secret IDs, parse-dependent timestamps, or colon-bearing hashes.

## Ownership By File

- `bundle.yaml`: bundle kind, whole-bundle schema version, and stable bundle ID.
- `brain.yaml`: exactly `runtime`, `logging`, `storage`, `speech`, and
  `inference` sections for Brain process/pipeline limits, logging/tracing,
  Brain-owned storage integration, shared speech roles, and shared inference
  transport/fallback-router runtime policy.
- `access.yaml`: Oracle-facing ingress, trust, exposure, operator boundary,
  fleet-wide enrollment/authentication policy, and mechanism-specific
  authentication bindings for non-satellite stable sources.
- `household.yaml`: household identity, timezone, locale, optional single home
  location, users, rooms, aliases, modes, defaults, optional ordered UI
  escape-hatch links, stable request sources,
  associations, and user capability secret references.
- `satellites.yaml`: managed runtimes, their household source references,
  desired runtime/audio/UI configuration, capabilities, and satellite-specific
  credential references.
- domain roles: domain policy, provider selection, provider mappings, and
  domain-owned definitions.

Shared provider transport belongs in `brain.yaml` only when it is genuinely a
Brain-wide service, such as speech or shared inference. Domain use and provider
selection remain domain-owned. Oracle has no universal provider registry file.

STT, TTS, and the shared inference backend are independently enabled roles.
Broad parent-level speech or inference enablement is invalid, avoiding
conflicting parent/child state and permitting text-only or inference-free
deployments.

Alerts do not receive a domain file. Their scheduled records are operational
state and their interpretation is code-owned. Existing configurable concerns
remain with Brain storage, satellite projections, or notification policy. This
is an intentional application of the file-role threshold, not a missing domain.

Playback authority likewise receives no domain file. Shared handoff and
interruption law is code-owned; media-domain policy remains domain-local;
target execution and timing remain satellite-owned; and playback/session truth
is operational state. Configuration does not expose cross-domain behavior
merely to justify another file role.

## Engine Composition

One configuration engine is shared by the service, startup, CLI, migration, and
tests. It contains:

1. a fixed file-role registry;
2. one pinned restricted `ruamel.yaml` round-trip parser;
3. code-owned Pydantic v2 model fragments registered by domain owners;
4. normalization and safe-default expansion;
5. deterministic cross-file validators;
6. semantic diff and safety classification;
7. canonical JSON serialization and hashing;
8. projection generation; and
9. immutable generation persistence.

Schema fragments compose one typed graph. They cannot discover plugins, execute
provider calls, read arbitrary files, or register runtime behavior from
configuration.

The composed Pydantic models are executable schema authority. They generate
versioned JSON Schema for System Mode and tooling, including declared extensions
for ownership, restart impact, write-only secrets, and safety classification.
Generated schema is tested output, not a second hand-maintained authority.
Whole-bundle reference and compatibility rules remain deterministic validators.

Ratification fixes the architectural envelope and representative shapes rather
than implementing every provider leaf on paper. Stage 3 may define exact fields,
bounds, and adapter options inside their already ratified owner and invariants.
Crossing an ownership, authority, precedence, secret, trust, lifecycle, or file-
role boundary returns to explicit architecture review. Generated JSON Schema
and tests become the exact field-level implementation record.

The parser validates the YAML node graph before converting accepted values to
plain primitives for Pydantic. Managed-writable edits preserve comments and
stable formatting where possible. Comments remain non-semantic and absent from
canonical JSON. Oracle does not maintain separate validation/editing parsers.

## Candidate Pipeline

```text
authoring workspace or importer
        |
        v
candidate snapshot under exclusive service transaction
        |
        v
restricted parse -> normalize -> whole-graph validation
        |                         |
        |                         +-> validation findings
        v
canonical deterministic JSON -> config_revision
        |
        +-> semantic diff, provenance, restart impact, acknowledgements
        |
        +-> secret-reference validation against candidate secret generation
        |
        +-> generate and validate satellite projections
        v
persist immutable config, secret, activation, reports
        |
        v
atomic selected-pointer replacement
```

Every snapshot receives candidate and authored-content revisions. Successfully
normalized graphs receive a normalized candidate revision even if semantic
validation blocks activation. Only activation-eligible graphs become installed
config generations; parse/normalization failures remain candidate-bound reports.

Validators never mutate the normalized graph. Hash input is the exact canonical
JSON graph that passed validation. Reports are persisted against that revision.

The pure candidate inspector performs no writes. The configuration service wraps
it with durable review: report persistence includes the validation-engine
version and exact selected baseline, then each invocation appends a distinct
actor-attributed audit event. Implementations may content-address or deduplicate
identical reports only when every validation input matches; operator review
events are never collapsed. Because review is non-mutating, it does not enter
the selection, authoring, or secret recovery-journal protocols.

Candidate inspection then runs a distinct transition phase against one exact
selected activation/config/selection revision. It enforces two-generation
retirement for enabled optional roles, information capabilities, the network
anchor, and enabled identities. Transition blockers are persisted separately
from standalone validation and secret blockers while remaining activation
blockers. The service repeats this phase against the same selected identity
immediately before activation and fails if selection changed. Rollback invokes
the identical phase with its historical config generation as the candidate.
Rekeying has removal semantics unless a schema explicitly supplies stable
identity continuity; acknowledgements classify a lawful removal but do not
bypass the phase.

Candidate editing may span files, but validation operates on one snapshotted
candidate. Concurrent edits use revision checks. A partially written authored
workspace can fail candidate loading but cannot alter the selected generation.
Service transactions and backups live in the installed candidate/store area,
not under the authored bundle root where they could look like file roles.
Bootstrap enforces this physically after resolving trusted roots: bundle and
store are non-overlapping trees, and the socket is outside the bundle. Symlinked
roots and mount points remain supported because topology is checked against
their resolved effective locations.

Managed edits build and validate a complete staged candidate before touching the
authoring workspace. Commit holds an exclusive lock and uses a transaction
journal, per-file atomic replacements, and recoverable backups. An interrupted
transaction is completed or restored before another begins. Immutable installed
artifacts and authored commit are durable before selected-pointer replacement,
so authored drift can require recovery but runtime never applies a partial tree.

## Runtime Adoption

At startup, the process resolves the selected activation, verifies integrity and
compatibility, loads canonical JSON and the bound secret generation, constructs
typed immutable objects, then starts domain consumers.

```text
selected activation
   +-- config generation -> typed EffectiveConfig
   +-- secret generation -> restricted immutable SecretSnapshot
                              |
                              v
                    provider/domain factories
```

Domains receive their typed fragment through dependency injection. They do not
open YAML, inspect environment variables, or implement local fallbacks.

`BrainEffectiveRuntimeSettings` is the process-level construction root. It
retains the exact immutable `EffectiveConfig`, constructs all required consumer
views, and constructs optional domain/network views only for fixed roles that
are present. This makes the later lifespan authority switch consume one
complete tested snapshot rather than assemble configuration opportunistically
while starting subsystems. Construction itself has no side effects.

Satellite fleet control edges and enabled satellite UI definitions are exposed
as separate immutable read views because their consumers and projections are
different. Both derive from the same `satellites.yaml` role and effective
revision, so this separation introduces neither another authority nor runtime
precedence. Home Assistant Home/House/Room membership likewise remains inside
the domain's immutable view and references only its typed mapping registry.

The first execution-consumer adapter is `BrainCoreRuntimeConsumers`. It maps
the Brain snapshot's selected STT, TTS, and Ollama definitions into the existing
runtime provider objects and one immutable inference execution view. Explicit
speech/inference warmup seams accept those constructed values, so canonical
startup need not translate them into legacy getter dictionaries. Creation does
not load a model or contact a provider.

The voice execution helpers consume constructed STT/TTS provider objects, and
the dispatch builder accepts the immutable inference view when constructing its
fallback handler. Canonical composition supplies every consumer explicitly and
does not use a process-global configuration adapter or translate typed settings
into compatibility dictionaries.

Canonical composition also constructs a route capability registry bound to the
same immutable household view. Home Assistant room matching, normalization,
pending clarification, and follow-up refinement therefore use canonical room
identity and aliases without consulting external room/source registries. Dynamic
HA entity discovery remains operational evidence, and collisions cannot
override configured room terms.

`CanonicalBrainApplicationComposition` is the pre-start application boundary.
It accepts only a complete canonical startup resolution, then assembles the
applied runtime snapshot, core provider consumers, explicit dispatch registry,
and separately store-backed projection resolver. It does not install those
objects into FastAPI or start any lifecycle component. This keeps composition
testable before the one-way lifespan switch while preserving the resolver's
independent desired-state authority.

FastAPI application state owns the selected canonical composition object.
Tests install that composition and exercise voice/dispatch through its
constructed providers and registry without a global configuration shim. The
installed lifespan attaches the composition's independently store-backed
resolver to the fixed projection routes.

The same application state provides a sanitized applied-generation read model
for canonical `/health/config`. It describes the immutable Brain snapshot and
does not substitute current selected desired state for applied state.

The selected projection resolver is not a member of this applied snapshot. It
is a store-backed lifecycle surface whose desired satellite selection may move
ahead of the Brain's applied generation, as described by the projection
boundary below.

The configuration service reports selected and per-process applied generation.
Default adoption occurs only at restart. Any future hot-reload registration must
prove safe whole-snapshot replacement and cannot introduce per-field precedence.

## Secrets

`secrets.env` is parsed as strict data into a candidate secret snapshot. Logical
references are validated by role. Raw values never enter canonical JSON,
provenance, diffs, reports, or audit.

The companion's logical path is always `<bundle-root>/secrets.env`. Bootstrap
does not redirect it. Read-only package/configuration trees may mount that
single path independently writable. If the companion is read-only, the service
can validate and activate externally supplied secret candidates but cannot
replace it in place. Installed immutable secret generations remain in the
writable installed store.

`EffectiveConfig` retains logical references. Restricted provider construction
resolves required values from the activation's immutable `SecretSnapshot`.
Consumers cannot use secret resolution to add configuration structure or select
an undeclared provider.

Secret-only changes preserve `config_revision` and create new secret and
activation generations. Normal configuration rollback pairs the prior config
generation with current compatible secrets.

Secret retention is bounded independently from non-secret generation history.
Revoked generations cannot be activated; obsolete raw values are pruned after
rotation and satellite acknowledgement gates, leaving sanitized metadata only.

## Service And Clients

The Brain-side configuration service owns candidate transactions, validation,
diffs, activation, rollback, secret replacement, migrations, generation
inspection, projection desired state, accepted typed satellite compatibility
evidence, and audit. Compatibility evidence remains operational state outside
the bundle and cannot supply configuration values.

System Mode uses operator HTTP APIs across the trusted boundary. The CLI uses a
filesystem-protected Unix-domain socket into the same service. An offline CLI
may use the same engine directly only when the service is stopped and it holds
the store lock. Socket/store paths are bootstrap settings; no shared operator
API token is introduced.

The service may write the configured authoring workspace but does not own Git.
It reports authored-versus-selected drift and Git status without committing or
repairing either automatically.

Bootstrap selects `managed_writable` or `external_read_only` authoring. The
latter supports NixOS, container, Kubernetes, and GitOps trees: System Mode is
YAML-inspection-only and external tooling supplies complete candidates. No
writable overlay is introduced. Secret storage remains independently writable
only when the deployment provides a writable mount at the fixed companion path.

## Access Architecture

`access.yaml` describes Oracle-facing trust assertions through a fixed schema.
The current external gateway remains a valid implementation, but the architecture
does not require a particular proxy. Localhost, authenticated private networks,
SSH tunnels, or later bounded implementations can satisfy the declared boundary
when supported by the schema.

Oracle does not manage household accounts or roles. Operator access is binary.
Source authentication is separate: an ingress establishes a configured stable
source or creates a server-controlled ephemeral source. Household source
association is applied only after that proof succeeds.

For non-satellite sources, `access.yaml` contains finite mechanism-specific
bindings from authenticated ingress proof to exactly one enabled,
type-compatible source in `household.yaml`. It cannot accept an untyped generic
binding list. V2's only such mechanism is `credential_bindings`. Unbound ingress
receives an ephemeral identity with no stable associations. Credentials remain
logical secret references.

Satellites use a different proof path: unique satellite credentials establish
`satellite_id`, and the satellite record supplies `source_id`. They do not also
appear in the non-satellite access-binding structures. Neither source-user nor
source-room association contributes authentication evidence.

The canonical shared-command ingress is a small adapter in front of request
composition. It ignores payload source authority, resolves non-satellite
credentials through the immutable applied access view, and uses a satellite's
claimed source only to select the applied fleet candidate. Satellite proof is
checked against the exact projection activation recorded in the running
Brain's `EffectiveConfig`, not a newer desired selection followed by projection
delivery. Invalid presented credentials return `401`; missing credentials
produce an unassociated `ephemeral_http` source. A missing ephemeral session is
replaced with a fresh server-generated session before ordinary session
resolution.

This adapter is used by context-bearing HTTP routes. Internal callers without
authenticated ingress are assigned an unassociated internal ephemeral source.
Browser credential transport, accounts, roles, permissions, and wider
shared-endpoint enforcement remain outside this seam.

`credential_bindings` authenticates one source with one unique high-entropy
logical credential. Browser use requires a supported protected cookie/server
transport and cannot expose the credential to scripts or local storage. There
is no shared credential or implicit fallback mechanism.

Access safety classification is owned beside this closed schema. A complete
field-disposition table drives semantic diff classification for operator
inspection/mutation exposure, the one trusted-boundary object, public health,
and active credential bindings. It distinguishes expansion from restriction and
uses the current household enabled-source set only to determine whether a
binding is active. Table-driven schema coverage fails when a new access field
has no explicit disposition; no generic future credential-role model is
inferred.

Only one credential value is accepted for a stable non-satellite source at a
time. A new secret generation performs an atomic cutover and immediately
revokes the old value; Oracle provides no overlap, automatic restoration,
self-service enrollment, or credential delivery. Operators coordinate client
provisioning around that cutover. Source disablement blocks authentication but
does not silently delete the separately managed secret.

Every enabled non-satellite stable source has exactly one binding. It cannot use
another mechanism in V2. Disabled sources may retain one inactive binding.
Credential logical secret references are unique across source bindings.
Satellite authentication remains outside this one-to-one binding model.

This uniqueness rule applies to configured logical references, not resolved
secret-value equality. The engine does not compare or fingerprint raw values to
detect duplicate source credentials. Accidental reuse under distinct logical
IDs is an operator error outside V2 enforcement and requires a later explicit
security design if deployments need stronger guarantees.

The Brain's frozen access consumer seam resolves values only for bindings whose
household source is enabled in the same adopted activation. Request
authentication compares a presented credential against those active bindings
without a configured-value deduplication pass and yields a stable source only
for one match. A duplicated raw operator value therefore fails safe at use
without creating hashes, fingerprints, or durable equality state. The seam does
not choose cookie/header transport or turn trusted-boundary proof into source
proof; those remain ingress responsibilities.

A configured trusted boundary and a proved stable request source are separate
concepts. Each `boundary_id` references a typed enabled access entry, but V2
uses it only to authorize operator/System Mode access and assigns those requests
ephemeral source identities. User identity, localhost, VPN membership, network
location, SSH tunneling, and arbitrary headers are not stable-source proof.
Trusted-boundary stable-source binding remains a future extension requiring a
concrete installation-level proof mechanism and contract/schema review; Oracle
cannot accept configuration for an unimplemented proof mechanism.

The V2 access graph contains at most one enabled trusted-boundary object. It has
a stable ID and one code-known type, and `operator_access.boundary_id` selects it
when trusted-boundary mode is active. The host-local service remains independent.
Multiple simultaneously active remote boundaries are a later schema extension.

## Satellite Architecture

The Brain derives a projection from the complete typed graph:

```text
bundle activation
   |
   +-> satellite record (`satellite_id`)
   +-> referenced household source (`source_id`)
   +-> relevant Brain/access/domain policy
   +-> satellite-specific secret references
   v
canonical projection JSON + minimal local secret generation
```

The projection contains no unrelated household users, rooms, provider accounts,
or domain definitions. Projection format version is independent of authored
bundle schema version and declares runtime compatibility.

Runtime compatibility is reported at installation level with two typed
components. `interaction_runtime` owns voice capture, wake, cues, Brain
interaction, and conversational/TTS rendering. `control_service` owns native
media playback, normalized transport, playback authority, and implemented
volume backends. Component versions remain independent. UI is Brain-served and
does not form a third compatibility component.

The generated configuration mirrors that ownership split. Brain-only targeting,
including `control_service.base_url`, is not projected. The interaction output
selector applies only to conversational/TTS and cue rendering; native-media
output remains deployment-owned. Local speaker arbitration coordinates access
without creating shared output-device configuration ownership.

One common installation-level `brain_client` block sits alongside the two
component-owned blocks in every enabled satellite projection. It provides the
authoritative Brain URL and directional credential needed for projection refresh
regardless of voice, display, or playback capability. Voice interaction may
consume the same block, but this does not add a compatibility component or a
second authority.

Brain-side persistence keeps each satellite's canonical projection bytes,
minimal local secret snapshot, and activation pair in separate immutable
generation collections. Loading an activation revalidates canonical bytes,
projection revision, satellite/source identity, and the exact secret-reference
set derived from the projection. A local activation cannot pair a projection
with missing or unrelated secrets. The activation, rather than the reusable
projection payload, binds the pair to one exact Brain configuration revision.

`selected.json` carries `satellite_projection_activation_ids`, with exactly one
activation per enabled projection-bearing satellite. That map is part of the
existing global selection journal and atomic pointer replacement, so activation,
rollback, and crash recovery prove the Brain generation and complete satellite
selection together. A Brain-only change creates lightweight new satellite
activations while reusing unchanged projection and local-secret generations.
This selection adds no separate desired-state or delivery store.

The Brain's immutable applied fleet seam carries that snapshot's satellite/
source ownership and projection activation identities plus only the remote
Brain-to-control edge needed for playback-capable satellites. It excludes
projected Brain-client, local-control, audio, UI, wake, and enrollment details.
The pull/enrollment/wake receiver continues authenticating against the selected
projection resolver because selected desired state may advance before the
Brain's applied process snapshot; the fleet seam must not become a competing
lifecycle credential authority.

The transport-neutral pull envelope is
`oracle-satellite-projection-pull-v1`. It is derived from one authenticated
resolver result, so its selection operation/revision, satellite activation,
Brain configuration revision, projection generation, and local-secret
generation cannot be independently selected. The envelope carries two nested
payloads: canonical projection JSON and the exact minimal local-secret values.
Combining transport does not combine their lifecycle or revision authority.
Raw values exist only at explicit response serialization; safe object
representations contain identity metadata only. The envelope creates no
delivery record, acknowledgement, applied-state transition, or enrollment
behavior.

The Brain exposes that envelope only through LAN-only
`GET /api/satellite/projection/{satellite_id}`. A standard Bearer header carries
the selected satellite's directional `brain_client` credential. The endpoint
adds no second identity authority: the path is only a selector and the resolver
must authenticate the same selected satellite. Its production resolver remains
unavailable until canonical Brain startup supplies the installed store.

All projections are generated before Brain activation. After activation the
selected activation map is the Brain's desired projection state and is delivered
asynchronously. Each satellite verifies,
persists, and switches its local projection/secret pointer atomically. Current
and previous valid local pairs support bounded offline restart and fallback.

The platform-neutral local store is a small durable boundary shared by the
installation rather than owned independently by the interaction runtime or
control service. It verifies the canonical pull envelope, expected
`satellite_id`, projection digest, exact current compatibility report, and exact
logical-secret closure. It then writes separate immutable projection and secret
files and atomically replaces `selected.json`. The directory name and selected
pointer reuse the Brain-issued `sat_activation_` ID. Activation metadata does
not duplicate global selection operation/revision because rollback can reselect
an existing activation; those monotonically observed values live only in the
pointer. The same pointer carries an optional restart-required activation ID.
Changing either payload generation sets that latch atomically; a lightweight
activation that reuses both generations does not. A pending latch follows any
later selection until deployment clears it after successful consumer restart.
Directory fsync is used where the host platform supports it, while file fsync
and atomic replacement remain required on every platform.

`oracle_satellite_projection_sync.py` is the single installation writer. It is
a bounded one-shot command rather than a long-running third runtime component.
For an established installation it loads the selected local activation, uses
that activation's authoritative Brain URL and credential, requires the strict
non-cacheable JSON response boundary, and installs through the local store. A
selection-only update does not request process restart. Nor does a lightweight
activation that reuses both the prior projection and local-secret generations.
A changed payload generation sets the restart latch, and exit `3` means that
durable latch is pending rather than merely that an activation ID changed.
Platform deployment wrappers clear it only after all installed interaction and
control consumers accept restart. A failed restart or power loss leaves it for
retry, while a crash after restart but before clear can cause one harmless
repeat. This is bounded retry state, not delivery, acknowledgement, applied-
state, or health evidence.
For established installations the command has no URL, credential, polling,
service-name, or arbitrary restart override. Runtime compatibility JSON is
operational evidence and must exactly match the projected report; its file path
and the store/installation identity are bootstrap inputs. The same one-shot
command has a disjoint empty-store provisioning mode that requires both the
credential-free Brain rendezvous URL and a restricted enrollment-credential
file. It uses a dedicated enrollment route once, installs the ordinary envelope,
and never consults those inputs again. Scheduled refresh does not receive them.

Desired, delivered, acknowledged, and applied state are distinct. Delivery
failure is operational drift; generation or known compatibility failure is an
activation blocker.

Enrollment uses a narrowly scoped credential for one declared satellite.
Successful enrollment provisions unique directional operational credentials.
Disabling and decommissioning remain distinct so configuration activation never
pretends to perform remote lifecycle mutation.

Enrollment is authentication plus projection delivery, not a durable workflow.
The Brain validates the selected enabled satellite's enrollment secret and
returns its already selected immutable envelope. V2 records no enrollment row,
consumes no one-time token, rotates no credential, and performs no automatic
credential deletion or delivery. Operators may explicitly replace, disable, or
delete the reusable provisioning credential through existing configuration and
secret operations.

Before a new satellite can pull this desired state, its installation supplies
one `brain_bootstrap_url` outside the canonical bundle. The value locates the
projection service only. It is not projected, normalized into the configuration
revision, or consulted after a valid projection supplies the authoritative
`brain_client.base_url`. Existing satellites prefer their last valid local pair
for offline restart; fresh installations fail closed when first contact is not
possible.

## Migration Architecture

Migration between supported canonical schema versions runs outside runtime
resolution:

1. inventory every field and source;
2. map each source field to a canonical role or explicit retirement;
3. extract raw secrets into logical references and a secret candidate;
4. report conflicts and effective-source mappings;
5. validate the complete candidate generation;
6. activate the complete generation; and
7. reject retired inputs after migration.

A satellite projection migration is installation-wide. It is accepted only
after every declared installed component validates one exact selected
projection. Publishing or selecting Brain-side desired state does not by itself
prove satellite adoption.

## State And Audit

Authored candidates are editable input. Config, secret, activation, projection,
satellite-secret, and satellite-activation generations are immutable
configuration artifacts. The selected
pointer and per-process applied records are small mutable control state.

Operational domain state remains outside configuration. Audit records contain
IDs, actors, timestamps, semantic change summaries, acknowledgements, outcomes,
and sanitized failure data. They never contain raw secret or provider payloads.

Household modes illustrate the boundary: household configuration defines mode
identity and policy, a domain mapping connects it to a provider, and current
on/off state remains provider/runtime truth.

## Failure Behavior

- Brain startup fails closed on selected-generation failure; it does not
  automatically roll back.
- Offline tooling can inspect and select a compatible generation under lock.
- Satellite startup uses its current valid local pair and may fall back once to
  its retained previous compatible pair, reporting durable degraded state.
- Provider unavailability affects readiness, not deterministic validation.
- No activation path executes provider or domain actions.
