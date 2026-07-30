# Oracle Satellites

This directory contains Oracle's retained satellite runtime implementation and
its supporting clients and services:

- `pc_push_to_talk.py`: desktop/manual integration test client
- `pi_wake_satellite.py`: headless Raspberry Pi wake-word satellite
- `control_service.py`: lightweight local music control plane for Plexamp playback
- `wake_capture/`: optional satellite-side wake clip capture module

## Document Role

This README describes stable satellite architecture, contracts, and setup guidance.

It is not the authoritative source for a household's deployed host, endpoint,
or service values. Those belong to that household's private deployment record.

## Scope Guidance

Reusable satellite work should focus on:

- `pi_wake_satellite.py`
- `pi_runtime/`
- `control_service.py`
- `longform_player.py`

Secondary/test client scope:

- `pc_push_to_talk.py` should be treated as a manual/dev test client
- it is outside normal cleanup and review scope unless a task explicitly targets desktop push-to-talk behavior

## Satellite Contract

Satellites are intentionally thin clients for capability execution.

For local audio, however, the satellite runtime is the authority for:

- local playback state
- active playback session truth
- interruption outcome
- resumability status

They do **not** execute capabilities locally. Instead they:

1. capture audio
2. send audio to Oracle `/stt`
3. send transcript to Oracle `/command`
4. use Oracle `reply_text` from `/command` response
5. send `reply_text` to Oracle `/tts`
6. play the returned reply audio using the response media type
7. reuse a short-lived `session_id` so Oracle can handle follow-up requests in the same conversation
8. poll Oracle for due timers, alarms, and reminders while idle
9. if Oracle is waiting on a confirmation or clarification, allow one same-session follow-up reply without requiring the wake word again

This keeps business logic and capability execution centralized in Oracle for future low-power satellite hardware while still letting the local runtime own local playback truth.

Foreground-audio note:

- the Pi runtime now routes spoken reply, ack, follow-up cue, due-alert audio, and sleep-expiry foreground decisions through one explicit local handoff path
- that coordinator is not a second authority layer; it only normalizes one local speaker-borrowing or speaker-replacement decision at a time

Current cleanup note:

- the contract says satellites should treat Oracle `reply_text` as canonical
- minimal fallback spoken-reply extraction still exists on the Pi path for missing `reply_text`, but normal reply shaping belongs on the brain

## Satellite Control Plane

Local playback control is exposed through a small local HTTP service:

- `POST /control`
- `GET /health`
- `GET /playback-authority`

The control service is intentionally thin in policy, but it is the local runtime boundary for playback authority.

Oracle resolves intents centrally, chooses execution policy centrally, and sends explicit commands to the requesting satellite.
The satellite runtime is still the source of truth for what is actually playing locally and what interruption or resume actions succeeded.

Canonical control-service launch inputs select one installed satellite
projection. Credentials are resolved from that projection and are never passed
as command-line values:

```bash
ORACLE_SATELLITE_ID=test_satellite_alpha \
ORACLE_SATELLITE_PROJECTION_STORE_ROOT=/path/to/projection-store \
ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH=/path/to/runtime-compatibility.json \
python control_service.py
```

Legacy argument-driven adapter launches remain private compatibility behavior
and are not a reusable setup path.

Current control-service endpoint details belong in private household deployment
state.

Phase E direction:

- satellites are not merely remote button-press targets for local audio
- they are the runtime authority for local playback state and playback-session truth
- Oracle-native local music playback is the preferred long-term path on hosts that support it
- Plexamp client control is retired from canonical projections
- interruption-for-reply is a deliberate policy direction, not an accidental side effect
- Oracle-native music capability is enabled per host through:
  - `ORACLE_SUPPORTS_ORACLE_NATIVE_MUSIC`
  - optional `ORACLE_NATIVE_MUSIC_PLAYER_BIN`
- Oracle chooses playback policy; hosts advertise capability rather than backend preference
- Plex remains the resolver and stream source for Oracle-native music
- shared Plex provider access remains available to Oracle-native music; this does not retain Plexamp client control in V2

Canonical projection foundation:

- shared `oracle_satellite_projection.py` validates canonical pull envelopes and
  persists projection and secret payloads separately
