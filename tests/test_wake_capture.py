from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from satellite.wake_capture.collector import WakeCaptureCollector
from satellite.wake_capture.models import WakeCaptureConfig, WakeCaptureUploadConfig
from satellite.wake_capture.storage import iter_pending_files
from satellite.wake_capture.sync import sync_pending_captures, sync_pending_captures_http


class _UploadResponse:
    def __init__(self, body: bytes | None = None) -> None:
        if body is None:
            body = json.dumps({"ok": True, "capture_id": "a" * 64}).encode()
        self.body = body
        self.headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


class WakeCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._root = Path(self._tmpdir.name)
        self._known_hosts = self._root / "known_hosts"
        self._known_hosts.write_text(
            "oracle.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly\n",
            encoding="utf-8",
        )
        self._known_hosts.chmod(0o600)
        self._ssh_environment = patch.dict(
            "os.environ",
            {"ORACLE_SSH_KNOWN_HOSTS_FILE": str(self._known_hosts)},
        )
        self._ssh_environment.start()
        self.addCleanup(self._ssh_environment.stop)
        self._logger = logging.getLogger("test-wake-capture")
        self._frame = (np.ones(1280, dtype=np.int16) * 100).tobytes()

    def _config(self, **overrides) -> WakeCaptureConfig:
        config = WakeCaptureConfig(
            enabled=True,
            source_id="test-satellite",
            capture_activation=True,
            capture_near_threshold=True,
            pre_roll_ms=160,
            post_roll_ms=160,
            near_threshold_fraction=0.85,
            event_cooldown_seconds=1.0,
            local_storage_path=self._root / "local",
            sync_enabled=False,
            server_sync_path=str(self._root / "server"),
            delete_local_after_sync=True,
            input_gain=1.0,
        )
        return types.SimpleNamespace(**{**config.__dict__, **overrides})  # type: ignore[arg-type]

    def test_activation_capture_writes_canonical_wav_and_metadata(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config().__dict__), logger=self._logger)

        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.91, playback_active=True, ducking_triggered=True, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        files = iter_pending_files(self._root / "local")
        self.assertEqual(len(files), 2)
        wav_path = next(path for path in files if path.suffix == ".wav")
        json_path = next(path for path in files if path.suffix == ".json")

        with wave.open(str(wav_path), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getsampwidth(), 2)

        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["event_type"], "activation")
        self.assertEqual(metadata["source_id"], "test-satellite")
        self.assertTrue(metadata["playback_active"])
        self.assertTrue(metadata["ducking_triggered"])

    def test_near_threshold_capture_records_peak_on_band_exit(self) -> None:
        collector = WakeCaptureCollector(
            config=WakeCaptureConfig(**self._config(capture_activation=False).__dict__),
            logger=self._logger,
        )

        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)
        collector.observe_score(
            score=0.44,
            active_threshold=0.50,
            playback_active=False,
            ducking_triggered=False,
            now=1000.0,
        )
        collector.observe_score(
            score=0.47,
            active_threshold=0.50,
            playback_active=False,
            ducking_triggered=False,
            now=1000.08,
        )
        collector.observe_score(
            score=0.30,
            active_threshold=0.50,
            playback_active=False,
            ducking_triggered=False,
            now=1000.16,
        )
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        files = iter_pending_files(self._root / "local")
        self.assertEqual(len(files), 2)
        json_path = next(path for path in files if path.suffix == ".json")
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["event_type"], "near_threshold")
        self.assertAlmostEqual(metadata["score"], 0.47, places=2)

    def test_disabled_mode_creates_no_collector_output(self) -> None:
        config = WakeCaptureConfig(**self._config(enabled=False).__dict__)
        collector = WakeCaptureCollector(config=config, logger=self._logger)

        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1.0)
        collector.observe_score(
            score=0.4,
            active_threshold=0.5,
            playback_active=False,
            ducking_triggered=False,
            now=1.0,
        )

        self.assertEqual(iter_pending_files(self._root / "local"), [])

    def test_sync_copies_files_and_deletes_local_when_configured(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(**self._config(sync_enabled=True, delete_local_after_sync=True).__dict__)
        result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 2)
        self.assertEqual(iter_pending_files(self._root / "local"), [])
        server_files = sorted((self._root / "server").rglob("*"))
        self.assertTrue(any(path.suffix == ".wav" for path in server_files))
        self.assertTrue(any(path.suffix == ".json" for path in server_files))

    def test_sync_keeps_local_copy_in_synced_tree_when_delete_disabled(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(**self._config(sync_enabled=True, delete_local_after_sync=False).__dict__)
        result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 2)
        self.assertEqual(iter_pending_files(self._root / "local"), [])
        synced_files = sorted((self._root / "local" / "synced").rglob("*"))
        self.assertTrue(any(path.suffix == ".wav" for path in synced_files))

    def test_sync_failure_keeps_local_files(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(**self._config(sync_enabled=True).__dict__)
        with patch.object(shutil, "copy2", side_effect=RuntimeError("copy failed")):
            result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 0)
        self.assertEqual(len(iter_pending_files(self._root / "local")), 2)

    def test_remote_auto_sync_uses_rsync_when_available(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(
            **self._config(
                sync_enabled=True,
                sync_host="oracle.example",
                sync_user="capture",
                sync_ssh_key_path="/keys/wake",
                sync_transport="auto",
            ).__dict__
        )
        with patch.object(shutil, "which", return_value="/usr/bin/rsync"), patch.object(
            subprocess,
            "run",
        ) as mock_run:
            result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 2)
        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(any(command[0] == "rsync" for command in commands))
        self.assertFalse(any(command[0] == "scp" for command in commands))
        rsync_command = next(command for command in commands if command[0] == "rsync")
        self.assertIn("StrictHostKeyChecking=yes", rsync_command[3])
        self.assertIn(f"UserKnownHostsFile={self._known_hosts}", rsync_command[3])
        mkdir_command = next(command for command in commands if command[0] == "ssh")
        self.assertEqual(
            shlex.split(mkdir_command[-1]),
            ["mkdir", "-p", str(self._root / "server")],
        )

    def test_remote_auto_sync_uses_scp_when_rsync_is_unavailable(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(
            **self._config(
                sync_enabled=True,
                sync_host="oracle.example",
                sync_user="capture",
                sync_ssh_key_path="C:/Oracle/wake_ed25519",
                sync_transport="auto",
                server_sync_path="/var/lib/example/wake-capture",
            ).__dict__
        )
        with patch.object(shutil, "which", return_value=None), patch.object(subprocess, "run") as mock_run:
            result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 2)
        commands = [call.args[0] for call in mock_run.call_args_list]
        scp_command = next(command for command in commands if command[0] == "scp")
        self.assertIn("-r", scp_command)
        self.assertIn("C:/Oracle/wake_ed25519", scp_command)
        self.assertIn("StrictHostKeyChecking=yes", scp_command)
        self.assertIn(f"UserKnownHostsFile={self._known_hosts}", scp_command)
        self.assertTrue(scp_command[-2].endswith("pending/test-satellite"))
        self.assertEqual(scp_command[-1], "capture@oracle.example:/var/lib/example/wake-capture/")

    def test_remote_scp_failure_keeps_pending_files(self) -> None:
        collector = WakeCaptureCollector(config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__), logger=self._logger)
        collector.append_frame_bytes(self._frame)
        collector.record_activation(score=0.9, playback_active=False, ducking_triggered=False, now=1000.0)
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        config = WakeCaptureConfig(
            **self._config(
                sync_enabled=True,
                sync_host="oracle.example",
                sync_user="capture",
                sync_transport="scp",
            ).__dict__
        )

        def fail_scp(command, **_kwargs):
            if command[0] == "scp":
                raise subprocess.CalledProcessError(1, command)
            return None

        with patch.object(subprocess, "run", side_effect=fail_scp):
            result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 0)
        self.assertEqual(len(iter_pending_files(self._root / "local")), 2)

    def test_remote_sync_without_validated_host_identity_fails_closed(self) -> None:
        collector = WakeCaptureCollector(
            config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__),
            logger=self._logger,
        )
        collector.append_frame_bytes(self._frame)
        collector.record_activation(
            score=0.9,
            playback_active=False,
            ducking_triggered=False,
            now=1000.0,
        )
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)
        config = WakeCaptureConfig(
            **self._config(
                sync_enabled=True,
                sync_host="oracle.example",
                sync_user="capture",
                sync_transport="scp",
            ).__dict__
        )

        environment = dict(os.environ)
        environment.pop("ORACLE_SSH_KNOWN_HOSTS_FILE", None)
        with patch.dict("os.environ", environment, clear=True), patch.object(
            subprocess, "run"
        ) as run:
            result = sync_pending_captures(config=config, logger=self._logger)

        self.assertEqual(result.synced_files, 0)
        self.assertEqual(len(iter_pending_files(self._root / "local")), 2)
        run.assert_not_called()

    def test_canonical_http_sync_uploads_one_pair_and_deletes_only_after_success(self) -> None:
        collector = WakeCaptureCollector(
            config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__),
            logger=self._logger,
        )
        collector.append_frame_bytes(self._frame)
        collector.record_activation(
            score=0.9,
            playback_active=False,
            ducking_triggered=False,
            now=1000.0,
        )
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)
        requests = []

        def open_url(request, **_kwargs):
            requests.append(request)
            return _UploadResponse()

        result = sync_pending_captures_http(
            config=self._upload_config(delete_local_after_sync=True),
            logger=self._logger,
            open_url=open_url,
        )

        self.assertEqual(result.synced_files, 2)
        self.assertEqual(result.deleted_local_files, 2)
        self.assertEqual(iter_pending_files(self._root / "local"), [])
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            request.full_url,
            "http://brain.example:8011/api/satellite/wake-captures/living_room_satellite",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer brain-token")
        self.assertIn(b'name="metadata"', request.data)
        self.assertIn(b'name="audio"; filename="capture.wav"', request.data)

    def test_canonical_http_sync_failure_and_orphan_files_remain_pending(self) -> None:
        collector = WakeCaptureCollector(
            config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__),
            logger=self._logger,
        )
        collector.append_frame_bytes(self._frame)
        collector.record_activation(
            score=0.9,
            playback_active=False,
            ducking_triggered=False,
            now=1000.0,
        )
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)
        orphan = self._root / "local" / "pending" / "orphan.wav"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")

        def fail(_request, **_kwargs):
            raise OSError("offline")

        result = sync_pending_captures_http(
            config=self._upload_config(delete_local_after_sync=True),
            logger=self._logger,
            open_url=fail,
        )

        self.assertEqual(result.synced_files, 0)
        self.assertEqual(len(iter_pending_files(self._root / "local")), 3)

    def test_canonical_http_sync_retains_uploaded_pair_when_configured(self) -> None:
        collector = WakeCaptureCollector(
            config=WakeCaptureConfig(**self._config(sync_enabled=True).__dict__),
            logger=self._logger,
        )
        collector.append_frame_bytes(self._frame)
        collector.record_activation(
            score=0.9,
            playback_active=False,
            ducking_triggered=False,
            now=1000.0,
        )
        collector.append_frame_bytes(self._frame)
        collector.append_frame_bytes(self._frame)

        result = sync_pending_captures_http(
            config=self._upload_config(delete_local_after_sync=False),
            logger=self._logger,
            open_url=lambda *_args, **_kwargs: _UploadResponse(),
        )

        self.assertEqual(result.retained_local_files, 2)
        self.assertEqual(iter_pending_files(self._root / "local"), [])
        self.assertEqual(len(list((self._root / "local" / "synced").rglob("*.*"))), 2)

    def _upload_config(self, *, delete_local_after_sync: bool) -> WakeCaptureUploadConfig:
        return WakeCaptureUploadConfig(
            enabled=True,
            satellite_id="living_room_satellite",
            source_id="test-satellite",
            local_storage_path=self._root / "local",
            brain_base_url="http://brain.example:8011",
            brain_credential="brain-token",
            sync_interval_seconds=3600.0,
            delete_local_after_sync=delete_local_after_sync,
            synced_local_retention_days=7,
        )


if __name__ == "__main__":
    unittest.main()
