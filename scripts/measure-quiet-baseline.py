from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from satellite.pi_runtime.audio import open_input_stream, resolve_audio_input_config
from satellite.pi_runtime.wake import FRAME_LENGTH, SAMPLE_RATE, frame_rms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure host-local quiet baseline energy")
    parser.add_argument("--duration-seconds", type=float, default=120.0)
    parser.add_argument("--input-device-index", type=int, default=None)
    parser.add_argument("--input-alsa-device", default=None)
    parser.add_argument("--input-gain", type=float, default=1.0)
    parser.add_argument("--host-label", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = logging.getLogger("measure-quiet-baseline")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    energies: list[float] = []
    lock = threading.Lock()

    def callback(indata, _frames, _time_info, _status) -> None:
        frame = np.frombuffer(indata, dtype=np.int16)
        if frame.size == 0:
            return
        if args.input_gain != 1.0:
            frame = np.clip(frame.astype(np.float32) * args.input_gain, -32768, 32767).astype(np.int16)
        energy = frame_rms(frame)
        with lock:
            energies.append(energy)

    input_config = resolve_audio_input_config(args)
    start = time.time()
    logger.info(
        "Starting quiet baseline capture host=%s backend=%s device=%s duration_seconds=%.1f input_gain=%.3f",
        args.host_label or "-",
        input_config.backend,
        input_config.label,
        args.duration_seconds,
        args.input_gain,
    )

    with open_input_stream(
        sample_rate=SAMPLE_RATE,
        frame_length=FRAME_LENGTH,
        callback=callback,
        args=args,
        logger=logger,
    ):
        time.sleep(max(0.0, args.duration_seconds))

    elapsed = time.time() - start
    with lock:
        samples = list(energies)

    payload = {
        "host_label": args.host_label,
        "duration_seconds": round(elapsed, 3),
        "sample_rate": SAMPLE_RATE,
        "frame_length": FRAME_LENGTH,
        "backend": input_config.backend,
        "device": input_config.label,
        "input_gain": args.input_gain,
        "frames_captured": len(samples),
        "average_energy": round(float(np.mean(samples)) if samples else 0.0, 6),
        "p95_energy": round(float(np.percentile(samples, 95)) if samples else 0.0, 6),
        "max_energy": round(float(np.max(samples)) if samples else 0.0, 6),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
