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

There is no top-level `chat` target or top-level `ollama` rollback route.
Informational misses go through `fallback_router`, which may propose `facts`;
Ollama remains a backend provider only.

## Route Refinement

After capability evaluation, routing applies a second refinement stage.

Current route refinement covers:

- active-media transport
- audiobook sleep-timer refinement
- strong session-context refinement

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
