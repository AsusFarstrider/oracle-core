from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


NOW_PLAYING_PATTERNS = (
    "what's playing",
    "what is playing",
    "what song is playing",
    "what track is playing",
    "what song is this",
    "what album is this from",
    "what is this song",
)

PLAY_PREFIXES = (
    "play me ",
    "play ",
    "play some ",
    "start ",
    "queue up ",
    "cue up ",
    "can you play ",
    "could you play ",
    "would you play ",
    "throw on some ",
    "throw on ",
    "put on some ",
    "put on ",
    "lets hear ",
    "let's hear ",
    "i want to hear ",
    "i wanna hear ",
    "i want to listen to ",
    "listen to ",
)


@dataclass(frozen=True)
class MusicIntent:
    intent: str
    media_type: str | None
    title: str | None
    artist: str | None
    album: str | None
    playlist: str | None
    genre: str | None
    qualifiers: list[str]
    mode: str
    original_text: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "media_type": self.media_type,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "playlist": self.playlist,
            "genre": self.genre,
            "qualifiers": list(self.qualifiers),
            "mode": self.mode,
            "original_text": self.original_text,
        }


def is_music_request(text: str) -> bool:
    return parse_music_intent(text) is not None


def parse_music_intent(text: str) -> MusicIntent | None:
    normalized = " ".join(str(text).strip().lower().split())
    if not normalized:
        return None

    album_lookup = _parse_album_lookup_request(normalized)
    if album_lookup is not None:
        return album_lookup

    if normalized in {"pause", "pause music", "pause the music"}:
        return _intent("pause", original_text=normalized)
    if normalized in {"resume", "resume music", "resume the music", "resume playback", "continue", "continue music"}:
        return _intent("resume", original_text=normalized)
    if normalized in {"stop", "stop music", "stop the music", "stop playback"}:
        return _intent("stop", original_text=normalized)
    if normalized in {"next", "next song", "skip", "skip song", "skip this"}:
        return _intent("next", original_text=normalized)
    if normalized in {"previous", "previous song", "go back", "back"}:
        return _intent("previous", original_text=normalized)
    if normalized in {"restart", "restart song", "restart track", "restart this"}:
        return _intent("restart", original_text=normalized)

    if normalized in {
        "volume up",
        "volume_up",
        "turn it up",
        "turn the music up",
        "turn the volume up",
        "turn up the volume",
        "turn up volume",
        "increase the volume",
        "make the volume louder",
        "make oracle louder",
        "turn oracle volume up",
        "turn up oracle volume",
    }:
        return _intent("volume_up", original_text=normalized)
    if normalized in {
        "volume down",
        "volume_down",
        "turn it down",
        "turn the music down",
        "turn the volume down",
        "turn down the volume",
        "turn down volume",
        "decrease the volume",
        "lower the volume",
        "make the volume quieter",
        "make oracle quieter",
        "turn oracle volume down",
        "turn down oracle volume",
    }:
        return _intent("volume_down", original_text=normalized)

    match = re.match(r"^(?:set (?:the )?volume to )(\d{1,3})$", normalized)
    if match:
        return _intent(
            "set_volume",
            original_text=normalized,
            qualifiers=[match.group(1)],
        )

    if any(pattern in normalized for pattern in NOW_PLAYING_PATTERNS):
        return _intent("what_is_playing", original_text=normalized)

    play_match = _parse_play_request(normalized)
    if play_match is not None:
        return play_match

    if normalized.startswith("music "):
        return _intent("what_is_playing", original_text=normalized)

    return None


