from __future__ import annotations

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from stt import SttError, SttResult
from oracle_app.api import _transcribe_audio_with_provider
from oracle_app.memory.correlation import correlation_context
from oracle_app.memory import sources, transcripts
from oracle_app.memory.retention import retention_policy_from_configuration
from oracle_app.configuration.runtime_models import MemoryRetentionConfiguration


POLICY = retention_policy_from_configuration(MemoryRetentionConfiguration())


class FakeSttProvider:
    model = "small.en"

    def __init__(self, *, text: str = "turn on the kitchen lights", error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def transcribe(self, audio_bytes: bytes, filename: str) -> SttResult:
        if self.error is not None:
            raise self.error
        return SttResult(text=self.text, provider="fast-whisper")


class SttTranscriptObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "oracle-memory.sqlite3"

    def _transcribe(
        self,
        *,
        provider: FakeSttProvider,
        correlation_id: str | None = "corr-stt-1",
        source: str | None = None,
    ):
        upload = _upload_file()
        with patch(
            "oracle_app.memory.transcripts.DB_PATH",
            self.db_path,
        ):
            with correlation_context(correlation_id):
                return _run_transcribe(upload, provider=provider, source=source)

    def test_successful_stt_response_body_and_status_are_unchanged(self) -> None:
        response = self._transcribe(provider=FakeSttProvider(text="turn on the kitchen lights"))

        self.assertEqual(response.model_dump(), {"text": "turn on the kitchen lights", "provider": "fast-whisper"})

    def test_successful_stt_records_transcript_row(self) -> None:
        response = self._transcribe(provider=FakeSttProvider(text="turn on the kitchen lights"))

        self.assertEqual(response.model_dump(), {"text": "turn on the kitchen lights", "provider": "fast-whisper"})
        rows = transcripts.query_transcripts(db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn("raw_transcript", row)
        self.assertEqual(row["correlation_id"], "corr-stt-1")
        self.assertIsNone(row["session_id"])
        self.assertIsNone(row["source_id"])
        self.assertIsNone(row["user_id"])
        self.assertEqual(row["normalized_text"], None)
        self.assertEqual(row["stt_provider"], "fast-whisper")
        self.assertEqual(row["stt_model"], "small.en")
        self.assertIsNone(row["confidence"])
        self.assertEqual(row["route_result"], {})
        self.assertFalse(row["fallback_used"])
        self.assertIsNone(row["fallback_reason"])
        self.assertIsNone(row["final_domain"])
        self.assertIsNone(row["final_intent"])
        self.assertEqual(row["final_status"], "succeeded")
        self.assertIsNone(row["failure_stage"])
        self.assertEqual(
            row["payload"],
            {
                "audio_bytes": len(b"fake-wav-bytes"),
                "endpoint": "/api/speech/stt",
                "filename_suffix": ".wav",
            },
        )

        raw_row = transcripts.get_transcript(
            row["transcript_id"],
            include_raw_transcript=True,
            db_path=self.db_path,
        )
        self.assertEqual(raw_row["raw_transcript"], "turn on the kitchen lights")

    def test_invalid_or_missing_correlation_uses_generated_context(self) -> None:
        response = self._transcribe(provider=FakeSttProvider(), correlation_id="bad value")

        self.assertEqual(response.model_dump(), {"text": "turn on the kitchen lights", "provider": "fast-whisper"})
        [row] = transcripts.query_transcripts(db_path=self.db_path)
        self.assertTrue(str(row["correlation_id"]).startswith("corr_"))

    def test_memory_write_failure_fails_open_for_successful_stt(self) -> None:
        with patch(
            "oracle_app.api.safe_record_transcript",
            return_value=False,
        ):
            with correlation_context("corr-stt-1"):
                response = _run_transcribe(_upload_file(), provider=FakeSttProvider(text="hello"))

        self.assertEqual(response.model_dump(), {"text": "hello", "provider": "fast-whisper"})

    def test_stt_error_response_is_unchanged_and_records_failed_row(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            self._transcribe(provider=FakeSttProvider(error=SttError("offline")))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "offline")
        [row] = transcripts.query_transcripts(db_path=self.db_path)
        self.assertNotIn("raw_transcript", row)
        self.assertEqual(row["correlation_id"], "corr-stt-1")
        self.assertEqual(row["stt_provider"], "FakeSttProvider")
        self.assertEqual(row["stt_model"], "small.en")
        self.assertEqual(row["final_status"], "failed")
        self.assertEqual(row["failure_stage"], "stt")
        self.assertEqual(
            row["payload"],
            {
                "audio_bytes": len(b"fake-wav-bytes"),
                "endpoint": "/api/speech/stt",
                "error_type": "SttError",
                "filename_suffix": ".wav",
            },
        )
        raw_row = transcripts.get_transcript(
            row["transcript_id"],
            include_raw_transcript=True,
            db_path=self.db_path,
        )
        self.assertIsNone(raw_row["raw_transcript"])

    def test_memory_write_failure_fails_open_for_stt_error(self) -> None:
        with patch(
            "oracle_app.api.safe_record_transcript",
            return_value=False,
        ):
            with self.assertRaises(HTTPException) as raised:
                with correlation_context("corr-stt-1"):
                    _run_transcribe(_upload_file(), provider=FakeSttProvider(error=SttError("offline")))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "offline")

    def test_raw_audio_is_not_stored(self) -> None:
        response = self._transcribe(provider=FakeSttProvider(text="hello"))

        self.assertEqual(response.model_dump(), {"text": "hello", "provider": "fast-whisper"})
        [row] = transcripts.query_transcripts(db_path=self.db_path)
        self.assertNotIn("raw_audio", row)
        self.assertNotIn("audio_blob", row)
        self.assertNotIn("audio_bytes", row)
        self.assertEqual(row["payload"]["audio_bytes"], len(b"fake-wav-bytes"))

    def test_successful_stt_accepts_optional_source_metadata(self) -> None:
        sources.upsert_source(
            source_id="test_satellite_alpha",
            source_type="satellite",
            display_name="Kitchen Satellite",
            db_path=self.db_path,
        )

        response = self._transcribe(provider=FakeSttProvider(text="hello"), source="test_satellite_alpha")

        self.assertEqual(response.model_dump(), {"text": "hello", "provider": "fast-whisper"})
        [row] = transcripts.query_transcripts(db_path=self.db_path)
        self.assertEqual(row["source_id"], "test_satellite_alpha")


def _upload_file() -> UploadFile:
    file_obj = tempfile.SpooledTemporaryFile()
    file_obj.write(b"fake-wav-bytes")
    file_obj.seek(0)
    return UploadFile(file=file_obj, filename="speech.wav")


def _run_transcribe(upload: UploadFile, *, provider: FakeSttProvider, source: str | None = None):
    try:
        return asyncio.run(_transcribe_audio_with_provider(
            upload, provider, source=source, retention_policy=POLICY
        ))
    finally:
        asyncio.run(upload.close())


if __name__ == "__main__":
    unittest.main()
