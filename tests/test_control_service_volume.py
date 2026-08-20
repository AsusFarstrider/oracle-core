from __future__ import annotations

import json
import sys
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import ModuleType, SimpleNamespace
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from satellite.control_service import ControlServer
from satellite.control_service_runtime import CommandResult
from satellite.control_service_runtime.adapters import PlexampHttpAdapter, ShellPlexampAdapter
from satellite.control_service_runtime.cache import CommandCache
from satellite.control_service_runtime.longform import LongformShellController
import satellite.control_service_runtime.playback_authority as playback_authority_runtime
from satellite.control_service_runtime.playback_authority import (
    build_playback_authority_state,
    interrupt_for_oracle,
    resume_after_oracle,
)
from satellite.control_service_runtime.reply_audio import ReplyAudioStateStore
from satellite.control_service_runtime.server import (
    ControlRequestHandler,
    _validate_control_request_payload,
)
from satellite.control_service_runtime.system_volume import (
    SystemVolumeController,
    build_system_volume_config,
    windows_default_endpoint_support_status,
)
import satellite.control_service_runtime.system_volume as system_volume_runtime
from oracle_app.config_reporting import choose_config_report_format


class ControlServiceTests(unittest.TestCase):
    def _build_server_like(
        self,
        *,
        reply_audio_state_path: str = "",
        reply_audio_stop_path: str = "",
    ) -> ControlServer:
        server = ControlServer.__new__(ControlServer)
        server.reply_audio = ReplyAudioStateStore(reply_audio_state_path, reply_audio_stop_path)
        return server

    def test_system_volume_config_reads_alsa_args(self) -> None:
        args = SimpleNamespace(
            output_volume_backend="alsa",
            output_volume_card="4",
            output_volume_control="PCM",
        )

        config = build_system_volume_config(args)

        self.assertEqual(config.backend, "alsa")
        self.assertEqual(config.card, "4")
        self.assertEqual(config.control, "PCM")

    def test_plexamp_set_volume_confirms_reported_level(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        volume_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" volume="40"><Track title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=volume_timeline,
        ):
            result = adapter.set_volume(40)

        self.assertTrue(result.ok)
        assert result.payload is not None
        self.assertEqual(result.payload["volume_level"], 40)

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_system_volume_controller_sets_alsa_output_volume(self, run_mock) -> None:
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="alsa",
                    output_volume_card="4",
                    output_volume_control="PCM",
                )
            )
        )
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
        ]

        result = controller.set_volume(40)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 40})
        self.assertEqual(run_mock.call_args_list[0].args[0], ["amixer", "-c", "4", "sset", "PCM", "40%"])
        self.assertEqual(run_mock.call_args_list[1].args[0], ["amixer", "-c", "4", "sget", "PCM"])

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_system_volume_controller_accepts_small_alsa_rounding_difference(self, run_mock) -> None:
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="alsa",
                    output_volume_card="4",
                    output_volume_control="PCM",
                )
            )
        )
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [38%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [38%] [on]\n", stderr=""),
        ]

        result = controller.set_volume(40)

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 38})

    def test_system_volume_controller_sets_windows_default_endpoint_volume(self) -> None:
        class Endpoint:
            scalar = 0.25

            def GetMasterVolumeLevelScalar(self):
                return self.scalar

            def SetMasterVolumeLevelScalar(self, scalar, _context):
                self.scalar = scalar

        endpoint = Endpoint()
        controller = SystemVolumeController(
            build_system_volume_config(
                SimpleNamespace(
                    output_volume_backend="windows_default_endpoint",
                    output_volume_card="",
                    output_volume_control="",
                )
            )
        )

        with patch(
            "satellite.control_service_runtime.system_volume._load_windows_endpoint",
            return_value=endpoint,
        ):
            result = controller.set_volume(40)
            current = controller.current_level()

        self.assertTrue(result.ok)
        self.assertEqual(result.payload, {"volume_level": 40})
        self.assertEqual(current, 40)

    def test_windows_default_endpoint_readiness_checks_platform_and_endpoint(self) -> None:
        endpoint = SimpleNamespace(GetMasterVolumeLevelScalar=lambda: 0.5)
        with patch("satellite.control_service_runtime.system_volume.sys.platform", "win32"), patch(
            "satellite.control_service_runtime.system_volume._load_windows_endpoint",
            return_value=endpoint,
        ):
            self.assertEqual(windows_default_endpoint_support_status(), (True, ""))
        with patch("satellite.control_service_runtime.system_volume.sys.platform", "linux"):
            available, message = windows_default_endpoint_support_status()
        self.assertFalse(available)
        self.assertIn("requires Windows", message)

    def test_windows_endpoint_initializes_com_once_per_calling_thread(self) -> None:
        endpoint = SimpleNamespace()
        audio_utilities = SimpleNamespace(
            GetSpeakers=lambda: SimpleNamespace(EndpointVolume=endpoint)
        )
        pycaw_package = ModuleType("pycaw")
        pycaw_module = ModuleType("pycaw.pycaw")
        pycaw_module.AudioUtilities = audio_utilities
        pycaw_package.pycaw = pycaw_module
        comtypes_module = ModuleType("comtypes")
        initialized: list[bool] = []
        comtypes_module.CoInitialize = lambda: initialized.append(True)

        with patch.object(system_volume_runtime.sys, "platform", "win32"), patch.object(
            system_volume_runtime,
            "_WINDOWS_COM_STATE",
            SimpleNamespace(),
        ), patch.dict(
            sys.modules,
            {
                "comtypes": comtypes_module,
                "pycaw": pycaw_package,
                "pycaw.pycaw": pycaw_module,
            },
        ):
            self.assertIs(system_volume_runtime._load_windows_endpoint(), endpoint)
            self.assertIs(system_volume_runtime._load_windows_endpoint(), endpoint)

        self.assertEqual(initialized, [True])

    @patch("satellite.control_service_runtime.system_volume.subprocess.run")
    def test_plexamp_adapter_prefers_system_volume_when_configured(self, run_mock) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            output_volume_backend="alsa",
            output_volume_card="4",
            output_volume_control="PCM",
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        run_mock.side_effect = [
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Playback 6 [40%] [on]\n", stderr=""),
        ]

        with patch.object(adapter, "_request") as request_mock:
            result = adapter.set_volume(40)

        self.assertTrue(result.ok)
        request_mock.assert_not_called()

    def test_plexamp_set_volume_fails_when_reported_level_does_not_change(self) -> None:
        args = SimpleNamespace(
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url="http://127.0.0.1:32400",
            plex_token="token",
            plex_machine_identifier="machine",
            http_timeout_seconds=5.0,
            play_longform_audio_cmd="",
            pause_longform_audio_cmd="",
            resume_longform_audio_cmd="",
            stop_longform_audio_cmd="",
            seek_longform_audio_cmd="",
            longform_state_cmd="",
        )
        adapter = PlexampHttpAdapter(args)
        stale_volume_timeline = """<MediaContainer><Timeline type="music" state="playing" title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" volume="100"><Track title="Heroes" grandparentTitle="David Bowie" parentTitle="Heroes" /></Timeline></MediaContainer>"""

        with patch.object(adapter, "_request", return_value=CommandResult(ok=True, state="accepted")), patch.object(
            adapter,
            "_timeline",
            return_value=stale_volume_timeline,
        ):
            result = adapter.set_volume(40)

        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "Plexamp accepted set_volume but did not report the requested level")

if __name__ == "__main__":
    unittest.main()
