from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sounddevice as sd


@dataclass(frozen=True)
class AudioInputConfig:
    backend: str
    device: Optional[object]
    label: str
    explicitly_configured: bool


@dataclass(frozen=True)
class AudioOutputConfig:
    backend: str
    device: Optional[object]
    label: str
    explicitly_configured: bool


def _device_channel_count(device: object, field_name: str) -> int:
    if isinstance(device, dict):
        raw = device.get(field_name, 0)
    else:
        raw = getattr(device, field_name, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _device_name(device: object) -> str:
    if isinstance(device, dict):
        raw = device.get("name", "")
    else:
        raw = getattr(device, "name", "")
    return str(raw or "")


def resolve_portaudio_device_name(device_name: str, *, direction: str) -> int:
    clean_name = str(device_name or "").strip()
    if not clean_name:
        raise ValueError("PortAudio device name must not be empty.")
    if direction not in {"input", "output"}:
        raise ValueError(f"Unsupported PortAudio device direction: {direction}")

    channel_field = "max_input_channels" if direction == "input" else "max_output_channels"
    candidates: list[tuple[int, str]] = []
    for index, device in enumerate(sd.query_devices()):
        name = _device_name(device)
        if not name or _device_channel_count(device, channel_field) <= 0:
            continue
        candidates.append((index, name))

    exact_matches = [(index, name) for index, name in candidates if name.lower() == clean_name.lower()]
    if len(exact_matches) == 1:
        return exact_matches[0][0]
    if len(exact_matches) > 1:
        names = ", ".join(f"{index}:{name}" for index, name in exact_matches)
        raise ValueError(f"PortAudio {direction} device name is ambiguous: {clean_name} ({names})")

    partial_matches = [(index, name) for index, name in candidates if clean_name.lower() in name.lower()]
    if len(partial_matches) == 1:
        return partial_matches[0][0]
    if len(partial_matches) > 1:
        names = ", ".join(f"{index}:{name}" for index, name in partial_matches)
        raise ValueError(f"PortAudio {direction} device name is ambiguous: {clean_name} ({names})")

    available = ", ".join(f"{index}:{name}" for index, name in candidates) or "none"
    raise ValueError(f"PortAudio {direction} device not found: {clean_name}. Available {direction} devices: {available}")


def resolve_audio_input_config(args) -> AudioInputConfig:
    if getattr(args, "input_alsa_device", None):
        alsa_device = str(args.input_alsa_device).strip()
        return AudioInputConfig(
            backend="alsa_arecord",
            device=alsa_device,
            label=alsa_device,
            explicitly_configured=True,
        )
    if getattr(args, "input_device_index", None) is not None:
        device_index = int(args.input_device_index)
        return AudioInputConfig(
            backend="portaudio_device_index",
            device=device_index,
            label=str(device_index),
            explicitly_configured=True,
        )
    if getattr(args, "input_device_name", None):
        device_name = str(args.input_device_name).strip()
        device_index = resolve_portaudio_device_name(device_name, direction="input")
        return AudioInputConfig(
            backend="portaudio_device_name",
            device=device_index,
            label=f"{device_name} ({device_index})",
            explicitly_configured=True,
        )
    return AudioInputConfig(
        backend="default_input_device",
        device=None,
        label="default",
        explicitly_configured=False,
    )


def resolve_audio_output_config(args) -> AudioOutputConfig:
    if getattr(args, "output_device_index", None) is not None:
        device_index = int(args.output_device_index)
        return AudioOutputConfig(
            backend="portaudio_output_device_index",
            device=device_index,
            label=str(device_index),
            explicitly_configured=True,
        )
    if getattr(args, "output_device_name", None):
        device_name = str(args.output_device_name).strip()
        device_index = resolve_portaudio_device_name(device_name, direction="output")
        return AudioOutputConfig(
            backend="portaudio_output_device_name",
            device=device_index,
            label=f"{device_name} ({device_index})",
            explicitly_configured=True,
        )
    return AudioOutputConfig(
        backend="default_output_device",
        device=None,
        label="default",
        explicitly_configured=False,
    )


def resolve_input_device(args) -> Optional[object]:
    return resolve_audio_input_config(args).device


def resolve_output_device(args) -> Optional[object]:
    return resolve_audio_output_config(args).device
