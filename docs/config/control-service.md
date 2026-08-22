# Control-Service Configuration

The retained control-service implementation consumes one immutable selected
satellite projection and local secret activation. Its typed
`control_service` component owns listener settings, the Brain-facing control
edge, playback adapters, volume behavior, reply-audio coordination, and
logging. Canonical startup does not read authored household YAML or accept
behavioral environment or command-line overrides.

Host bootstrap may supply only finite installation mechanics such as the local
projection-store location and logging bootstrap admitted by the runtime
contract. Household endpoints, credentials, player choices, and behavior
remain in the canonical projection and its secret generation.

The implementation is retained reusable core. Stage 4 does not certify a
standard satellite/control-service installation profile. Its Linux and Windows
launch assets are retained implementation and development tooling until their
dependencies, permissions, reboot behavior, update, recovery, and rollback
have separate clean-host evidence.

Runtime configuration validation and reporting follow
[validation.md](validation.md); secret handling follows
[security.md](security.md). The canonical satellite model is described in
[shareable-config.md](shareable-config.md).
