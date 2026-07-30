from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator

from .domain_models import CredentialFreeUrl
from .model_base import CanonicalId, ConfigurationModel, DisplayText, SecretReference
from .runtime_models import (
    BrainRuntimeConfiguration,
    BrainStorageConfiguration,
    InferenceConfiguration,
    SatelliteAudioConfiguration,
    SatelliteUiConfiguration,
    SatelliteWakeConfiguration,
    SpeechConfiguration,
)


_LOCALE_PATTERN = r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"


class BundleManifest(ConfigurationModel):
    kind: Literal["oracle_configuration_bundle"]
    schema_version: Literal[1]
    bundle_id: CanonicalId


class SecretMutationInput(ConfigurationModel):
    logical_id: SecretReference
    value: SecretStr = Field(json_schema_extra={"writeOnly": True, "x-oracle-raw-secret": True})


class LoggingConfiguration(ConfigurationModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class BrainConfiguration(ConfigurationModel):
    runtime: BrainRuntimeConfiguration
    logging: LoggingConfiguration
    storage: BrainStorageConfiguration
    speech: SpeechConfiguration
    inference: InferenceConfiguration


class OperatorAccess(ConfigurationModel):
    mode: Literal["host_local_only", "trusted_boundary"]
    boundary_id: CanonicalId | None = None
    browser_inspection: bool = False
    browser_mutation: bool
    csrf_protection: Literal["boundary_proof"] | None = None
    host_local_cli: Literal[True]

    @model_validator(mode="after")
    def validate_mode(self) -> OperatorAccess:
        if self.mode == "host_local_only":
            if self.boundary_id is not None or self.browser_mutation or self.csrf_protection is not None:
                raise ValueError("host_local_only cannot configure boundary mutation or proof.")
        elif self.boundary_id is None:
            raise ValueError("trusted_boundary mode requires boundary_id.")
        if self.browser_mutation and self.csrf_protection != "boundary_proof":
            raise ValueError("Browser mutation requires boundary_proof CSRF protection.")
        return self


class TrustedBoundary(ConfigurationModel):
    boundary_id: CanonicalId
    enabled: bool
    type: Literal["authenticated_reverse_proxy"]
    trusted_proxy_ids: list[CanonicalId] = Field(min_length=1)
    accepted_headers: list[Literal["authenticated_request"]] = Field(min_length=1)


class PublicHealth(ConfigurationModel):
    enabled: bool


class SatelliteAuthentication(ConfigurationModel):
    enrollment_mode: Literal["per_satellite"]
    directional_credentials_required: Literal[True]


class CredentialBinding(ConfigurationModel):
    source_id: CanonicalId
    credential_secret: SecretReference


class SourceAuthentication(ConfigurationModel):
    credential_bindings: list[CredentialBinding]

    @field_validator("credential_bindings")
    @classmethod
    def reject_duplicate_bindings(cls, bindings: list[CredentialBinding]) -> list[CredentialBinding]:
        source_ids = [binding.source_id for binding in bindings]
        secret_refs = [binding.credential_secret for binding in bindings]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("A source can have only one credential binding.")
        if len(secret_refs) != len(set(secret_refs)):
            raise ValueError("A credential secret reference can belong to only one source binding.")
        return bindings


class AccessConfiguration(ConfigurationModel):
    operator_access: OperatorAccess
    trusted_boundary: TrustedBoundary | None = None
    public_health: PublicHealth
    satellite_authentication: SatelliteAuthentication
    source_authentication: SourceAuthentication | None = None

    @model_validator(mode="after")
    def validate_boundary_reference(self) -> AccessConfiguration:
        boundary_id = self.operator_access.boundary_id
        if self.operator_access.mode == "trusted_boundary":
            if self.trusted_boundary is None or not self.trusted_boundary.enabled:
                raise ValueError("trusted_boundary mode requires one enabled trusted boundary.")
            if self.trusted_boundary.boundary_id != boundary_id:
                raise ValueError("operator_access.boundary_id must match trusted_boundary.boundary_id.")
        elif self.trusted_boundary is not None and self.trusted_boundary.enabled:
            raise ValueError("An enabled trusted boundary requires trusted_boundary operator mode.")
        return self


class HomeLocation(ConfigurationModel):
    locality: DisplayText | None = None
    region: DisplayText | None = None
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    latitude: Annotated[float, Field(ge=-90, le=90)] | None = None
    longitude: Annotated[float, Field(ge=-180, le=180)] | None = None


class HouseholdIdentity(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    timezone: str = Field(min_length=1, max_length=128)
    locale: str = Field(min_length=2, max_length=64, pattern=_LOCALE_PATTERN)
    home_location: HomeLocation | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an installed IANA timezone identifier.") from exc
        return value


class AudiobookUserCapability(ConfigurationModel):
    enabled: bool
    account_id: CanonicalId | None = None
    credential_secret: SecretReference | None = None

    @model_validator(mode="after")
    def require_enabled_fields(self) -> AudiobookUserCapability:
        if self.enabled and (self.account_id is None or self.credential_secret is None):
            raise ValueError("Enabled audiobook capability requires account_id and credential_secret.")
        return self


class UserCapabilities(ConfigurationModel):
    audiobooks: AudiobookUserCapability | None = None


class UserConfiguration(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    display_name: DisplayText
    aliases: list[DisplayText]
    capabilities: UserCapabilities


class HouseholdDefaults(ConfigurationModel):
    user_id: CanonicalId | None = None


class RoomConfiguration(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    display_name: DisplayText
    aliases: list[DisplayText]


class SourceConfiguration(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    type: Literal["satellite", "mobile_app", "browser", "desktop_app", "kiosk"]
    fixed: bool
    associated_room_id: CanonicalId | None = None
    associated_user_id: CanonicalId | None = None

    @model_validator(mode="after")
    def fixed_room_association(self) -> SourceConfiguration:
        if self.associated_room_id is not None and not self.fixed:
            raise ValueError("associated_room_id is allowed only when fixed is true.")
        return self


class ModeConfiguration(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    display_name: DisplayText
    aliases: list[DisplayText] = Field(default_factory=list)


class UiEscapeHatchLink(ConfigurationModel):
    label: DisplayText
    url: CredentialFreeUrl
    icon: Literal[
        "open_in_new",
        "cloud",
        "calendar_month",
        "music_note",
        "home",
    ] | None = None


class HouseholdUiConfiguration(ConfigurationModel):
    escape_hatches: dict[
        Literal["weather", "calendar", "audio", "house"],
        list[UiEscapeHatchLink],
    ] = Field(default_factory=dict)


class HouseholdConfiguration(ConfigurationModel):
    household: HouseholdIdentity
    defaults: HouseholdDefaults
    users: list[UserConfiguration]
    rooms: list[RoomConfiguration]
    sources: list[SourceConfiguration]
    modes: list[ModeConfiguration]
    ui: HouseholdUiConfiguration = Field(default_factory=HouseholdUiConfiguration)


class SatelliteCapabilities(ConfigurationModel):
    voice: bool
    display: bool
    music_playback: bool
    audiobook_playback: bool


class CredentialConfiguration(ConfigurationModel):
    credential_secret: SecretReference


class SatelliteBrainClientConfiguration(ConfigurationModel):
    base_url: CredentialFreeUrl | None = None
    credential_secret: SecretReference


class SatelliteControlServiceConfiguration(ConfigurationModel):
    base_url: CredentialFreeUrl | None = None
    local_client_url: CredentialFreeUrl | None = None
    credential_secret: SecretReference


class SatelliteConfiguration(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    source_id: CanonicalId | None = None
    platform: Literal["linux", "windows"] | None = None
    capabilities: SatelliteCapabilities | None = None
    brain_client: SatelliteBrainClientConfiguration | None = None
    control_service: SatelliteControlServiceConfiguration | None = None
    enrollment: CredentialConfiguration | None = None
    audio: SatelliteAudioConfiguration | None = None
    ui: SatelliteUiConfiguration | None = None
    wake: SatelliteWakeConfiguration | None = None

    @model_validator(mode="after")
    def require_enabled_fields(self) -> SatelliteConfiguration:
        required = (
            self.source_id,
            self.platform,
            self.capabilities,
            self.brain_client,
            self.control_service,
            self.enrollment,
        )
        if self.enabled and any(value is None for value in required):
            raise ValueError("Enabled satellites require source, platform, capabilities, and directional credentials.")
        if self.enabled and self.capabilities is not None:
            if self.brain_client is None or self.brain_client.base_url is None:
                raise ValueError("Enabled satellites require a satellite-to-Brain URL.")
            if self.capabilities.voice and (self.audio is None or self.wake is None):
                raise ValueError("Voice-capable satellites require typed audio and wake configuration.")
            if (
                self.capabilities.music_playback or self.capabilities.audiobook_playback
            ) and self.audio is None:
                raise ValueError("Playback-capable satellites require typed audio configuration.")
            if (
                self.capabilities.music_playback or self.capabilities.audiobook_playback
            ) and (self.control_service is None or self.control_service.base_url is None):
                raise ValueError("Playback-capable satellites require a Brain-facing control-service URL.")
            if (
                self.capabilities.voice
                or self.capabilities.music_playback
                or self.capabilities.audiobook_playback
            ) and (
                self.control_service is None or self.control_service.local_client_url is None
            ):
                raise ValueError("Voice- or playback-capable satellites require a local control-service client URL.")
            if self.capabilities.display and (self.ui is None or not self.ui.enabled):
                raise ValueError("Display-capable satellites require enabled UI configuration.")
            if not self.capabilities.display and self.ui is not None and self.ui.enabled:
                raise ValueError("Enabled UI requires the satellite display capability.")
            if not self.capabilities.voice and self.wake is not None and self.wake.enabled:
                raise ValueError("Enabled wake detection requires the satellite voice capability.")
        if self.platform == "windows" and self.audio is not None and self.audio.input.type == "alsa_arecord":
            raise ValueError("Windows satellites cannot select the ALSA capture adapter.")
        if self.audio is not None and self.audio.playback.volume_control is not None:
            volume_type = self.audio.playback.volume_control.type
            if self.platform == "windows" and volume_type == "alsa":
                raise ValueError("Windows satellites cannot select ALSA volume control.")
            if self.platform == "linux" and volume_type == "windows_default_endpoint":
                raise ValueError("Linux satellites cannot select Windows default-endpoint volume control.")
        return self


class SatellitesConfiguration(ConfigurationModel):
    satellites: list[SatelliteConfiguration]


from .domain_models import (  # noqa: E402
    AudiobooksConfiguration,
    CalendarConfiguration,
    HomeAssistantConfiguration,
    InformationConfiguration,
    MusicConfiguration,
    NetworkAdaptersConfiguration,
    NetworkInventoryConfiguration,
    NetworkPolicyConfiguration,
    NotificationsConfiguration,
    RoutinesConfiguration,
    WeatherConfiguration,
)


REQUIRED_ROLE_MODELS: dict[str, type[ConfigurationModel]] = {
    "bundle.yaml": BundleManifest,
    "brain.yaml": BrainConfiguration,
    "access.yaml": AccessConfiguration,
    "household.yaml": HouseholdConfiguration,
    "satellites.yaml": SatellitesConfiguration,
}

OPTIONAL_ROLE_MODELS: dict[str, type[ConfigurationModel]] = {
    "domains/information.yaml": InformationConfiguration,
    "domains/music.yaml": MusicConfiguration,
    "domains/audiobooks.yaml": AudiobooksConfiguration,
    "domains/weather.yaml": WeatherConfiguration,
    "domains/calendar.yaml": CalendarConfiguration,
    "domains/home-assistant.yaml": HomeAssistantConfiguration,
    "domains/notifications.yaml": NotificationsConfiguration,
    "domains/routines.yaml": RoutinesConfiguration,
    "domains/network/inventory.yaml": NetworkInventoryConfiguration,
    "domains/network/policy.yaml": NetworkPolicyConfiguration,
    "domains/network/adapters.yaml": NetworkAdaptersConfiguration,
}

ROLE_MODELS = REQUIRED_ROLE_MODELS | OPTIONAL_ROLE_MODELS


def validate_required_role(path: str, primitive: dict[str, object]) -> ConfigurationModel:
    try:
        model = REQUIRED_ROLE_MODELS[path]
    except KeyError as exc:
        raise ValueError(f"No required-role schema is registered for {path!r}.") from exc
    return model.model_validate(primitive)


def validate_role(path: str, primitive: dict[str, object]) -> ConfigurationModel:
    try:
        model = ROLE_MODELS[path]
    except KeyError as exc:
        raise ValueError(f"No role schema is registered for {path!r}.") from exc
    return model.model_validate(primitive)
