# Oracle Shared Inference and Fallback Contract

Status: ratified V2 Stage 7 contract implemented through Slice 5 for both shared
consumers.

## Purpose

This document defines Oracle's bounded shared-inference contract after removal
of the user-facing `chat` domain and the legacy top-level `ollama` route.

Oracle may use Luna or Ollama as inference providers. Neither provider is a
behavioral route target, a factual authority, or an execution owner. This is a
narrow exception to the normal one-provider-per-role rule, not a generalized
LLM framework or provider marketplace.

## Scope and ownership

Stage 7 shared inference has two semantic consumers:

1. `fallback_router`, which proposes a bounded second-pass interpretation; and
2. Facts summarization, which presents already-retrieved evidence.

Each consumer owns its prompt policy, input minimization, semantic result,
validation, failure mapping, and final Oracle behavior. Provider implementations
own only authentication, request/response transport, provider payload parsing,
timeouts, and provider-specific operational errors.

Inference consumers must not branch on provider or model identity. Provider-
specific prompt adapters are allowed behind the consumer boundary when required
to preserve an existing model's quality, but every adapter must satisfy the same
consumer-owned semantic contract.

## Shared rules

### Deterministic authority

Deterministic routing, parsing, validation, clarification, and clean failure
retain priority. Oracle must remain functionally correct when Luna and Ollama
are both disabled, unavailable, or unconfigured. Inference cannot invent facts,
parameters, dates, durations, units, identities, recipients, rooms, targets,
recurrence, permissions, or executable state.

### Per-consumer provider order

Fallback routing and Facts summarization may each configure an ordered subset
of enabled shared-inference providers. Their order is independent. A provider
is considered only when it is explicitly enabled and selected by that consumer.
Credential presence never enables cloud inference, and no empty or invalid order
silently chooses a provider.

The allowed Stage 7 provider implementations are Luna and Ollama only. There is
no automatic model discovery, voting, cheapest-model selection, generalized
registry, or arbitrary third-provider extension point.

### Failover boundary

The next configured provider may be attempted only when the preferred provider
has an operational failure or violates the consumer contract. Examples include
transport, timeout, authentication, service, rate-limit, malformed-response, and
schema/contract-validation failure.

A valid semantic result is terminal. In particular:

- fallback `unresolved` does not try another provider;
- fallback `unsupported` does not try another provider; and
- Facts `insufficient` does not try another provider.

Oracle never asks multiple providers for a semantic vote and never retries a
provider merely because its valid answer was inconvenient.

Health may temporarily bypass a known-unhealthy preferred provider to avoid
repeated interactive delay, but recovery must make it eligible again. Bypass is
operational state, not a change to configured order or semantic authority.

### Trust and observability

Cloud inference is explicit opt-in. A cloud request contains only the minimum
consumer-owned packet and must not contain secrets, unrelated history, arbitrary
Memory data, unrelated household state, raw diagnostics, Music prompts, or
OpenClaw evidence packets. Logs, traces, diagnostics, and exports must not expose
credentials or raw sensitive prompts.

Provider/model identity, selected consumer order, outcome class, timeout,
failover/bypass, and recovery must be operator-visible without making provider
payloads part of the public domain contract.

## Fallback router semantic contract

Fallback is a bounded second pass after deterministic routing misses. It returns
exactly one of these successful semantic outcomes:

- `resolved`: a supported domain proposal and minimally normalized text, with an
  optional advisory configured user identity;
- `unresolved`: the request cannot be classified confidently enough to propose a
  supported domain; or
- `unsupported`: the request is understood but lies outside Oracle's supported
  product surface, including creative generation and general conversation.

`unresolved` and `unsupported` are valid successful inference outcomes, not
provider failures. Both lead to concise Brain-owned replies and no dispatch.
Malformed or contract-invalid output is a provider/consumer contract failure and
may activate the next configured provider.

For `resolved`, the router proposes only domain, normalized text, and optional
advisory `user_id`. It never executes or answers. The Brain validates the domain,
identity, and dispatchability, then re-enters the selected owner's ordinary
deterministic parser, validation, session, and clarification path:

