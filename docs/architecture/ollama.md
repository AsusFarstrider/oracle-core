# Oracle LLM Surfaces

This document describes the current implementation shape of Oracle's brain-side LLM surfaces.

For the canonical behavioral rules, see
[ollama-policy.md](../contracts/ollama-policy.md).

## Current Implementation And Stage 7 Boundary

The current composition injects one `InferenceClient`, with these callers:

- `fallback_router`
- Facts evidence summarization
- provider-specific backend helpers in Music

There is no top-level `chat` behavior target and no top-level `ollama` rollback
route. Evidence-backed informational requests may be proposed as `facts` by
fallback; creative/general-assistant requests return the fallback consumer's
valid unsupported outcome.

The shared decision parser uses `answer` for a non-executable provider reply.
That mode is currently consumed only by bounded domain helpers such as music
information lookup; it is not a route target and does not replace `facts`.

## `fallback_router`

`fallback_router` is an interpretation-only domain.

It:

- accepts the fallen-through, already-normalized user request
- sends that request through the configured backend model
- returns one bounded semantic outcome: `resolved`, `unresolved`, or
  `unsupported`
- includes domain, minimally normalized text, and optional advisory `user_id`
  only for `resolved`

It does not:

- answer the user directly
- execute a capability directly
- dispatch to `facts` or another domain on its own

The brain validates the proposal and then performs any domain transition.

## Backend Boundary

The execution boundary now contains exactly two provider implementations:
`ollama` and explicitly opted-in `openai_luna`. Fallback and Facts each have an
independent configured order and whole-chain timeout. One call is made per
eligible provider; operational or consumer-contract failure may advance, while
a valid semantic result is terminal. A failed consumer/provider relationship is
temporarily bypassed and automatically becomes eligible after its bounded
cooldown.

Provider-native transport lives in `inference_bridges.py`. Luna uses a bounded
Responses API request with structured output, `store: false`, no tools, and a
canonical secret. Ollama's new ordered path makes one transport attempt so
provider failover cannot multiply its historical retry delay. Ollama keeps its
mature JSON-output mode and Oracle applies consumer validation; sending the full
schema as an Ollama 0.20.2 grammar was rejected and crashed the runner. Both
fallback and Facts now use their provider-independent `execute` contracts. The
mature `llm_bridge.py` call remains in place only for Music's explicitly local
compatibility helpers.

Music remains explicitly bound to the selected local Ollama compatibility
provider. A Luna-only configuration therefore has no Music inference helper;
Music's deterministic behavior still works. OpenClaw Suggestions remains a
separate advisory edge.

The shared inference bridge is transport-only.

It owns:

- transport
- timeout / retry behavior
- returning response data or errors

It does not own:

- prompts
- semantic interpretation
- domain selection
- user-facing reply shaping

## Current Warmup And Keep-Alive

Current local-model policy:

- `fallback_router` warms only its first selected local Ollama provider, if any
- startup warmup never generates a Luna request
- domain-specific backend helper calls may use Ollama when explicitly owned by that domain
- Luna is never warmed by generation or selected for Music

## V2 Configuration Reconciliation

Shared inference transport may be defined narrowly in `brain.yaml`.
Fallback-router and Facts policy remain consumer-owned, and each explicitly
selects its provider order. Environment presence, credentials, definition
order, or health never enables or selects Luna or Ollama implicitly. Valid
unresolved, unsupported, and insufficient outcomes are terminal; only
operational or contract failure may use the next configured provider.

`/api/admin/health/inference` reports configured consumer orders, safe
provider/model identities, and cooldown/bypass eligibility without generating
or exposing prompts or credentials. `/api/admin/health/ollama` remains the
local transport/version diagnostic for the compatibility provider. Bounded live
Luna/Phi access and quality evidence is recorded in the Slice 4 fallback and
Slice 5 Facts evaluation reports; neither implies production activation.
