# Oracle Dispatch

This document records the current dispatch structure on the brain.

Dispatch happens after routing in the `/api/conversation/command` flow.

At a high level, dispatch has two stages:

1. build a `DispatchPlan`
2. execute that plan through the handler registry

## Typed Dispatch Boundary

Canonical execution validates every plan through one target-owned payload DTO
before a handler runs. All ten route targets have distinct executable payload
schema classes, backed by shared immutable context and failure primitives. The
finite cross-target field vocabulary rejects arbitrary extension fields.

The temporary internal `DispatchPlan` transport shape still includes:

- `target`: the dispatch target selected by routing
- `hook`: the planned upstream action name for that target
- `payload`: the target-owned DTO's serialized handler payload
- `status`: the current dispatch state
- `result`: private domain/provider detail normalized through a typed target
  outcome before leaving the registry

`DispatchPlan` is not a public command response. The canonical conversation
API exposes only the finite `ConversationResult` contract.

## Planning

Dispatch planning is target-specific at a high level:

- text-oriented targets build `<target>.execute` hooks
- `facts` planning builds `facts.lookup` with the original user utterance and normalized query
- `fallback_router` planning builds `fallback_router.decide` with the brain-normalized fallen-through request
- Home Assistant planning uses normalized text
- `system` planning classifies the normalized text into a system action and hook

## Registry Execution

After planning, dispatch execution is registry-driven.

The handler registry resolves the dispatch target to a registered handler and delegates execution to that handler.

Current registered handler targets:

- `system`
- `home_assistant`
- `calendar`
- `facts`
- `fallback_router`
- `music`
- `audiobook`
- `news`

This allows the dispatch layer to route execution by target without embedding all handler logic in one central execution block.

## Split-Path Note

On the split path, `fallback_router` execution returns a proposal, not a direct capability execution result.

The brain then:

- validates the proposed domain
- takes the router failure path if the proposal is invalid
- or dispatches to the selected domain

This keeps the transition brain-mediated instead of allowing direct `fallback_router -> domain` execution.

## Unknown Targets

If the registry does not have a handler for the requested dispatch target:

- dispatch status becomes `failed`
- dispatch result includes `unknown_dispatch_target`

Unknown system operations likewise fail as `system.unknown_operation`; they do
not fall through to the cache-refresh maintenance action.