`fallback_router -> Brain -> domain`

Never:

`fallback_router -> domain`

Explicit wording wins over advisory identity. Invalid or unsupported identities
are ignored and never mutate session state. A downstream provider or execution
failure remains owned by the selected domain and is not retried as a routing
question.

Facts is a valid resolved domain only for requests within the evidence-backed
Facts contract. Greetings retain their bounded deterministic owner. Jokes,
stories, role-play, open-ended conversation, and other creative requests are
unsupported; they are not disguised as Facts queries.

The Slice 4 runtime implements this shape. Unresolved and unsupported are valid
terminal results with distinct Brain-owned replies. Invalid output is a contract
failure and may advance to the next configured provider.

## Facts summarization semantic contract

Facts inference receives only the user question and normalized evidence packet
owned by Facts. A successful result is one of:

- `summary`: a concise answer supported by the supplied evidence; or
- `insufficient`: the supplied evidence cannot reliably answer the exact
  question.

`insufficient` is a valid terminal result. It must not trigger a secondary model
or authorize model knowledge outside the evidence. Malformed, generic,
unsupported, or evidence-contradicting output is contract failure and may try the
next configured provider. If inference is absent or exhausted, Facts preserves
its deterministic evidence-based presentation and normalized provider result.

## Music compatibility disposition

Music's existing Ollama-assisted intent extraction, bounded candidate selection,
generic-title best guess, and library-grounded information help remain local-
Ollama compatibility consumers through Stage 7. Their mature prompt and fallback
behavior must not be rewritten for provider symmetry, and configuring Luna for
fallback or Facts must not send Music traffic to Luna. Stage 9 owns any later
decision to migrate these consumers.

Inside Music, deterministic routing, parsing, media rescue, matching, and
clarification retain priority. Bounded inference cannot override a defensible
deterministic result, operate on weak/unbounded candidates, or smooth over a
downstream playback/provider failure.

## Suggestions inference disposition

Suggestions is a separate opt-in advisory contract, not an ordered
shared-inference consumer. Its canonical configuration explicitly selects
direct OpenAI/Luna or OpenClaw/local-model execution. Direct Luna may reuse the
OpenAI transport implementation, but it receives only the bounded/redacted
Suggestions packet and does not inherit fallback/Facts order or failover.
OpenClaw uses the configured local model and a fresh session per run. Suggestions
retains its own normalization, review-only behavior, storage, health, and
failure contract; neither backend automatically switches, votes, or executes.

## Current compatibility and migration boundary

Canonical composition injects one immutable `InferenceClient` with
exactly two provider implementations, independent fallback/Facts orders,
bounded one-attempt-per-provider failover, and health-aware cooldown/recovery.
The provider-independent `execute` method accepts only a named approved consumer,
its bounded prompt/schema, and its consumer-owned validator. It returns safe
provider/model/attempt identity alongside the validated semantic value.

Fallback now calls `execute` with its owned prompt, exact schema and validator.
Facts now calls `execute` with its owned evidence packet, exact result schema and
semantic validator. A valid `insufficient` is terminal. The retained local
`generate` compatibility method remains only for Music's explicitly local
helpers; this preserves mature Music behavior without allowing Luna traffic.

Luna uses `gpt-5.6-luna` through the Responses API with strict structured output,
`store: false`, no tools, and bounded output. Its credential is resolved only
from the immutable canonical secret snapshot when an enabled consumer selects
it. Provider diagnostics expose no credentials or raw prompts. The generic
inference health endpoint reports configured eligibility/cooldown without making
a generation request; the Ollama endpoint retains its local version check.

Ollama intentionally uses provider-native JSON mode rather than compiling the
full consumer schema into a grammar; Oracle performs the exact same semantic
validation after generation. This preserves the mature Phi path and avoids an
observed Ollama 0.20.2 grammar-parser crash. The difference is transport-level
and does not leak provider or model policy into fallback.

Canonical configuration remains the only authority. Environment variables and
local files are importer inputs only and cannot override enablement, consumer
order, cloud opt-in, or deterministic-first behavior.
