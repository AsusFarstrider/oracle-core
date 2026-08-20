# Provider Role Registry

This is the Stage 5 ownership ledger for Oracle's current external-I/O seams.
The machine-readable authority is
[`provider-role-registry.json`](provider-role-registry.json). It records every
role's owner, consumers, health owner, failure vocabulary, cache owner,
configuration authority, implementations, and characterization tests.

This ledger is not runtime configuration and does not create a universal
provider abstraction. Provider selection remains in the owning domain or in a
narrow shared Brain role. The registry also records justified non-bridge seams:
observation adapters, network/control executors, speech and inference providers,
purpose-specific media proxies, and satellite-local control.

The provider-bridge package is fully reconciled by the ledger. Its formal
domain translators remain provider bridges. Network observation DTOs/probes are
classified by their actual role. Canonical service/router/SSH execution now
lives behind typed network platform adapters; the remaining provider-bridge
control functions are isolated V1 compatibility scheduled for Slice 11 deletion. OpenClaw
remains Suggestions advisory transport. No speculative adapter is promoted by
this record: the existing WebSocket module remains recorded only as part of the
currently selectable/validated OpenClaw adapter surface and is not made active.

The Slice 3 boundary is now:

- domains own question interpretation, ranking, matching, confirmation,
  expected outcomes, fallback, session policy, and replies;
- provider bridges own provider requests, authentication, endpoint mechanics,
  provider payload parsing, light normalization, and domain-scoped provider
  errors;
- web routes alone translate provider/domain failures to HTTP responses;
- `InferenceClient` is the one canonical typed Ollama dependency, while each
  consumer retains its prompt and decision policy;
- health composition, caches, media streaming, network policy/platform execution, and satellite
  playback retain the owners named in the ledger.

Verification is enforced by `tests/test_provider_role_registry.py`, which
checks the schema, role set, referenced paths/tests, and complete coverage of
the provider-bridge implementation modules.
