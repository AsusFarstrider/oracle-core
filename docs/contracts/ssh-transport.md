# SSH Transport

Reusable SSH-backed provider and control adapters use native OpenSSH host
authentication. `ORACLE_SSH_KNOWN_HOSTS_FILE` must identify an absolute,
readable, nonempty regular file which is not group- or world-writable. The
standard installation supplies that path from household deployment material;
an experimental installation may instead supply an independently maintained
file after validating it through the same boundary.

Oracle invokes OpenSSH with strict host-key checking against only that selected
file. A missing file, an unknown host, or a changed host key fails closed.
Discovery may identify a candidate key but never authorizes it. Enrollment and
rotation are explicit operator maintenance actions outside provider execution.

Password-backed adapters pass the SSH password through `SSHPASS`, not process
arguments. The finite adapter remains responsible for its remote command;
configuration cannot introduce arbitrary commands through this transport.
Known-host entries, target identities, credentials, and operational commands
remain household deployment or secret material rather than reusable defaults.
