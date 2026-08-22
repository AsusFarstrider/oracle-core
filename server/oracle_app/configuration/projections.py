from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from .domain_models import CredentialFreeUrl, MusicConfiguration, PlexMusicProvider
from .loader import LoadedBundle
from .model_base import CanonicalId, ConfigurationModel, SecretReference
from .models import (
    SatelliteAudioConfiguration,
    SatelliteBrainClientConfiguration,
    SatelliteConfiguration,
    SatelliteWakeConfiguration,
)
from .runtime_models import (
    AudioInputDevice,
    AudioOutputDevice,
    CueConfiguration,
    FollowupConfiguration,
    Gain,
    InterimAcknowledgementConfiguration,
    NonNegativeSeconds,
    PositiveSeconds,
    UnitFraction,
    VadConfiguration,
    VolumeControlConfiguration,
)
from .normalization import canonicalize_json
from .generations import GenerationIntegrityError, GenerationStore, _atomic_replace, _read_json
from .secrets import SecretSnapshot


PROJECTION_SCHEMA_VERSION = 1
PROJECTION_REVISION_PREFIX = "oracle-projection-v1:sha256:"
class ProjectionGenerationError(ValueError):
    pass


def _unique(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("Runtime compatibility lists cannot contain duplicates.")
    return sorted(values)


class InteractionRuntimeCompatibility(ConfigurationModel):
    runtime_version: Annotated[str, Field(min_length=1, max_length=128)]
    voice_capture: bool
    brain_interaction: bool
    conversational_audio: bool
    wake_processing: bool
    cues: bool
    audio_input_types: list[Literal["system_default", "portaudio_name", "portaudio_index", "alsa_arecord"]]
    interaction_output_types: list[Literal["system_default", "portaudio_name", "portaudio_index"]]
    wake_model_formats: list[Literal["onnx", "tflite"]]

    @field_validator(
        "audio_input_types",
        "interaction_output_types",
        "wake_model_formats",
    )
    @classmethod
    def unique_sorted(cls, values: list[Any]) -> list[Any]:
        return _unique(values)


class ControlServiceCompatibility(ConfigurationModel):
    runtime_version: Annotated[str, Field(min_length=1, max_length=128)]
    playback_authority_schema_versions: list[Annotated[int, Field(ge=1)]]
    oracle_native_music: bool
    oracle_audiobook: bool
    volume_control_types: list[Literal["alsa", "windows_default_endpoint"]]

    @field_validator("playback_authority_schema_versions", "volume_control_types")
    @classmethod
    def unique_sorted(cls, values: list[Any]) -> list[Any]:
        return _unique(values)


class SatelliteRuntimeCompatibility(ConfigurationModel):
    platform: Literal["linux", "windows"]
    projection_schema_versions: list[Annotated[int, Field(ge=1)]]
    interaction_runtime: InteractionRuntimeCompatibility
    control_service: ControlServiceCompatibility

    @field_validator("projection_schema_versions")
    @classmethod
    def unique_sorted(cls, values: list[Any]) -> list[Any]:
        return _unique(values)


class ProjectedMusicConfiguration(ConfigurationModel):
    provider_id: CanonicalId
    provider: PlexMusicProvider


class ProjectedControlServiceClient(ConfigurationModel):
    local_client_url: CredentialFreeUrl
    credential_secret: SecretReference


class ProjectedInteractionPlaybackConfiguration(ConfigurationModel):
    interrupt_replies: bool
    post_playback_block_seconds: NonNegativeSeconds
    interrupt_settle_seconds: NonNegativeSeconds
    duck_volume: Annotated[int, Field(ge=0, le=100)]
    duck_stage_one_volume: Annotated[int, Field(ge=0, le=100)]
    duck_stage_two_volume: Annotated[int, Field(ge=0, le=100)]
    duck_stage_three_volume: Annotated[int, Field(ge=0, le=100)]
    duck_trigger_threshold: UnitFraction
    duck_max_seconds: PositiveSeconds


class ProjectedInteractionAudioConfiguration(ConfigurationModel):
    input: AudioInputDevice
    interaction_output: AudioOutputDevice
    input_gain: Gain
    playback_gain: Gain
    vad: VadConfiguration
    followup: FollowupConfiguration
    cues: CueConfiguration
    interim_acknowledgement: InterimAcknowledgementConfiguration
    alerts_poll_seconds: PositiveSeconds
    playback: ProjectedInteractionPlaybackConfiguration


class ProjectedInteractionRuntimeConfiguration(ConfigurationModel):
    control_service_client: ProjectedControlServiceClient
    audio: ProjectedInteractionAudioConfiguration | None
    wake: SatelliteWakeConfiguration | None


class ProjectedControlServiceConfiguration(ConfigurationModel):
    credential_secret: SecretReference
    adapter: Literal["oracle_native"]
    volume_control: VolumeControlConfiguration | None
    music: ProjectedMusicConfiguration | None = None


class SatelliteProjectedConfiguration(ConfigurationModel):
    brain_client: SatelliteBrainClientConfiguration
    interaction_runtime: ProjectedInteractionRuntimeConfiguration | None
    control_service: ProjectedControlServiceConfiguration | None


class SatelliteProjection(ConfigurationModel):
    kind: Literal["oracle-satellite-projection"]
    projection_schema_version: Literal[1]
    satellite_id: CanonicalId
    source_id: CanonicalId
    projection_revision: Annotated[str, Field(pattern=r"^oracle-projection-v1:sha256:[0-9a-f]{64}$")]
    runtime_compatibility: SatelliteRuntimeCompatibility
    configuration: SatelliteProjectedConfiguration


@dataclass(frozen=True)
class GeneratedSatelliteProjection:
    source_config_revision: str
    projection: SatelliteProjection
    canonical_bytes: bytes
    required_secret_ids: frozenset[str]
    secrets: SecretSnapshot


@dataclass(frozen=True)
class AcceptedSatelliteRuntimeCompatibility:
    satellite_id: str
    accepted_at: str
    report: SatelliteRuntimeCompatibility


class SatelliteRuntimeCompatibilityStore:
    FORMAT = "oracle-satellite-runtime-compatibility-v1"
    _SATELLITE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")

    def __init__(self, store: GenerationStore) -> None:
        store.validate_initialized()
        self.store = store
        self.directory = Path(store.root) / "runtime-compatibility"

    def accept(
        self,
        satellite_id: str,
        report: SatelliteRuntimeCompatibility,
        *,
        accepted_at: str | None = None,
    ) -> AcceptedSatelliteRuntimeCompatibility:
        path = self._path(satellite_id)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.resolve(strict=True).is_relative_to(self.store.root):
            raise GenerationIntegrityError("Runtime compatibility state escapes the installed store.")
        timestamp = accepted_at or datetime.now(UTC).isoformat()
        payload = {
            "format": self.FORMAT,
            "satellite_id": satellite_id,
            "accepted_at": timestamp,
            "report": report.model_dump(mode="json"),
        }
        _atomic_replace(path, canonicalize_json(payload), mode=0o600)
        return AcceptedSatelliteRuntimeCompatibility(satellite_id, timestamp, report)

    def load(self, satellite_id: str) -> AcceptedSatelliteRuntimeCompatibility | None:
        path = self._path(satellite_id)
        if not path.exists() and not path.is_symlink():
            return None
        payload = _read_json(path)
        if not isinstance(payload, dict) or set(payload) != {"format", "satellite_id", "accepted_at", "report"}:
            raise GenerationIntegrityError("Runtime compatibility report has an invalid envelope.")
        if payload["format"] != self.FORMAT or payload["satellite_id"] != satellite_id:
            raise GenerationIntegrityError("Runtime compatibility report identity is invalid.")
        if not isinstance(payload["accepted_at"], str) or not payload["accepted_at"]:
            raise GenerationIntegrityError("Runtime compatibility acceptance time is invalid.")
        try:
            report = SatelliteRuntimeCompatibility.model_validate(payload["report"])
        except Exception as exc:
            raise GenerationIntegrityError("Runtime compatibility report is invalid.") from exc
        return AcceptedSatelliteRuntimeCompatibility(satellite_id, payload["accepted_at"], report)

    def _path(self, satellite_id: str) -> Path:
        if self._SATELLITE_ID.fullmatch(satellite_id) is None:
            raise ValueError("Satellite compatibility identity is invalid.")
        return self.directory / f"{satellite_id}.json"


def generate_satellite_projection(
    bundle: LoadedBundle,
    *,
    source_config_revision: str,
    satellite_id: str,
    runtime_compatibility: SatelliteRuntimeCompatibility,
    secrets: SecretSnapshot,
) -> GeneratedSatelliteProjection:
    if re.fullmatch(r"oracle-config-v2:sha256:[0-9a-f]{64}", source_config_revision) is None:
        raise ProjectionGenerationError("Projection source configuration revision is invalid.")
    satellite = next((item for item in bundle.satellites.satellites if item.id == satellite_id), None)
    if satellite is None or not satellite.enabled:
        raise ProjectionGenerationError("Projection requires one enabled configured satellite.")
    _validate_runtime_compatibility(satellite, runtime_compatibility)
    if (
        satellite.source_id is None
        or satellite.platform is None
        or satellite.capabilities is None
        or satellite.brain_client is None
        or satellite.control_service is None
    ):
        raise ProjectionGenerationError("Enabled satellite is missing required projected configuration.")

    music = _project_music(bundle, satellite)
    capabilities = satellite.capabilities
    interaction_runtime = None
    if capabilities.voice:
        interaction_runtime = ProjectedInteractionRuntimeConfiguration(
            control_service_client=ProjectedControlServiceClient(
                local_client_url=satellite.control_service.local_client_url,
                credential_secret=satellite.control_service.credential_secret,
            ),
            audio=_project_interaction_audio(satellite.audio),
            wake=satellite.wake,
        )
    control_service = None
    if capabilities.music_playback or capabilities.audiobook_playback:
        control_service = ProjectedControlServiceConfiguration(
            credential_secret=satellite.control_service.credential_secret,
            adapter=satellite.audio.playback.adapter,
            volume_control=satellite.audio.playback.volume_control,
            music=music,
        )
    configuration = SatelliteProjectedConfiguration(
        brain_client=satellite.brain_client,
        interaction_runtime=interaction_runtime,
        control_service=control_service,
    )
    revision_payload: dict[str, Any] = {
        "kind": "oracle-satellite-projection",
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "satellite_id": satellite.id,
        "source_id": satellite.source_id,
        "runtime_compatibility": runtime_compatibility.model_dump(mode="json"),
        "configuration": configuration.model_dump(mode="json"),
    }
    revision_bytes = canonicalize_json(revision_payload)
    revision = f"{PROJECTION_REVISION_PREFIX}{hashlib.sha256(revision_bytes).hexdigest()}"
    projection = SatelliteProjection(
        **revision_payload,
        projection_revision=revision,
    )
    canonical_bytes = canonicalize_json(projection.model_dump(mode="json"))
    required_ids = _projection_secret_ids(satellite, music)
    missing = required_ids - secrets.present_ids
    if missing:
        raise ProjectionGenerationError("Projection is missing required satellite-local secret values.")
    local_secrets = SecretSnapshot({logical_id: secrets.resolve(logical_id) or "" for logical_id in required_ids})
    return GeneratedSatelliteProjection(source_config_revision, projection, canonical_bytes, required_ids, local_secrets)


def _validate_runtime_compatibility(
    satellite: SatelliteConfiguration,
    runtime: SatelliteRuntimeCompatibility,
) -> None:
    if satellite.platform != runtime.platform or PROJECTION_SCHEMA_VERSION not in runtime.projection_schema_versions:
        raise ProjectionGenerationError("Satellite runtime platform or projection schema is incompatible.")
    capabilities = satellite.capabilities
    if capabilities is None:
        raise ProjectionGenerationError("Satellite capabilities are missing.")
    interaction = runtime.interaction_runtime
    if capabilities.voice:
        if not all(
            (
                interaction.voice_capture,
                interaction.brain_interaction,
                interaction.conversational_audio,
                interaction.cues,
            )
        ):
            raise ProjectionGenerationError("Voice-capable projection requires a compatible interaction runtime.")
        if satellite.audio is None or satellite.audio.input.type not in interaction.audio_input_types:
            raise ProjectionGenerationError("Interaction runtime lacks the configured audio input adapter.")
        if satellite.audio.interaction_output.type not in interaction.interaction_output_types:
            raise ProjectionGenerationError("Interaction runtime lacks the configured conversational output adapter.")
        if satellite.wake is not None and satellite.wake.enabled:
            if not interaction.wake_processing or satellite.wake.model is None or satellite.wake.model.format not in interaction.wake_model_formats:
                raise ProjectionGenerationError("Interaction runtime lacks the configured wake mechanics.")
    control = runtime.control_service
    if capabilities.music_playback or capabilities.audiobook_playback:
        if 1 not in control.playback_authority_schema_versions:
            raise ProjectionGenerationError("Playback-capable projection requires a compatible control service.")
        if capabilities.music_playback and not control.oracle_native_music:
            raise ProjectionGenerationError("Music-capable projection requires Oracle native music.")
        if capabilities.audiobook_playback and not control.oracle_audiobook:
            raise ProjectionGenerationError("Audiobook-capable projection requires Oracle audiobook playback.")
    if satellite.audio is not None:
        volume = satellite.audio.playback.volume_control
        if volume is not None and volume.type not in control.volume_control_types:
            raise ProjectionGenerationError("Control service lacks the configured volume-control backend.")


def _project_interaction_audio(audio: SatelliteAudioConfiguration | None) -> ProjectedInteractionAudioConfiguration | None:
    if audio is None:
        return None
    return ProjectedInteractionAudioConfiguration(
        input=audio.input,
        interaction_output=audio.interaction_output,
        input_gain=audio.input_gain,
        playback_gain=audio.playback_gain,
        vad=audio.vad,
        followup=audio.followup,
        cues=audio.cues,
        interim_acknowledgement=audio.interim_acknowledgement,
        alerts_poll_seconds=audio.alerts_poll_seconds,
        playback=ProjectedInteractionPlaybackConfiguration(
            **audio.playback.model_dump(exclude={"adapter", "volume_control"})
        ),
    )


def _project_music(bundle: LoadedBundle, satellite: SatelliteConfiguration) -> ProjectedMusicConfiguration | None:
    if satellite.capabilities is None or not satellite.capabilities.music_playback:
        return None
    role = bundle.roles.get("domains/music.yaml")
    if not isinstance(role, MusicConfiguration) or not role.enabled or role.provider is None:
        raise ProjectionGenerationError("Music-capable satellite requires one enabled selected music provider.")
    if satellite.source_id not in role.playback.source_ids:
        raise ProjectionGenerationError("Music-capable satellite source is not enabled for music playback.")
    return ProjectedMusicConfiguration(provider_id=role.provider, provider=role.providers[role.provider])


def _projection_secret_ids(
    satellite: SatelliteConfiguration,
    music: ProjectedMusicConfiguration | None,
) -> frozenset[str]:
    ids: set[str] = set()
    capabilities = satellite.capabilities
    ids.add(satellite.brain_client.credential_secret)  # type: ignore[union-attr]
    if capabilities is not None and capabilities.voice:
        ids.add(satellite.control_service.credential_secret)  # type: ignore[union-attr]
    if capabilities is not None and (capabilities.music_playback or capabilities.audiobook_playback):
        ids.add(satellite.control_service.credential_secret)  # type: ignore[union-attr]
    if music is not None:
        ids.add(music.provider.credential_secret)
    return frozenset(ids)
