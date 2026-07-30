from __future__ import annotations

import io
import queue
import threading
import time
import tkinter as tk
import uuid
import wave
from dataclasses import dataclass
from tkinter import ttk

import numpy as np
import requests
import sounddevice as sd


DEFAULT_ORACLE_URL = ""
CONVERSATION_TIMEOUT_SECONDS = 90.0
ALERTS_POLL_MILLISECONDS = 2000
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"


def play_ack_tone() -> None:
    sample_rate = 22050
    segments: list[np.ndarray] = []
    for frequency_hz, duration_seconds in ((740.0, 0.045), (880.0, 0.065)):
        frame_count = max(1, int(sample_rate * duration_seconds))
        times = np.arange(frame_count, dtype=np.float32) / sample_rate
        segment = 0.5 * np.sin(2.0 * np.pi * frequency_hz * times)
        envelope = np.linspace(0.0, 1.0, frame_count, dtype=np.float32)
        envelope = np.minimum(envelope, envelope[::-1])
        segments.append(segment * envelope * 0.16)
        segments.append(np.zeros(int(sample_rate * 0.015), dtype=np.float32))

    sd.play(np.concatenate(segments), samplerate=sample_rate)
    sd.wait()


@dataclass
class CommandOutcome:
    transcript: str
    spoken_reply: str
    raw_response: dict


class PushToTalkApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Oracle Push-To-Talk")
        self.root.geometry("720x480")

        self.oracle_url = tk.StringVar(value=DEFAULT_ORACLE_URL)
        self.status_var = tk.StringVar(value="Idle")
        self.active_session_id: str | None = None
        self.last_conversation_activity_at: float | None = None

        self.recording = False
        self.processing = False
        self.audio_chunks: list[bytes] = []
        self.stream: sd.RawInputStream | None = None
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._pending_space_release: str | None = None

        self._build_ui()
        self.root.bind("<KeyPress-space>", self._on_space_down)
        self.root.bind("<KeyRelease-space>", self._on_space_up)
        self.root.bind("<Escape>", self._on_escape)
        self.root.after(50, self._drain_events)
        self.root.after(ALERTS_POLL_MILLISECONDS, self._poll_pending_alerts)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        url_row = ttk.Frame(frame)
        url_row.pack(fill="x")
        ttk.Label(url_row, text="Oracle URL").pack(side="left")
        ttk.Entry(url_row, textvariable=self.oracle_url).pack(
            side="left", fill="x", expand=True, padx=(12, 0)
        )

        status_row = ttk.Frame(frame)
        status_row.pack(fill="x", pady=(12, 0))
        ttk.Label(status_row, text="Status").pack(side="left")
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left", padx=(12, 0))

        hint = (
            "Focus this window, hold SPACE to record, release SPACE to send.\n"
            "Press Escape to clear the current transcript and reply."
        )
        ttk.Label(frame, text=hint).pack(anchor="w", pady=(12, 12))

        ttk.Label(frame, text="Transcript").pack(anchor="w")
        self.transcript_box = tk.Text(frame, height=5, wrap="word")
        self.transcript_box.pack(fill="x")

        ttk.Label(frame, text="Oracle Reply").pack(anchor="w", pady=(12, 0))
        self.reply_box = tk.Text(frame, height=7, wrap="word")
        self.reply_box.pack(fill="both", expand=True)

    def _on_space_down(self, event: tk.Event[tk.Misc]) -> str | None:
        if self._pending_space_release is not None:
            self.root.after_cancel(self._pending_space_release)
            self._pending_space_release = None
        if self.processing or self.recording:
            return "break"
        self._start_recording()
        return "break"

    def _on_space_up(self, event: tk.Event[tk.Misc]) -> str | None:
        if self.recording:
            self._pending_space_release = self.root.after(40, self._finish_space_release)
        return "break"

    def _finish_space_release(self) -> None:
        self._pending_space_release = None
        if self.recording:
            self._stop_recording_and_send()

    def _on_escape(self, event: tk.Event[tk.Misc]) -> str | None:
        self._set_text(self.transcript_box, "")
        self._set_text(self.reply_box, "")
        self.status_var.set("Idle")
        return "break"

    def _start_recording(self) -> None:
        self.audio_chunks = []
        self.recording = True
        self.status_var.set("Recording...")

        def callback(indata: bytes, frames: int, time_info: object, status: sd.CallbackFlags) -> None:
            if status:
                self.event_queue.put(("error", f"Audio input warning: {status}"))
            self.audio_chunks.append(bytes(indata))

        try:
            self.stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                callback=callback,
                blocksize=0,
            )
            self.stream.start()
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.recording = False
            self.stream = None
            self.status_var.set("Idle")
            self.event_queue.put(("error", f"Could not start recording: {exc}"))

    def _stop_recording_and_send(self) -> None:
        self.recording = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        audio_bytes = b"".join(self.audio_chunks)
        if not audio_bytes:
            self.status_var.set("Idle")
            self.event_queue.put(("error", "No audio captured"))
            return

        self.processing = True
        self.status_var.set("Sending to Oracle...")
        worker = threading.Thread(target=self._process_audio, args=(audio_bytes,), daemon=True)
        worker.start()

    def _process_audio(self, pcm_bytes: bytes) -> None:
        try:
            wav_bytes = self._pcm_to_wav(pcm_bytes)
            transcript = self._send_stt(wav_bytes)
            play_ack_tone()
            outcome = self._send_command(transcript)
            audio = self._request_tts(outcome.spoken_reply) if outcome.spoken_reply else None
            self.event_queue.put(("success", (outcome, audio)))
        except Exception as exc:
            self.event_queue.put(("error", str(exc)))
        finally:
            self.event_queue.put(("done", None))

    def _pcm_to_wav(self, pcm_bytes: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm_bytes)
        return buffer.getvalue()

    def _oracle_base_url(self) -> str:
        value = self.oracle_url.get().strip().rstrip("/")
        if not value:
            raise RuntimeError("Enter the Oracle URL before sending a request")
        return value

    def _send_stt(self, wav_bytes: bytes) -> str:
        response = requests.post(
            f"{self._oracle_base_url()}/stt",
            files={"audio": ("speech.wav", wav_bytes, "audio/wav")},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        text = str(data.get("text", "")).strip()
        if not text:
            raise RuntimeError("Oracle returned an empty transcript")
        return text

    def _get_active_session_id(self) -> str:
        now = time.time()
        if (
            self.active_session_id is None
            or self.last_conversation_activity_at is None
            or now - self.last_conversation_activity_at > CONVERSATION_TIMEOUT_SECONDS
        ):
            self.active_session_id = f"pc-push-to-talk-{uuid.uuid4().hex[:10]}"
        self.last_conversation_activity_at = now
        return self.active_session_id

    def _send_command(self, transcript: str) -> CommandOutcome:
        session_id = self._get_active_session_id()
        response = requests.post(
            f"{self._oracle_base_url()}/command",
            json={"text": transcript, "source": "pc-push-to-talk", "session_id": session_id},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        spoken_reply = self._extract_spoken_reply(data)
        return CommandOutcome(transcript=transcript, spoken_reply=spoken_reply, raw_response=data)

    def _extract_spoken_reply(self, payload: dict) -> str:
        reply_text = str(payload.get("reply_text", "")).strip()
        if reply_text:
            return reply_text

        dispatch = payload.get("dispatch", {})
        target = dispatch.get("target")
        status = dispatch.get("status")
        result = dispatch.get("result") or {}

        if status == "pending_confirmation":
            return str(result.get("prompt", "Please confirm before I proceed.")).strip()
        if status == "pending_clarification":
            return str(result.get("prompt", "I found multiple matches. Which one did you want?")).strip()

        if target == "home_assistant":
            return self._extract_home_assistant_speech(result) or "Done."

        if target == "calendar":
            if status == "failed":
                error = str(result.get("error", "")).strip()
                if error == "calendar_query_failed":
                    return "I couldn't read your calendar right now."
                return "I couldn't complete that calendar request."
            events = result.get("events") or []
            spoken = [str(event.get("summary", "")).strip() for event in events[:3] if str(event.get("summary", "")).strip()]
            if not spoken:
                return "You have nothing on your calendar for that time."
            if len(spoken) == 1:
                return f"You have {spoken[0]}."
            if len(spoken) == 2:
                return f"You have {spoken[0]}, and {spoken[1]}."
            return f"You have {spoken[0]}, {spoken[1]}, and {spoken[2]}."

        if target == "news":
            if status == "failed":
                return "I couldn't get the latest headlines right now."
            source_label = str(result.get("source_label", "the news")).strip()
            spoken = [str(item.get("title", "")).strip() for item in (result.get("headlines") or [])[:3] if str(item.get("title", "")).strip()]
            if not spoken:
                return f"I couldn't find any current headlines from {source_label}."
            if len(spoken) == 1:
                return f"From {source_label}: {spoken[0]}."
            if len(spoken) == 2:
                return f"From {source_label}: {spoken[0]}. Also, {spoken[1]}."
            return f"From {source_label}: {spoken[0]}. Also, {spoken[1]}. And {spoken[2]}."

        if target == "system":
            if result.get("action") == "ignore":
                return ""
            if result.get("action") == "refresh_cache":
                return "My device cache has been refreshed."
            if result.get("action") == "cancel_pending":
                return "Canceled."
            if result.get("action") == "current_weather":
                speech = str(result.get("speech", "")).strip()
                if speech:
                    return speech
                return "I could not get current weather right now."
            if result.get("action") == "weather_forecast":
                speech = str(result.get("speech", "")).strip()
                if speech:
                    return speech
                return "I could not get the forecast right now."
            if result.get("action") == "calculation":
                speech = str(result.get("speech", "")).strip()
                if speech:
                    return speech
                return "I could not calculate that right now."
            if result.get("action") == "alerts":
                speech = str(result.get("speech", "")).strip()
                if speech:
                    return speech
                return "I could not manage that timer, alarm, or reminder right now."
            if result.get("action") in {"current_time", "current_date", "current_time_date"}:
                speech = str(result.get("speech", "")).strip()
                if speech:
                    return speech
                return "I could not get the current time or date right now."
            confirmed = result.get("confirmed_dispatch") or {}
            confirmed_result = confirmed.get("result") or {}
            return self._extract_home_assistant_speech(confirmed_result) or "Confirmed."

        if target == "ollama":
            decision = result.get("decision") or {}
            reply = str(decision.get("reply", "")).strip()
            if reply:
                return reply
            ha_result = result.get("home_assistant") or {}
            return self._extract_home_assistant_speech(ha_result) or "Done."

        if target == "music":
            action = str(result.get("action", "")).strip()
            if status == "failed":
                error = str(result.get("error", "")).strip()
                if error == "music_not_found":
                    return "I couldn't find that in Plex."
                if error == "satellite_command_failed":
                    return "I couldn't reach the playback satellite."
                if error == "plex_search_failed":
                    return "I couldn't search Plex right now."
                return "I couldn't complete that music request."
            if action == "what_is_playing":
                now_playing = result.get("now_playing") or {}
                title = str(now_playing.get("title", "")).strip()
                artist = str(now_playing.get("artist", "")).strip()
                if title and artist:
                    return f"You're listening to {title} by {artist}."
                return "Nothing is playing right now."
            if action == "play":
                selected = result.get("selected") or {}
                title = str(selected.get("title", "")).strip()
                artist = str(selected.get("artist", "")).strip()
                if title and artist:
                    return f"Playing {title} by {artist}."
                if title:
                    return f"Playing {title}."
            if action in {"pause", "resume", "stop", "next", "previous", "restart"}:
                return {
                    "pause": "Paused.",
                    "resume": "Resumed.",
                    "stop": "Stopped.",
                    "next": "Skipping.",
                    "previous": "Going back.",
                    "restart": "Restarting.",
                }.get(action, "Done.")
            if action == "set_volume":
                return "Volume updated."
            if action == "volume_up":
                return "Turning it up."
            if action == "volume_down":
                return "Turning it down."
            prompt = str(result.get("prompt", "")).strip()
            if prompt:
                return prompt

        return "Done."

    def _extract_home_assistant_speech(self, payload: dict) -> str:
        response = payload.get("response") or {}
        speech = response.get("speech") or {}
        plain = speech.get("plain") or {}
        return str(plain.get("speech", "")).strip()

    def _request_tts(self, text: str) -> bytes:
        response = requests.post(
            f"{self._oracle_base_url()}/tts",
            json={"text": text},
            timeout=120,
        )
        response.raise_for_status()
        return response.content

    def _poll_pending_alerts(self) -> None:
        try:
            if not self.processing and not self.recording:
                response = requests.get(
                    f"{self._oracle_base_url()}/alerts/pending",
                    params={"source": "pc-push-to-talk"},
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
                alerts = data.get("alerts") or []
                for alert in alerts:
                    message = str(alert.get("message", "")).strip()
                    if not message:
                        continue
                    audio = self._request_tts(message)
                    self.status_var.set("Playing alert...")
                    self._play_wav_bytes(audio)
        except Exception:
            pass
        finally:
            self.root.after(ALERTS_POLL_MILLISECONDS, self._poll_pending_alerts)

    def _play_wav_bytes(self, wav_bytes: bytes) -> None:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            frames = wav_file.readframes(wav_file.getnframes())
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()

        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        dtype = dtype_map.get(sample_width)
        if dtype is None:
            raise RuntimeError(f"Unsupported WAV sample width: {sample_width}")

        audio = np.frombuffer(frames, dtype=dtype)
        if channels > 1:
            audio = audio.reshape(-1, channels)

        sd.play(audio, samplerate=sample_rate)
        sd.wait()

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "success":
                outcome, wav_bytes = payload
                self._set_text(self.transcript_box, outcome.transcript)
                self._set_text(self.reply_box, outcome.spoken_reply)
                if wav_bytes is not None:
                    self.status_var.set("Playing reply...")
                    try:
                        self._play_wav_bytes(wav_bytes)
                    except Exception as exc:  # pragma: no cover - hardware dependent
                        self._set_text(
                            self.reply_box,
                            f"{outcome.spoken_reply}\n\nAudio playback failed: {exc}",
                        )
                self.status_var.set("Idle")
            elif kind == "error":
                self.status_var.set("Error")
                self._set_text(self.reply_box, str(payload))
            elif kind == "done":
                self.processing = False
                if self.status_var.get() != "Error":
                    self.status_var.set("Idle")

        self.root.after(50, self._drain_events)

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)


def main() -> None:
    root = tk.Tk()
    app = PushToTalkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
