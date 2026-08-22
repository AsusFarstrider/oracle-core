# Windows Satellite Runbook

## Scope

Windows support is a wrapper around Oracle's shared satellite runtime, not a
fork of wake, request, reply, identity, projection, or control behavior. This
guide describes reusable Windows host considerations. Household host values,
device indices, task names, credentials, and validation evidence belong in the
household deployment authority.

The Stage 4 Debian Brain walking skeleton does not certify a Windows satellite
installation lifecycle. Current support claims must identify the exact Windows
version, architecture, runtime revision, projection, and evidence separately.

## Appliance Posture

A room satellite may use a dedicated local Windows account and Task Scheduler
to start the native runtime at login. A separately configured browser task may
open the Oracle satellite UI in kiosk or fullscreen mode. Oracle does not
globally supervise the browser.

For a dedicated wake appliance:

- keep the host awake while plugged in;
- allow display timeout without full sleep;
- disable unrelated notification and background activity where appropriate;
- start the runtime through the repository-owned task installer and wrapper;
- avoid ad hoc launch scripts as the durable entrypoint.

## Canonical Configuration

Canonical task installers select an immutable local projection and secret
activation. The task action must not carry Brain URLs, source association,
device behavior, provider credentials, control credentials, or playback
commands when canonical mode is active. The selected projection supplies
behavior and its owned logical secret references.

During a reviewed cutover, stop existing runtime and control tasks, register
both canonical task definitions without starting them, inspect the actions and
projection selection, arm the one-way boundary, and then start the tasks.

## Browser Microphone Permission

The browser satellite UI uses `getUserMedia` for push-to-talk capture. A kiosk
installation should grant only the selected Oracle origin through the
applicable Edge policy so a restart does not require an interactive permission
prompt. Keep the permitted origin identical to the browser task's configured
Oracle origin.

The tracked kiosk-permission and UI-task installers provide the reusable
configuration surfaces. Origin, browser URL, satellite identity, and insecure-
origin exceptions remain explicit household inputs.

## Audio Model

Preferred behavior is:

```text
input = selected deliberately
output = dynamic Windows default unless explicitly pinned
```

Microphone input may require an explicit device index or stable name. When no
Oracle output override is configured, reply playback should use the current
Windows default output. An explicit output index or name pins output and
disables default-device following.

Windows short-cue playback may require the current WASAPI default output even
when longer reply playback succeeds through the ordinary default path. Treat
cue and reply hot-switch behavior as independently verified host capabilities;
do not change the shared waveform merely to hide device-selection failure.

If output fails, report the selected backend and device evidence. Do not
silently choose an unrelated output.

## Wake Capture

The optional wake-capture collector stages clips under the account's local
Oracle application-data directory. A separate scheduled sync task may transfer
pending clips with a host-specific credential and delete them only after
confirmed transfer. Keys, destinations, clips, and logs are household or
runtime material, not reusable core content.

Wake capture is optional. Its absence must not make a satellite unhealthy when
canonically disabled.

## Installation Inputs

A reusable Windows satellite installation may require:

- a compatible Python interpreter and isolated dependency environment;
- the declared satellite Python dependencies;
- the required Microsoft runtime libraries;
- wake-model runtime resources when wake capture is enabled;
- optional provider or audio facilities selected by the deployment;
- a validated immutable projection and separately supplied secrets.

Discover and validate existing candidates before installing replacements.
Record exact versions, architecture, paths, acquisition source, and validation
results in deployment evidence.

## Validation

Validate at least:

1. scheduled runtime and control task definitions point to tracked wrappers;
2. the selected projection and secret activation load without errors;
3. the local configuration health surface reports the intended readiness;
4. the declared wake model loads when wake is enabled;
5. acoustic wake, STT, Brain request, TTS, and reply playback work for enabled
   voice profiles;
6. reply and cue playback use the declared output behavior;
7. browser microphone access and kiosk restart work when the UI is enabled;
8. restart or login re-establishes the intended appliance state;
9. credentials and runtime logs remain outside immutable application content.

Record physical device indices and observed limitations per household host.
They are evidence, not reusable defaults.

## Troubleshooting

Use the tracked device-listing option before changing configuration. Compare
MME and WASAPI device identities, current Windows defaults, explicit Oracle
overrides, and task-owned arguments. Inspect the task definition, runtime error
log, and local configuration-health response before changing shared runtime
behavior.

Any standard Windows satellite packaging, update, failed-activation recovery,
rollback, and platform-support claim requires its own clean-host lifecycle
evidence. A working private deployment is useful evidence but does not by itself
certify that standard installation profile.
