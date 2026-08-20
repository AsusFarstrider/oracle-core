# Oracle LLM Fallback Contract

## Purpose

This document defines Oracle's brain-side LLM fallback contract after removal
of the old user-facing `chat` domain and the legacy top-level `ollama` rollback
route.

Oracle may still use Ollama as a backend provider, but `ollama` is not a
behavioral route target.

## Scope

This contract defines two LLM-related surfaces:

1. `fallback_router`
2. domain-owned backend helpers that call Ollama, such as bounded music
   information helpers

The facts domain owns factual and informational user interaction. If fallback
interpretation determines that a request is factual, conversational,
explanatory, creative, or otherwise not a deterministic action, it should
propose `facts`.

## Shared Principles

### 1. Deterministic handling keeps priority

If Oracle already has enough bounded deterministic evidence to route, clarify,
or fail cleanly, LLM fallback must not replace that outcome.

### 2. The brain remains the routing authority

`fallback_router` proposes a domain and normalized text.

The brain:

- validates the proposed domain;
- decides whether the proposal is dispatchable;
- performs the domain transition.

All routing must remain:

- `fallback_router -> brain -> domain`

Never:

- `fallback_router -> domain`

### 3. Behavioral domains are separate from the backend provider

`fallback_router` and `facts` are behavioral domains.

`ollama` is a backend provider.

Backend transport does not own prompt policy, semantic interpretation, or
user-facing reply logic.

### 4. Interpretation failure is not facts

Malformed or invalid `fallback_router` output is a router failure.

It is not:

- a facts classification;
- a capability execution failure.

### 5. Execution-level failures stay domain-owned

`fallback_router` must not smooth over downstream runtime failures such as:

- dependency unreachable;
- timeout;
- auth or config failure;
- satellite or control-service execution failure.

Those failures remain visible as domain failures.

## `fallback_router` Contract

### Purpose

`fallback_router` is a bounded second-pass interpretation surface after
deterministic routing misses.

### Allowed output

`fallback_router` may propose only:

- `domain`;
- `normalized_text`;
- advisory `user_id`.

It does not:

- execute anything;
- answer the user directly;
- return executable permission on its own.

### Facts transition rule

If `fallback_router` determines the request belongs to facts or any removed
general-chat behavior, it returns:

- `domain = facts`

It does not call `facts` directly.

The brain then dispatches to `facts`. Facts routing still respects the facts
domain configuration and must fail gracefully when fact lookup is disabled.

### Input boundary

Input to `fallback_router` is the fallen-through user request after existing
safe brain-owned preprocessing, such as wake-word stripping and stable
normalization already used by Oracle.

`fallback_router` may apply additional bounded normalization for domain rescue.

It must not become a second broad preprocessing layer.

It must not depend on rich evolving context to do basic rescue work.

### Advisory `user_id`

`user_id` is advisory only.

The brain:

- validates it against canonical configured users;
- ignores it for unsupported domains;
- ignores it if invalid.

Explicit user wording always wins over a router suggestion.

Router-suggested `user_id` must not mutate session state.

### Router failure behavior

If `fallback_router` output is invalid or malformed:

- the brain takes the router failure path;
- the brain returns a canonical brain-owned spoken reply;
- the failure is logged as a distinct operator-visible failure class.

Slice-one canonical reply:

- `I'm sorry, I didn't understand what you said.`

Slice-one failure code:

- `fallback_router_invalid_output`

## Music-Domain Ollama Behavior

### Ordering rule

Inside the music domain, deterministic handling retains priority over Ollama
assistance.

Ordering:

1. deterministic routing and parsing
2. deterministic media rescue checks
3. deterministic clarification when defensible
4. bounded Ollama assistance only where this document explicitly allows it
5. deterministic failure if the allowed rescue path is exhausted

### Bounded tie-break selection

Ollama tie-break selection is allowed inside the music domain when:

- the candidate set is small;
- the candidate set is explicit and inspectable;
- deterministic cross-domain rescue has already had priority;
- deterministic clarification has already had priority;
- no defensible deterministic outcome already exists.

It must not override a valid deterministic outcome.

It must not be used on large or weak candidate sets.

### Best-guess prompting after `not_found`

Music-domain Ollama best-guess prompting after `not_found` is allowed only for
generic title-only play intents.

It is not allowed for:

- broader structured artist/album/track play requests;
- informational questions.

### Informational music questions

If a request is a music informational question that the music domain can answer
from Plex or library evidence, Oracle must use deterministic domain lookup
first.

Ollama may answer only if deterministic library lookup bottoms out or cannot
support the question.

If a question mixes library-grounded and general-facts aspects, Oracle must
prefer answering the library-grounded part first when possible.

## V2 Configuration Reconciliation

Shared Ollama transport/configuration may live narrowly in `brain.yaml` because
it is a Brain-wide inference service. Each consuming domain still owns whether
and how it selects that role. Environment and local-file settings become
importer inputs after canonical cutover; they cannot override domain selection
or the deterministic-first policy.

Canonical runtime composition constructs one immutable typed inference client
and injects it into fallback routing, facts summarization, music inference,
warmup, and health. The shared client owns transport settings and mechanics
only. Consumer prompts, parsing, fallback thresholds, and executable decisions
remain with their domain owners. Canonical consumers must not reopen legacy
configuration getters.
