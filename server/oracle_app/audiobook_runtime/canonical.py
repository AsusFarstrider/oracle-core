from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from oracle_app.audiobook import LONGFORM_SUPPORTED_MIME_TYPES, build_longform_payload
from oracle_app.audiobook_runtime.matching import find_audiobook_series_entry
from oracle_app.configuration.audiobook_runtime_settings import AudiobookRuntimeSettings
from oracle_app.music_runtime.control import (
    SatelliteControlTarget,
    execute_satellite_command,
    fetch_satellite_audiobook_session,
    fetch_satellite_music_session,
    fetch_satellite_playback_authority,
)
from oracle_app.provider_bridges.audiobookshelf_audiobook import (
    AudiobookProviderConnection,
    AudiobookshelfAudiobookBridge,
)


class CanonicalAudiobookExecution:
    """Typed audiobook provider and satellite-control execution snapshot."""

    def __init__(
        self,
        settings: AudiobookRuntimeSettings,
        *,
        satellite_control_timeout_seconds: float,
    ) -> None:
        if not settings.enabled or settings.provider is None:
            raise ValueError("Canonical audiobook execution requires an enabled provider.")
        self.settings = settings
        self.satellite_control_timeout_seconds = satellite_control_timeout_seconds
        self.bridge = AudiobookshelfAudiobookBridge(self._provider_connection)

    def search_audiobooks(
        self,
        query: str,
        narrator_preference: str | None = None,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.bridge.search_titles(query, narrator_preference, user_id=user_id)

    def find_series_entry(
        self,
        series: str,
        ordinal: int,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        return find_audiobook_series_entry(
            series,
            ordinal,
            candidates=self.search_audiobooks(series, user_id=user_id),
        )

    def fetch_item(self, library_item_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        return self.bridge.fetch_item(library_item_id, user_id=user_id)

    def fetch_current_progress(self, *, user_id: str | None = None) -> dict[str, Any] | None:
        return self.bridge.fetch_current_progress(user_id=user_id)

    def open_playback_session(
        self,
        library_item_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return self.bridge.open_playback_session(
            library_item_id,
            supported_mime_types=list(LONGFORM_SUPPORTED_MIME_TYPES),
            user_id=user_id,
        )

    def sync_session(self, session_id: str, **kwargs: Any) -> None:
        self.bridge.sync_session(session_id, **kwargs)

    def close_session(self, session_id: str, **kwargs: Any) -> None:
        self.bridge.close_session(session_id, **kwargs)

    def fetch_stream(
        self,
        playback: dict[str, Any],
        track_index: int,
        *,
        range_header: str | None = None,
    ):
        return self.bridge.fetch_stream(playback, track_index, range_header=range_header)

    def request_raw(
        self,
        path: str,
        *,
        method: str,
        user_id: str | None = None,
    ):
        return self.bridge.request_raw(path, method=method, user_id=user_id)

    def request_json(
        self,
        path: str,
        *,
        method: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        return self.bridge.request_json(path, method=method, user_id=user_id)

    def build_longform_payload(
        self,
        session: dict[str, Any],
        *,
        source: str,
        user_id: str | None = None,
        start_paused: bool = False,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        stream_base_url = self.settings.stream_base_url(source)
        if stream_base_url is None:
            raise RuntimeError("Canonical audiobook target has no Brain client base URL.")
        return build_longform_payload(
            session,
            source=source,
            user_id=user_id,
            start_paused=start_paused,
            oracle_base_url=stream_base_url,
        )

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

    def fetch_playback_authority(self, source: str | None) -> dict[str, Any]:
        return fetch_satellite_playback_authority(source, control_target=self._control_target(source))

    def _provider_connection(self, user_id: str | None) -> AudiobookProviderConnection:
        normalized_user_id = str(user_id or "").strip()
        account = self.settings.user_account(normalized_user_id)
        provider = self.settings.provider
        if account is None or provider is None:
            return AudiobookProviderConnection(
                base_url="",
                library_id="",
                api_key="",
                timeout_seconds=10,
                configured=False,
                user_id=normalized_user_id or None,
                user_enabled=bool(normalized_user_id),
            )
        return AudiobookProviderConnection(
            base_url=provider.base_url,
            library_id=provider.library_id,
            api_key=account.credential,
            timeout_seconds=provider.timeout_seconds,
            user_id=account.user_id,
        )

    def _playback_target(self, source: str | None):
        target = self.settings.playback_target(source)
        if target is None:
            raise HTTPException(status_code=400, detail="Source is not an admitted canonical audiobook target")
        return target

    def _control_target(self, source: str | None) -> SatelliteControlTarget:
        target = self._playback_target(source)
        if target.control_service_base_url is None or target.control_service_credential is None:
            raise RuntimeError("Canonical audiobook target has no control-service edge.")
        return SatelliteControlTarget(
            base_url=target.control_service_base_url,
            credential=target.control_service_credential,
            timeout_seconds=self.satellite_control_timeout_seconds,
        )
