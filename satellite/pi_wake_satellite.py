from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pi_runtime import (
    AudioInputConfig,
    CommandOutcome,
    build_satellite_runtime_report,
    build_wake_model,
    capture_utterance_after_wake,
    collect_followup_pre_roll_frames,
    detect_default_model,
    frame_rms,
    is_transport_playback_command,
    resolve_audio_input_config,
    resolve_input_device,
    run,
    should_resume_after_reply_for_transport_command,
    should_listen_for_followup_reply,
)
from pi_runtime.startup import resolve_interaction_runtime_settings


def main() -> None:
    args = resolve_interaction_runtime_settings()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