- the local selected pointer reuses the Brain-issued satellite activation ID;
  no second interaction-runtime or control-service activation identity exists
- the selected pair can be loaded without Brain access
- satellite dependencies include the pinned RFC 8785 implementation used to
  verify the Brain's canonical bytes and projection digest consistently on
  Linux and Windows
- established installations use one shared one-shot
  `oracle_satellite_projection_sync.py` writer; it derives Brain connectivity
  only from the selected local activation and emits fixed JSON/exit results
- a fresh installation runs that command once with `--brain-bootstrap-url` and
  `--enrollment-credential-file`; the credential file is local, absolute, and
  restricted, and neither input belongs in the recurring scheduler
- a projection or local-secret generation change atomically sets a durable
  restart latch; activation-only reuse of both generations does not
- checked-in Linux and Windows wrappers restart only installed interaction and
  control consumers, never the UI, then clear the latch; interruption leaves it
  for retry and may cause at most one harmless repeated restart
- the Linux systemd timer and Windows Scheduled Task installers use a fixed
  startup-plus-one-minute cadence with overlap suppressed and require an
  explicit enable action
- `oracle_satellite_runtime_config.py` now loads separate immutable interaction
  and control snapshots directly from the selected local activation, resolving
  only component-owned secrets and never rendering canonical values into
  environment or argv behavior inputs
- `ControlServiceSettings` is the frozen internal control-service seam; its
  canonical constructor uses only the immutable component snapshot and typed
  host-local bootstrap
- control-service volume supports both schema-v1 implementations: ALSA card/
  control on Linux and scalar master volume on the current Windows default
  endpoint; `pycaw` is pinned as a Windows-only satellite dependency, and
  missing endpoint support blocks readiness rather than falling back
- `InteractionRuntimeSettings` is the equivalent frozen wake/voice seam; its
  canonical constructor maps
  the immutable interaction snapshot plus typed listener/logging/IPC/installed-
  asset bootstrap data; projected audio, wake, cue, capture, Brain/control
  endpoints, and owned credentials cannot be mixed with another authority
- every interaction-runtime Brain request now accepts that snapshot's Brain
  credential and emits one standard Bearer header; this includes multipart STT,
  commands, TTS, arbitration, polling, activity, and silent Brain-routed control
- canonical interaction settings retain distinct satellite and source IDs;
  wake arbitration sends the installation ID
- Stage 3 authenticates `brain_client` for projection pull and the fixed
  wake-capture upload route; shared
  interaction endpoints receive the preparatory header but do not yet derive
  identity, authorize stable-source claims, or advertise authenticated ingress
- `pi_wake_satellite.py` resolves authority before parsing behavior: the
  canonical store selects only the immutable interaction snapshot plus finite
  host bootstrap, rejects behavioral overrides, and never renders projection
  values back into argv or environment
- `control_service.py` uses the same authority-first boundary: canonical
  startup accepts no behavior argv, rejects behavioral environment
  inputs, derives its native-player and long-form adapters from tracked Oracle
  code, and limits host bootstrap to listener/logging/reply-audio IPC mechanics
- Linux and Windows canonical launchers pass only installation selectors and
  permitted host bootstrap
- canonical systemd templates load only projection bootstrap plus finite
  optional host-bootstrap files; matching Windows runtime/control installers
  use the contract task names
- empty-store first contact is an explicit one-shot enrollment POST using one
  Brain rendezvous URL and a restricted credential file; it installs the normal
  immutable envelope, after which only the projected operational edge is used
- projection pull and wake-capture upload enforce the projected satellite
  credential; shared interaction ingress deliberately does not infer broader
  source authorization from it
- projection adoption, scheduled refresh, wake-capture helpers, and Linux and
  Windows launch definitions are retained implementations

These assets are not a Stage 4 validated standard-installation satellite
profile. Private deployed use is evidence about a household installation, not
clean-host certification. A satellite profile must separately prove declared
dependencies and permissions, actual audio behavior, reboot, update,
failed-activation recovery, and rollback before it is listed as validated.

First-contact example after provisioning the enrollment value into a mode-0600
file with a secure editor:

