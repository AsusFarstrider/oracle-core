# Satellite Configuration

This document records the canonical interaction-runtime configuration boundary
for the retained reusable satellite implementation.

It answers a narrow question: what configuration exists for the satellite
runtime and how it is validated and secured. Shared current rules are defined
in [validation.md](validation.md) and [security.md](security.md).

## Canonical Runtime

The canonical interaction runtime
loads one immutable locally selected projection/secret activation, adopts only
its typed `interaction_runtime` component, and can restart from that selection
while the Brain is unavailable. It never reads authored YAML or legacy
behavior inputs.

The top-level entrypoint is `satellite/pi_wake_satellite.py`, while the runtime
configuration surface lives primarily in `satellite/pi_runtime/`.

The implemented V2 model is documented in
[shareable-config.md](shareable-config.md). In
that target shape, managed runtime settings and satellite-specific logical
credential references belong in `satellites.yaml`; household `source_id` and
room/user associations belong in `household.yaml`. The runtime receives a
minimal immutable projection rather than reading either file.

Every enabled satellite projection carries one common `brain_client` URL and
directional credential reference for authenticated refresh. The field is not
limited to voice runtimes and does not add a third compatibility component.

The executable schema-v1 leaves are now fixed. `audio` owns typed device
selection, VAD/follow-up timing, cue IDs, polling, gains, and local playback
interruption/ducking behavior. `ui` owns display/touch enablement and ordered
code-known profile/layout/page selections. `wake` owns model selection,
thresholds, cooldowns, arbitration-client timing, playback suppression, and
diagnostic capture policy. Linux may select the ALSA capture edge; Windows may
not. All platforms may use the shared default, named PortAudio, or indexed
PortAudio edges implemented by the runtime.

Canonical wake capture sync deliberately omits the legacy SSH host, user, key,
and transport fields. Projection transport is Oracle-owned and authenticated;
the bundle describes whether/when retained diagnostics sync, not an arbitrary
file-transfer mechanism. Listener/bind addresses remain deployment metadata;
only the first-contact `brain_bootstrap_url` is an external rendezvous locator,
while the projected `brain_client.base_url` is authoritative after adoption.

The Brain's canonical applied-runtime view is narrower than the projection. Its
frozen fleet settings retain satellite/source identity, capabilities, the
applied projection activation ID, and playback-capable Brain-facing control
edges. They do not copy satellite-local audio, UI, wake, local-control, or Brain
client endpoint settings. Projection pull, enrollment, and wake-upload
authentication remain owned by the selected projection resolver rather than a
second credential path in the Brain's applied settings.

## Current Grouping

The current satellite config surface is grouped around these families:

- brain connectivity and source identity
- wake-model selection and wake-detection thresholds
- audio input and output selection
- reply-audio and follow-up timing settings
- local alert-sound selection for timers and alarms
- local playback interruption and ducking settings
- config HTTP surface settings
- wake-capture enablement, storage, and sync settings
- visual UI capability, profile, layout, and page settings
- logging and runtime-mode flags

## Resolution Shape

Runtime behavior is resolved from the selected immutable projection and local
secret generation. Finite host bootstrap supplies installation mechanics only;
it cannot override projected behavior. The runtime exposes sanitized
configuration reporting as described in [validation.md](validation.md).

## Local Alert Sounds

The interaction projection selects code-known timer and alarm cue IDs. The
installed satellite profile resolves those IDs to distributable Oracle assets
or other validated installation-owned assets. These settings affect only local
playback after the Brain has scheduled and delivered an alert.

## Deployment Shape

Deployment metadata may select the projection store and finite host facilities,
but systemd and installer inputs do not become a second behavior authority.

## Wake Model Selection

Wake-model selection is now a first-class part of the satellite config surface.

The selected canonical satellite entry declares the model identity and
installation-owned model location. Host discovery validates that exact model;
environment variables and command-line flags cannot override it.

The runtime infers the backend from the selected model artifact:

- `.onnx` selects the ONNX path
- `.tflite` selects the TensorFlow Lite path

If the selected backend runtime is unavailable, startup must fail closed instead of silently falling back to a different backend.

## Support Status

The satellite runtime and its Linux and Windows launch/provisioning assets are
retained reusable implementations. Stage 4's validated installation profile is
the provider-free Brain only; it does not certify a standard satellite, audio,
wake, STT, or TTS profile. These components require their own clean-host
dependency, permission, reboot, update, recovery, and rollback evidence before
being listed as validated installation profiles.
