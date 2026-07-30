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

The provider bridge owns transport and normalized result parsing. It does not
collect Oracle evidence, write the suggestion inbox, own review policy, or
execute suggestions. HTTP, SSH CLI, and explicit mock transport are finite
typed options; disabled Suggestions selects none of them.

## Configuration And Secrets

The independently enabled `suggestions` section of
`domains/information.yaml` selects one adapter and its bounded timeout and
model options. Credential-bearing URLs and passwords are logical secrets.
Executable paths needed by a selected runtime belong at the provider edge.
The SSH CLI adapter also requires the shared strict SSH host-verification
contract; it has no trust-on-first-use or unchecked-host fallback.

Only the selected adapter's secret references are resolved from the active
secret generation. Raw values do not enter configuration representations,
packets, logs, UI payloads, saved responses, or diagnostics. There is no
implicit provider or transport fallback.

## Evidence And Storage

Collectors receive the installed canonical application composition and use
its typed read-only Home Assistant, calendar, media, information, provider, and
network views. Missing capabilities are reported as unavailable; collectors do
not reopen legacy settings or read provider databases directly.

Packets contain bounded run metadata, redacted evidence sections, collector
failures, and prior review outcomes needed to reduce repeated suggestions. A
partial collector failure is represented rather than hidden. Debug artifacts,
when enabled, are redacted before storage.

Suggestion runs and review history are durable Oracle operational data. Review
records may preserve decisions, notes, corrections, rejection reasons, and
repeat-suppression preference without becoming executable policy.

## Lifecycle And Future Boundary

Generation is asynchronous from the UI: Oracle records the run, performs one
provider call with a declared timeout, validates and stores the redacted result,
then updates the local run status. The UI may poll Oracle's status; Oracle does
not continuously poll the provider for speculative progress.

A future pull model may expose a bounded read-only Oracle evidence API to an
advisory provider. Oracle must remain the authority for redaction, pagination,
authorization, and audit. Direct provider access to Oracle databases, files,
control endpoints, or host commands remains outside this boundary.
