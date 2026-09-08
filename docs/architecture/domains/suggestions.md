# Suggestions Domain

## Purpose

Suggestions is an optional System Mode advisory surface. It gathers read-only
Oracle context, builds a redacted packet, submits that packet through a selected
provider bridge, stores structured suggestions, and presents them for human
review. It plans; it does not act.

## Authority Boundary

Suggestions has no voice routing, command dispatch, satellite behavior,
provider writes, automatic remediation, service restart, or shell-action
authority. Provider output is advisory only and cannot execute Oracle actions.

One Suggestions-owned packet, prompt, result schema, and normalized intake sits
above two intentional backend families: direct OpenAI/Luna and OpenClaw for a
remote or local model. A backend owns provider translation and transport only.
It does not collect Oracle evidence, write the suggestion inbox, own review
policy, or execute suggestions. Direct Luna, OpenClaw HTTP, OpenClaw SSH CLI,
and explicit mock execution are finite typed options; disabled Suggestions
selects none of them. WebSocket is not a supported canonical or internal option.

Direct Luna receives only the bounded/redacted Suggestions contract packet. Its
request uses provider-native structured output and `store: false`, with no
Oracle tools, workspace, memory, persistent conversation, unrelated household
context, or execution authority. OpenClaw remains the optional advisory
environment for models not hosted by Oracle. OpenClaw agent calls use one fresh,
run-derived session and one bounded model pass; accumulated conversation history
is not part of the Suggestions contract.

## Configuration And Secrets

The independently enabled `suggestions` section of
`domains/information.yaml` selects exactly one backend/provider and its bounded
timeout, model, and output options. Selection is explicit canonical
configuration; there is no automatic backend failover, voting, arbitration, or
hybrid execution. Credential-bearing URLs, passwords, and cloud credentials are
logical secrets. Executable paths needed by a selected runtime belong at the
provider edge. OpenClaw agent and infer modes receive the explicitly configured
provider model.
The SSH CLI adapter also requires the shared strict SSH host-verification
contract; it has no trust-on-first-use or unchecked-host fallback.

Only the selected provider's secret references are resolved from the active
secret generation. Raw values do not enter configuration representations,
packets, logs, UI payloads, saved responses, or diagnostics. There is no
implicit provider or transport fallback.

## Evidence And Storage

Collectors receive the installed canonical application composition and use
its typed read-only Home Assistant, calendar, media, information, provider, and
network views. Missing capabilities are reported as unavailable; collectors do
not reopen legacy settings or read provider databases directly.

Packets contain bounded run metadata, requested-window payload-free Memory
events, explicitly current snapshot sections, per-collector availability and
provenance, omission counts, and prior review outcomes. Source sections are
limited to 20,480 serialized bytes and the whole packet to 131,072 bytes. A
disabled or unconfigured collector is unavailable evidence. A partial collector
failure is represented rather than hidden; if every requested collector is
unavailable, Oracle records that failure without asking a provider to guess.
Secret-bearing keys and credential patterns in free-form logs/errors are
redacted before transport or current-exchange storage.

Suggestion runs and review history are durable Oracle operational data. Review
records may preserve decisions, notes, corrections, rejection reasons, and
repeat-suppression preference without becoming executable policy.

Suggestions storage is part of the Memory-owned schema and transaction
boundary. The latest redacted packet and provider response overwrite one
current exchange row instead of accumulating sidecar files. A single Memory
retention executor protects active and genuine unreviewed work, scrubs raw
evidence and diagnostics after their approved horizons, removes mock work on
its shorter horizon, and retains completed provider envelopes only while no
durable suggestion depends on them.

Schema `0011_suggestions_advisory_review` adds run-level collection status,
provider failure class, and deterministic suppression count. Direct Luna and
OpenClaw HTTP/SSH responses pass through the same Oracle intake: a non-empty
title, summary, suggested action, and bounded evidence string are required. The prompt
requires packet-derived evidence; Oracle does not semantically prove a free-text
claim against the packet, so human review remains the final trust boundary.
HTTP responses and SSH CLI output are bounded before persistence.
Configuration, transport, agent-execution, response-validation, and unexpected
integration failures remain distinct. Unexpected exceptions leave a terminal
run. A valid item in a partially malformed response may be retained while the
run records partial response-validation truth.

When an explicitly negative/corrective review requests repeat suppression,
Oracle does not store a materially identical later item unless it contains new
normalized evidence. New evidence permits a linked advisory item. Accepted
items remain durable for later human work; neither review nor a historical
future-automation marker grants execution authority.

## Lifecycle And Future Boundary

Generation is asynchronous from the UI: Oracle records the run, performs one
provider call with a declared timeout, validates and stores the redacted result,
then updates the local run status. The UI exposes collection, provider mode,
model authority, failure, provenance, and suppression without an execution
control. It may poll Oracle's status; Oracle does not continuously poll the
provider for speculative progress.

A future pull model may expose a bounded read-only Oracle evidence API to an
advisory provider. Oracle must remain the authority for redaction, pagination,
authorization, and audit. Direct provider access to Oracle databases, files,
control endpoints, or host commands remains outside this boundary.
