from .alsa_arecord import candidate_alsa_input_devices, open_alsa_arecord_stream
from .config import (
    AudioInputConfig,
    AudioOutputConfig,
    resolve_audio_input_config,
    resolve_audio_output_config,
    resolve_input_device,
    resolve_output_device,
    resolve_portaudio_device_name,
)
from .playback import (
    clear_reply_audio_stop_request,
    decode_wav_bytes,
    play_ack_tone,
    play_error_tone,
    play_followup_listen_cue,
    play_wav_bytes,
    play_wav_bytes_with_wake_interrupt,
    reply_audio_stop_requested,
    write_reply_audio_state,
)
from .portaudio import open_portaudio_input_stream


def open_input_stream(
    *,
    sample_rate: int,
    frame_length: int,
    callback,
    args,
    logger=None,
):
    input_config = resolve_audio_input_config(args)
    if input_config.backend == "alsa_arecord":
        return open_alsa_arecord_stream(
            sample_rate=sample_rate,
            frame_length=frame_length,
            callback=callback,
            configured_device=input_config.device,
            logger=logger,
        )
    return open_portaudio_input_stream(
        sample_rate=sample_rate,
        frame_length=frame_length,
        callback=callback,
        device=input_config.device,
    )


__all__ = [
    "AudioInputConfig",
    "AudioOutputConfig",
    "candidate_alsa_input_devices",
    "clear_reply_audio_stop_request",
    "decode_wav_bytes",
    "open_alsa_arecord_stream",
    "open_input_stream",
    "open_portaudio_input_stream",
    "play_ack_tone",
    "play_error_tone",
    "play_followup_listen_cue",
    "play_wav_bytes",
    "play_wav_bytes_with_wake_interrupt",
    "reply_audio_stop_requested",
    "resolve_audio_input_config",
    "resolve_audio_output_config",
    "resolve_input_device",
    "resolve_output_device",
    "resolve_portaudio_device_name",
    "write_reply_audio_state",
]
