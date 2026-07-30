from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .domain_models import CredentialFreeUrl, MachinePath
from .model_base import CanonicalId, ConfigurationModel


PositiveSeconds = Annotated[float, Field(gt=0, le=86400)]
NonNegativeSeconds = Annotated[float, Field(ge=0, le=86400)]
Gain = Annotated[float, Field(ge=0, le=8)]
UnitFraction = Annotated[float, Field(ge=0, le=1)]
PortAudioIndex = Annotated[int, Field(ge=0, le=65535)]


class WakeArbitrationConfiguration(ConfigurationModel):
    window_ms: Annotated[int, Field(ge=100, le=10000)] = 1000
    scoring_strategy: Literal["audio_level_confidence_recent"] = "audio_level_confidence_recent"
    loser_suppression_ms: Annotated[int, Field(ge=0, le=60000)] = 10000


class BrainRuntimeConfiguration(ConfigurationModel):
    wake_arbitration: WakeArbitrationConfiguration = Field(default_factory=WakeArbitrationConfiguration)
    satellite_control_timeout_seconds: PositiveSeconds = 6.0


class MemoryRetentionConfiguration(ConfigurationModel):
    successful_raw_transcript_days: Annotated[int, Field(ge=0, le=3650)] = 14
    failed_raw_transcript_days: Annotated[int, Field(ge=0, le=3650)] = 30
    transcript_metadata_days: Annotated[int, Field(ge=0, le=3650)] = 90
    routine_event_days: Annotated[int, Field(ge=0, le=3650)] = 90
    warning_event_days: Annotated[int, Field(ge=0, le=3650)] = 180
    error_event_days: Annotated[int, Field(ge=0, le=3650)] = 365
    critical_event_days: Annotated[int, Field(ge=0, le=3650)] = 730
    provider_status_event_days: Annotated[int, Field(ge=0, le=3650)] = 180
    lifecycle_event_days: Annotated[int, Field(ge=0, le=3650)] = 365
    snapshot_hourly_days: Annotated[int, Field(ge=0, le=3650)] = 14
    snapshot_daily_days: Annotated[int, Field(ge=0, le=3650)] = 90
    cache_history_days: Annotated[int, Field(ge=0, le=3650)] = 30
    rollup_days: Annotated[int, Field(ge=0, le=3650)] = 365
    evidence_ref_days: Annotated[int, Field(ge=0, le=3650)] = 90


class MemoryStorageConfiguration(ConfigurationModel):
    backend: Literal["sqlite"] = "sqlite"
    database_path: MachinePath = "data/oracle-memory.sqlite3"
    retention: MemoryRetentionConfiguration = Field(default_factory=MemoryRetentionConfiguration)


class AlertStorageConfiguration(ConfigurationModel):
    backend: Literal["json_file"] = "json_file"
    state_path: MachinePath = "data/alerts-state.json"


class BrainStorageConfiguration(ConfigurationModel):
    memory: MemoryStorageConfiguration = Field(default_factory=MemoryStorageConfiguration)
    alerts: AlertStorageConfiguration = Field(default_factory=AlertStorageConfiguration)


class WhisperCppProvider(ConfigurationModel):
    type: Literal["whisper_cpp"]
    binary_path: MachinePath
    model_path: MachinePath
    threads: Annotated[int, Field(ge=1, le=256)] = 8


class FastWhisperProvider(ConfigurationModel):
    type: Literal["fast_whisper"]
    model: Annotated[str, Field(min_length=1, max_length=256)]
    threads: Annotated[int, Field(ge=1, le=256)] = 8


SttProvider = Annotated[WhisperCppProvider | FastWhisperProvider, Field(discriminator="type")]


class PiperProvider(ConfigurationModel):
    type: Literal["piper"]
    binary_path: MachinePath
    model_path: MachinePath


TtsProvider = Annotated[PiperProvider, Field(discriminator="type")]


class SttRole(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, SttProvider] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_selection(self) -> SttRole:
        _validate_provider_selection(self.enabled, self.provider, self.providers, "STT")
        return self


class TtsRole(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, TtsProvider] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_selection(self) -> TtsRole:
        _validate_provider_selection(self.enabled, self.provider, self.providers, "TTS")
        return self


class SpeechConfiguration(ConfigurationModel):
    stt: SttRole
    tts: TtsRole


KeepAlive = Annotated[str, Field(pattern=r"^-?[0-9]+(?:ms|s|m|h)?$", max_length=32)]


class OllamaRequestOptions(ConfigurationModel):
    num_predict: Annotated[int, Field(ge=1, le=32768)] = 120
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.1
    top_p: UnitFraction = 0.9
    seed: int = 7


