from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BeforeValidator, Field, field_validator, model_validator

from .model_base import CanonicalId, ConfigurationModel, DisplayText, SecretReference


PositiveSeconds = Annotated[int, Field(ge=1, le=86400)]
BoundedText = Annotated[str, Field(min_length=1, max_length=2048)]
MachinePath = Annotated[str, Field(min_length=1, max_length=1024)]
_SYSTEMD_UNIT_PATTERN = re.compile(r"^[A-Za-z0-9@_.-]+$")
_DOCKER_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_WINDOWS_TASK_PATTERN = re.compile(r"^[A-Za-z0-9_. -]+$")


def _credential_free_url(value: object) -> object:
    if not isinstance(value, str):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL must be an absolute credential-free HTTP(S) URL.")
    return value


CredentialFreeUrl = Annotated[
    str,
    BeforeValidator(_credential_free_url),
    Field(min_length=1, max_length=2048),
]


def _reject_duplicates(values: list[str], *, label: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique.")
    return values


def _require_selected_provider(
    *, enabled: bool, provider: str | None, providers: dict[str, object], label: str
) -> None:
    if enabled and provider is None:
        raise ValueError(f"Enabled {label} requires explicit provider selection.")
    if provider is not None and provider not in providers:
        raise ValueError(f"Selected {label} provider must have a typed definition.")


class StaticFactAnswer(ConfigurationModel):
    text: BoundedText


class StaticFactProvenance(ConfigurationModel):
    url: CredentialFreeUrl | None = None


class StaticFactEvidence(ConfigurationModel):
    title: DisplayText
    snippet: BoundedText
    source_name: DisplayText
    source_type: CanonicalId = "static"
    provenance: StaticFactProvenance | None = None


class StaticFactItem(ConfigurationModel):
    id: CanonicalId
    status: Literal["answered", "evidence_only", "no_result", "provider_error"] | None = None
    queries: list[DisplayText] = Field(min_length=1)
    answer: StaticFactAnswer | None = None
    evidence: list[StaticFactEvidence] = Field(default_factory=list)
    answer_type: CanonicalId | None = None
    detail: BoundedText | None = None

    @model_validator(mode="after")
    def validate_fixture(self) -> StaticFactItem:
        normalized = [" ".join(value.casefold().split()) for value in self.queries]
        _reject_duplicates(normalized, label="Static fact queries")
        if self.status == "answered" and self.answer is None:
            raise ValueError("Answered static fact requires answer text.")
        if self.status == "evidence_only" and not self.evidence:
            raise ValueError("Evidence-only static fact requires evidence.")
        return self


class StaticFactsProvider(ConfigurationModel):
    type: Literal["static"]
    items: list[StaticFactItem]

    @model_validator(mode="after")
    def unique_items_and_queries(self) -> StaticFactsProvider:
        _reject_duplicates([item.id for item in self.items], label="Static fact item IDs")
        queries = [" ".join(query.casefold().split()) for item in self.items for query in item.queries]
        _reject_duplicates(queries, label="Static fact provider queries")
        return self


class WikipediaFactsProvider(ConfigurationModel):
    type: Literal["wikipedia_api"]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}$")] = "en"
    timeout_seconds: PositiveSeconds = 8


FactsProvider = Annotated[StaticFactsProvider | WikipediaFactsProvider, Field(discriminator="type")]


class FactsConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, FactsProvider] = Field(default_factory=dict)
    summarizer_enabled: bool = False
    acknowledgement_enabled: bool = True
    timeout_seconds: PositiveSeconds = 8
    cache_enabled: bool = False
    cache_ttl_seconds: PositiveSeconds = 86400

    @model_validator(mode="after")
    def validate_selection(self) -> FactsConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="facts",
        )
        return self


class RssNewsProvider(ConfigurationModel):
    type: Literal["rss"]
    timeout_seconds: PositiveSeconds = 8


class NewsSource(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    aliases: list[DisplayText] = Field(default_factory=list)
    provider: CanonicalId
    feed_url: CredentialFreeUrl


class NewsConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, RssNewsProvider] = Field(default_factory=dict)
    sources: list[NewsSource] = Field(default_factory=list)
    max_headlines: Annotated[int, Field(ge=1, le=10)] = 3
    fresh_seconds: PositiveSeconds = 300
    stale_if_error_seconds: PositiveSeconds = 1800

    @model_validator(mode="after")
    def validate_selection_and_sources(self) -> NewsConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="news",
        )
        source_ids = [source.id for source in self.sources]
        _reject_duplicates(source_ids, label="News source IDs")
        resolution_terms = [
            " ".join(term.casefold().split())
            for source in self.sources
            for term in (source.id, source.display_name, *source.aliases)
        ]
        _reject_duplicates(resolution_terms, label="News source resolution terms")
        for source in self.sources:
            if source.provider not in self.providers:
                raise ValueError(f"News source {source.id!r} references an undefined provider.")
        if self.enabled and not self.sources:
            raise ValueError("Enabled news requires at least one source.")
        return self


class OpenClawHttpProvider(ConfigurationModel):
    adapter: Literal["http"]
    base_url: CredentialFreeUrl | None = None
    base_url_secret: SecretReference | None = None
    endpoint_path: Annotated[str, Field(pattern=r"^/[^\s]*$")]
    timeout_seconds: PositiveSeconds = 20

    @model_validator(mode="after")
    def one_base_url(self) -> OpenClawHttpProvider:
        if (self.base_url is None) == (self.base_url_secret is None):
            raise ValueError("OpenClaw HTTP provider requires one URL or whole-URL secret reference.")
        return self


class OpenClawSshCliProvider(ConfigurationModel):
    adapter: Literal["ssh_cli"]
    target: Annotated[str, Field(min_length=1, max_length=256)]
    password_secret: SecretReference | None = None
    identity_file: MachinePath | None = None
    connect_timeout_seconds: PositiveSeconds = 8
    timeout_seconds: PositiveSeconds = 20
    cli_path: MachinePath
    cli_mode: Literal["agent", "infer"] = "agent"
    agent: CanonicalId | None = None
    model: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    start_gateway: bool = False
    gateway_port: Annotated[int, Field(ge=1, le=65535)] = 18789

    @model_validator(mode="after")
    def validate_agent_mode(self) -> OpenClawSshCliProvider:
        if self.cli_mode == "agent" and self.agent is None:
            raise ValueError("OpenClaw agent mode requires an agent ID.")
        return self


