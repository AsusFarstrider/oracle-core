from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from .schemas import SttResponse


RouteHandler = Callable[..., Any]


def register_speech_routes(
    app: FastAPI,
    *,
    synthesize_speech: RouteHandler,
    transcribe_audio: RouteHandler,
) -> None:
    app.post("/api/speech/tts")(synthesize_speech)
    app.post("/api/speech/stt", response_model=SttResponse)(transcribe_audio)
