from __future__ import annotations

from pathlib import Path
import os

import sounddevice as sd


MODEL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ALARM_SOUND_PATH = MODEL_DIR / "sounds" / "alarm.wav"
DEFAULT_TIMER_SOUND_PATH = MODEL_DIR / "sounds" / "timer.wav"
DEFAULT_ONNX_MODELS = (
    MODEL_DIR / "hey_oracle.onnx",
    MODEL_DIR / "oracle.onnx",
    MODEL_DIR / "Oracle.onnx",
)
DEFAULT_TFLITE_MODELS = (
    MODEL_DIR / "oracle.tflite",
    MODEL_DIR / "Oracle.tflite",
)


def detect_default_model() -> Path:
    for candidate in DEFAULT_ONNX_MODELS:
        if candidate.exists():
            return candidate
    for candidate in DEFAULT_TFLITE_MODELS:
        if candidate.exists():
            return candidate
    return DEFAULT_ONNX_MODELS[0]


def list_devices() -> None:
    print(sd.query_devices())


def set_alarm_display_attention(active: bool) -> bool:
    """Hold or release Windows display attention for a ringing alarm."""

    if os.name != "nt":
        return True
    import ctypes

    continuous = 0x80000000
    system_required = 0x00000001
    display_required = 0x00000002
    flags = continuous | system_required | display_required if active else continuous
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
