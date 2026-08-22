from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Optional


def candidate_alsa_input_devices(configured_device: Optional[str]) -> list[str]:
    if configured_device is None:
        return []
    stripped = configured_device.strip()
    if not stripped:
        return []
    if stripped.lower() != "auto":
        lowered = stripped.lower()
        if lowered.startswith(("plughw:", "sysdefault:", "hw:")):
            return [stripped]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return [
        "plughw:CARD=Bar,DEV=0",
        "sysdefault:CARD=Bar",
        "plughw:CARD=Device,DEV=0",
        "sysdefault:CARD=Device",
        "plughw:CARD=acp,DEV=0",
        "sysdefault:CARD=acp",
    ]


@contextmanager
def open_alsa_arecord_stream(
    *,
    sample_rate: int,
    frame_length: int,
    callback,
    configured_device: Optional[str],
    logger=None,
):
    last_error: Optional[BaseException] = None
    for candidate in candidate_alsa_input_devices(configured_device):
        command = [
            "arecord",
            "-q",
            "-D",
            candidate,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            "1",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        stop_event = threading.Event()
        frame_bytes = frame_length * 2
        reader_error: Optional[BaseException] = None

        def read_stderr() -> None:
            if process.stderr is None:
                return
            for raw_line in iter(process.stderr.readline, b""):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line and logger is not None:
                    logger.warning("arecord[%s]: %s", candidate, line)

        def read_stdout() -> None:
            nonlocal reader_error
            assert process.stdout is not None
            buffer = bytearray()
            try:
                while not stop_event.is_set():
                    chunk = process.stdout.read(frame_bytes - len(buffer))
                    if not chunk:
                        return
                    buffer.extend(chunk)
                    if len(buffer) < frame_bytes:
                        continue
                    callback(bytes(buffer), frame_length, None, None)
                    buffer.clear()
            except BaseException as exc:
                reader_error = exc

        stderr_thread = threading.Thread(target=read_stderr, name="arecord-stderr", daemon=True)
        stdout_thread = threading.Thread(target=read_stdout, name="arecord-stdout", daemon=True)
        stderr_thread.start()
        stdout_thread.start()
        time.sleep(0.2)

        if process.poll() not in (None, 0):
            last_error = RuntimeError(f"arecord failed for {candidate} with status {process.returncode}")
            stop_event.set()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            continue

        if logger is not None:
            logger.info("Using ALSA input device: %s", candidate)
        try:
            yield process
            if reader_error is not None:
                raise reader_error
            if process.poll() not in (None, 0):
                raise RuntimeError(f"arecord exited with status {process.returncode}")
            return
        finally:
            stop_event.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

    if last_error is not None:
        raise last_error
    raise RuntimeError("No usable ALSA input device candidates found")