def _parse_album_lookup_request(normalized: str) -> MusicIntent | None:
    patterns = (
        r"^what album is (?P<title>.+?) on$",
        r"^what album is (?P<title>.+?) from$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        title = match.group("title").strip(" ,")
        if not title:
            continue
        return _intent(
            "lookup_album",
            media_type="track",
            title=title,
            original_text=normalized,
        )
    return None


def _parse_play_request(normalized: str) -> MusicIntent | None:
    for prefix in PLAY_PREFIXES:
        if normalized.startswith(prefix):
            remainder = _clean_play_remainder(normalized[len(prefix) :].strip())
            break
    else:
        return None

    if not remainder:
        return None

    if remainder in {"a song by", "song by"}:
        return None
    if remainder.startswith("a song by "):
        return _intent("play", media_type="artist", artist=remainder[10:].strip(), original_text=normalized)

    album_suffix_by_artist_match = re.match(r"^(?:the )?(?P<album>.+?) album by (?P<artist>.+)$", remainder)
    if album_suffix_by_artist_match:
        album = album_suffix_by_artist_match.group("album").strip(" ,")
        artist = album_suffix_by_artist_match.group("artist").strip(" ,")
        if album and artist:
            return _intent(
                "play",
                media_type="album",
                album=album,
                artist=artist,
                original_text=normalized,
            )

    album_by_artist_match = re.match(r"^(?:the )?album (?P<album>.+?) by (?P<artist>.+)$", remainder)
    if album_by_artist_match:
        album = album_by_artist_match.group("album").strip(" ,")
        artist = album_by_artist_match.group("artist").strip(" ,")
        if album and artist:
            return _intent(
                "play",
                media_type="album",
                album=album,
                artist=artist,
                original_text=normalized,
            )

    title_song_by_artist_match = re.match(r"^(?:the )?(?P<title>.+?) song by (?P<artist>.+)$", remainder)
    if title_song_by_artist_match:
        title = title_song_by_artist_match.group("title").strip(" ,")
        artist = title_song_by_artist_match.group("artist").strip(" ,")
        if title and artist:
            return _intent(
                "play",
                media_type="track",
                title=title,
                artist=artist,
                original_text=normalized,
            )

    artist_song_match = re.match(r"^(?:the )?(?:new )?(?P<artist>.+?) song (?P<title>.+)$", remainder)
    if artist_song_match:
        artist = artist_song_match.group("artist").strip(" ,")
        title = artist_song_match.group("title").strip(" ,")
        if artist and title:
            return _intent(
                "play",
                media_type="track",
                title=title,
                artist=artist,
                original_text=normalized,
            )

    artist_version_match = re.match(r"^(?P<artist>.+?)'?s version of (?P<title>.+)$", remainder)
    if artist_version_match:
        artist = artist_version_match.group("artist").strip(" ,")
        title = artist_version_match.group("title").strip(" ,")
        if artist in {"taylor", "taylor swift"}:
            artist = "taylor swift"
        if artist and title:
            return _intent(
                "play",
                media_type="track",
                title=title,
                artist=artist,
                original_text=normalized,
            )

    if remainder.startswith("something by "):
        return _intent("play", media_type="artist", artist=remainder[13:].strip(), original_text=normalized)
    if remainder.startswith("songs by "):
        return _intent("play", media_type="artist", artist=remainder[9:].strip(), original_text=normalized)
    if remainder.startswith("music by "):
        return _intent("play", media_type="artist", artist=remainder[9:].strip(), original_text=normalized)
    if remainder.startswith("the soundtrack to "):
        return _intent("play", media_type="album", album=remainder[17:].strip(), original_text=normalized)
    if remainder.startswith("soundtrack to "):
        return _intent("play", media_type="album", album=remainder[14:].strip(), original_text=normalized)
    if remainder.startswith("the soundtrack from "):
        return _intent("play", media_type="album", album=remainder[19:].strip(), original_text=normalized)
    if remainder.startswith("soundtrack from "):
        return _intent("play", media_type="album", album=remainder[16:].strip(), original_text=normalized)
    if remainder.startswith("the music from "):
        return _intent("play", media_type="album", album=remainder[15:].strip(), original_text=normalized)
    if remainder.startswith("music from "):
        return _intent("play", media_type="album", album=remainder[11:].strip(), original_text=normalized)
    if remainder.startswith("songs from "):
        return _intent("play", media_type="album", album=remainder[11:].strip(), original_text=normalized)

    if remainder.startswith("the album "):
        return _intent("play", media_type="album", album=remainder[10:].strip(), original_text=normalized)
    if remainder.startswith("album "):
        return _intent("play", media_type="album", album=remainder[6:].strip(), original_text=normalized)
    album_suffix_match = re.match(r"^(?:the )?(?P<album>.+?) album$", remainder)
    if album_suffix_match:
        album = album_suffix_match.group("album").strip(" ,")
        if album:
            return _intent("play", media_type="album", album=album, original_text=normalized)
    artist_album_suffix_match = re.match(r"^(?:the )?(?P<artist>.+?) (?P<album>.+?) album$", remainder)
    if artist_album_suffix_match:
        artist = artist_album_suffix_match.group("artist").strip(" ,")
        album = artist_album_suffix_match.group("album").strip(" ,")
        if artist and album and len(artist.split()) <= 3:
            return _intent(
                "play",
                media_type="album",
                album=album,
                artist=artist,
                original_text=normalized,
            )
    artist_album_match = re.match(r"^(?:the )?(?P<artist>.+?) album (?P<album>.+)$", remainder)
    if artist_album_match:
        artist = artist_album_match.group("artist").strip(" ,")
        album = artist_album_match.group("album").strip(" ,")
        if artist and album:
            return _intent(
                "play",
                media_type="album",
                album=album,
                artist=artist,
                original_text=normalized,
            )
    if remainder.startswith("my ") and remainder.endswith(" playlist"):
        playlist = remainder[3:-9].strip()
        return _intent("play", media_type="playlist", playlist=playlist, original_text=normalized)
    if remainder.endswith(" playlist"):
        playlist = remainder[:-9].strip()
        return _intent("play", media_type="playlist", playlist=playlist, original_text=normalized)

    from_album_match = re.match(r"^(?P<title>.+?) from (?P<album>.+)$", remainder)
    if from_album_match:
        title = from_album_match.group("title").strip()
        album = from_album_match.group("album").strip()
        if album.startswith("the soundtrack "):
            album = album[14:].strip()
        if album.startswith("soundtrack "):
            album = album[11:].strip()
        return _intent(
            "play",
            media_type="track",
            title=title,
            album=album,
            original_text=normalized,
        )

    off_album_match = re.match(r"^(?P<title>.+?) off (?P<album>.+)$", remainder)
    if off_album_match:
        title = off_album_match.group("title").strip()
        album = off_album_match.group("album").strip()
        return _intent(
            "play",
            media_type="track",
            title=title,
            album=album,
            original_text=normalized,
        )

    by_match = re.match(r"^(?P<title>.+?) by (?P<artist>.+)$", remainder)
    if by_match:
        media_type = "track"
        qualifiers: list[str] = []
        title = by_match.group("title").strip()
        artist = by_match.group("artist").strip()
        if title.startswith("the album "):
            media_type = "album"
            title = title[10:].strip()
            return _intent(
                "play",
                media_type=media_type,
                album=title,
                artist=artist,
                qualifiers=qualifiers,
                original_text=normalized,
            )
        return _intent(
            "play",
            media_type=media_type,
            title=title,
            artist=artist,
            qualifiers=qualifiers,
            original_text=normalized,
        )

    if remainder.startswith("artist "):
        return _intent("play", media_type="artist", artist=remainder[7:].strip(), original_text=normalized)

    if remainder.startswith("the artist "):
        return _intent("play", media_type="artist", artist=remainder[11:].strip(), original_text=normalized)

    return _intent("play", title=remainder, original_text=normalized)


def _clean_play_remainder(remainder: str) -> str:
    cleaned = remainder.strip()
    for prefix in ("some ", "the song ", "song ", "the track ", "track "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    if cleaned.startswith("the album "):
        return cleaned
    return cleaned


def _intent(
    intent: str,
    *,
    media_type: str | None = None,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    playlist: str | None = None,
    genre: str | None = None,
    qualifiers: list[str] | None = None,
    mode: str = "replace",
    original_text: str,
) -> MusicIntent:
    return MusicIntent(
        intent=intent,
        media_type=media_type,
        title=title,
        artist=artist,
        album=album,
        playlist=playlist,
        genre=genre,
        qualifiers=qualifiers or [],
        mode=mode,
        original_text=original_text,
    )


def optional_str(value: Any) -> str | None:
    cleaned = str(value).strip()
    if cleaned.lower() in {"", "none", "null"}:
        return None
    return cleaned


def optional_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        cleaned = str(item).strip()
        if cleaned:
            result.append(cleaned)
    return result
