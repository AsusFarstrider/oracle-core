from __future__ import annotations

from collections import deque

import numpy as np


class AudioRingBuffer:
    def __init__(self, *, max_frames: int) -> None:
        self._frames: deque[np.ndarray] = deque(maxlen=max_frames)

    def append(self, frame: np.ndarray) -> None:
        self._frames.append(frame.copy())

    def snapshot(self) -> list[np.ndarray]:
        return [frame.copy() for frame in self._frames]
