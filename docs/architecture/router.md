# Oracle Router

The router is Oracle's brain-side capability selection layer.

Routing lives in `server/oracle_app/routing.py` and uses a `CapabilityRegistry`.

Routing chooses a semantic target; dispatch and handlers perform execution.

It currently routes among:

- `system`
- `home_assistant`
- `calendar`
- `news`
- `music`
- `audiobook`
- `facts`
- `fallback_router`

## Current Shape

The current routing path has two structural stages:

1. capability-registry evaluation
2. post-route refinement

Input text is normalized on the brain before capability evaluation begins.

## Capability Registry

The capability registry evaluates capabilities in priority order until one returns a decision.

If no capability returns a decision, the registry returns:

- `fallback_router`

Current registered capability surfaces in the implementation are:

- system commands
- implied home
- time/date
- math and conversion
- alerts
- audiobook
- calendar
- news
- pending audiobook
- pending music
- pending home
- probable audiobook title
- music
- forecast
- weather
- keyword home
- facts, when explicitly enabled
- fallback router

The fallback capability remains the last route surface.

Stage 6 retains this ordered registry. Slice 2 added a canonical owner-validation
step for fallback proposals and a shared strict value parser; later utility
slices extend the individual owner probes and grammars. The work is
consolidation, not a replacement router. A supported Stage 6 request is
dispatchable only after its deterministic owner can parse it or return a
bounded clarification.

There is no top-level `chat` target or top-level `ollama` rollback route.
Informational misses go through `fallback_router`, which may propose `facts`;
Ollama remains a backend provider only.

## Route Refinement

After capability evaluation, routing applies a second refinement stage.

Current route refinement covers:

- typed active-alert context before ambiguous media transport words
- active-media transport
- audiobook sleep-timer refinement
- strong session-context refinement

An alert context is eligible only when the canonical session contains a typed
stable alert, occurrence, or schedule identifier. This Slice 2 refinement
selects the system owner; later alert slices own the actual occurrence-aware
mutation and must fail honestly until they implement it. Absent typed alert
state, existing media and domain precedence remains.

Fallback-normalized deterministic text re-enters `choose_route()` and proceeds
only when the canonical route target agrees with the proposed domain. System
utility proposals are stricter: fallback must preserve the normalized request
exactly, so it can classify but cannot rewrite numbers, dates, recurrence, or
mutation parameters. A fallback audiobook user override is accepted only when
that configured user's id, name, or alias occurs explicitly in the original
request. Rejected proposals fail as `fallback_router_unvalidated_proposal` and
never reach a domain handler.

## Current API

- `GET /health`
- `POST /api/conversation/route`

Example request body:

```json
{
  "text": "Turn on the kitchen lights",
  "source": "kitchen-satellite",
  "session_id": "demo-001"
}
```

Example response body:

```json
{
  "target": "home_assistant",
  "confidence": 0.82,
  "reason": "Matched home automation keyword: turn on",
  "normalized_text": "turn on the kitchen lights"
}
```

For the full request/response contract and the relationship between routing and
dispatch, see [api.md](api.md) and [runtime.md](../contracts/runtime.md).
