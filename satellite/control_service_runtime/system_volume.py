from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
from dataclasses import dataclass

from satellite.control_service_runtime.longform import CommandResult


_PERCENT_PATTERN = re.compile(r"\[(\d{1,3})%\]")
_LEVEL_TOLERANCE = 2
_WINDOWS_COM_STATE = threading.local()


@dataclass(frozen=True)
class SystemVolumeConfig:
    backend: str
    card: str
    control: str

    @property
    def enabled(self) -> bool:
        return bool(self.backend)


def build_system_volume_config(args: argparse.Namespace) -> SystemVolumeConfig:
    return SystemVolumeConfig(
        backend=str(getattr(args, "output_volume_backend", "") or "").strip().lower(),
        card=str(getattr(args, "output_volume_card", "") or "").strip(),
        control=str(getattr(args, "output_volume_control", "") or "").strip(),
    )


class SystemVolumeController:
    def __init__(self, config: SystemVolumeConfig) -> None:
        self._config = config

    def is_enabled(self) -> bool:
        return self._config.enabled

    def health_payload(self) -> dict[str, object]:
        return {
            "enabled": self._config.enabled,
            "backend": self._config.backend,
            "card": self._config.card,
            "control": self._config.control,
        }

    def current_level(self) -> int | None:
        config_error = self._config_error_result()
        if config_error is not None:
            return None
        if self._config.backend == "windows_default_endpoint":
            try:
                scalar = float(_load_windows_endpoint().GetMasterVolumeLevelScalar())
            except Exception:
                return None
            return max(0, min(100, round(scalar * 100)))
        completed = self._run(["amixer", "-c", self._config.card, "sget", self._config.control])
        if completed.returncode != 0:
            return None
        return _parse_percent_level(completed.stdout)

    def set_volume(self, level: int) -> CommandResult:
        config_error = self._config_error_result()
        if config_error is not None:
            return config_error
        target_level = max(0, min(100, int(level)))
        if self._config.backend == "windows_default_endpoint":
            try:
                _load_windows_endpoint().SetMasterVolumeLevelScalar(target_level / 100.0, None)
            except Exception:
                return CommandResult(
                    ok=False,
                    state="failed",
                    detail="Windows default endpoint volume operation failed",
                )
            confirmed_level = self.current_level()
            if confirmed_level is not None and abs(confirmed_level - target_level) <= _LEVEL_TOLERANCE:
                return CommandResult(
                    ok=True,
                    state="accepted",
                    payload={"volume_level": confirmed_level},
                )
            return CommandResult(
                ok=False,
                state="failed",
                detail="Windows default endpoint did not report the requested level",
                payload={"volume_level": confirmed_level},
            )
        completed = self._run(["amixer", "-c", self._config.card, "sset", self._config.control, f"{target_level}%"])
        if completed.returncode != 0:
            return CommandResult(
                ok=False,
                state="failed",
                detail=_command_failure_detail(completed),
            )
        confirmed_level = self.current_level()
        if confirmed_level is not None and abs(confirmed_level - target_level) <= _LEVEL_TOLERANCE:
            return CommandResult(ok=True, state="accepted", payload={"volume_level": confirmed_level})
        return CommandResult(
            ok=False,
            state="failed",
            detail="System mixer accepted set_volume but did not report the requested level",
            payload={"volume_level": confirmed_level},
        )

    def volume_up(self) -> CommandResult:
        return self._adjust_volume(10)

    def volume_down(self) -> CommandResult:
        return self._adjust_volume(-10)

    def _adjust_volume(self, delta: int) -> CommandResult:
        current_level = self.current_level()
        if current_level is None:
            config_error = self._config_error_result()
            if config_error is not None:
                return config_error
            return CommandResult(ok=False, state="failed", detail="Current system output volume is unavailable")
        return self.set_volume(max(0, min(100, current_level + delta)))

    def _config_error_result(self) -> CommandResult | None:
        if not self._config.enabled:
            return CommandResult(ok=False, state="unsupported", detail="System output volume is not configured")
        if self._config.backend not in {"alsa", "windows_default_endpoint"}:
            return CommandResult(
                ok=False,
                state="invalid_configuration",
                detail=f"Unsupported output volume backend {self._config.backend}",
            )
        if self._config.backend == "alsa" and (not self._config.card or not self._config.control):
            return CommandResult(
                ok=False,
                state="invalid_configuration",
                detail="ALSA output volume control requires both card and control",
            )
        return None

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, check=False)


def _parse_percent_level(output: str) -> int | None:
    matches = [int(match) for match in _PERCENT_PATTERN.findall(output or "")]
    if not matches:
        return None
    return matches[-1]


def _command_failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    return stderr or stdout or f"Command exited with {completed.returncode}"


def windows_default_endpoint_support_status() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows default-endpoint volume control requires Windows."
    try:
        endpoint = _load_windows_endpoint()
        endpoint.GetMasterVolumeLevelScalar()
    except Exception:
        return False, "Windows default-endpoint volume control is unavailable."
    return True, ""


def _load_windows_endpoint():
    if sys.platform == "win32":
        _ensure_windows_com_initialized()
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def _ensure_windows_com_initialized() -> None:
    if getattr(_WINDOWS_COM_STATE, "initialized", False):
        return
    from comtypes import CoInitialize

    CoInitialize()
    _WINDOWS_COM_STATE.initialized = True
