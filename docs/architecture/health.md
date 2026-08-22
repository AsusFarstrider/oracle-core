# Oracle Health Surface

Oracle exposes shallow Brain liveness at `GET /health` and purpose-owned
readiness under `GET /api/admin/health` and
`GET /api/admin/health/<subsystem>`. The normative semantics are defined in
[health-ownership.md](../contracts/health-ownership.md).

## Ownership

- `health_routes.py` owns HTTP registration and response composition.
- `health.py` normalizes domain/provider results.
- each domain or typed provider owns the actual readiness probe.
- deterministic configuration validation and activation compatibility remain
  separate surfaces.

Every registered readiness request requires the installed
`CanonicalBrainApplicationComposition`. Missing composition returns a
no-store `503`; health never reconstructs configuration from environment,
repository files, or process-local getters.

## Endpoint Roles

`GET /health` is inexpensive process liveness. It does not perform a broad
provider sweep and remains the one intentionally retained root health path.

The admin health family reports the selected canonical capabilities. Disabled
optional capabilities report `disabled`; configured capabilities report their
bounded provider or local-runtime readiness without silently selecting a
different provider.

Current typed probe ownership includes:

- Home Assistant: selected URL, credential, and timeout from immutable runtime
  settings; bounded request to the HA API root.
- Inference: the shared injected `InferenceClient`; bounded Ollama version
  request.
- STT and TTS: the exact installed provider objects and their `status()`
  methods.
- Calendar, news, music, audiobooks, and network/LibreNMS: their installed
  canonical execution objects.
- configuration: selected generation and activation identities, reported
  without secret values.

Provider status is operational evidence, not configuration authority. A
provider outage does not rewrite or invalidate the selected bundle, and a
valid bundle does not imply that an external dependency is reachable.
