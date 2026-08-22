from __future__ import annotations

import logging
import importlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class SttError(RuntimeError):
    pass


LOGGER = logging.getLogger("oracle-stt")


@dataclass
class SttStatus:
    provider: str
    configured: bool
    available: bool
    detail: str


@dataclass
class SttResult:
    text: str
    provider: str


class SttProvider:
    def transcribe(self, audio_bytes: bytes, filename: str) -> SttResult:
        raise NotImplementedError

    def status(self) -> SttStatus:
        raise NotImplementedError

    def warmup(self) -> None:
        return None


def _prepare_audio_input(audio_path: Path, *, provider_name: str) -> Path:
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SttError(
            f"STT requires ffmpeg to transcode {audio_path.suffix.lower()} uploads for {provider_name}"
        )

    normalized_path = audio_path.with_name("input-normalized.wav")
    completed = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(audio_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(normalized_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0 or not normalized_path.exists():
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SttError(detail or "ffmpeg could not transcode the uploaded audio")

    return normalized_path


class WhisperCppProvider(SttProvider):
    def __init__(self, binary: str, model: str | None, threads: int) -> None:
        self.binary = binary
        self.model = model
        self.threads = threads

    def transcribe(self, audio_bytes: bytes, filename: str) -> SttResult:
        started_at = time.perf_counter()
        resolved = shutil.which(self.binary)
        if not resolved:
            raise SttError(f"whisper.cpp binary not found: {self.binary}")
        if not self.model:
            raise SttError("whisper.cpp model is not configured")
        if not Path(self.model).exists():
            raise SttError(f"whisper.cpp model not found: {self.model}")

        suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            audio_path = temp_dir / f"input{suffix}"
            write_started_at = time.perf_counter()
            audio_path.write_bytes(audio_bytes)
            write_elapsed_ms = (time.perf_counter() - write_started_at) * 1000.0

            prepared_audio_path = _prepare_audio_input(audio_path, provider_name="whisper.cpp")

            output_base = temp_dir / "transcript"
            cmd = [
                resolved,
                "-m",
                self.model,
                "-f",
                str(prepared_audio_path),
                "-t",
                str(self.threads),
                "-nt",
                "-np",
                "-otxt",
                "-of",
                str(output_base),
            ]

            process_started_at = time.perf_counter()
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            process_elapsed_ms = (time.perf_counter() - process_started_at) * 1000.0
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise SttError(detail or "whisper.cpp transcription failed")

            text_path = output_base.with_suffix(".txt")
            if not text_path.exists():
                raise SttError("whisper.cpp did not produce a transcript file")

            read_started_at = time.perf_counter()
            text = text_path.read_text(encoding="utf-8").strip()
            read_elapsed_ms = (time.perf_counter() - read_started_at) * 1000.0
            total_elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            LOGGER.info(
                "whisper.cpp timing total=%.1fms process=%.1fms write=%.1fms read=%.1fms bytes=%d model=%s threads=%d",
                total_elapsed_ms,
                process_elapsed_ms,
                write_elapsed_ms,
                read_elapsed_ms,
                len(audio_bytes),
                self.model,
                self.threads,
            )
            return SttResult(text=text, provider="whisper.cpp")

    def status(self) -> SttStatus:
        resolved = shutil.which(self.binary)
        configured = bool(self.model)
        if not resolved:
            return SttStatus(
                provider="whisper.cpp",
                configured=configured,
                available=False,
                detail=f"whisper.cpp binary not found: {self.binary}",
            )
        if not configured:
            return SttStatus(
                provider="whisper.cpp",
                configured=False,
                available=False,
                detail="whisper.cpp model is not configured",
            )
        if not Path(self.model).exists():
            return SttStatus(
                provider="whisper.cpp",
                configured=True,
                available=False,
                detail=f"whisper.cpp model not found: {self.model}",
            )
        return SttStatus(
            provider="whisper.cpp",
            configured=True,
            available=True,
            detail=f"Ready with model {self.model}",
        )


def _resolve_fast_whisper_model(model: str | None) -> str | None:
    if not model:
        return None
    value = str(model).strip()
    if not value:
        return None

    filename = Path(value).name
    match = re.fullmatch(r"ggml-(.+)\.bin", filename)
    if match:
        return match.group(1)

    return value


class FastWhisperProvider(SttProvider):
    _MODEL_CACHE: dict[tuple[str, int, str], object] = {}
    _CACHE_LOCK = threading.Lock()
    _WARMUP_LOCK = threading.Lock()
    _WARMUP_STARTED = False

    def __init__(self, model: str | None, threads: int, compute_type: str = "int8") -> None:
        self.source_model = model
        self.model = _resolve_fast_whisper_model(model)
        self.threads = threads
        self.compute_type = compute_type

    def _load_runtime(self):
        try:
            module = importlib.import_module("faster_whisper")
        except ImportError as exc:
            raise SttError(
                "Fast-Whisper is not installed. Add the faster-whisper package to use this STT provider."
            ) from exc
        return getattr(module, "WhisperModel")

    def _get_model(self):
        if not self.model:
            raise SttError("Fast-Whisper model is not configured")

        key = (self.model, self.threads, self.compute_type)
        cached = self._MODEL_CACHE.get(key)
        if cached is not None:
            return cached, False

        WhisperModel = self._load_runtime()
        with self._CACHE_LOCK:
            cached = self._MODEL_CACHE.get(key)
            if cached is not None:
                return cached, False
            model = WhisperModel(
                self.model,
                device="cpu",
                compute_type=self.compute_type,
                cpu_threads=self.threads,
                num_workers=1,
            )
            self._MODEL_CACHE[key] = model
            return model, True

    def transcribe(self, audio_bytes: bytes, filename: str) -> SttResult:
        started_at = time.perf_counter()
        if not self.model:
            raise SttError("Fast-Whisper model is not configured")

        suffix = Path(filename or "audio.wav").suffix.lower() or ".wav"
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            audio_path = temp_dir / f"input{suffix}"
            write_started_at = time.perf_counter()
            audio_path.write_bytes(audio_bytes)
            write_elapsed_ms = (time.perf_counter() - write_started_at) * 1000.0

            prepared_audio_path = _prepare_audio_input(audio_path, provider_name="fast-whisper")
            load_started_at = time.perf_counter()
            model, loaded_now = self._get_model()
            load_elapsed_ms = (time.perf_counter() - load_started_at) * 1000.0

            process_started_at = time.perf_counter()
            segments, _info = model.transcribe(
                str(prepared_audio_path),
                language="en",
                beam_size=5,
                best_of=5,
                temperature=0.0,
                condition_on_previous_text=True,
                without_timestamps=True,
                vad_filter=False,
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            process_elapsed_ms = (time.perf_counter() - process_started_at) * 1000.0
            total_elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            LOGGER.info(
                "fast-whisper timing total=%.1fms process=%.1fms load=%.1fms write=%.1fms bytes=%d model=%s threads=%d compute_type=%s loaded_now=%s",
                total_elapsed_ms,
                process_elapsed_ms,
                load_elapsed_ms,
                write_elapsed_ms,
                len(audio_bytes),
                self.model,
                self.threads,
                self.compute_type,
                str(loaded_now).lower(),
            )
            return SttResult(text=text, provider="fast-whisper")

    def warmup(self) -> None:
        started_at = time.perf_counter()
        model, loaded_now = self._get_model()
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        LOGGER.info(
            "fast-whisper warmup_succeeded total=%.1fms model=%s threads=%d compute_type=%s loaded_now=%s model_cached=%s",
            elapsed_ms,
            self.model,
            self.threads,
            self.compute_type,
            str(loaded_now).lower(),
            str(model is not None).lower(),
        )

    def status(self) -> SttStatus:
        if not self.model:
            return SttStatus(
                provider="fast-whisper",
                configured=False,
                available=False,
                detail="Fast-Whisper model is not configured",
            )
        try:
            self._load_runtime()
        except SttError as exc:
            return SttStatus(
                provider="fast-whisper",
                configured=True,
                available=False,
                detail=str(exc),
            )
        return SttStatus(
            provider="fast-whisper",
            configured=True,
            available=True,
            detail=f"Ready with model {self.model} on CPU ({self.compute_type})",
        )

    @classmethod
    def begin_warmup(cls, provider: "FastWhisperProvider") -> bool:
        with cls._WARMUP_LOCK:
            if cls._WARMUP_STARTED:
                return False
            cls._WARMUP_STARTED = True

        thread = threading.Thread(
            target=_run_fast_whisper_warmup,
            args=(provider,),
            name="fast-whisper-warmup",
            daemon=True,
        )
        thread.start()
        return True


class DisabledSttProvider(SttProvider):
    def transcribe(self, audio_bytes: bytes, filename: str) -> SttResult:
        raise SttError("STT is disabled")

    def status(self) -> SttStatus:
        return SttStatus(
            provider="disabled",
            configured=False,
            available=False,
            detail="STT provider is disabled",
        )


def build_stt_provider(config: dict[str, object]) -> SttProvider:
    provider_name = str(config.get("stt_provider") or "whisper.cpp").strip()

    if provider_name == "disabled":
        return DisabledSttProvider()

    if provider_name == "whisper.cpp":
        binary = str(config.get("stt_whisper_binary") or "whisper-cli").strip()
        model = config.get("stt_whisper_model")
        model_text = str(model).strip() if model else None
        threads = int(config.get("stt_whisper_threads") or 8)
        return WhisperCppProvider(binary=binary, model=model_text, threads=threads)

    if provider_name == "fast-whisper":
        model = config.get("stt_whisper_model")
        model_text = str(model).strip() if model else None
        threads = int(config.get("stt_whisper_threads") or 8)
        return FastWhisperProvider(model=model_text, threads=threads)

    raise SttError(f"Unsupported STT provider: {provider_name}")


def _run_fast_whisper_warmup(provider: FastWhisperProvider) -> None:
    LOGGER.info(
        "fast-whisper warmup_started model=%s threads=%d compute_type=%s",
        provider.model,
        provider.threads,
        provider.compute_type,
    )
    try:
        provider.warmup()
    except Exception as exc:
        LOGGER.warning(
            "fast-whisper warmup_failed model=%s threads=%d compute_type=%s detail=%s",
            provider.model,
            provider.threads,
            provider.compute_type,
            exc,
        )


def attempt_stt_warmup(config: dict[str, object]) -> None:
    attempt_stt_provider_warmup(build_stt_provider(config))


def attempt_stt_provider_warmup(provider: SttProvider) -> None:
    if not isinstance(provider, FastWhisperProvider):
        return None

    if not FastWhisperProvider.begin_warmup(provider):
        LOGGER.info(
            "fast-whisper warmup_skipped reason=already_started model=%s threads=%d compute_type=%s",
            provider.model,
            provider.threads,
            provider.compute_type,
        )
    return None
