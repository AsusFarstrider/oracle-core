from __future__ import annotations

from typing import Any

from oracle_app.configuration.music_runtime_settings import MusicRuntimeSettings
from oracle_app.music_runtime.control import (
    SatelliteControlTarget,
    execute_satellite_command,
    fetch_satellite_audiobook_session,
    fetch_satellite_music_session,
    fetch_satellite_playback_authority,
    fetch_satellite_reply_audio_session,
)
from oracle_app.music_runtime.parsing import MusicIntent
from oracle_app.provider_bridges.plex_music import (
    PlexMusicBridge,
    PlexMusicProviderConnection,
)


class CanonicalMusicExecution:
    """Music provider and control dependencies bound to one applied snapshot."""

    def __init__(
        self,
        settings: MusicRuntimeSettings,
        *,
        satellite_control_timeout_seconds: float,
    ) -> None:
        if not settings.enabled or settings.provider is None or settings.provider_credential is None:
            raise ValueError("Canonical music execution requires one enabled selected provider.")
        self.settings = settings
        self.satellite_control_timeout_seconds = satellite_control_timeout_seconds
        provider = settings.provider
        self.bridge = PlexMusicBridge(
            connection=PlexMusicProviderConnection(
                base_url=provider.base_url,
                credential=settings.provider_credential,
                timeout_seconds=provider.timeout_seconds,
                music_section_id=provider.music_section_id,
                machine_identifier=provider.machine_identifier,
            )
        )

    def search(self, intent: MusicIntent) -> list[dict[str, Any]]:
        return self.bridge.search(intent)

    def build_native_queue_manifest(self, selection: dict[str, Any]) -> dict[str, Any] | None:
        return self.bridge.build_native_queue_manifest(selection)

    def fetch_artwork(self, path: str):
        return self.bridge.fetch_artwork(path)

    def active_sessions_status(self) -> dict[str, Any]:
        return self.bridge.get_active_sessions_status()

    def execute_satellite_command(
        self,
        source: str | None,
        action: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return execute_satellite_command(
            source,
            action,
            args,
            control_target=self._control_target(source),
        )

    def fetch_satellite_music_session(self, source: str | None) -> dict[str, Any] | None:
        return fetch_satellite_music_session(source, control_target=self._control_target(source))

    def fetch_satellite_audiobook_session(self, source: str | None) -> dict[str, Any] | None:
        return fetch_satellite_audiobook_session(source, control_target=self._control_target(source))

    def fetch_satellite_reply_audio_session(self, source: str | None) -> dict[str, Any] | None:
        return fetch_satellite_reply_audio_session(source, control_target=self._control_target(source))

    def fetch_playback_authority(self, source: str | None) -> dict[str, Any]:
        return fetch_satellite_playback_authority(source, control_target=self._control_target(source))

    def backend_hint(self, source: str | None, *, media_type: str | None = None) -> str:
        del media_type
        self._control_target(source)
        return "oracle_native_music"

    def _control_target(self, source: str | None) -> SatelliteControlTarget:
        target = self.settings.playback_target(source)
        if (
            target is None
            or target.control_service_base_url is None
            or target.control_service_credential is None
        ):
            raise ValueError("Source is not an admitted canonical music playback target.")
        return SatelliteControlTarget(
            base_url=target.control_service_base_url,
            credential=target.control_service_credential,
            timeout_seconds=self.satellite_control_timeout_seconds,
        )
