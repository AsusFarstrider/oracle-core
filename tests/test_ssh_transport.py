from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oracle_app.provider_bridges.ssh_transport import SshHostVerificationError, strict_ssh_options


class StrictSshTransportTests(unittest.TestCase):
    def test_requires_explicit_known_hosts_file(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SshHostVerificationError):
                strict_ssh_options()

    def test_rejects_relative_missing_empty_and_writable_known_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [Path("known_hosts"), root / "missing", root / "empty", root / "writable"]
            (root / "empty").touch(mode=0o600)
            (root / "writable").write_text("host ssh-ed25519 AAAATest\n")
            (root / "writable").chmod(0o620)
            for candidate in cases:
                with self.subTest(candidate=candidate):
                    with patch.dict(
                        "os.environ", {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(candidate)}, clear=True
                    ):
                        with self.assertRaises(SshHostVerificationError):
                            strict_ssh_options()

    def test_returns_strict_options_for_validated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            known_hosts = Path(directory) / "known_hosts"
            known_hosts.write_text("host ssh-ed25519 AAAATest\n")
            known_hosts.chmod(0o600)
            with patch.dict(
                "os.environ", {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(known_hosts)}, clear=True
            ):
                options = strict_ssh_options(connect_timeout_seconds=8)

        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertIn(f"UserKnownHostsFile={known_hosts}", options)
        self.assertIn("GlobalKnownHostsFile=/dev/null", options)
        self.assertIn("ConnectTimeout=8", options)


if __name__ == "__main__":
    unittest.main()
