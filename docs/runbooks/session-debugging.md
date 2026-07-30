# Session Debugging

This runbook is the operator reference for inspecting the Brain
`/api/voice/session` surface.

## What This Runbook Is For

Use this runbook when you need to inspect current session state, pending follow-up state, or continuation context for a given `source + session_id`.

## Primary Endpoint

Use:

- `GET /api/voice/session?source=...&session_id=...`

Example:

```bash
curl -sS "http://127.0.0.1:8011/api/voice/session?source=satellite-example&session_id=session-123"
```

If the session is missing or expired, the endpoint returns `404`.

## Session Inspection Flow

1. call `GET /api/voice/session`
2. check for `404`
3. inspect `pending_state`, `active_context`, and `lifecycle`
4. use `session_meta` and `derived` to interpret precedence, expiry, and anchor state

Stop on missing session: if `/api/voice/session` returns `404`, stop inspecting
fields and treat the problem as missing, expired, or not-yet-created session
state first.

## Key Fields

### `session_meta`

Session identity and timing metadata.

Important fields:

- `source`: current source key
- `client_session_id`: caller-supplied session id
- `effective_session_id`: session id currently used by the brain
- `fallback_generated`: whether the brain had to synthesize an effective session id
- `created_at`: session creation time
- `refreshed_at`: most recent refresh time
- `session_timeout_seconds`: session lifetime
- `pending_timeout_seconds`: pending-state lifetime

### `active_context`

Current conversational anchor.

Important fields:

- `route_target`: current anchored route
- `dispatch_hook`: current anchored dispatch path
- `action`: current anchored action when present
- `anchor_strength`: anchor strength for continuation

`strong` means a deterministic capability or pending-state anchor. `weak` means conversational continuity only.

### `pending_state`

Current pending confirmation or clarification state.

Important fields:

- `type`: pending kind
- `domain`: pending domain owner
- `payload`: pending data surface

### `lifecycle`

Most recent classified session events.

Important buckets:

- `session`: creation, reuse, reset, expiry, and user-context mutations
- `pending`: pending creation, explicit clear, or timeout expiry
- `followup`: active-context bind or clear

### `derived`

Current interpreted session state.

Important fields:

- `session_active`: whether the session is still active
- `pending_active`: whether a pending item is still active
- `pending_expired`: whether pending state has expired
- `anchor_strength`: current derived anchor strength
- `follow_up_resolution_order`: current precedence winner
- `waiting_on_user`: whether Oracle is actively waiting on a pending reply
- `next_route_target`: what Oracle would consult first if the user replied now
- `pending_domain`: current pending owner when present
- `session_seconds_remaining`: remaining session window
- `pending_seconds_remaining`: remaining pending window when present

## Common Checks

### Why did `yes` or `no` not work?

Inspect:

- `pending_state`
- `derived.pending_active`
- `derived.pending_expired`
- `lifecycle.pending`

Bare `yes` and `no` only work when a valid pending confirmation is still active.

### Why did a follow-up route a certain way?

Inspect:

- `derived.follow_up_resolution_order`
- `active_context`
- current live playback state when transport is involved

Resolution order is always:

1. `pending_state`
2. `active_context`
3. general routing

### Why did a Home Assistant continuation work or fail?

Inspect:

- `active_context.route_target`
- `active_context.anchor_strength`
- whether the session still exists

Home Assistant continuation depends on the current anchor plus the downstream Home Assistant conversation path.

## Common Patterns

- `session_active: true`, `pending_active: true`
  Oracle is actively waiting for confirmation or clarification.
- `session_active: true`, `pending_active: false`, `pending_expired: true`
  The broader session still exists, but the pending item timed out.
- `follow_up_resolution_order: pending_state`
  Oracle will consult pending state before any active-context or convenience follow-up path.
- `follow_up_resolution_order: active_context`
  No pending item is active; Oracle may bind a follow-up through the current strong anchor.
- `follow_up_resolution_order: general_routing`
  There is no active pending item or strong anchor; the next reply will be treated as a fresh routed request.
- `404`
  The session itself is missing or expired.

## Expiry Behavior

Current timeout values:

- session timeout: `90` seconds
- pending timeout: `30` seconds

Current expiry behavior:

- if pending expires first, `pending_state` is cleared
- if the full session expires, `GET /api/voice/session` returns `404`
- if a timed-out session is recreated with the same `source + session_id`, old session context is cleared before the new session becomes active
