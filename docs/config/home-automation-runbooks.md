# Home-Automation Runbook Configuration

Canonical Home Assistant event mappings and bounded automation definitions are
owned by `domains/home-assistant.yaml`. They are selected only through an
immutable canonical configuration activation.

Event mappings are the only configuration location in this surface where
provider entity IDs belong. They map allowlisted provider evidence to canonical
subjects. Automation definitions may select only Oracle-owned finite lifecycle
operations and policy fields declared by the schema; they cannot introduce
commands, scripts, URLs, provider service names, credentials, arbitrary
expressions, or untyped notification text.

The event-ingress credential is a logical secret reference resolved from the
selected secret generation. Missing, ambiguous, disabled, or invalid mappings
fail closed. There is no JSON-file, local-config, or environment-variable
runtime precedence in clean core.

See [../contracts/home-automation-runbooks.md](../contracts/home-automation-runbooks.md)
for execution and lifecycle behavior.