class OpenClawMockProvider(ConfigurationModel):
    adapter: Literal["mock"]


SuggestionsProvider = Annotated[
    OpenClawHttpProvider | OpenClawSshCliProvider | OpenClawMockProvider,
    Field(discriminator="adapter"),
]


class SuggestionsConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, SuggestionsProvider] = Field(default_factory=dict)
    max_suggestions: Annotated[int, Field(ge=1, le=50)] = 10

    @model_validator(mode="after")
    def validate_selection(self) -> SuggestionsConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="suggestions",
        )
        return self


class InformationConfiguration(ConfigurationModel):
    facts: FactsConfiguration
    news: NewsConfiguration
    suggestions: SuggestionsConfiguration


class PlexMusicProvider(ConfigurationModel):
    type: Literal["plex"]
    base_url: CredentialFreeUrl
    credential_secret: SecretReference
    timeout_seconds: PositiveSeconds = 8
    music_section_id: Annotated[int, Field(ge=1)]
    machine_identifier: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class MusicMatchingPolicy(ConfigurationModel):
    maximum_candidates: Annotated[int, Field(ge=1, le=50)] = 10
    clarification_enabled: bool = True


class MusicPlaybackPolicy(ConfigurationModel):
    source_ids: list[CanonicalId] = Field(default_factory=list)

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, values: list[str]) -> list[str]:
        return _reject_duplicates(values, label="Music playback source IDs")


class MusicConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, PlexMusicProvider] = Field(default_factory=dict)
    matching: MusicMatchingPolicy = Field(default_factory=MusicMatchingPolicy)
    playback: MusicPlaybackPolicy = Field(default_factory=MusicPlaybackPolicy)

    @model_validator(mode="after")
    def validate_selection(self) -> MusicConfiguration:
        _require_selected_provider(enabled=self.enabled, provider=self.provider, providers=self.providers, label="music")
        return self


class AudiobookshelfProvider(ConfigurationModel):
    type: Literal["audiobookshelf"]
    base_url: CredentialFreeUrl
    library_id: Annotated[str, Field(min_length=1, max_length=256)]
    timeout_seconds: PositiveSeconds = 10


class AudiobookPlaybackPolicy(ConfigurationModel):
    source_ids: list[CanonicalId] = Field(default_factory=list)
    default_sleep_timer_minutes: Annotated[int, Field(ge=1, le=1440)] | None = None

    @field_validator("source_ids")
    @classmethod
    def unique_sources(cls, values: list[str]) -> list[str]:
        return _reject_duplicates(values, label="Audiobook playback source IDs")


class AudiobooksConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, AudiobookshelfProvider] = Field(default_factory=dict)
    playback: AudiobookPlaybackPolicy = Field(default_factory=AudiobookPlaybackPolicy)

    @model_validator(mode="after")
    def validate_selection(self) -> AudiobooksConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="audiobooks",
        )
        return self


class WeatherHistorySsh(ConfigurationModel):
    host: Annotated[str, Field(min_length=1, max_length=256)]
    user: Annotated[str, Field(min_length=1, max_length=128)]
    password_secret: SecretReference
    database_path: MachinePath
    timeout_seconds: PositiveSeconds = 8


class WeeWxWeatherProvider(ConfigurationModel):
    type: Literal["weewx"]
    current_url: CredentialFreeUrl
    history_url: CredentialFreeUrl | None = None
    history_ssh_fallback: WeatherHistorySsh | None = None
    timeout_seconds: PositiveSeconds = 8
    stale_after_seconds: PositiveSeconds = 900


class NwsWeatherProvider(ConfigurationModel):
    type: Literal["nws"]
    latitude: Annotated[float, Field(ge=-90, le=90)] | None = None
    longitude: Annotated[float, Field(ge=-180, le=180)] | None = None
    office: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    user_agent: Annotated[str, Field(min_length=1, max_length=256)]
    timeout_seconds: PositiveSeconds = 8

    @model_validator(mode="after")
    def paired_coordinates(self) -> NwsWeatherProvider:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("NWS latitude and longitude must be supplied together.")
        return self


WeatherProvider = Annotated[WeeWxWeatherProvider | NwsWeatherProvider, Field(discriminator="type")]