class OllamaProvider(ConfigurationModel):
    type: Literal["ollama"]
    base_url: CredentialFreeUrl
    model: Annotated[str, Field(min_length=1, max_length=256)]
    timeout_seconds: PositiveSeconds = 20.0
    keep_alive: int | KeepAlive = -1
    options: OllamaRequestOptions = Field(default_factory=OllamaRequestOptions)


InferenceProvider = Annotated[OllamaProvider, Field(discriminator="type")]


class FallbackRouterConfiguration(ConfigurationModel):
    model: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    timeout_seconds: PositiveSeconds | None = None


class SharedInferenceRole(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, InferenceProvider] = Field(default_factory=dict)
    fallback_router: FallbackRouterConfiguration = Field(default_factory=FallbackRouterConfiguration)

    @model_validator(mode="after")
    def validate_selection(self) -> SharedInferenceRole:
        _validate_provider_selection(self.enabled, self.provider, self.providers, "shared inference")
        return self


class InferenceConfiguration(ConfigurationModel):
    shared_backend: SharedInferenceRole


def _validate_provider_selection(
    enabled: bool,
    provider: str | None,
    providers: dict[str, object],
    label: str,
) -> None:
    if enabled and provider is None:
        raise ValueError(f"Enabled {label} requires explicit provider selection.")
    if provider is not None and provider not in providers:
        raise ValueError(f"Selected {label} provider must have a typed definition.")


class DefaultAudioDevice(ConfigurationModel):
    type: Literal["system_default"]


class PortAudioNamedDevice(ConfigurationModel):
    type: Literal["portaudio_name"]
    name: Annotated[str, Field(min_length=1, max_length=256)]


class PortAudioIndexedDevice(ConfigurationModel):
    type: Literal["portaudio_index"]
    index: PortAudioIndex


class AlsaCaptureDevice(ConfigurationModel):
    type: Literal["alsa_arecord"]
    device: Annotated[str, Field(min_length=1, max_length=256)]


AudioInputDevice = Annotated[
    DefaultAudioDevice | PortAudioNamedDevice | PortAudioIndexedDevice | AlsaCaptureDevice,
    Field(discriminator="type"),
]
AudioOutputDevice = Annotated[
    DefaultAudioDevice | PortAudioNamedDevice | PortAudioIndexedDevice,
    Field(discriminator="type"),
]


class VadConfiguration(ConfigurationModel):
    threshold: UnitFraction = 0.015
    noise_multiplier: Annotated[float, Field(ge=1, le=20)] = 1.6
    noise_offset: UnitFraction = 0.006
    release_multiplier: Annotated[float, Field(ge=1, le=20)] = 1.15
    release_offset: UnitFraction = 0.003
    max_speech_threshold: UnitFraction = 0.42
    max_silence_threshold: UnitFraction = 0.30
    silence_seconds: PositiveSeconds = 0.75
    speech_start_timeout_seconds: PositiveSeconds = 1.6
    false_start_silence_seconds: PositiveSeconds = 0.45
    max_record_seconds: PositiveSeconds = 8.0
    min_speech_seconds: PositiveSeconds = 0.2


class FollowupConfiguration(ConfigurationModel):
    conversation_timeout_seconds: PositiveSeconds = 90.0
    silence_seconds: PositiveSeconds = 0.3
    max_record_seconds: PositiveSeconds = 4.0
    speech_start_timeout_seconds: PositiveSeconds = 2.5


class CueConfiguration(ConfigurationModel):
    ack_enabled: bool = True
    ack_gain: Gain = 0.16
    error_enabled: bool = True
    error_cooldown_seconds: NonNegativeSeconds = 3.0
    alarm_asset: CanonicalId = "alarm"
    timer_asset: CanonicalId = "timer"


class InterimAcknowledgementConfiguration(ConfigurationModel):
    enabled: bool = True
    poll_interval_seconds: PositiveSeconds = 0.15
    request_timeout_seconds: PositiveSeconds = 0.75


class AlsaVolumeControlConfiguration(ConfigurationModel):
    type: Literal["alsa"]
    card: Annotated[str, Field(min_length=1, max_length=256)]
    control: Annotated[str, Field(min_length=1, max_length=256)]


class WindowsDefaultEndpointVolumeControlConfiguration(ConfigurationModel):
    type: Literal["windows_default_endpoint"]


VolumeControlConfiguration = Annotated[
    AlsaVolumeControlConfiguration | WindowsDefaultEndpointVolumeControlConfiguration,
    Field(discriminator="type"),
]


class PlaybackConfiguration(ConfigurationModel):
    adapter: Literal["oracle_native"]
    volume_control: VolumeControlConfiguration | None = None
    interrupt_replies: bool = False
    post_playback_block_seconds: NonNegativeSeconds = 2.0
    interrupt_settle_seconds: NonNegativeSeconds = 0.35
    duck_volume: Annotated[int, Field(ge=0, le=100)] = 18
    duck_stage_one_volume: Annotated[int, Field(ge=0, le=100)] = 28
    duck_stage_two_volume: Annotated[int, Field(ge=0, le=100)] = 22
    duck_stage_three_volume: Annotated[int, Field(ge=0, le=100)] = 18
    duck_trigger_threshold: UnitFraction = 0.12
    duck_max_seconds: PositiveSeconds = 4.0


