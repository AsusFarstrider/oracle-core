# Home-Automation Domain Architecture

The home-automation domain turns authenticated provider-state evidence into
durable household workflows. It owns correlation, timing, verification,
repetition, and stop conditions. It does not own notification rendering,
recipients, suppression, or delivery channels.

```text
provider event -> authenticated ingress -> canonical mapping
  -> domain controller -> durable wait -> fresh provider verification
  -> typed notification capability
```

## Boundaries

- `home_automation_routes.py` authenticates provider event ingress.
- `home_automation/events.py` maps provider evidence and enforces lifecycle
  ownership.
- `home_automation/controller.py` owns correlation, waits, verification,
  bounded repeats, retries, and typed notification calls.
- `runbook_kernel/` owns persistence and lifecycle transitions.
- `notifications/` owns communication policy and delivery.
- `admin_home_automation_routes.py` exposes read-only sanitized run status and
  history for operator diagnostics.

Provider-native identifiers remain at the configuration and adapter edge.
Canonical definitions supply typed mappings, timing, verification, and
notification references; application code does not embed a household's
entities or workflow instances.

## Durable Operation

The newest canonical state is retained as operational evidence per mapped
subject. Duplicate event identities and strictly older evidence are rejected
before controller activation, preserving event ordering across Brain restart.

Waiting runs may resume from durable state. A mutation that was running during
restart is marked interrupted and is not replayed automatically. Scheduler
ownership remains partitioned by domain so another controller cannot resume a
home-automation run.

Admin inspection is read-only. It reports sanitized definition identity,
latest canonical state, active or latest run, due time, notification counts,
and recent operation steps. It does not fetch live provider state, submit a
notification, or expose provider credentials.

## Configuration

Canonical Home Assistant domain configuration owns definitions and mappings.
The controller receives one typed frozen definition and never reads authored
configuration files directly. Configuration activation does not create,
mutate, resume, or cancel an operational run.