class WeatherCapability(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None


class WeatherConfiguration(ConfigurationModel):
    enabled: bool
    current: WeatherCapability = Field(default_factory=lambda: WeatherCapability(enabled=False))
    forecast: WeatherCapability = Field(default_factory=lambda: WeatherCapability(enabled=False))
    history: WeatherCapability = Field(default_factory=lambda: WeatherCapability(enabled=False))
    remote: WeatherCapability = Field(default_factory=lambda: WeatherCapability(enabled=False))
    providers: dict[CanonicalId, WeatherProvider] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capabilities(self) -> WeatherConfiguration:
        named_capabilities = {
            "current": self.current,
            "forecast": self.forecast,
            "history": self.history,
            "remote": self.remote,
        }
        capabilities = tuple(named_capabilities.values())
        if self.enabled and not any(capability.enabled for capability in capabilities):
            raise ValueError("Enabled weather requires at least one enabled capability.")
        if not self.enabled and any(capability.enabled for capability in capabilities):
            raise ValueError("Disabled weather cannot enable a capability.")
        for capability in capabilities:
            _require_selected_provider(
                enabled=capability.enabled,
                provider=capability.provider,
                providers=self.providers,
                label="weather capability",
            )
        for name, capability in named_capabilities.items():
            if not capability.enabled or capability.provider is None:
                continue
            provider = self.providers[capability.provider]
            expected_type = "weewx" if name in {"current", "history"} else "nws"
            if provider.type != expected_type:
                raise ValueError(f"Weather {name} requires provider type {expected_type!r}.")
            if name == "history" and provider.history_url is None and provider.history_ssh_fallback is None:
                raise ValueError("Weather history requires a static history URL or typed SSH fallback.")
        return self


class CalendarFeed(ConfigurationModel):
    id: CanonicalId
    kind: Literal["events", "holidays"]
    ics_url: CredentialFreeUrl | None = None
    ics_url_secret: SecretReference | None = None

    @model_validator(mode="after")
    def one_url_source(self) -> CalendarFeed:
        if (self.ics_url is None) == (self.ics_url_secret is None):
            raise ValueError("Calendar feed requires exactly one credential-free URL or whole-URL secret reference.")
        return self


class NextcloudCalendarProvider(ConfigurationModel):
    type: Literal["nextcloud"]
    feeds: list[CalendarFeed]
    timeout_seconds: PositiveSeconds = 8
    write_base_url: CredentialFreeUrl | None = None
    write_user: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    write_credential_secret: SecretReference | None = None
    write_calendar_uri: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @model_validator(mode="after")
    def validate_write_tuple(self) -> NextcloudCalendarProvider:
        fields = (self.write_base_url, self.write_user, self.write_credential_secret, self.write_calendar_uri)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError("Nextcloud write configuration must be complete or absent.")
        _reject_duplicates([feed.id for feed in self.feeds], label="Calendar feed IDs")
        return self


class CalendarPolicy(ConfigurationModel):
    read_enabled: bool = True
    write_enabled: bool = False
    confirmation_required: Literal[True] = True
    fresh_seconds: PositiveSeconds = 60
    stale_if_error_seconds: PositiveSeconds = 600


class CalendarConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, NextcloudCalendarProvider] = Field(default_factory=dict)
    policy: CalendarPolicy = Field(default_factory=CalendarPolicy)

    @model_validator(mode="after")
    def validate_selection(self) -> CalendarConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="calendar",
        )
        if self.enabled and not (self.policy.read_enabled or self.policy.write_enabled):
            raise ValueError("Enabled calendar requires read or write policy.")
        if self.enabled and self.policy.read_enabled:
            provider = self.providers[self.provider]  # type: ignore[index]
            if not provider.feeds:
                raise ValueError("Calendar read policy requires at least one feed.")
        if self.enabled and self.policy.write_enabled:
            provider = self.providers[self.provider]  # type: ignore[index]
            if provider.write_base_url is None:
                raise ValueError("Calendar write policy requires complete provider write configuration.")
        return self


class HomeAssistantProvider(ConfigurationModel):
    type: Literal["home_assistant"]
    base_url: CredentialFreeUrl
    credential_secret: SecretReference
    event_ingress_secret: SecretReference | None = None
    timeout_seconds: PositiveSeconds = 8
    snapshot_root: Annotated[str, Field(min_length=1, max_length=512)] | None = None

    @field_validator("snapshot_root")
    @classmethod
    def validate_snapshot_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("Home Assistant snapshot_root must be an absolute URL path without query or fragment.")
        path = PurePosixPath(value)
        if ".." in path.parts or str(path) != value.rstrip("/"):
            raise ValueError("Home Assistant snapshot_root must be a normalized absolute URL path.")
        return value.rstrip("/")


class HomeAssistantObjectMapping(ConfigurationModel):
    kind: Literal["room", "entity", "action", "camera", "mode"]
    oracle_id: CanonicalId
    entity_id: Annotated[str, Field(min_length=1, max_length=256)]
    allowed_operations: list[CanonicalId] = Field(default_factory=list)


class HomeAssistantEventMapping(ConfigurationModel):
    kind: Literal["event"]
    event_type: Literal["entry_state", "mode_state"]
    subject: CanonicalId
    entity_id: Annotated[str, Field(min_length=1, max_length=256)]
    active_state: Annotated[str, Field(min_length=1, max_length=128)]
    inactive_state: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def entry_state_pair(self) -> HomeAssistantEventMapping:
        if self.event_type == "entry_state" and self.inactive_state is None:
            raise ValueError("Entry-state mapping requires an inactive state.")
        return self


HomeAssistantMapping = Annotated[
    HomeAssistantObjectMapping | HomeAssistantEventMapping,
    Field(discriminator="kind"),
]


class HomeAssistantViewReference(ConfigurationModel):
    mapping_id: CanonicalId
    label: DisplayText | None = None


class HomeAssistantControlViewReference(HomeAssistantViewReference):
    status_mapping_id: CanonicalId | None = None
    action_ids: list[CanonicalId] = Field(default_factory=list)

    @field_validator("action_ids")
    @classmethod
    def unique_actions(cls, values: list[str]) -> list[str]:
        return _reject_duplicates(values, label="Home Assistant view action IDs")


class HomeAssistantEnvironmentViewReference(HomeAssistantViewReference):
    metric: Literal["temperature", "humidity", "climate"]


class HomeAssistantCameraViewReference(HomeAssistantViewReference):
    snapshot_ref: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @field_validator("snapshot_ref")
    @classmethod
    def validate_snapshot_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Home Assistant camera snapshot_ref must be a confined relative logical path.")
        if str(path) != value or "?" in value or "#" in value:
            raise ValueError("Home Assistant camera snapshot_ref must be a normalized relative logical path.")
        return value


def _unique_view_mappings(values: list[HomeAssistantViewReference], *, label: str) -> list[HomeAssistantViewReference]:
    _reject_duplicates([item.mapping_id for item in values], label=label)
    return values


class HomeAssistantHomeView(ConfigurationModel):
    controls: list[HomeAssistantControlViewReference] = Field(default_factory=list)
    actions: list[HomeAssistantViewReference] = Field(default_factory=list)

    @field_validator("controls", "actions")
    @classmethod
    def unique_controls(cls, values: list[HomeAssistantControlViewReference]) -> list[HomeAssistantControlViewReference]:
        return _unique_view_mappings(values, label="Home Assistant Home control mappings")


