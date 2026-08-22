from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from oracle_satellite_runtime_config import ControlServiceEffectiveConfig


@dataclass(frozen=True)
class ControlServiceHostBootstrap:
    bind_host: str
    bind_port: int
    oracle_native_music_player_bin: str
    play_longform_audio_cmd: str
    pause_longform_audio_cmd: str
    resume_longform_audio_cmd: str
    stop_longform_audio_cmd: str
    seek_longform_audio_cmd: str
    longform_state_cmd: str
    reply_audio_state_path: str
    reply_audio_stop_path: str
    log_level: str = "INFO"


@dataclass(frozen=True)
class ControlServiceSettings:
    adapter: str
    bind_host: str
    bind_port: int
    api_key: str = field(repr=False)
    plexamp_url: str
    plex_server_url: str
    plex_token: str = field(repr=False)
    plex_machine_identifier: str
    disable_plexamp_external: bool
    http_timeout_seconds: float
    supports_oracle_native_music: bool
    oracle_native_music_player_bin: str
    output_volume_backend: str
    output_volume_card: str
    output_volume_control: str
    pause_cmd: str
    resume_cmd: str
    stop_cmd: str
    next_cmd: str
    previous_cmd: str
    restart_cmd: str
    set_volume_cmd: str
    play_media_cmd: str
    now_playing_cmd: str
    play_longform_audio_cmd: str
    pause_longform_audio_cmd: str
    resume_longform_audio_cmd: str
    stop_longform_audio_cmd: str
    seek_longform_audio_cmd: str
    longform_state_cmd: str
    reply_audio_state_path: str
    reply_audio_stop_path: str
    log_level: str

    @classmethod
    def from_canonical(
        cls,
        effective: ControlServiceEffectiveConfig,
        bootstrap: ControlServiceHostBootstrap,
    ) -> ControlServiceSettings:
        config = effective.configuration
        if config.get("adapter") != "oracle_native":
            raise ValueError("Canonical control-service adapter is unsupported.")
        volume_backend = ""
        volume_card = ""
        volume_control_name = ""
        volume = config.get("volume_control")
        if volume is not None:
            volume = _mapping(volume, "volume control")
            volume_backend = _text(volume.get("type"), "volume-control type")
            if volume_backend == "alsa":
                volume_card = _text(volume.get("card"), "ALSA card")
                volume_control_name = _text(volume.get("control"), "ALSA control")
            elif volume_backend != "windows_default_endpoint":
                raise ValueError("Canonical volume-control adapter is unsupported.")

        plex_server_url = ""
        plex_token = ""
        plex_machine_identifier = ""
        http_timeout_seconds = 5.0
        music = config.get("music")
        if music is not None:
            provider = _mapping(_mapping(music, "music configuration").get("provider"), "music provider")
            if provider.get("type") != "plex":
                raise ValueError("Canonical control-service music provider is unsupported.")
            plex_server_url = _text(provider.get("base_url"), "Plex endpoint")
            plex_token = effective.music_provider_credential or ""
            if not plex_token:
                raise ValueError("Canonical control-service music credential is unavailable.")
            machine_identifier = provider.get("machine_identifier")
            if machine_identifier is not None:
                plex_machine_identifier = _text(machine_identifier, "Plex machine identifier")
            http_timeout_seconds = _positive_number(provider.get("timeout_seconds"), "Plex timeout")

        return cls(
            adapter="local_playback",
            bind_host=_text(bootstrap.bind_host, "control-service bind host"),
            bind_port=_port(bootstrap.bind_port),
            api_key=effective.api_credential,
            # The current adapter still carries this retired endpoint internally,
            # but external Plexamp control is unconditionally disabled in canonical mode.
            plexamp_url="http://127.0.0.1:32500",
            plex_server_url=plex_server_url,
            plex_token=plex_token,
            plex_machine_identifier=plex_machine_identifier,
            disable_plexamp_external=True,
            http_timeout_seconds=http_timeout_seconds,
            supports_oracle_native_music=music is not None,
            oracle_native_music_player_bin=_text(
                bootstrap.oracle_native_music_player_bin,
                "native music player executable",
            ),
            output_volume_backend=volume_backend,
            output_volume_card=volume_card,
            output_volume_control=volume_control_name,
            pause_cmd="",
            resume_cmd="",
            stop_cmd="",
            next_cmd="",
            previous_cmd="",
            restart_cmd="",
            set_volume_cmd="",
            play_media_cmd="",
            now_playing_cmd="",
            play_longform_audio_cmd=bootstrap.play_longform_audio_cmd,
            pause_longform_audio_cmd=bootstrap.pause_longform_audio_cmd,
            resume_longform_audio_cmd=bootstrap.resume_longform_audio_cmd,
            stop_longform_audio_cmd=bootstrap.stop_longform_audio_cmd,
            seek_longform_audio_cmd=bootstrap.seek_longform_audio_cmd,
            longform_state_cmd=bootstrap.longform_state_cmd,
            reply_audio_state_path=_text(bootstrap.reply_audio_state_path, "reply-audio state path"),
            reply_audio_stop_path=_text(bootstrap.reply_audio_stop_path, "reply-audio stop path"),
            log_level=_text(bootstrap.log_level, "control-service log level"),
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Canonical {label} is invalid.")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Canonical {label} is invalid.")
    return value.strip()


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"Canonical {label} is invalid.")
    return float(value)


def _port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 65535:
        raise ValueError("Control-service bind port is invalid.")
    return value
