from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from oracle_satellite_runtime_config import InteractionRuntimeEffectiveConfig


@dataclass(frozen=True)
class InteractionRuntimeHostBootstrap:
    config_bind_host: str
    config_bind_port: int
    reply_audio_state_path: str
    reply_audio_stop_path: str
    packaged_asset_paths: Mapping[str, str]
    wake_capture_default_storage_path: str
    list_devices: bool = False
    log_level: str = "INFO"


@dataclass(frozen=True)
class InteractionRuntimeSettings:
    oracle_url: str
    brain_api_key: str = field(repr=False)
    satellite_id: str
    source: str
    model_path: str
    wake_threshold: float
    wake_log_threshold: float
    wake_playback_threshold: float
    wake_playback_log_threshold: float
    wake_playback_poll_seconds: float
    wake_playback_hold_seconds: float
    wake_playback_consecutive_frames: int
    wake_cooldown_seconds: float
    wake_retry_cooldown_seconds: float
    wake_arbitration_timeout_seconds: float
    wake_arbitration_loser_suppression_ms: int
    input_device_index: int | None
    input_device_name: str | None
    input_alsa_device: str | None
    output_device_index: int | None
    output_device_name: str | None
    input_gain: float
    playback_gain: float
    alarm_sound_path: str
    timer_sound_path: str
    reply_audio_state_path: str
    reply_audio_stop_path: str
    interim_ack_poll_interval_seconds: float
    interim_ack_request_timeout_seconds: float
    config_bind_host: str
    config_bind_port: int
    error_tone_cooldown_seconds: float
    ack_tone_enabled: bool
    error_tone_enabled: bool
    ack_tone_gain: float
    conversation_timeout_seconds: float
    alerts_poll_seconds: float
    vad_threshold: float
    vad_noise_multiplier: float
    vad_noise_offset: float
    vad_release_multiplier: float
    vad_release_offset: float
    vad_max_speech_threshold: float
    vad_max_silence_threshold: float
    silence_seconds: float
    speech_start_timeout_seconds: float
    false_start_silence_seconds: float
    post_playback_block_seconds: float
    max_record_seconds: float
    min_speech_seconds: float
    followup_silence_seconds: float
    followup_max_record_seconds: float
    followup_speech_start_timeout_seconds: float
    music_control_url: str
    music_control_api_key: str = field(repr=False)
    music_duck_volume: int
    music_duck_stage_one_volume: int
    music_duck_stage_two_volume: int
    music_duck_stage_three_volume: int
    music_duck_trigger_threshold: float
    music_duck_max_seconds: float
    playback_interrupt_settle_seconds: float
    wake_capture_enabled: bool
    wake_capture_activation: bool
    wake_capture_near_threshold: bool
    wake_capture_pre_roll_ms: int
    wake_capture_post_roll_ms: int
    wake_capture_near_threshold_fraction: float
    wake_capture_event_cooldown_seconds: float
    wake_capture_local_storage_path: str
    wake_capture_sync_enabled: bool
    wake_capture_sync_interval_seconds: float
    wake_capture_sync_delete_local_after_sync: bool
    wake_capture_sync_synced_local_retention_days: int
    interrupt_replies: bool
    interim_ack_enabled: bool
    list_devices: bool
    log_level: str

    @classmethod
    def from_canonical(
        cls,
        effective: InteractionRuntimeEffectiveConfig,
        bootstrap: InteractionRuntimeHostBootstrap,
    ) -> InteractionRuntimeSettings:
        config = effective.configuration
        audio = _mapping(config.get("audio"), "interaction audio")
        wake = _mapping(config.get("wake"), "wake configuration")
        if wake.get("enabled") is not True:
            raise ValueError("Canonical wake interaction runtime must enable wake processing.")

        model = _mapping(wake.get("model"), "wake model")
        model_path = _machine_or_asset_path(
            model,
            bootstrap.packaged_asset_paths,
            "wake model",
        )
        model_format = _text(model.get("format"), "wake-model format")
        if not model_path.lower().endswith(f".{model_format}"):
            raise ValueError("Canonical wake-model path does not match its format.")

        input_device_index, input_device_name, input_alsa_device = _input_device(
            _mapping(audio.get("input"), "audio input")
        )
        output_device_index, output_device_name = _output_device(
            _mapping(audio.get("interaction_output"), "interaction output")
        )
        vad = _mapping(audio.get("vad"), "VAD configuration")
        followup = _mapping(audio.get("followup"), "follow-up configuration")
        cues = _mapping(audio.get("cues"), "cue configuration")
        interim = _mapping(
            audio.get("interim_acknowledgement"),
            "interim acknowledgement configuration",
        )
        playback = _mapping(audio.get("playback"), "interaction playback")
        suppression = _mapping(wake.get("playback_suppression"), "wake playback suppression")
        capture = _mapping(wake.get("capture"), "wake capture")
        capture_sync = _mapping(capture.get("sync"), "wake capture sync")

        capture_storage = capture.get("local_storage_path")
        if capture_storage is None:
            capture_storage = bootstrap.wake_capture_default_storage_path

        return cls(
            oracle_url=_text(effective.brain_base_url, "Brain endpoint"),
            brain_api_key=_text(effective.brain_credential, "Brain credential"),
            satellite_id=_text(effective.satellite_id, "satellite identity"),
            source=_text(effective.source_id, "source identity"),
            model_path=model_path,
            wake_threshold=_number(wake.get("threshold"), "wake threshold"),
            wake_log_threshold=_number(wake.get("log_threshold"), "wake log threshold"),
            wake_playback_threshold=_number(suppression.get("threshold"), "wake playback threshold"),
            wake_playback_log_threshold=_number(
                suppression.get("log_threshold"), "wake playback log threshold"
            ),
            wake_playback_poll_seconds=_number(suppression.get("poll_seconds"), "wake playback poll"),
            wake_playback_hold_seconds=_number(suppression.get("hold_seconds"), "wake playback hold"),
            wake_playback_consecutive_frames=_integer(
                suppression.get("consecutive_frames"), "wake playback consecutive frames"
            ),
            wake_cooldown_seconds=_number(wake.get("cooldown_seconds"), "wake cooldown"),
            wake_retry_cooldown_seconds=_number(
                wake.get("retry_cooldown_seconds"), "wake retry cooldown"
            ),
            wake_arbitration_timeout_seconds=_number(
                wake.get("arbitration_timeout_seconds"), "wake arbitration timeout"
            ),
            wake_arbitration_loser_suppression_ms=_integer(
                wake.get("arbitration_loser_suppression_ms"),
                "wake arbitration loser suppression",
            ),
            input_device_index=input_device_index,
            input_device_name=input_device_name,
            input_alsa_device=input_alsa_device,
            output_device_index=output_device_index,
            output_device_name=output_device_name,
            input_gain=_number(audio.get("input_gain"), "input gain"),
            playback_gain=_number(audio.get("playback_gain"), "playback gain"),
            alarm_sound_path=_asset_path(
                cues.get("alarm_asset"), bootstrap.packaged_asset_paths, "alarm cue"
            ),
            timer_sound_path=_asset_path(
                cues.get("timer_asset"), bootstrap.packaged_asset_paths, "timer cue"
            ),
            reply_audio_state_path=_text(bootstrap.reply_audio_state_path, "reply-audio state path"),
            reply_audio_stop_path=_text(bootstrap.reply_audio_stop_path, "reply-audio stop path"),
            interim_ack_poll_interval_seconds=_number(
                interim.get("poll_interval_seconds"), "interim acknowledgement poll interval"
            ),
            interim_ack_request_timeout_seconds=_number(
                interim.get("request_timeout_seconds"), "interim acknowledgement request timeout"
            ),
            config_bind_host=_text(bootstrap.config_bind_host, "configuration listener host"),
            config_bind_port=_port(bootstrap.config_bind_port),
            error_tone_cooldown_seconds=_number(
                cues.get("error_cooldown_seconds"), "error cue cooldown"
            ),
            ack_tone_enabled=_boolean(cues.get("ack_enabled"), "ack cue enabled"),
            error_tone_enabled=_boolean(cues.get("error_enabled"), "error cue enabled"),
            ack_tone_gain=_number(cues.get("ack_gain"), "ack cue gain"),
            conversation_timeout_seconds=_number(
                followup.get("conversation_timeout_seconds"), "conversation timeout"
            ),
            alerts_poll_seconds=_number(audio.get("alerts_poll_seconds"), "alerts poll interval"),
            vad_threshold=_number(vad.get("threshold"), "VAD threshold"),
            vad_noise_multiplier=_number(vad.get("noise_multiplier"), "VAD noise multiplier"),
            vad_noise_offset=_number(vad.get("noise_offset"), "VAD noise offset"),
            vad_release_multiplier=_number(vad.get("release_multiplier"), "VAD release multiplier"),
            vad_release_offset=_number(vad.get("release_offset"), "VAD release offset"),
            vad_max_speech_threshold=_number(
                vad.get("max_speech_threshold"), "VAD maximum speech threshold"
            ),
            vad_max_silence_threshold=_number(
                vad.get("max_silence_threshold"), "VAD maximum silence threshold"
            ),
            silence_seconds=_number(vad.get("silence_seconds"), "VAD silence duration"),
            speech_start_timeout_seconds=_number(
                vad.get("speech_start_timeout_seconds"), "speech-start timeout"
            ),
            false_start_silence_seconds=_number(
                vad.get("false_start_silence_seconds"), "false-start silence duration"
            ),
            post_playback_block_seconds=_number(
                playback.get("post_playback_block_seconds"), "post-playback block"
            ),
            max_record_seconds=_number(vad.get("max_record_seconds"), "maximum recording duration"),
            min_speech_seconds=_number(vad.get("min_speech_seconds"), "minimum speech duration"),
            followup_silence_seconds=_number(followup.get("silence_seconds"), "follow-up silence"),
            followup_max_record_seconds=_number(
                followup.get("max_record_seconds"), "follow-up maximum recording duration"
            ),
            followup_speech_start_timeout_seconds=_number(
                followup.get("speech_start_timeout_seconds"), "follow-up speech-start timeout"
            ),
            music_control_url=_text(effective.control_service_base_url, "control-service endpoint"),
            music_control_api_key=_text(
                effective.control_service_credential, "control-service credential"
            ),
            music_duck_volume=_integer(playback.get("duck_volume"), "music duck volume"),
            music_duck_stage_one_volume=_integer(
                playback.get("duck_stage_one_volume"), "music duck stage-one volume"
            ),
            music_duck_stage_two_volume=_integer(
                playback.get("duck_stage_two_volume"), "music duck stage-two volume"
            ),
            music_duck_stage_three_volume=_integer(
                playback.get("duck_stage_three_volume"), "music duck stage-three volume"
            ),
            music_duck_trigger_threshold=_number(
                playback.get("duck_trigger_threshold"), "music duck trigger threshold"
            ),
            music_duck_max_seconds=_number(playback.get("duck_max_seconds"), "music duck maximum"),
            playback_interrupt_settle_seconds=_number(
                playback.get("interrupt_settle_seconds"), "playback interrupt settle"
            ),
            wake_capture_enabled=_boolean(capture.get("enabled"), "wake capture enabled"),
            wake_capture_activation=_boolean(
                capture.get("capture_activation"), "wake activation capture"
            ),
            wake_capture_near_threshold=_boolean(
                capture.get("capture_near_threshold"), "near-threshold wake capture"
            ),
            wake_capture_pre_roll_ms=_integer(capture.get("pre_roll_ms"), "wake capture pre-roll"),
            wake_capture_post_roll_ms=_integer(capture.get("post_roll_ms"), "wake capture post-roll"),
            wake_capture_near_threshold_fraction=_number(
                capture.get("near_threshold_fraction"), "wake capture near-threshold fraction"
            ),
            wake_capture_event_cooldown_seconds=_number(
                capture.get("event_cooldown_seconds"), "wake capture event cooldown"
            ),
            wake_capture_local_storage_path=_text(capture_storage, "wake capture storage path"),
            wake_capture_sync_enabled=_boolean(capture_sync.get("enabled"), "wake capture sync enabled"),
            wake_capture_sync_interval_seconds=_number(
                capture_sync.get("interval_seconds"), "wake capture sync interval"
            ),
            wake_capture_sync_delete_local_after_sync=_boolean(
                capture_sync.get("delete_local_after_sync"), "wake capture delete-after-sync"
            ),
            wake_capture_sync_synced_local_retention_days=_integer(
                capture_sync.get("synced_local_retention_days"), "wake capture local retention"
            ),
            interrupt_replies=_boolean(playback.get("interrupt_replies"), "reply interruption"),
            interim_ack_enabled=_boolean(interim.get("enabled"), "interim acknowledgement enabled"),
            list_devices=bool(bootstrap.list_devices),
            log_level=_text(bootstrap.log_level, "interaction-runtime log level"),
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Canonical {label} is invalid.")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Canonical {label} is invalid.")
    return value.strip()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Canonical {label} is invalid.")
    return float(value)


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Canonical {label} is invalid.")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Canonical {label} is invalid.")
    return value


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 65535:
        raise ValueError("Interaction-runtime configuration listener port is invalid.")
    return value