class HomeAssistantHouseView(ConfigurationModel):
    front_door: HomeAssistantControlViewReference | None = None
    temperatures: list[HomeAssistantViewReference] = Field(default_factory=list)
    climate: list[HomeAssistantControlViewReference] = Field(default_factory=list)
    lights: list[HomeAssistantControlViewReference] = Field(default_factory=list)
    cameras: list[HomeAssistantCameraViewReference] = Field(default_factory=list)
    actions: list[HomeAssistantViewReference] = Field(default_factory=list)

    @field_validator("temperatures", "climate", "lights", "cameras", "actions")
    @classmethod
    def unique_sections(cls, values: list[HomeAssistantViewReference]) -> list[HomeAssistantViewReference]:
        return _unique_view_mappings(values, label="Home Assistant House section mappings")


class HomeAssistantRoomView(ConfigurationModel):
    controls: list[HomeAssistantControlViewReference] = Field(default_factory=list)
    environment: list[HomeAssistantEnvironmentViewReference] = Field(default_factory=list)
    environment_title: DisplayText | None = None

    @field_validator("controls", "environment")
    @classmethod
    def unique_sections(cls, values: list[HomeAssistantViewReference]) -> list[HomeAssistantViewReference]:
        return _unique_view_mappings(values, label="Home Assistant Room section mappings")


class HomeAssistantViews(ConfigurationModel):
    home: HomeAssistantHomeView = Field(default_factory=HomeAssistantHomeView)
    house: HomeAssistantHouseView = Field(default_factory=HomeAssistantHouseView)
    rooms: dict[CanonicalId, HomeAssistantRoomView] = Field(default_factory=dict)


