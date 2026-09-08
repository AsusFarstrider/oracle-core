# `oraclectl` Break-Glass CLI

`oraclectl` is the small, host-local first-response interface for the managed
Oracle Brain. It is not configuration, activation, deployment, or repair
authority.

The installed executable is deliberately self-contained. Read-only commands
continue to start when `/srv/oracle/selection/active` is absent, malformed, or
points to a broken activation. They inspect the fixed managed layout, systemd,
and fixed loopback health surfaces without importing code through that selector.

## Commands

```sh
oraclectl status
oraclectl doctor
oraclectl activation
oraclectl logs
oraclectl logs --follow
oraclectl logs --since "10 minutes ago"
sudo oraclectl restart
```

`status` is fast and bounded. It reads the four immutable selectors, cheap
systemd properties, shallow Brain health, and applied configuration identity.
It reports the configured satellite count when the existing configuration
health response supplies projection identities. It does not sweep providers or
satellite hosts.

`doctor` performs the deeper bounded cross-system pass. Its reusable diagnostic
records contain `severity`, `component`, `explanation`, and an optional
`remediation`. Human output is:

```text
PASS/WARN/FAIL/UNKNOWN  component  explanation
```

Exit status is `0` for healthy, `1` for degraded, failed, or materially unknown,
and `2` only when the CLI itself could not complete. An unreachable individual
probe is diagnostic `UNKNOWN`, not a CLI crash. Outputs include no secret values,
credentials, authenticated URLs, or environment contents.

`activation` is a read-only rendering of the canonical selector records. It
does not reconcile or modify them. `logs` executes `journalctl` for the fixed
`oracle-brain.service` only.

`restart` is the sole mutation. It refuses to run without a valid active
activation, records that activation, restarts only `oracle-brain.service`, waits
for semantic Brain health, and verifies that the active activation did not
change. It never deploys, changes selectors, rebuilds content, changes
configuration, or rolls forward/back.
