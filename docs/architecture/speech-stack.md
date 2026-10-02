# Speech Stack

This document records the current speech input and output stack used by Oracle.

The brain provides STT and TTS services, while satellites handle local capture and playback.

## Brain Speech Services

STT and TTS are exposed as brain API services.

- STT is provided through `/api/speech/stt`
- TTS is provided through `/api/speech/tts`

Provider implementations are selected by canonical configuration. Their
current status is deliberately distinct:

- Fast-Whisper is the primary integration deployment's current STT provider and a retained reusable
  implementation.
- Piper remains a supported selectable TTS implementation. The approved Stage 8
  addendum adds Pocket/Federation as the production default; activation evidence
  is tracked in the Stage 8 acceptance ledger.
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

The `TtsProvider` boundary in `server/tts.py` provides Piper, Pocket, and explicit
disabled implementations. Canonical `brain.yaml` selects one typed definition.
`application_speech.py`, composition, and `/api/speech/tts` consume the same
provider contract and return the existing completed WAV response. Both engines
emit PCM WAV suitable for current satellite decoders. There is no streaming,
satellite, playback, or UI redesign and no automatic Pocket-to-Piper fallback.

Pocket construction is cheap. Lifespan explicitly initiates daemon warmup;
model/voice loading is also lazy on synthesis. The model and exported voice
state stay resident on CPU. A provider lock serializes warmup and generation;
`copy_state=True` preserves the accepted voice state for consecutive requests.
Health checks dependency presence and local assets without synthesis/model
loading; it reports not loaded, warming, load failure, missing assets, or ready.
Provider errors map through existing `TtsError`/HTTP 503 semantics.

The managed full-production profile installs both engines. The accepted voice
is copied byte-for-byte, never retrained or retuned. Its SHA-256 is
`0faae5b80a8d531aac54fba1b5eb2d092d542f5eb3e5104a15505ae4c2cc99e9`.
Its selected installed path is
`/srv/oracle/selection/active/deployment/assets/tts/pocket/oracle-federation-computer.safetensors`.
The model and tokenizer reproduce the prototype's pinned English assets using
local paths rather than runtime downloads.

The TTS layer owns one versioned clip cache. Its identity includes the exact
synthesis text, provider, model, provider configuration content identity, and
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
3. send audio to `/api/speech/stt`
4. send text to `/api/conversation/command`
5. request reply audio from `/api/speech/tts`
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

## Cache preservation and warming

Both providers use shared `CachedTtsProvider` clip operations and unchanged v2
bounds, expiry, LRU, and atomic writes. Piper's serialized key is byte-for-byte
unchanged. Pocket additionally hashes model YAML, model weights, tokenizer,
exported voice bytes, and Pocket/PyTorch version identities. File-stat-keyed
digest reuse avoids rehashing model weights for each utterance; an observed
asset change reloads residency and cannot reuse the prior clip identity.

The cache has no plaintext reverse index. Retained audio hashes cannot recover
arbitrary original text. Warm only exact texts available in authoritative
Oracle source/records, without reconstructing hashes or adding a plaintext
speech index. Piper clips coexist and remain available when Piper is selected.
Normal retention still applies to both providers; bound migration warming to
available cache capacity. Record cache preservation and warmed text provenance
in acceptance evidence, without publishing household speech content.
