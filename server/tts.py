from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from oracle_app.runtime_paths import RUNTIME_PATHS


class TtsError(RuntimeError):
    pass


PREGENERATED_DIR = RUNTIME_PATHS.tts_cache
PREGENERATED_PHRASES = {
    "done.": "done.wav",
    "ok.": "ok.wav",
    "canceled.": "canceled.wav",
    "confirmed.": "confirmed.wav",
    "my device cache has been refreshed.": "cache-refreshed.wav",
    "please confirm before i proceed.": "please-confirm.wav",
}


@dataclass
class TtsResult:
    audio_bytes: bytes
    media_type: str
    provider: str


@dataclass
class TtsStatus:
    provider: str
    configured: bool
    available: bool
    detail: str


class TtsProvider:
    def synthesize(self, text: str) -> TtsResult:
        raise NotImplementedError

    def status(self) -> TtsStatus:
        raise NotImplementedError


class PiperTtsProvider(TtsProvider):
    def __init__(self, binary: str, model: str | None) -> None:
        self.binary = binary
        self.model = model
        self.config = f"{model}.json" if model else None

    def synthesize(self, text: str) -> TtsResult:
        cached = self._load_cached_clip(text)
        if cached is not None:
            return TtsResult(
                audio_bytes=cached,
                media_type="audio/wav",
                provider="piper-cache",
            )

        resolved = shutil.which(self.binary)
        if not resolved:
            raise TtsError(f"Piper binary not found: {self.binary}")
        if not self.model:
            raise TtsError("Piper model is not configured")
        if not self.config or not Path(self.config).exists():
            raise TtsError(f"Piper config file not found: {self.config}")

        audio_bytes = self._synthesize_text(resolved, text)
        self._store_cached_clip(text, audio_bytes)

        return TtsResult(
            audio_bytes=audio_bytes,
            media_type="audio/wav",
            provider="piper",
        )

    def _normalize_text(self, text: str) -> str:
        return " ".join(text.strip().lower().split())

    def _cache_path_for_text(self, text: str) -> Path:
        normalized = self._normalize_text(text)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return PREGENERATED_DIR / f"phrase-{digest}.wav"

    def _load_cached_clip(self, text: str) -> bytes | None:
        normalized = " ".join(text.strip().lower().split())
        filename = PREGENERATED_PHRASES.get(normalized)
        if filename:
            path = PREGENERATED_DIR / filename
            if path.exists():
                return path.read_bytes()

        path = self._cache_path_for_text(text)
        if path.exists():
            return path.read_bytes()

        return None

    def _store_cached_clip(self, text: str, audio_bytes: bytes) -> None:
        path = self._cache_path_for_text(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio_bytes)

    def _synthesize_text(self, resolved_binary: str, text: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            output_path = Path(handle.name)

        try:
            cmd = [
                resolved_binary,
                "--model",
                self.model,
                "--config",
                self.config,
                "--output_file",
                str(output_path),
            ]
            completed = subprocess.run(
                cmd,
                input=f"{text.rstrip()}\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise TtsError(detail or "Piper synthesis failed")
            return output_path.read_bytes()
        finally:
            output_path.unlink(missing_ok=True)

    def status(self) -> TtsStatus:
        resolved = shutil.which(self.binary)
        configured = bool(self.model)
        if not resolved:
            return TtsStatus(
                provider="piper",
                configured=configured,
                available=False,
                detail=f"Piper binary not found: {self.binary}",
            )
        if not configured:
            return TtsStatus(
                provider="piper",
                configured=False,
                available=False,
                detail="Piper model is not configured",
            )
        if not self.config or not Path(self.config).exists():
            return TtsStatus(
                provider="piper",
                configured=True,
                available=False,
                detail=f"Piper config file not found: {self.config}",
            )
        return TtsStatus(
            provider="piper",
            configured=True,
            available=True,
            detail=f"Ready with model {self.model}",
        )


class DisabledTtsProvider(TtsProvider):
    def synthesize(self, text: str) -> TtsResult:
        raise TtsError("TTS is disabled")

    def status(self) -> TtsStatus:
        return TtsStatus(
            provider="disabled",
            configured=False,
            available=False,
            detail="TTS provider is disabled",
        )


def build_tts_provider(config: dict[str, object]) -> TtsProvider:
    provider_name = str(config.get("tts_provider") or "piper").strip()

    if provider_name == "disabled":
        return DisabledTtsProvider()

    if provider_name == "piper":
        binary = str(config.get("tts_piper_binary") or "piper").strip()
        model = config.get("tts_piper_model")
        model_text = str(model).strip() if model else None
        return PiperTtsProvider(binary=binary, model=model_text)

    raise TtsError(f"Unsupported TTS provider: {provider_name}")
