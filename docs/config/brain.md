# Brain Configuration

## Canonical V2 Owner

`brain.yaml` is a narrow required role for Brain process behavior and genuinely
shared Brain services such as speech and inference transport. It does not own
household identity, users, rooms, access trust, domain policy, satellites, or a
universal provider registry.

Household semantics live in `household.yaml`; ingress trust in `access.yaml`;
provider selection and policy in each fixed domain role.

At startup the Brain adopts one selected immutable activation, constructs typed
`EffectiveConfig` and `SecretSnapshot` objects, and injects owned fragments into
consumers. It never rereads YAML or uses env/local JSON fallback in canonical
mode.

The pure frozen `BrainRuntimeSettings` construction seam maps the complete
`brain.yaml` role from that adopted snapshot without recreating historical
  getter dictionaries. It carries the exact activation and selection identity,
retains the typed runtime/logging/storage leaves, and resolves operational STT,
TTS, and shared-inference definitions only when their roles are enabled with an
explicit selection. It does not read environment variables, local JSON, the
  authored bundle, or the current selected pointer. The Brain lifespan installs
  this one complete canonical composition before startup work begins.

Brain instance identity, bundle/store locations, command mode, and authoring
mode are bootstrap/deployment metadata and cannot override behavior.

## Executable V2 Leaves

The schema-v1 Brain role is now closed and typed:

- `runtime` owns shared wake arbitration and the Brain's satellite-control
  client timeout;
- `logging` owns only the Brain log level;
- `storage` owns the Memory SQLite and alert JSON persistence mechanisms plus
  bounded Memory retention;
- `speech.stt` admits typed `whisper_cpp` and `fast_whisper` definitions;
- `speech.tts` admits typed `piper` definitions; and
- `inference.shared_backend` admits typed credential-free `ollama` definitions
  and the fallback-router model/timeout override.

Enabled roles select a present typed definition explicitly. Provider maps do
not imply preference or fallback. Machine-specific binary, model, and database
paths are valid only at these owning edges; the generic example uses no
household or user path. Listener URLs, trust, household semantics, domain
providers, and arbitrary plugin/provider definitions remain invalid.

## Canonical Authority

The live Brain is canonical-only. Environment variables, local JSON files, and
retired domain registries are not runtime behavior authorities. Supported
migrations create and validate a complete canonical candidate generation before
activation.

The whisper.cpp provider, when selected, requires an explicitly validated
external executable and model location. It never falls back to a developer
checkout. Fast-Whisper uses its distinct Python dependency and model contract.
