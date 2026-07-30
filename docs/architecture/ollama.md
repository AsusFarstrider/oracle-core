# Oracle LLM Surfaces

This document describes the current implementation shape of Oracle's brain-side LLM surfaces.

For the canonical behavioral rules, see
[ollama-policy.md](../contracts/ollama-policy.md).

## Current Slice-One Shape

Oracle now has two LLM-adjacent surfaces:

- `fallback_router`
- provider-specific backend helpers such as the music-domain Ollama helper

There is no top-level `chat` behavior target and no top-level `ollama` rollback
route. Informational requests should be routed toward `facts`, usually by way
of `fallback_router`.

The shared decision parser uses `answer` for a non-executable provider reply.
That mode is currently consumed only by bounded domain helpers such as music
information lookup; it is not a route target and does not replace `facts`.

## `fallback_router`

`fallback_router` is an interpretation-only domain.

It:

- accepts the fallen-through, already-normalized user request
- sends that request through the configured backend model
- returns a bounded proposal:
  - `domain`
  - `normalized_text`
  - advisory `user_id`

It does not:

- answer the user directly
- execute a capability directly
- dispatch to `facts` or another domain on its own

The brain validates the proposal and then performs any domain transition.

## Backend Boundary

`ollama` is the current backend provider.

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

## Warmup And Keep-Alive

Slice-one model policy:

- `fallback_router` is the always-warmed low-latency path
- startup warmup preserves the current keep-alive behavior so the configured model remains resident when possible
- domain-specific backend helper calls may use Ollama when explicitly owned by that domain

## V2 Configuration Reconciliation

The shared inference transport may be defined narrowly in `brain.yaml`.
Fallback-router policy remains Brain-owned, and each consuming domain explicitly
selects its own role. Environment presence, credentials, definition order, or
health never select Ollama implicitly.
