# Oracle dependency profiles

Oracle separates the smallest Brain production runtime, optional host-capability
profiles, and clean-core test tooling. A standard installation validates a host
interpreter, leaves its package environment untouched, and creates or reuses an
immutable native Python environment from the matching complete hash lock.

| Profile | Direct declaration | Complete lock | Python baseline | Purpose |
| --- | --- | --- | --- | --- |
| `minimal-brain` | `server/requirements.txt` | `server/requirements.lock` | CPython 3.13 on the Stage 4 Debian tuple | Provider-free Brain production runtime |
| `fast-whisper` | `server/requirements-fast-whisper.txt` | `server/requirements-fast-whisper.lock` | CPython 3.13 on the Stage 4 Debian tuple | Additive Fast-Whisper STT implementation |
| `full-production-brain` | `server/requirements-full-production.txt` | `server/requirements-full-production.lock` | CPython 3.13 on the Stage 4 Debian tuple | Fixed Fast-Whisper plus Piper environment used by the full production Brain lifecycle |
| `wake-satellite` | `satellite/requirements.txt` | `satellite/requirements.lock` | CPython 3.11 on Linux | Retained wake/audio satellite runtime; TensorFlow Lite currently prevents Python 3.13 resolution |
| `clean-core-test` | `server/requirements.txt` plus `requirements-dev.txt` | `requirements-test.lock` | CPython 3.13 on the CI baseline | Required test tooling; never installed into production environments |

The lock file bytes are part of each Python-environment identity along with the
exact interpreter, ABI, platform, architecture, and selected profile. Install
with pip's `--require-hashes` option. The direct declarations remain the small
reviewable inputs; locks contain the complete resolved graph and accepted
artifact hashes. Regenerate locks with `pip==25.3` and `pip-tools==7.5.2` under
the profile's declared Python baseline using
`--allow-unsafe --strip-extras --generate-hashes`.

The standard Debian builder constructs a native environment directly at its
final managed path because native `venv` script shebangs are not relocatable.
The complete semantic identity remains
`oracle-python-environment-v1:sha256:<digest>`; its deterministic filesystem
name is `environment-<digest>`, avoiding platform-reserved path characters
without weakening the recorded identity. Activation records bind the complete
semantic identity and resolve it through that one validated mapping.
An explicit incomplete-build marker prevents that candidate from being reused
or selected before validation. Successful construction requires exact locked
package comparison, `pip check`, an interpreter-identity recheck, and a complete
environment-tree integrity hash; only then is the marker replaced by the
immutable environment record and the tree made read-only. A matching marked
interruption is safely discarded and rebuilt. An unmarked or identity-mismatched
partial tree fails closed for explicit repair.

The staged `oracle-admin` bootstrap commands `preflight`, `stage-plan`, and
`stage` load only Oracle's standard-library bootstrap modules and run with the
explicitly discovered host interpreter. After staging, administration commands
re-execute through the exact validated immutable environment selected by the
requested environment identity or staged complete activation. The host Python
package environment remains untouched.

`stage-plan` and `stage` also require one explicit existing local operator
account. The approved staging plan records that exact account and the elevated
operation adds it to `oracle-admin` only after creating and validating the
Oracle identities. No login account is enrolled merely because it invoked the
installer or has sudo eligibility.

The wake-satellite profile is retained reusable functionality, not part of the
provider-free Stage 4 Brain installation. Its separate interpreter constraint
does not change the standard minimal Brain contract or certify the satellite as
a validated Stage 4 installation profile.

System packages, shared libraries, devices, models, external executables, and
provider services remain governed by their installation-profile declarations
and host preflight. They are not hidden inside these Python lock identities.

## Conditional local-playback facility

The provider-free `minimal-brain` profile does not require a multimedia player.
Deployments that enable Oracle's satellite-local long-form playback facility
must provide one validated compatible executable: either `ffplay` or `mpv`.
Those executables are alternative mandatory host dependencies for that enabled
facility, not universal Oracle dependencies.

Installation-profile preflight must discover and validate the selected player
before declaring local playback ready. A paused long-form session may be
constructed without a player because it launches no process; actual playback,
resume, and seek resolve and validate the selected executable at their execution
boundary. A disabled local-playback facility requires neither executable and
their absence must not make the minimal Brain unhealthy.

## Clean-core test classification

Every promoted file matching `tests/test_*.py` is a required clean-core test;
there is no implicit optional test tier inside the distribution repository.
`scripts/run-clean-core-ci.py` collects that complete set and compares every
observed skip with the exact reviewed IDs in
`tests/clean-core-allowed-skips.txt`. Collection errors, test failures, new
skips, and declared skips that silently disappear all fail the job. Tests kept
outside core are private integration, live-provider, or hardware evidence and
are not made optional through runtime skip behavior.
