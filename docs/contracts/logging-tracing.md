# Oracle Logging And Tracing

## Purpose

This document defines Oracle's logging and tracing contract.

It defines:

- the standard log vocabulary for brain, satellite, and control-service paths
- the required correlation fields for request-path logs
- the trace expectations for end-to-end observability

Related contract:

- [memory.md](memory.md) defines structured Memory events, evidence references,
  retention, and Memory-owned correlation expectations.

## Ownership Rule

Logging follows runtime ownership boundaries.

The ownership contract is:

- the brain logs request interpretation, route selection, dispatch planning, dispatch execution, and reply construction
- an enabled satellite runtime logs capture, transcript submission, command response receipt, reply playback, and follow-up capture
- the local control service logs control command receipt, command ids, adapter execution, and result status

Each layer logs its own decisions and outcomes and does not reconstruct another layer's internal story as a substitute for that layer's logs.

## Correlation Fields

### Required Fields

The required correlation fields for request-path logs are:

- `source`
- `session_id`
- `route_target`
- `dispatch_hook`

These fields must appear when known. If a field is not known yet at a given event point, it may be omitted or logged as `-`.

### Optional Fields

Additional optional fields where relevant are:

- `correlation_id`
- `status`
- `action`
- `command_id`
- `failure_class`
- `owning_component`
- `reply_chars`
- `transcript_chars`
- elapsed timings in milliseconds for expensive operations

`correlation_id` is the Memory-aligned cross-table tracing field. It should be created at the earliest reliable boundary and propagated where practical, but its absence must not break runtime behavior.

## Structured Memory Events

Logs and Memory events are related but not interchangeable.

Logs remain raw operational evidence. Memory events are structured, queryable facts recorded by the owning layer.

Memory may store evidence references back to raw logs, but it must not copy raw logs wholesale or become a continuous raw-log-eating daemon.

## Standard Event Vocabulary

The following event names define the canonical standard vocabulary for core request and control paths:

- `command_received`
- `route_chosen`
- `dispatch_planned`
- `dispatch_executed`
- `reply_built`
- `pending_created`
- `pending_resolved`
- `fallback_invoked`
- `control_command_received`
- `control_command_sent`
- `control_command_result`

Additional events may exist, but these names are not casually replaced.

For the fallback-router path, the following additional request-path events are also standard:

- `fallback_router_requested`
- `fallback_router_succeeded`
- `fallback_router_failed`
- `router_failure_path_taken`
- `failure_path_selected`

For seam-contract and playback-authority inspection, the following events are also standard where applicable:

- `dispatch_contract_failed`
- `playback_authority_degraded`
- `playback_authority_interrupt_started`
- `playback_authority_interrupt_finished`
- `playback_authority_resume_lookup`
- `playback_authority_resume_skipped`
- `playback_authority_resume_decision`
- `foreground_handoff`

For session inspection and follow-up classification, the following events are also standard where applicable:

- `session_created`
- `session_reset`
- `session_expired`
- `pending_expired`
- `followup_resolution_observed`
- `followup_bound`

For Oracle Memory live write failures, the following warning event is standard:

- `memory_event_write_failed`

## Operator Inspection

During fallback-router soak, operators should be able to confirm the request flow directly from the brain journal.

Useful journal patterns include:

- `journalctl -u oracle-brain.service -n 200 --no-pager`
- `journalctl -u oracle-brain.service --since "10 minutes ago" --no-pager | rg "fallback_router_|facts_|router_failure_path_taken|command_received|route_chosen|dispatch_planned|dispatch_executed|reply_built"`

For a healthy fallback-router request, operators should expect to see:

1. `command_received`
2. `route_chosen`
3. `dispatch_planned`
4. `fallback_router_requested`
5. `fallback_router_succeeded` or `fallback_router_failed`
6. facts dispatch when the fallback path selects `facts`
7. `dispatch_executed`
8. `reply_built`

During soak, malformed router output must remain visible as:

- `fallback_router_failed`
- `router_failure_path_taken`

When a failure path is chosen at a seam boundary, logs should also make the selected ownership visible through:

- `failure_class`
- `owning_component`

## End-To-End Trace Expectations

For a normal `/command` request on the brain, logs must allow an operator to follow:

1. request received
2. route chosen
3. dispatch planned
4. dispatch executed
5. reply built

When the fallback-router path is exercised, logs must also allow an operator to distinguish:

1. fallback router requested
2. fallback router succeeded or failed
3. router failure path taken when malformed output occurs
4. facts requested, succeeded, or failed when the request is routed to `facts`

For seam contract failures and shared-runtime authority failures, logs must allow an operator to distinguish:

1. contract failure at the seam entrypoint
2. transport or control-service failure across the control-plane boundary
3. authority mismatch between playback truth and handoff or resume behavior
4. the selected failure owner via `failure_class` and `owning_component`

For the production Pi satellite path, logs must allow an operator to follow:

1. capture started or ended
2. transcript obtained
3. command response received
4. reply playback started, ended, or failed
5. same-session follow-up capture when applicable

Reply-playback logs must report the actual playback outcome.

They must not emit a misleading completed-style terminal event after a playback exception or output-device failure.

For the control service, logs must allow an operator to follow:

1. command received
2. command id
3. adapter path taken
4. result status

For the playback-authority and foreground-handoff path, logs must allow an operator to follow:

1. degraded authority state when present
2. interruption attempt start and finish
3. resume lookup and skip reasons
4. one final foreground handoff decision per event

## Configuration Lifecycle Events

Validation, migration, activation, rollback, secret rotation, projection
delivery/adoption, and selected-versus-applied drift produce sanitized events.
Events may include `config_revision`, opaque `secret_generation_id`,
`activation_generation_id`, `satellite_id`, `source_id`, finding codes,
acknowledgements, and outcomes.

They must not include secret values or fingerprints, authored private absolute
paths, raw provider payloads, credentials, or secret-bearing compatibility
values. Internal actors and household request sources are separate fields.