class HomeAssistantAutomation(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    migration_mode: Literal["direct_notification", "runbook"]
    event_mapping_id: CanonicalId
    notification_type: CanonicalId
    notification_delivery_enabled: bool
    delay_seconds: Annotated[int, Field(ge=0, le=86400)] = 0
    repeat_interval_seconds: PositiveSeconds | None = None
    max_notifications: Annotated[int, Field(ge=1, le=100)] = 1
    max_lateness_seconds: Annotated[int, Field(ge=0, le=86400)] = 120
    provider_retry_seconds: Annotated[int, Field(ge=1, le=3600)] = 30
    max_provider_failures: Annotated[int, Field(ge=0, le=20)] = 3

    @model_validator(mode="after")
    def validate_repetition(self) -> HomeAssistantAutomation:
        if self.max_notifications > 1 and self.repeat_interval_seconds is None:
            raise ValueError("Repeated Home Assistant automation requires repeat_interval_seconds.")
        return self


class HomeAssistantConfiguration(ConfigurationModel):
    enabled: bool
    provider: CanonicalId | None = None
    providers: dict[CanonicalId, HomeAssistantProvider] = Field(default_factory=dict)
    mappings: dict[CanonicalId, HomeAssistantMapping]
    views: HomeAssistantViews = Field(default_factory=HomeAssistantViews)
    automations: list[HomeAssistantAutomation]

    @model_validator(mode="after")
    def validate_selection_and_ids(self) -> HomeAssistantConfiguration:
        _require_selected_provider(
            enabled=self.enabled,
            provider=self.provider,
            providers=self.providers,
            label="Home Assistant",
        )
        _reject_duplicates([item.id for item in self.automations], label="Home Assistant automation IDs")
        if not self.enabled and any(item.enabled for item in self.automations):
            raise ValueError("Disabled Home Assistant cannot contain enabled automations.")
        if not self.enabled and self.views != HomeAssistantViews():
            raise ValueError("Disabled Home Assistant cannot publish read-model views.")
        if self.enabled and any(item.enabled for item in self.automations):
            provider = self.providers[self.provider]  # type: ignore[index]
            if provider.event_ingress_secret is None:
                raise ValueError("Enabled Home Assistant automations require an event-ingress secret reference.")
        return self


class AppriseNotificationProvider(ConfigurationModel):
    type: Literal["apprise"]
    base_url: CredentialFreeUrl | None = None
    base_url_secret: SecretReference | None = None
    timeout_seconds: PositiveSeconds = 8

    @model_validator(mode="after")
    def one_base_url(self) -> AppriseNotificationProvider:
        if (self.base_url is None) == (self.base_url_secret is None):
            raise ValueError("Apprise provider requires one URL or whole-URL secret reference.")
        return self


class NotificationRecipientGroup(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    provider: CanonicalId
    configuration_key: CanonicalId
    routing_tag: Annotated[str, Field(min_length=1, max_length=256)]


class ExternalNotificationPolicy(ConfigurationModel):
    enabled: bool
    recipient_groups: list[CanonicalId]
    delivery_ttl_seconds: Annotated[int, Field(ge=5, le=3600)] = 300
    max_attempts: Annotated[int, Field(ge=1, le=5)] = 3
    retry_seconds: Annotated[int, Field(ge=1, le=3600)] = 30
    quiet_hours_policy: Literal["respect", "bypass"] = "respect"
    repeat_policy: Literal["first_per_correlation", "every_occurrence"] = "first_per_correlation"
    failure_policy: Literal["best_effort", "required"] = "best_effort"

    @model_validator(mode="after")
    def validate_enabled_policy(self) -> ExternalNotificationPolicy:
        if self.enabled and not self.recipient_groups:
            raise ValueError("Enabled external delivery requires at least one recipient group.")
        if self.enabled and self.quiet_hours_policy != "bypass":
            raise ValueError("V2 external delivery currently requires quiet-hours bypass when enabled.")
        return self


class NotificationAudience(ConfigurationModel):
    type: Literal["source"]
    id: CanonicalId


class NotificationType(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    message: BoundedText
    audience: list[NotificationAudience]
    suppressed_by: list[CanonicalId] = Field(default_factory=list)
    delivery_ttl_seconds: Annotated[int, Field(ge=5, le=3600)] = 90
    audio_policy: Literal["pause_resume"] = "pause_resume"
    external_delivery: ExternalNotificationPolicy | None = None


class NotificationsConfiguration(ConfigurationModel):
    enabled: bool
    providers: dict[CanonicalId, AppriseNotificationProvider] = Field(default_factory=dict)
    types: list[NotificationType]
    recipient_groups: list[NotificationRecipientGroup]

    @model_validator(mode="after")
    def validate_references(self) -> NotificationsConfiguration:
        _reject_duplicates([item.id for item in self.types], label="Notification type IDs")
        _reject_duplicates([item.id for item in self.recipient_groups], label="Recipient group IDs")
        groups = {item.id: item for item in self.recipient_groups}
        for group in self.recipient_groups:
            if group.provider not in self.providers:
                raise ValueError(f"Recipient group {group.id!r} references an undefined provider.")
        for notification in self.types:
            audience_keys = [f"{item.type}:{item.id}" for item in notification.audience]
            _reject_duplicates(audience_keys, label=f"Notification {notification.id!r} audience")
            _reject_duplicates(notification.suppressed_by, label=f"Notification {notification.id!r} suppression modes")
            if notification.external_delivery is None:
                continue
            _reject_duplicates(
                notification.external_delivery.recipient_groups,
                label=f"Notification {notification.id!r} external recipient groups",
            )
            for group_id in notification.external_delivery.recipient_groups:
                if group_id not in groups:
                    raise ValueError(f"Notification {notification.id!r} references an undefined recipient group.")
                if notification.external_delivery.enabled and not groups[group_id].enabled:
                    raise ValueError(f"Notification {notification.id!r} external delivery references a disabled recipient group.")
        if self.enabled and not any(item.enabled for item in self.types):
            raise ValueError("Enabled notifications requires at least one enabled notification type.")
        if not self.enabled and any(item.enabled for item in self.types):
            raise ValueError("Disabled notifications cannot contain enabled notification types.")
        return self


class RoutineTriggers(ConfigurationModel):
    ui: bool
    voice: bool
    source_phrases: list[DisplayText] = Field(default_factory=list)
    global_phrases: list[DisplayText] = Field(default_factory=list)


class IntegerRoutineInput(ConfigurationModel):
    type: Literal["integer"]
    default: int
    minimum: int
    maximum: int
    prompt: DisplayText | None = Field(default=None, exclude_if=lambda value: value is None)
    spoken_duration: bool = Field(default=False, exclude_if=lambda value: value is False)
    no_timer_value: int | None = Field(default=None, exclude_if=lambda value: value is None)
    confirm_duration: bool = Field(default=False, exclude_if=lambda value: value is False)

    @model_validator(mode="after")
    def validate_bounds(self) -> IntegerRoutineInput:
        if self.minimum > self.default or self.default > self.maximum:
            raise ValueError("Routine input default must be inside its bounds.")
        if self.spoken_duration and self.prompt is None:
            raise ValueError("Spoken-duration routine input requires a prompt.")
        if self.no_timer_value is not None and not self.spoken_duration:
            raise ValueError("no_timer_value requires spoken_duration.")
        if self.confirm_duration and not self.spoken_duration:
            raise ValueError("confirm_duration requires spoken_duration.")
        if self.no_timer_value is not None and not (
            self.minimum <= self.no_timer_value <= self.maximum
        ):
            raise ValueError("no_timer_value must be inside the input bounds.")
        return self


class StringRoutineInput(ConfigurationModel):
    type: Literal["string"]
    default: Annotated[str, Field(max_length=256)] = ""
    allowed_values: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_default(self) -> StringRoutineInput:
        _reject_duplicates(self.allowed_values, label="Routine string input allowed values")
        if self.allowed_values and self.default not in self.allowed_values:
            raise ValueError("Routine string input default must be one of its allowed values.")
        return self


RoutineInput = Annotated[IntegerRoutineInput | StringRoutineInput, Field(discriminator="type")]


class RoutineStepCondition(ConfigurationModel):
    input_id: CanonicalId
    operator: Literal["equals", "not_equals", "greater_than"]
    value: int | str


class RoutineStepBase(ConfigurationModel):
    id: CanonicalId
    label: DisplayText
    required: bool
    on_failure: Literal["stop", "continue"]
    when: RoutineStepCondition | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def required_steps_stop(self) -> RoutineStepBase:
        if self.required and self.on_failure != "stop":
            raise ValueError("Required routine steps must stop on failure.")
        return self


class UiActionStep(RoutineStepBase):
    type: Literal["ui_action"]
    action_id: CanonicalId
    timeout_seconds: PositiveSeconds = 15


class AudiobookStartStep(RoutineStepBase):
    type: Literal["audiobook_start"]
    source_id: CanonicalId
    user_id: CanonicalId
    duration_seconds: PositiveSeconds | None = None
    duration_input: CanonicalId | None = None
    duration_unit: Literal["seconds", "minutes"] = "seconds"
    timeout_seconds: PositiveSeconds = 30

    @model_validator(mode="after")
    def one_duration(self) -> AudiobookStartStep:
        if self.duration_seconds is not None and self.duration_input is not None:
            raise ValueError("Audiobook step accepts at most one duration source.")
        return self


class SleepTimerStep(RoutineStepBase):
    type: Literal["sleep_timer"]
    source_id: CanonicalId
    duration_seconds: PositiveSeconds | None = None
    duration_input: CanonicalId | None = None
    duration_unit: Literal["seconds", "minutes"] = "seconds"
    timeout_seconds: PositiveSeconds = 15

    @model_validator(mode="after")
    def one_duration(self) -> SleepTimerStep:
        if (self.duration_seconds is None) == (self.duration_input is None):
            raise ValueError("Sleep-timer step requires exactly one duration source.")
        return self


class WaitStep(RoutineStepBase):
    type: Literal["wait"]
    duration_seconds: PositiveSeconds | None = None
    duration_input: CanonicalId | None = None
    duration_unit: Literal["seconds", "minutes"] = "seconds"
    max_lateness_seconds: Annotated[int, Field(ge=0, le=86400)]

    @model_validator(mode="after")
    def one_duration(self) -> WaitStep:
        if (self.duration_seconds is None) == (self.duration_input is None):
            raise ValueError("Wait step requires exactly one duration source.")
        return self


class PlaybackCheckStep(RoutineStepBase):
    type: Literal["playback_check"]
    source_id: CanonicalId
    check_id: Literal["routine_audiobook_stopped"]
    timeout_seconds: PositiveSeconds = 15
    remediation_action_id: CanonicalId | None = None


class StateCheckStep(RoutineStepBase):
    type: Literal["state_check"]
    check_id: CanonicalId
    expected_state: Annotated[str, Field(min_length=1, max_length=128)]
    timeout_seconds: PositiveSeconds = 15
    remediation_action_id: CanonicalId | None = None


class NotificationStep(RoutineStepBase):
    type: Literal["notification"]
    notification_id: CanonicalId
    timeout_seconds: PositiveSeconds = 15


class TimerSoundStep(RoutineStepBase):
    type: Literal["timer_sound"]
    source_id: CanonicalId
    timeout_seconds: PositiveSeconds = 15


RoutineStep = Annotated[
    UiActionStep | AudiobookStartStep | SleepTimerStep | WaitStep | PlaybackCheckStep | StateCheckStep | NotificationStep | TimerSoundStep,
    Field(discriminator="type"),
]


class RoutineDefinition(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    description: BoundedText
    enabled: bool
    user_id: CanonicalId | None = None
    source_ids: list[CanonicalId]
    triggers: RoutineTriggers
    inputs: dict[CanonicalId, RoutineInput]
    steps: list[RoutineStep]

    @model_validator(mode="after")
    def validate_definition(self) -> RoutineDefinition:
        _reject_duplicates([step.id for step in self.steps], label="Routine step IDs")
        for step in self.steps:
            if step.when is not None and step.when.input_id not in self.inputs:
                raise ValueError(f"Routine step {step.id!r} condition references an undefined input.")
            duration_input = getattr(step, "duration_input", None)
            if duration_input is not None and duration_input not in self.inputs:
                raise ValueError(f"Routine step {step.id!r} references an undefined input.")
            if duration_input is not None and self.inputs[duration_input].type != "integer":
                raise ValueError(f"Routine step {step.id!r} duration input must be an integer.")
            source_id = getattr(step, "source_id", None)
            if source_id is not None and source_id not in self.source_ids:
                raise ValueError(f"Routine step {step.id!r} source must belong to the routine.")
            user_id = getattr(step, "user_id", None)
            if user_id is not None and self.user_id is not None and user_id != self.user_id:
                raise ValueError(f"Routine step {step.id!r} user must match the routine owner.")
            remediation_action_id = getattr(step, "remediation_action_id", None)
            if remediation_action_id is not None and step.on_failure != "continue":
                raise ValueError(f"Routine step {step.id!r} remediation requires continue-on-failure policy.")
        if self.enabled and not self.steps:
            raise ValueError("Enabled routine requires at least one step.")
        if self.enabled and self.user_id is None:
            raise ValueError("Enabled routine requires an owning user.")
        if self.enabled and not self.source_ids:
            raise ValueError("Enabled routine requires at least one source.")
        if self.enabled and not (self.triggers.ui or self.triggers.voice):
            raise ValueError("Enabled routine requires at least one trigger surface.")
        phrases = self.triggers.source_phrases + self.triggers.global_phrases
        normalized_phrases = [" ".join(value.casefold().split()) for value in phrases]
        _reject_duplicates(normalized_phrases, label="Routine trigger phrases")
        if self.triggers.source_phrases and not self.source_ids:
            raise ValueError("Source-scoped trigger phrases require routine source IDs.")
        if self.triggers.voice and not phrases:
            raise ValueError("Voice-enabled routine requires at least one trigger phrase.")
        if not self.triggers.voice and phrases:
            raise ValueError("Voice-disabled routine cannot declare trigger phrases.")
        return self


class RoutinesConfiguration(ConfigurationModel):
    enabled: bool
    definitions: list[RoutineDefinition]

    @model_validator(mode="after")
    def validate_definitions(self) -> RoutinesConfiguration:
        _reject_duplicates([item.id for item in self.definitions], label="Routine definition IDs")
        if not self.enabled and any(item.enabled for item in self.definitions):
            raise ValueError("Disabled routines role cannot contain enabled definitions.")
        if self.enabled and not any(item.enabled for item in self.definitions):
            raise ValueError("Enabled routines requires at least one enabled definition.")
        global_phrases: dict[str, str] = {}
        source_phrases: dict[str, list[tuple[str, set[str]]]] = {}
        for definition in self.definitions:
            sources = set(definition.source_ids)
            for phrase in definition.triggers.global_phrases:
                normalized = " ".join(phrase.casefold().split())
                if normalized in global_phrases or normalized in source_phrases:
                    raise ValueError(f"Global routine phrase {phrase!r} has another owner.")
                global_phrases[normalized] = definition.id
            for phrase in definition.triggers.source_phrases:
                normalized = " ".join(phrase.casefold().split())
                if normalized in global_phrases or any(sources & owned for _, owned in source_phrases.get(normalized, [])):
                    raise ValueError(f"Source routine phrase {phrase!r} overlaps another owner.")
                source_phrases.setdefault(normalized, []).append((definition.id, sources))
        return self


class NetworkHost(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    kind: Literal["network_node", "server", "satellite", "appliance"]
    role: CanonicalId
    description: BoundedText | None = None


class NetworkDevice(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    kind: CanonicalId
    host_id: CanonicalId | None = None


class NetworkService(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    host_id: CanonicalId
    kind: CanonicalId
    description: BoundedText | None = None


class NetworkServiceGroup(ConfigurationModel):
    id: CanonicalId
    display_name: DisplayText
    host_id: CanonicalId
    service_ids: list[CanonicalId]
    collapsed: bool = False


class NetworkMonitor(ConfigurationModel):
    id: CanonicalId
    target_type: Literal["host", "device", "service"]
    target_id: CanonicalId
    adapter_id: CanonicalId


class NetworkDependency(ConfigurationModel):
    id: CanonicalId
    from_type: Literal["host", "device", "service"]
    from_id: CanonicalId
    to_type: Literal["host", "device", "service"]
    to_id: CanonicalId
    relationship: Literal["depends_on", "hosted_by", "connected_through"]


class NetworkPowerTarget(ConfigurationModel):
    id: CanonicalId
    host_id: CanonicalId
    enabled: bool
    adapter_id: CanonicalId
    capabilities: list[Literal["power_cycle"]]


class NetworkInventoryConfiguration(ConfigurationModel):
    enabled: bool
    internet_health_probe_adapter_id: CanonicalId | None = None
    hosts: list[NetworkHost]
    devices: list[NetworkDevice]
    services: list[NetworkService]
    service_groups: list[NetworkServiceGroup] = Field(default_factory=list)
    monitors: list[NetworkMonitor]
    dependencies: list[NetworkDependency] = Field(default_factory=list)
    power_targets: list[NetworkPowerTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_inventory(self) -> NetworkInventoryConfiguration:
        for label, items in (
            ("Network host IDs", self.hosts),
            ("Network device IDs", self.devices),
            ("Network service IDs", self.services),
            ("Network service-group IDs", self.service_groups),
            ("Network monitor IDs", self.monitors),
            ("Network dependency IDs", self.dependencies),
            ("Network power-target IDs", self.power_targets),
        ):
            _reject_duplicates([item.id for item in items], label=label)
        hosts = {item.id for item in self.hosts}
        for service in self.services:
            if service.host_id not in hosts:
                raise ValueError(f"Network service {service.id!r} references an undefined host.")
        services = {item.id: item for item in self.services}
        for group in self.service_groups:
            if group.host_id not in hosts:
                raise ValueError(f"Network service group {group.id!r} references an undefined host.")
            _reject_duplicates(group.service_ids, label=f"Network service group {group.id!r} service IDs")
            for service_id in group.service_ids:
                service = services.get(service_id)
                if service is None or service.host_id != group.host_id:
                    raise ValueError(f"Network service group {group.id!r} contains an unknown or cross-host service.")
        return self


class NetworkExecutionPolicy(ConfigurationModel):
    restart_timeout_seconds: PositiveSeconds | None = None
    shutdown_timeout_seconds: PositiveSeconds | None = None
    wait_seconds: Annotated[int, Field(ge=0, le=3600)] | None = None
    off_seconds: PositiveSeconds | None = None
    verification_timeout_seconds: PositiveSeconds | None = None
    recovery_timeout_seconds: PositiveSeconds | None = None
    recovery_poll_seconds: PositiveSeconds | None = None
    readiness_timeout_seconds: PositiveSeconds | None = None
    cooldown_seconds: Annotated[int, Field(ge=0, le=86400)] | None = None


class NetworkAction(ConfigurationModel):
    id: CanonicalId
    target_type: Literal["host", "device", "service", "power_target"]
    target_id: CanonicalId
    adapter_id: CanonicalId
    operation: Literal["restart_service", "restart_runtime", "restart_ui", "restart_host", "restart_router", "power_cycle"]
    enabled: bool
    requires_confirmation: Literal[True]
    requires_graceful_lifecycle: bool = False
    required_preconditions: list[
        Literal["plex_no_active_streams", "pihole_restart_continuity", "host_storage_safe_for_restart"]
    ] = Field(default_factory=list)
    execution: NetworkExecutionPolicy = Field(default_factory=NetworkExecutionPolicy)
    description: BoundedText

    @model_validator(mode="after")
    def validate_lifecycle_scope(self) -> NetworkAction:
        if self.requires_graceful_lifecycle and self.operation != "restart_host":
            raise ValueError("Graceful lifecycle is valid only for host restart operations.")
        target_operation = (self.target_type, self.operation)
        restart_targets = {("service", "restart_service"), ("host", "restart_host")}
        for precondition in self.required_preconditions:
            allowed = (
                {("host", "restart_host")}
                if precondition == "host_storage_safe_for_restart"
                else restart_targets
            )
            if target_operation not in allowed:
                raise ValueError(
                    f"Network precondition {precondition!r} is not valid for "
                    f"{self.target_type}:{self.operation}."
                )
        return self


class NetworkRecoveryTriggers(ConfigurationModel):
    ui: bool
    voice: bool
    global_phrases: list[DisplayText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_voice(self) -> NetworkRecoveryTriggers:
        normalized = [" ".join(value.casefold().split()) for value in self.global_phrases]
        _reject_duplicates(normalized, label="Network recovery trigger phrases")
        if self.voice and not self.global_phrases:
            raise ValueError("Voice-enabled network recovery requires a global phrase.")
        if not self.voice and self.global_phrases:
            raise ValueError("Voice-disabled network recovery cannot declare phrases.")
        return self


class NetworkRecovery(ConfigurationModel):
    id: CanonicalId
    enabled: bool
    display_name: DisplayText
    description: BoundedText
    approval_mode: Literal["plan"]
    diagnostic_profile: CanonicalId
    remediation_profile: CanonicalId
    triggers: NetworkRecoveryTriggers


class NetworkPolicyConfiguration(ConfigurationModel):
    actions: list[NetworkAction]
    recoveries: list[NetworkRecovery]

    @model_validator(mode="after")
    def validate_ids(self) -> NetworkPolicyConfiguration:
        _reject_duplicates([item.id for item in self.actions], label="Network action IDs")
        _reject_duplicates(
            [
                f"{item.target_type}:{item.target_id}:{item.operation}"
                for item in self.actions
            ],
            label="Network target operations",
        )
        _reject_duplicates([item.id for item in self.recoveries], label="Network recovery IDs")
        phrases: dict[str, str] = {}
        for recovery in self.recoveries:
            for phrase in recovery.triggers.global_phrases:
                normalized = " ".join(phrase.casefold().split())
                if normalized in phrases:
                    raise ValueError(f"Network recovery phrase {phrase!r} has another owner.")
                phrases[normalized] = recovery.id
        return self


class DirectProbeAdapter(ConfigurationModel):
    type: Literal["direct_probe"]
    dns_host: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    http_url: CredentialFreeUrl | None = None
    timeout_seconds: PositiveSeconds = 3

    @model_validator(mode="after")
    def require_probe(self) -> DirectProbeAdapter:
        if self.dns_host is None and self.http_url is None:
            raise ValueError("Direct probe adapter requires dns_host or http_url.")
        return self


class LibreNmsAdapter(ConfigurationModel):
    type: Literal["librenms"]
    base_url: CredentialFreeUrl
    credential_secret: SecretReference
    timeout_seconds: PositiveSeconds = 5
    device_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    hostname: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    service_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    service_name: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    interface_name: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def require_match(self) -> LibreNmsAdapter:
        if not any((self.device_id, self.hostname, self.service_id, self.service_name, self.interface_name)):
            raise ValueError("LibreNMS adapter requires at least one typed provider match field.")
        return self


class HomeAssistantPowerAdapter(ConfigurationModel):
    type: Literal["home_assistant_power"]
    power_target_id: CanonicalId
    entity_id: Annotated[str, Field(min_length=1, max_length=256)]


class ServiceControlClientRelease(ConfigurationModel):
    host_id: CanonicalId
    mount_path: MachinePath
    mount_service_target: Annotated[str, Field(min_length=1, max_length=256)]
    service_adapter_ids: list[CanonicalId]


class ServiceControlStorageClosure(ConfigurationModel):
    array_id: Annotated[str, Field(min_length=1, max_length=128)]
    mount_path: MachinePath
    sharing_service_adapter_id: CanonicalId


class ServiceControlLifecycle(ConfigurationModel):
    mode: Literal["graceful"]
    prepare_service_adapter_ids: list[CanonicalId] = Field(default_factory=list)
    client_release: ServiceControlClientRelease | None = None
    storage: ServiceControlStorageClosure | None = None


class ServiceControlAdapter(ConfigurationModel):
    type: Literal["service_control"]
    target_kind: Literal["host", "service"]
    host_id: CanonicalId
    transport: Literal["local", "ssh"]
    platform: Literal["linux", "windows"]
    address: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    user: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    password_secret: SecretReference | None = None
    service_target: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    lifecycle_service_targets: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(default_factory=list)
    service_adapter: Literal["systemd", "docker", "windows_scheduled_task"] | None = None
    restart_mode: Literal["immediate", "deferred_self_restart", "restart_edge_kiosk"] = "immediate"
    deferred_delay_seconds: Annotated[int, Field(ge=1, le=60)] | None = None
    verification_mode: Literal["running", "edge_running"] | None = None
    readiness_service_adapter_ids: list[CanonicalId] = Field(default_factory=list)
    readiness_read_write_paths: list[MachinePath] = Field(default_factory=list)
    readiness_http_urls: list[CredentialFreeUrl] = Field(default_factory=list)
    lifecycle: ServiceControlLifecycle | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> ServiceControlAdapter:
        if self.transport == "ssh" and (self.address is None or self.user is None or self.password_secret is None):
            raise ValueError("SSH service-control adapter requires address, user, and password secret.")
        if self.transport == "local" and any(value is not None for value in (self.address, self.user, self.password_secret)):
            raise ValueError("Local service-control adapter cannot configure SSH connection fields.")
        if self.target_kind == "service" and (self.service_target is None or self.service_adapter is None):
            raise ValueError("Service target adapter requires native service target and adapter type.")
        if self.target_kind == "host" and (self.service_target is not None or self.service_adapter is not None):
            raise ValueError("Host service-control adapter cannot contain a native service target.")
        if self.target_kind == "host" and self.lifecycle_service_targets:
            raise ValueError("Host service-control adapter cannot contain lifecycle service targets.")
        if self.lifecycle_service_targets and self.service_adapter != "docker":
            raise ValueError("Lifecycle service targets are supported only by the Docker adapter.")
        _reject_duplicates(self.lifecycle_service_targets, label="Lifecycle service targets")
        if self.service_target in self.lifecycle_service_targets:
            raise ValueError("Primary service target cannot also be a lifecycle service target.")
        if self.target_kind == "service" and any(
            (self.readiness_service_adapter_ids, self.readiness_read_write_paths, self.readiness_http_urls)
        ):
            raise ValueError("Service target adapter cannot own host readiness policy.")
        if self.platform == "windows" and self.service_adapter not in {None, "windows_scheduled_task"}:
            raise ValueError("Windows service control requires the scheduled-task adapter.")
        if self.platform == "linux" and self.service_adapter == "windows_scheduled_task":
            raise ValueError("Linux service control cannot use the Windows scheduled-task adapter.")
        service_targets = [
            target
            for target in (self.service_target, *self.lifecycle_service_targets)
            if target is not None
        ]
        pattern = {
            "systemd": _SYSTEMD_UNIT_PATTERN,
            "docker": _DOCKER_TARGET_PATTERN,
            "windows_scheduled_task": _WINDOWS_TASK_PATTERN,
        }.get(self.service_adapter)
        if pattern is not None and any(
            pattern.fullmatch(target) is None for target in service_targets
        ):
            raise ValueError("Service-control target is invalid for its native adapter.")
        if self.restart_mode == "deferred_self_restart" and self.deferred_delay_seconds is None:
            raise ValueError("Deferred self-restart requires a bounded delay.")
        return self


class RouterControlAdapter(ConfigurationModel):
    type: Literal["router_control"]
    host_id: CanonicalId
    address: Annotated[str, Field(min_length=1, max_length=256)]
    user: Annotated[str, Field(min_length=1, max_length=128)]
    password_secret: SecretReference
    mechanism: Literal["ssh_reboot"]


NetworkAdapter = Annotated[
    DirectProbeAdapter | LibreNmsAdapter | HomeAssistantPowerAdapter | ServiceControlAdapter | RouterControlAdapter,
    Field(discriminator="type"),
]


class NetworkAdaptersConfiguration(ConfigurationModel):
    providers: dict[CanonicalId, NetworkAdapter]