def _asset_path(value: Any, asset_paths: Mapping[str, str], label: str) -> str:
    asset_id = _text(value, f"{label} asset ID")
    try:
        path = asset_paths[asset_id]
    except KeyError as exc:
        raise ValueError(f"Canonical {label} asset is not installed.") from exc
    return _text(path, f"{label} asset path")


def _machine_or_asset_path(
    value: Mapping[str, Any],
    asset_paths: Mapping[str, str],
    label: str,
) -> str:
    path = value.get("path")
    asset_id = value.get("asset_id")
    if path is not None and asset_id is None:
        return _text(path, f"{label} path")
    if asset_id is not None and path is None:
        return _asset_path(asset_id, asset_paths, label)
    raise ValueError(f"Canonical {label} location is invalid.")


def _input_device(value: Mapping[str, Any]) -> tuple[int | None, str | None, str | None]:
    kind = value.get("type")
    if kind == "system_default":
        return None, None, None
    if kind == "portaudio_name":
        return None, _text(value.get("name"), "PortAudio input name"), None
    if kind == "portaudio_index":
        return _integer(value.get("index"), "PortAudio input index"), None, None
    if kind == "alsa_arecord":
        return None, None, _text(value.get("device"), "ALSA capture device")
    raise ValueError("Canonical audio-input adapter is unsupported.")


def _output_device(value: Mapping[str, Any]) -> tuple[int | None, str | None]:
    kind = value.get("type")
    if kind == "system_default":
        return None, None
    if kind == "portaudio_name":
        return None, _text(value.get("name"), "PortAudio output name")
    if kind == "portaudio_index":
        return _integer(value.get("index"), "PortAudio output index"), None
    raise ValueError("Canonical interaction-output adapter is unsupported.")
