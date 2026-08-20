# Speech Stack

This document records the current speech input and output stack used by Oracle.

The brain provides STT and TTS services, while satellites handle local capture and playback.

## Brain Speech Services

STT and TTS are exposed as brain API services.

- STT is provided through `/stt`
- TTS is provided through `/tts`

Provider implementations are selected by canonical configuration. Their
current status is deliberately distinct:

- Fast-Whisper is the primary integration deployment's current STT provider and a retained reusable
  implementation.
- Piper is the primary integration deployment's current TTS provider and a retained reusable
  implementation.
- whisper.cpp is a retained alternate STT provider that was previously
  functional but is not currently deployed, freshly live-verified, or
  validated as a standard-installation profile.

The provider-free Stage 4 minimal profile disables STT and TTS through
canonical configuration. That walking skeleton neither removes nor certifies
any voice provider.

## STT Structure

The current STT path accepts uploaded audio bytes through the brain API.

Fast-Whisper uses its declared Python/runtime dependency and separately managed
model arrangement. The retained whisper.cpp adapter instead discovers and
validates a compatible external `whisper-cli` executable and model; the
upstream source checkout is not vendored in clean core.

Input normalization, including whether `ffmpeg` is required, is declared and
validated per provider and supported input path. Fast-Whisper and whisper.cpp
must not be treated as interchangeable dependency or model arrangements.

## TTS Structure

The primary integration deployment's current TTS path synthesizes WAV output
through the Piper provider.
Piper is separate from both STT implementations and has its own optional
installation-profile requirements.

The TTS layer owns one versioned clip cache. Its identity includes the exact
synthesis text, provider, model, Piper configuration content identity, and
cache version; case, whitespace, or configuration changes therefore cannot
reuse a semantically different clip. The older fixed-filename and normalized
phrase layers are not part of the canonical cache.

The cache retains at most 4,096 clips and 256 MiB. Clips idle for more than 90
days expire first, then least-recently-used clips are evicted to satisfy both
bounds. Reads refresh access time, writes are locked and atomically replaced,
and bounded maintenance runs at startup and after writes. The pre-versioned
cache is identity-unsafe and is reported for discard at the coordinated
cutover rather than reused.

## Satellite End-To-End Flow

At a high level, the satellite speech flow is:

1. capture PCM audio locally
2. convert PCM to WAV
3. send audio to `/stt`
4. send text to `/command`
5. request reply audio from `/tts`
6. play WAV reply locally

## Timing Instrumentation

Timing instrumentation exists on both sides of the speech path:

- the server STT provider records timing around transcription work
- the satellite request pipeline records timing across capture, STT, command, TTS, and playback stages

## V2 Configuration Reconciliation

Shared Brain STT/TTS provider definitions belong narrowly in `brain.yaml`.
Machine-specific executable/model paths have no household-specific core
defaults. Satellite capture/playback settings arrive through projection. The
existing Brain/satellite speech responsibility boundary does not change.
