# Wake Capture Subsystem

This document records the current wake-capture subsystem as implemented.

`satellite/wake_capture/` is a neighboring subsystem used by the shared Linux
and Windows satellite runtime. It sits beside `satellite/pi_runtime/` rather
than inside the main runtime package.

## Package Structure

The current package structure under `satellite/wake_capture/` is:

- `collector.py`
- `config.py`
- `models.py`
- `ring_buffer.py`
- `storage.py`
- `sync.py`

## Responsibility Split

The current subsystem is split by responsibility:

- `collector.py`: frame intake, event observation, and pending clip completion
- `ring_buffer.py`: rolling pre-roll audio storage
- `storage.py`: local WAV and JSON sidecar writing
- `sync.py`: pending-capture sync and retained-local-file management
- `config.py`: wake-capture configuration loading and shape construction
- `models.py`: event, clip, sync, and config data structures

## Event Structure

The current subsystem captures two event types:

- `activation`
- `near_threshold`

The collector observes wake scores and records capture events from the runtime integration points.

## Audio Structure

The subsystem stores audio as:

- WAV container
- mono
- 16 kHz
- 16-bit PCM

The collector maintains rolling pre-roll audio and combines it with post-roll audio before storage writes complete.

## Metadata Structure

The subsystem writes a JSON sidecar for each captured clip.

The stored metadata currently includes event fields plus basic audio-format metadata for the written clip.

## Local Storage Structure

The local storage structure is split into two trees:

- `pending/`
- `synced/`

Pending captures are written under the `pending/` tree.

If local copies are retained after sync, they are moved under the `synced/` tree.

Within those trees, files are organized by source, day, and event type.

## Sync Structure

The sync layer reads pending files from local storage and syncs them to the configured server path.

The current sync implementation supports:

- local-path sync
- remote-host sync through Linux `rsync` or OpenSSH `scp`
- `auto`, `rsync`, and `scp` transport selection
- delete-after-sync local cleanup
- retained-local-file movement into `synced/`
- pruning of old retained local files

Canonical mode groups only matching WAV/JSON sidecars, uploads one completed
pair per request to the fixed
Brain satellite-capture route, and leaves both files pending on failure. A
strict success permits projected deletion or movement to `synced/`; orphaned
files remain pending for operator inspection.

The Brain authenticates the projected operational satellite credential, checks
the sidecar source against the selected projection, validates bounded mono PCM
WAV and metadata shapes, and writes under a deployment-owned archive root. It
generates the satellite/date/event path and a content-derived capture ID; client
filenames and remote paths are ignored. The JSON sidecar is the completion
marker. Identical retry is idempotent, while contradictory archive state fails
closed. This is filesystem persistence, not an upload database, session,
chunking protocol, delivery queue, or separate credential lifecycle.

## Runtime Integration Shape

The shared satellite runtime integrates wake capture through the main
wake/runtime loop on Linux and Windows.

At the current integration points:

- the runtime builds the wake-capture collector
- incoming input frames are appended into the collector
- wake-score observations are reported to the collector
- activation events are reported to the collector on wake detection

This keeps wake-capture collection attached to the runtime's wake-processing path while remaining outside the main `pi_runtime` package.

## V2 Configuration Reconciliation

Wake-capture behavior, devices, storage policy, and sync settings become
satellite projection fields owned by the managed satellite record. Bootstrap
identity and enrollment locate/prove the projection but cannot override those
fields. Existing environment/CLI inputs remain importer evidence until that
satellite process cuts over completely.

The projection contains collection and retention policy, not raw delivery
mechanics. Legacy sync host, user, SSH key, server-path, and transport settings
are retired and cannot be reconstructed as scripts or provider-native commands.
An enabled sync policy is activatable only when the target satellite runtime
declares support for a code-owned sync mechanism. Canonical Linux and Windows
launch definitions run the code-owned helper continuously so its sleep cadence
comes from the selected projection; the old daily timer/task remains V1-only.
