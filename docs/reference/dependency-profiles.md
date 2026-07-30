# Oracle dependency profiles

Oracle separates the smallest Brain production runtime, optional host-capability
profiles, and clean-core test tooling. A standard installation validates a host
interpreter, leaves its package environment untouched, and creates or reuses an
immutable native Python environment from the matching complete hash lock.

| Profile | Direct declaration | Complete lock | Python baseline | Purpose |
| --- | --- | --- | --- | --- |
| `minimal-brain` | `server/requirements.txt` | `server/requirements.lock` | CPython 3.13 on the Stage 4 Debian tuple | Provider-free Brain production runtime |
| `fast-whisper` | `server/requirements-fast-whisper.txt` | `server/requirements-fast-whisper.lock` | CPython 3.13 on the Stage 4 Debian tuple | Additive Fast-Whisper STT implementation |
| `wake-satellite` | `satellite/requirements.txt` | `satellite/requirements.lock` | CPython 3.11 on Linux | Retained wake/audio satellite runtime; TensorFlow Lite currently prevents Python 3.13 resolution |
| `clean-core-test` | `server/requirements.txt` plus `requirements-dev.txt` | `requirements-test.lock` | CPython 3.13 on the CI baseline | Required test tooling; never installed into production environments |

The lock file bytes are part of each Python-environment identity along with the
exact interpreter, ABI, platform, architecture, and selected profile. Install
with pip's `--require-hashes` option. The direct declarations remain the small
reviewable inputs; locks contain the complete resolved graph and accepted
artifact hashes. Regenerate locks with `pip==25.3` and `pip-tools==7.5.2` under
the profile's declared Python baseline using
`--allow-unsafe --strip-extras --generate-hashes`.

The wake-satellite profile is retained reusable functionality, not part of the
provider-free Stage 4 Brain installation. Its separate interpreter constraint
does not change the standard minimal Brain contract or certify the satellite as
a validated Stage 4 installation profile.

System packages, shared libraries, devices, models, external executables, and
provider services remain governed by their installation-profile declarations
and host preflight. They are not hidden inside these Python lock identities.

## Clean-core test classification

Every promoted file matching `tests/test_*.py` is a required clean-core test;
there is no implicit optional test tier inside the distribution repository.
`scripts/run-clean-core-ci.py` collects that complete set and compares every
observed skip with the exact reviewed IDs in
`tests/clean-core-allowed-skips.txt`. Collection errors, test failures, new
skips, and declared skips that silently disappear all fail the job. Tests kept
outside core are private integration, live-provider, or hardware evidence and
are not made optional through runtime skip behavior.
