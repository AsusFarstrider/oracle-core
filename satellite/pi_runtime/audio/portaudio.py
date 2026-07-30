from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

import sounddevice as sd


@contextmanager
def open_portaudio_input_stream(
    *,
    sample_rate: int,
    frame_length: int,
    callback,
    device: Optional[object],
) -> Iterator[sd.RawInputStream]:
    with sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        callback=callback,
        blocksize=frame_length,
        device=device,
    ) as stream:
        yield stream