```bash
satellite/.venv/bin/python oracle_satellite_projection_sync.py \
  --satellite-id living_room_satellite \
  --store-root /var/lib/oracle-satellite/projections \
  --runtime-compatibility /var/lib/oracle-satellite/runtime-compatibility.json \
  --brain-bootstrap-url http://oracle-brain.local:8011 \
  --enrollment-credential-file /var/lib/oracle-satellite/enrollment.secret
```

After success, removing the local enrollment file is a separate explicit
operator action. Ordinary refresh reads the projected Brain edge and never
reopens this file.

The current control-service boundary is documented in the
[control-service architecture](../docs/architecture/control-service.md).

Phase 8 result:

- `control_service.py` is now a thin entrypoint over `control_service_runtime/`
- HTTP handling, auth, idempotency/cache, adapters, long-form control, and reply-audio state now live in dedicated modules
- Linux and Windows launchers execute the selected shared satellite runtime
  rather than maintaining per-host source forks

## Audiobook Playback Hooks

Audiobooks use a separate long-form playback path from Plex music.

Oracle resolves Audiobookshelf content centrally, then sends a generic `play_longform_audio` command to the local control service.
The Pi does not need to know about Audiobookshelf directly.

Canonical long-form playback commands and provider credentials are supplied by
the validated projection and code-owned host bootstrap. They are not authored
as secret-bearing command examples.

`longform_player.py` auto-detects `ffplay` or `mpv` and tracks state in `/tmp/oracle-longform-player/`.
For predictable deployments, it is better to standardize one player package across all satellites instead of relying on ambient package differences.

Phase E guardrail:

- audiobook playback is already valuable and should not be destabilized just to achieve code reuse
- if Oracle-native music playback would require invasive reuse of audiobook-specific long-form/session assumptions, prefer a separate small music playback path instead

To provision a retained player implementation for development or an
uncertified household satellite:

```bash
scripts/install-satellite-player.sh
```

Optional preference override:

```bash
scripts/install-satellite-player.sh ffplay
scripts/install-satellite-player.sh mpv
```

To exercise the retained Debian satellite bootstrap tooling:

```bash
scripts/bootstrap-satellite.sh
```

Household-specific service installation, identities, environment locations, and
host overrides belong to the private deployment authority. The reusable
`satellite/start_control_service.sh` wrapper is limited to runtime setup plus a
final `exec` and derives its default interpreter from the adjacent satellite
tree.

## Raspberry Pi Wake-Word Satellite

`pi_wake_satellite.py` flow:

1. Listen for wake word with openWakeWord (`.onnx` / `.tflite` model)
2. Capture utterance after wake-word detection
3. Send audio to Oracle `/stt`
4. Send transcript to Oracle `/command`
5. Use Oracle `reply_text`
6. Send text to Oracle `/tts`
7. Play the returned reply audio locally using the response media type
8. If Oracle returns `pending_confirmation` or `pending_clarification`, play a distinct local cue and open a short same-session follow-up listen without the wake word

The same Pi runtime foreground-handoff seam now also covers:

- spoken reply
- ack tone
- follow-up listen cue
- due timer / alarm / alert audio
- sleep-timer expiry foreground decisions

Phase 7 result:

- `pi_wake_satellite.py` is now a thin entrypoint over the shared runtime package in `pi_runtime/`
- wake/capture, Oracle calls, wake-loop mechanics, request/reply pipeline, alert polling, and audio backends are split into dedicated runtime modules
- host-specific audio behavior is now represented by tracked backend code plus tracked per-host override files

Host-audio boundary note:

- host-specific capture selection must be expressed through the existing satellite runtime inputs `--input-alsa-device` or `--input-device-index`
- shared wake runtime code must not grow hidden per-host audio-opening edits
- if one host needs ALSA-name capture and another needs a PortAudio index, that difference belongs in service/env wiring, not in a private fork of `pi_wake_satellite.py`

Wake-model boundary note:

- wake model selection should be expressed through `ORACLE_WAKE_MODEL_PATH` or `--model-path`
- per-host model choice belongs in tracked service/env wiring, not in private runtime forks
- `.tflite` model selection must fail closed if `tflite_runtime` is unavailable on that host

Phase D wake-tuning note:

- playback-aware wake tuning is now part of the shared runtime shape
- prefer a quieter playback-state polling cadence with a small anti-jitter hold window
- the anti-jitter hold itself is negligible in cost; the real operational cost is playback-state polling frequency

