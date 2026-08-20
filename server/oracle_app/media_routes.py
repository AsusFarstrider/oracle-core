from __future__ import annotations

from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .audiobook import fetch_audiobook_cover, fetch_audiobook_stream
from .audiobook_runtime.canonical import CanonicalAudiobookExecution
from .music_runtime.canonical import CanonicalMusicExecution
from .brain_application_composition import (
    BRAIN_APPLICATION_COMPOSITION_STATE_KEY,
    CanonicalBrainApplicationComposition,
)
from .config import get_music_settings
from .audiobook_state import get_active_audiobook_playback
from .provider_bridges.audiobookshelf_audiobook import AudiobookBridgeError


def ui_audio_audiobook_art(
    library_item_id: str,
    user_id: str | None = None,
    *,
    audiobook_execution: CanonicalAudiobookExecution | None = None,
    canonical_authority: bool = False,
) -> Response:
    try:
        if canonical_authority:
            if audiobook_execution is None:
                raise RuntimeError("Audiobooks are disabled in canonical configuration.")
            upstream = audiobook_execution.request_raw(
                f"/api/items/{urlparse.quote(library_item_id)}/cover",
                method="GET",
                user_id=user_id,
            )
        else:
            upstream = fetch_audiobook_cover(library_item_id, user_id=user_id)
        content = upstream.read()
        media_type = str(upstream.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Audiobook artwork unavailable: {exc}") from exc
    return Response(content=content, media_type=media_type)


def ui_audio_music_art(
    path: str,
    *,
    music_execution: CanonicalMusicExecution | None = None,
    canonical_authority: bool = False,
) -> Response:
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/") or "://" in normalized_path:
        raise HTTPException(status_code=400, detail="Invalid music artwork path")
    try:
        if canonical_authority:
            if music_execution is None:
                raise RuntimeError("Music is disabled in canonical configuration.")
            upstream_context = music_execution.fetch_artwork(normalized_path)
        else:
            settings = get_music_settings()
            if not settings.get("plex_configured"):
                raise RuntimeError("Music artwork unavailable")
            endpoint = f"{settings['plex_base_url']}{normalized_path}"
            separator = "&" if "?" in endpoint else "?"
            endpoint = f"{endpoint}{separator}X-Plex-Token={urlparse.quote(str(settings['plex_token']))}"
            upstream_context = urlrequest.urlopen(
                urlrequest.Request(endpoint, method="GET"),
                timeout=int(settings.get("plex_timeout_seconds") or 10),
            )
        with upstream_context as upstream:
            content = upstream.read()
            media_type = str(upstream.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
    except (RuntimeError, urlerror.HTTPError, urlerror.URLError) as exc:
        raise HTTPException(status_code=404, detail=f"Music artwork unavailable: {exc}") from exc
    return Response(content=content, media_type=media_type)


def stream_audiobook_track(playback_id: str, track_index: int, request: Request):
    playback = get_active_audiobook_playback(playback_id)
    if playback is None:
        raise HTTPException(status_code=404, detail="Audiobook playback not found")
    canonical, audiobook_execution = _canonical_audiobook_execution(request)
    if canonical:
        if audiobook_execution is None:
            raise HTTPException(status_code=404, detail="Audiobook playback is not configured")
        try:
            upstream = audiobook_execution.fetch_stream(
                playback,
                track_index,
                range_header=request.headers.get("range"),
            )
        except AudiobookBridgeError as exc:
            raise HTTPException(status_code=exc.http_status or 502, detail=exc.detail) from exc
    else:
        try:
            upstream = fetch_audiobook_stream(
                playback,
                track_index,
                range_header=request.headers.get("range"),
            )
        except AudiobookBridgeError as exc:
            raise HTTPException(status_code=exc.http_status or 502, detail=exc.detail) from exc
    content_type = str(upstream.headers.get("Content-Type") or "application/octet-stream")
    response_headers: dict[str, str] = {}
    for header_name in ("Accept-Ranges", "Content-Length", "Content-Range"):
        value = upstream.headers.get(header_name)
        if value:
            response_headers[header_name] = str(value)

    def iter_chunks():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        iter_chunks(),
        media_type=content_type,
        status_code=getattr(upstream, "status", 200),
        headers=response_headers,
    )


def ui_audio_audiobook_art_http(
    library_item_id: str,
    request: Request,
    user_id: str | None = None,
) -> Response:
    canonical, execution = _canonical_audiobook_execution(request)
    return ui_audio_audiobook_art(
        library_item_id,
        user_id,
        audiobook_execution=execution,
        canonical_authority=canonical,
    )


def ui_audio_music_art_http(path: str, request: Request) -> Response:
    composition = _canonical_composition(request)
    return ui_audio_music_art(
        path,
        music_execution=None if composition is None else composition.music_execution,
        canonical_authority=composition is not None,
    )


def _canonical_audiobook_execution(
    request: Request,
) -> tuple[bool, CanonicalAudiobookExecution | None]:
    composition = _canonical_composition(request)
    if composition is None:
        return False, None
    return True, composition.audiobook_execution


def _canonical_composition(request: Request) -> CanonicalBrainApplicationComposition | None:
    request_app = request.scope.get("app")
    composition = getattr(getattr(request_app, "state", None), BRAIN_APPLICATION_COMPOSITION_STATE_KEY, None)
    return composition if isinstance(composition, CanonicalBrainApplicationComposition) else None


def register_media_routes(app: FastAPI) -> None:
    app.get("/api/ui/audio/art/audiobook/{library_item_id}")(ui_audio_audiobook_art_http)
    app.get("/api/ui/audio/art/music")(ui_audio_music_art_http)
    app.get("/api/satellite/media/audiobooks/{playback_id}/tracks/{track_index}")(
        stream_audiobook_track
    )
