from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oracle_app.cache_lifecycle import CacheDiagnostics, CacheMaintenanceResult
from oracle_app.runtime_paths import RUNTIME_PATHS


class TtsError(RuntimeError):
    pass


PREGENERATED_DIR = RUNTIME_PATHS.tts_cache
TTS_CACHE_VERSION = 2
TTS_CACHE_MAX_BYTES = 256 * 1024 * 1024
TTS_CACHE_MAX_CLIPS = 4096
TTS_CACHE_MAX_IDLE_SECONDS = 90 * 24 * 60 * 60
_TTS_CACHE_LOCK = threading.RLock()


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
    def begin_warmup(self) -> None:
        """Explicit startup seam; providers without resident models need no work."""

    def synthesize(self, text: str) -> TtsResult:
        raise NotImplementedError

    def status(self) -> TtsStatus:
        raise NotImplementedError


class CachedTtsProvider(TtsProvider):
    cache_provider: str
    model: str | None

    def _configuration_identity(self) -> dict[str, object]:
        raise NotImplementedError

    def _cache_path_for_text(self, text: str) -> Path:
        payload = {
            "cache_version": TTS_CACHE_VERSION,
            "text": text,
            "provider": self.cache_provider,
            "model": self.model,
            "configuration": self._configuration_identity(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return PREGENERATED_DIR / f"v{TTS_CACHE_VERSION}-{hashlib.sha256(encoded).hexdigest()}.wav"

    def _load_cached_clip(self, text: str) -> bytes | None:
        path = self._cache_path_for_text(text)
        with _TTS_CACHE_LOCK:
            try:
                audio = path.read_bytes()
            except FileNotFoundError:
                return None
            except OSError:
                return None
            if not audio:
                path.unlink(missing_ok=True)
                return None
            try:
                os.utime(path, None)
            except OSError:
                pass
            return audio

    def _store_cached_clip(self, text: str, audio_bytes: bytes) -> None:
        path = self._cache_path_for_text(text)
        with _TTS_CACHE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(audio_bytes)
                handle.flush()
            try:
                tmp_path.replace(path)
            finally:
                tmp_path.unlink(missing_ok=True)
            self.maintain_cache()

    def cache_diagnostics(self, *, now: float | None = None) -> CacheDiagnostics:
        return tts_cache_diagnostics(now=now)

    def maintain_cache(self, *, now: float | None = None) -> CacheMaintenanceResult:
        return maintain_tts_cache(now=now)



class PiperTtsProvider(CachedTtsProvider):
    cache_provider = "piper"

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

    def _configuration_identity(self) -> dict[str, str | None]:
        config_hash = None
        if self.config:
            try:
                config_hash = hashlib.sha256(Path(self.config).read_bytes()).hexdigest()
            except OSError:
                config_hash = None
        return {
            "binary": self.binary,
            "config_path": self.config,
            "config_sha256": config_hash,
        }

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


class PocketTtsProvider(CachedTtsProvider):
    """Brain-owned CPU model residency; construction never imports or loads Pocket."""

    cache_provider = "pocket"

    def __init__(self, model_config_path: str, voice_state_path: str) -> None:
        self.model = model_config_path
        self.voice_state_path = voice_state_path
        self._lock = threading.RLock()
        self._model = None
        self._voice = None
        self._resident_identity = None
        self._asset_digests: dict[str, tuple[tuple[int, ...], str]] = {}
        self._warmup_started = False
        self._loading = False
        self._load_error: str | None = None

    def _local_model_assets(self) -> list[Path]:
        from ruamel.yaml import YAML

        try:
            config = YAML(typ="safe").load(Path(self.model).read_text(encoding="utf-8"))
            flow = config["flow_lm"]
            references = [
                config.get("weights_path"),
                config.get("weights_path_without_voice_cloning"),
                flow.get("weights_path"),
                config["mimi"].get("weights_path"),
                flow["lookup_table"]["tokenizer_path"],
            ]
            assets = [Path(self.model), Path(self.voice_state_path)]
            for reference in references:
                if reference is None:
                    continue
                # Managed model assets must already be installed. No runtime downloads,
                # fallback weights, implicit HF credentials, or mutable remote identities.
                path = Path(reference)
                if not path.is_absolute():
                    raise TtsError("Pocket model assets require absolute local paths")
                assets.append(path)
            for path in assets:
                if not path.is_file():
                    raise TtsError(f"Pocket voice/model asset unavailable: {path}")
            return assets
        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"Pocket voice/model configuration unavailable ({type(exc).__name__})") from exc

    def _configuration_identity(self) -> dict[str, object]:
        digests = {}
        for path in self._local_model_assets():
            signature = self._asset_signature(path)
            cached = self._asset_digests.get(str(path))
            if cached is None or cached[0] != signature:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                self._asset_digests[str(path)] = (signature, digest)
            digests[str(path)] = self._asset_digests[str(path)][1]
        self._asset_digests = {path: self._asset_digests[path] for path in digests}
        try:
            versions = {name: importlib.metadata.version(name) for name in ("pocket-tts", "torch")}
        except importlib.metadata.PackageNotFoundError as exc:
            raise TtsError("Pocket dependency/runtime unavailable") from exc
        return {"assets_sha256": digests, "runtime": versions}

    @staticmethod
    def _asset_signature(path: Path) -> tuple[int, ...]:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _load_model(self):
        from pocket_tts import TTSModel

        return TTSModel.load_model(config=self.model)

    def _ensure_resident(self, identity: dict[str, object]) -> None:
        if self._model is not None and identity == self._resident_identity:
            return
        self._loading = True
        started = time.perf_counter()
        try:
            model = self._load_model()
            voice = model.get_state_for_audio_prompt(Path(self.voice_state_path))
            if identity != self._configuration_identity():
                raise TtsError("Pocket voice/model assets changed during loading")
            self._model, self._voice, self._resident_identity = model, voice, identity
            self._load_error = None
            logging.getLogger("oracle.tts").info(
                "pocket_model_loaded elapsed_ms=%.1f", (time.perf_counter() - started) * 1000
            )
        except Exception as exc:
            self._model = self._voice = self._resident_identity = None
            self._load_error = f"Pocket voice/model loading failed ({type(exc).__name__})"
            raise TtsError(self._load_error) from exc
        finally:
            self._loading = False

    def warmup(self) -> None:
        with self._lock:
            self._ensure_resident(self._configuration_identity())

    def begin_warmup(self) -> None:
        with self._lock:
            if self._warmup_started:
                return
            self._warmup_started = True
        threading.Thread(target=self._run_warmup, name="pocket-tts-warmup", daemon=True).start()

    def _run_warmup(self) -> None:
        started = time.perf_counter()
        logger = logging.getLogger("oracle.tts")
        logger.info("pocket_warmup_started")
        try:
            self.warmup()
        except Exception as exc:
            self._load_error = str(exc) if isinstance(exc, TtsError) else "Pocket warmup failed"
            logger.warning("pocket_warmup_failed detail=%s", self._load_error)
        else:
            logger.info("pocket_warmup_succeeded elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)

    def synthesize(self, text: str) -> TtsResult:
        started = time.perf_counter()
        with self._lock:
            try:
                identity = self._configuration_identity()
                cached = self._load_cached_clip(text)
                if cached is not None:
                    return TtsResult(cached, "audio/wav", "pocket-cache")
                self._ensure_resident(identity)
                audio = self._model.generate_audio(self._voice, text, copy_state=True)
                audio_bytes = self._encode_audio(audio, self._model.sample_rate)
                if identity != self._configuration_identity():
                    raise TtsError("Pocket voice/model assets changed during synthesis")
                self._store_cached_clip(text, audio_bytes)
                logging.getLogger("oracle.tts").info(
                    "pocket_synthesis elapsed_ms=%.1f audio_bytes=%d",
                    (time.perf_counter() - started) * 1000, len(audio_bytes),
                )
                return TtsResult(audio_bytes, "audio/wav", "pocket")
            except TtsError:
                raise
            except Exception as exc:
                raise TtsError(f"Pocket synthesis failed ({type(exc).__name__})") from exc

    @staticmethod
    def _encode_audio(audio, sample_rate: int) -> bytes:
        import numpy as np
        from scipy.io.wavfile import write

        # Existing satellites decode PCM WAV with the standard-library wave reader.
        # Convert sample representation only; no voice processing or normalization.
        samples = audio.detach().cpu().numpy()
        samples = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        output = io.BytesIO()
        write(output, sample_rate, samples)
        return output.getvalue()

    def status(self) -> TtsStatus:
        configured = bool(self.model and self.voice_state_path)
        detail = "Pocket model and voice are not configured"
        if configured:
            if any(importlib.util.find_spec(name) is None for name in ("pocket_tts", "torch", "scipy")):
                detail = "Pocket dependency/runtime unavailable"
            else:
                try:
                    assets = self._local_model_assets()
                except TtsError as exc:
                    detail = str(exc)
                else:
                    resident = self._resident_identity
                    digests = dict(self._asset_digests)
                    if self._loading:
                        detail = "Pocket model/voice warmup in progress"
                    elif self._load_error:
                        detail = self._load_error
                    elif self._model is None or resident is None:
                        detail = "Pocket model/voice not loaded; warmup or synthesis required"
                    else:
                        try:
                            changed = any(
                                str(path) not in digests
                                or digests[str(path)][0] != self._asset_signature(path)
                                or digests[str(path)][1] != resident["assets_sha256"].get(str(path))
                                for path in assets
                            )
                        except OSError:
                            detail = "Pocket voice/model asset unavailable"
                        else:
                            if changed:
                                detail = "Pocket voice/model assets changed; reload required"
                            else:
                                return TtsStatus("pocket", True, True, "Ready with resident Pocket model and voice on CPU")
        return TtsStatus("pocket", configured, False, detail)


def attempt_tts_provider_warmup(provider: TtsProvider) -> None:
    provider.begin_warmup()


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


@dataclass(frozen=True)
class _TtsCacheEntry:
    path: Path
    size: int
    accessed_at: float
    current_version: bool
    malformed: bool


def tts_cache_diagnostics(*, now: float | None = None) -> CacheDiagnostics:
    current = time.time() if now is None else now
    with _TTS_CACHE_LOCK:
        entries = _inspect_tts_cache()
        expired = sum(
            1 for entry in entries
            if entry.current_version and not entry.malformed and current - entry.accessed_at > TTS_CACHE_MAX_IDLE_SECONDS
        )
        malformed = sum(1 for entry in entries if entry.malformed)
        legacy = sum(1 for entry in entries if not entry.current_version and not entry.malformed)
        total_bytes = sum(entry.size for entry in entries)
        current_entries = [entry for entry in entries if entry.current_version and not entry.malformed]
        oldest = min((entry.accessed_at for entry in current_entries), default=None)
        healthy = (
            not malformed and not legacy and not expired
            and len(current_entries) <= TTS_CACHE_MAX_CLIPS
            and total_bytes <= TTS_CACHE_MAX_BYTES
        )
        return CacheDiagnostics(
            cache_id="tts", path=str(PREGENERATED_DIR), exists=PREGENERATED_DIR.exists(),
            healthy=healthy, entry_count=len(entries), total_bytes=total_bytes,
            limit_entries=TTS_CACHE_MAX_CLIPS, limit_bytes=TTS_CACHE_MAX_BYTES,
            expired_entries=expired, malformed_entries=malformed, legacy_entries=legacy,
            oldest_accessed_at=_format_cache_timestamp(oldest) if oldest is not None else None,
        )


def maintain_tts_cache(*, now: float | None = None) -> CacheMaintenanceResult:
    current = time.time() if now is None else now
    with _TTS_CACHE_LOCK:
        entries = _inspect_tts_cache()
        removed_expired = removed_malformed = removed_legacy = removed_lru = reclaimed = 0
        retained: list[_TtsCacheEntry] = []
        for entry in entries:
            reason = None
            if entry.malformed:
                reason = "malformed"
            elif not entry.current_version:
                reason = "legacy"
            elif current - entry.accessed_at > TTS_CACHE_MAX_IDLE_SECONDS:
                reason = "expired"
            if reason is None:
                retained.append(entry)
                continue
            if _remove_cache_entry(entry.path):
                reclaimed += entry.size
                if reason == "malformed":
                    removed_malformed += 1
                elif reason == "legacy":
                    removed_legacy += 1
                else:
                    removed_expired += 1

        retained.sort(key=lambda entry: (entry.accessed_at, entry.path.name))
        retained_bytes = sum(entry.size for entry in retained)
        while len(retained) > TTS_CACHE_MAX_CLIPS or retained_bytes > TTS_CACHE_MAX_BYTES:
            entry = retained.pop(0)
            if _remove_cache_entry(entry.path):
                removed_lru += 1
                reclaimed += entry.size
                retained_bytes -= entry.size

        diagnostics = tts_cache_diagnostics(now=current)
        return CacheMaintenanceResult(
            cache_id="tts", inspected_entries=len(entries),
            removed_expired=removed_expired, removed_malformed=removed_malformed,
            removed_legacy=removed_legacy, removed_lru=removed_lru,
            bytes_reclaimed=reclaimed, diagnostics=diagnostics,
        )


def _inspect_tts_cache() -> list[_TtsCacheEntry]:
    try:
        paths = list(PREGENERATED_DIR.iterdir())
    except FileNotFoundError:
        return []
    except OSError:
        return []
    entries: list[_TtsCacheEntry] = []
    prefix = f"v{TTS_CACHE_VERSION}-"
    for path in paths:
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError:
            continue
        is_current = (
            path.name.startswith(prefix)
            and path.name.endswith(".wav")
            and len(path.name) == len(prefix) + 64 + 4
            and all(char in "0123456789abcdef" for char in path.name[len(prefix):-4])
        )
        malformed = not path.is_file() or path.is_symlink() or stat.st_size <= 0 or path.name.endswith(".tmp")
        entries.append(_TtsCacheEntry(path, stat.st_size, stat.st_atime, is_current, malformed))
    return entries


def _remove_cache_entry(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _format_cache_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