Current shared runtime module map:

- `pi_runtime/cli.py`
- `pi_runtime/runtime.py`
- `pi_runtime/wake.py`
- `pi_runtime/wake_loop.py`
- `pi_runtime/request_runtime.py`
- `pi_runtime/reply_runtime.py`
- `pi_runtime/alerts_runtime.py`
- `pi_runtime/local_control.py`
- `pi_runtime/models.py`
- `pi_runtime/oracle_client.py`
- `pi_runtime/audio/`

## Wake Capture Module

The satellite runtime now also includes an optional isolated wake-capture module:

- `wake_capture/collector.py`
- `wake_capture/storage.py`
- `wake_capture/sync.py`
- `wake_capture_sync.py`

Purpose:

- collect short wake-related clips for later manual review and wake-word training

It does not:

- change wake thresholds
- change routing
- retrain models
- alter Oracle command behavior

Current capture behavior:

- event classes:
  - `activation`
  - `near_threshold`
- canonical saved format:
  - WAV
  - mono
  - `16 kHz`
  - `16-bit PCM`
- local staging path:
  - Linux: typically `/tmp/oracle-wake-capture`
  - Windows: `%LOCALAPPDATA%\Oracle\wake-capture`

Sync model:

- separate reusable sync entrypoint via `wake_capture_sync.py`
- legacy private deployments may use rsync, SCP, systemd timers, or Windows
  tasks during migration
- canonical mode loads the selected interaction projection, uploads complete
  WAV/JSON pairs to the fixed authenticated Brain route, and uses projected
  cadence/deletion/retention
- canonical scheduling accepts selectors plus local storage bootstrap, not SSH
  or remote-path behavior; the concrete scheduler remains deployment-owned

Restarting a continuous canonical sync consumer forces one immediate HTTP upload
pass before it resumes the projected cadence. Retired daily scheduling remains
private migration history rather than reusable installation authority.

This feature is intended as a plugin-style utility:

- when disabled, it should have near-zero practical cost
- when enabled, it adds a rolling in-memory buffer, bounded event writes, and scheduled sync

## Deployment Notes

Live satellite hosts, active service arguments, and operational tuning belong
to the selected household deployment authority and installed deployment state.
This README contains only reusable architecture, setup, and troubleshooting
guidance.

## Prereqs

- Raspberry Pi with working mic + speaker
- Oracle brain reachable on LAN
- one explicitly selected and validated openWakeWord model supplied by the
  household installation; model files are not bundled implicitly

Clean core includes Oracle-owned `sounds/alarm.wav` and `sounds/timer.wav`
defaults. The alarm asset is an alternating attention tone played before Oracle
speaks the alarm due time. The timer asset is a short ascending completion
chime. Runtime configuration may continue selecting separately supplied
compatible sound assets through the existing logical alarm/timer asset
contract.

## Install

```bash
scripts/bootstrap-satellite.sh
```

That retained development tool attempts to install its declared Debian
packages, select a compatible Python interpreter, create an isolated
environment, install the satellite requirements, and validate a long-form
player. Its success is not clean-host lifecycle certification.

The retained runtime currently declares Python `3.9+`; the bootstrap fails
clearly when no compatible interpreter is available.

New-host service placement and host permission steps belong to the selected
household deployment procedure.

If openWakeWord model resources are missing:

```bash
. .venv/bin/activate
python - <<'PY'
from openwakeword.utils import download_models
download_models()
print("openWakeWord resources downloaded")
PY
```

## Run

```bash
python pi_wake_satellite.py --source hallway-satellite
```

Useful options:

- `--oracle-url <oracle-brain-url>`
- `--model-path /path/to/wake-model.onnx`
  The runtime also accepts the legacy uppercase filename if that is what the host still has.