class SatelliteAudioConfiguration(ConfigurationModel):
    input: AudioInputDevice
    interaction_output: AudioOutputDevice
    input_gain: Gain = 2.0
    playback_gain: Gain = 0.35
    vad: VadConfiguration = Field(default_factory=VadConfiguration)
    followup: FollowupConfiguration = Field(default_factory=FollowupConfiguration)
    cues: CueConfiguration = Field(default_factory=CueConfiguration)
    interim_acknowledgement: InterimAcknowledgementConfiguration = Field(
        default_factory=InterimAcknowledgementConfiguration
    )
    alerts_poll_seconds: PositiveSeconds = 2.0
    playback: PlaybackConfiguration


SatelliteUiPage = Literal["home", "weather", "calendar", "audio", "music", "audiobooks", "house"]


class SatelliteUiConfiguration(ConfigurationModel):
    enabled: bool
    touch: bool
    profile: CanonicalId | None = None
    layout: CanonicalId | None = None
    pages: list[SatelliteUiPage] = Field(default_factory=list)
    bottom_nav: list[SatelliteUiPage] = Field(default_factory=list)

    @field_validator("pages", "bottom_nav")
    @classmethod
    def reject_duplicate_pages(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Satellite UI page lists must not contain duplicates.")
        return values

    @model_validator(mode="after")
    def validate_enabled_ui(self) -> SatelliteUiConfiguration:
        if self.enabled and (self.profile is None or self.layout is None or not self.pages):
            raise ValueError("Enabled satellite UI requires profile, layout, and at least one page.")
        if any(page not in self.pages for page in self.bottom_nav):
            raise ValueError("Satellite UI bottom_nav entries must also appear in pages.")
        return self


class WakeModelConfiguration(ConfigurationModel):
    format: Literal["onnx", "tflite"]
    asset_id: CanonicalId | None = None
    path: MachinePath | None = None

    @model_validator(mode="after")
    def require_one_location(self) -> WakeModelConfiguration:
        if (self.asset_id is None) == (self.path is None):
            raise ValueError("Wake model requires exactly one package asset ID or machine path.")
        if self.path is not None and not self.path.lower().endswith(f".{self.format}"):
            raise ValueError("Wake model path suffix must match its declared format.")
        return self


class WakePlaybackSuppressionConfiguration(ConfigurationModel):
    threshold: UnitFraction = 0.16
    log_threshold: UnitFraction = 0.09
    poll_seconds: PositiveSeconds = 0.35
    hold_seconds: PositiveSeconds = 1.25
    consecutive_frames: Annotated[int, Field(ge=1, le=100)] = 2


class WakeCaptureSyncConfiguration(ConfigurationModel):
    enabled: bool = False
    interval_seconds: PositiveSeconds = 86400.0
    delete_local_after_sync: bool = True
    synced_local_retention_days: Annotated[int, Field(ge=0, le=3650)] = 7


class WakeCaptureConfiguration(ConfigurationModel):
    enabled: bool = False
    capture_activation: bool = True
    capture_near_threshold: bool = True
    pre_roll_ms: Annotated[int, Field(ge=0, le=30000)] = 2500
    post_roll_ms: Annotated[int, Field(ge=0, le=30000)] = 1500
    near_threshold_fraction: UnitFraction = 0.85
    event_cooldown_seconds: NonNegativeSeconds = 3.0
    local_storage_path: MachinePath | None = None
    sync: WakeCaptureSyncConfiguration = Field(default_factory=WakeCaptureSyncConfiguration)


class SatelliteWakeConfiguration(ConfigurationModel):
    enabled: bool
    model: WakeModelConfiguration | None = None
    threshold: UnitFraction = 0.2
    log_threshold: UnitFraction = 0.1
    cooldown_seconds: NonNegativeSeconds = 6.0
    retry_cooldown_seconds: NonNegativeSeconds = 1.0
    arbitration_timeout_seconds: PositiveSeconds = 5.0
    arbitration_loser_suppression_ms: Annotated[int, Field(ge=0, le=60000)] = 10000
    playback_suppression: WakePlaybackSuppressionConfiguration = Field(
        default_factory=WakePlaybackSuppressionConfiguration
    )
    capture: WakeCaptureConfiguration = Field(default_factory=WakeCaptureConfiguration)

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> SatelliteWakeConfiguration:
        if self.enabled and self.model is None:
            raise ValueError("Enabled wake detection requires a typed model selection.")
        return self