- `--wake-threshold 0.5`
- `--wake-log-threshold 0.2`
- `--wake-retry-cooldown-seconds 1.0`
- `--input-device-index N`
- `--input-alsa-device ALSA_NAME`
- `--output-device-index N`
- `--input-gain 1.0`
- `--vad-threshold 0.015`
- `--vad-noise-multiplier 1.6`
- `--vad-noise-offset 0.006`
- `--vad-release-multiplier 1.15`
- `--vad-release-offset 0.003`
- `--vad-max-speech-threshold 0.42`
- `--vad-max-silence-threshold 0.30`
- `--silence-seconds 0.45`
- `--max-record-seconds 8.0`
- `--playback-gain 1.0`
- `--no-ack-tone`
- `--ack-tone-gain 0.16`
- `--followup-silence-seconds 0.3`
- `--followup-max-record-seconds 4.0`
- `--followup-speech-start-timeout-seconds 2.5`
- `--post-playback-block-seconds 2`
- `--conversation-timeout-seconds 90`
- `--alerts-poll-seconds 2`
- `--music-control-url http://127.0.0.1:8021`
- `--music-duck-volume 18`
- `--music-duck-trigger-threshold 0.12`
- `--list-devices`

## Service Ops (Pi)

```bash
sudo systemctl status oracle-satellite.service --no-pager -l
sudo systemctl restart oracle-satellite.service
journalctl -u oracle-satellite.service -f
systemctl status oracle-wake-capture-sync.service --no-pager -l
systemctl is-enabled oracle-wake-capture-sync.timer
```

Each satellite deployment should record its complete host-specific audio,
wake-model, capture, and tuning inputs in its private deployment authority. Use
that pattern instead of keeping a separate private runtime source fork.

## Troubleshooting

- Wake never triggers:
- lower `--wake-threshold`
- increase `--input-gain`
- confirm input device via `--list-devices`
- prefer `--input-alsa-device` on hosts where PortAudio indices are unstable or where ALSA conversion is required for the working mic path

- Service exits with openWakeWord resource errors:
- rerun `openwakeword.utils.download_models()`

- Playback too loud:
- reduce `--playback-gain`

- Wake word is hard to trigger while music is playing:
- verify `control_service.py` is running locally on `:8021`
- verify interaction and control `/health/config` report the same selected
  projection activation and no legacy-input findings
- inspect whether the duck level is low enough for the room and speaker placement
- inspect whether playback-state polling is too aggressive for the host; prefer tuning poll cadence before removing the anti-jitter hold

- Commands fail while audiobook playback is active:
- verify the brain includes the updated bare audiobook transport parsing for phrases like `pause audiobook`
- verify the satellite script includes the local playback-interrupt path before capture
- if wake detection succeeds but STT text is still garbled during audiobook playback, treat that as a remaining capture-quality issue rather than a routing regression

- Control-service audiobook commands fail with `couldn't reach the playback satellite` even though `:8021` is up:
- inspect `/playback-authority` first
- verify the configured `longform_player.py` commands use `/usr/bin/python3` or another real interpreter on that host
- a missing interpreter can surface as a local `POST /control` `400`, which Oracle may report as a playback-satellite failure

- Frequent self-triggering after playback:
- keep cooldown enabled
- keep a short `--post-playback-block-seconds` window enabled
- avoid overly low wake threshold

- Command executes but wrong room/device selected:
- refresh Home Assistant cache on Oracle (`scripts/sync-home-assistant.py`)
- confirm Oracle brain is on a build that strips wake-word residue server-side

- Replies like `Confirmed.` or `Done.` after junk audio:
- satellites should now stay silent for `system.ignore`
- if generic `Done.` still returns for real requests, verify the satellite script and Oracle brain have both been restarted on current code

- Latency feels high before the acknowledgment tone:
- inspect satellite logs for `Pipeline timing ...`
- inspect Brain logs for the timing output emitted by the configured STT provider
- Fast-Whisper is the current deployed STT provider; whisper.cpp is retained as
  an alternate implementation but is not freshly verified
- compare capture, upload, transcription, and reply timing before assigning the bottleneck
- the acknowledgment tone now marks audio-upload completion, not transcription completion

- Service fails once on restart with `device -1`:
- verify that the configured capture device exists and is ready before service startup
- inspect the service restart policy and device initialization logs rather than assuming recovery

- Timers/reminders never speak:
- confirm the satellite is running updated code with alert polling enabled
- verify Oracle returns due items from `/alerts/pending?source=<your-source>`

- Follow-up questions lose context too quickly:
- increase `--conversation-timeout-seconds`

## Desktop Push-To-Talk

Manual desktop test client:

```bash
python pc_push_to_talk.py
```
