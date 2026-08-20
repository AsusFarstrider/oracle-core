from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from oracle_app.audiobook_runtime.playback import play_selected, sync_then_control


class AudiobookPlaybackRuntimeTests(unittest.TestCase):
    def test_failed_satellite_start_stops_playback_closes_session_and_clears_mapping(self) -> None:
        closed_sessions: list[dict[str, object]] = []
        cleared_playbacks: list[str] = []
        registered_playbacks: list[tuple[str, dict[str, object]]] = []
        actions: list[str] = []

        def _execute_satellite_command(source, action, payload):
            actions.append(action)
            raise RuntimeError("play_longform_audio did not reach a playable long-form state")

        status, result = play_selected(
            source="room_satellite",
            session_id="voice-1",
            user_id="reader_one",
            selection={"library_item_id": "book-1"},
            sleep_timer_seconds=None,
            fetch_audiobook_item=lambda library_item_id, user_id=None: {
                "userMediaProgress": {"currentTime": 123.0, "isFinished": False}
            },
            open_audiobook_playback_session=lambda library_item_id, user_id=None: {
                "id": "abs-session-1",
                "libraryItemId": library_item_id,
                "displayTitle": "Book",
                "displayAuthor": "Author",
                "duration": 1000.0,
                "currentTime": 100.0,
                "audioTracks": [{"contentUrl": "/stream/1", "mimeType": "audio/mpeg", "duration": 1000.0}],
            },
            build_longform_payload=lambda session: (
                "playback-1",
                {
                    "playback_id": "playback-1",
                    "session_id": "abs-session-1",
                    "title": "Book",
                    "author": "Author",
                    "duration_seconds": 1000.0,
                    "start_position_seconds": 100.0,
                    "tracks": [{"url": "http://oracle/audiobooks/stream/playback-1/0"}],
                },
                {
                    "playback_id": "playback-1",
                    "source": "room_satellite",
                    "abs_session_id": "abs-session-1",
                    "duration_seconds": 1000.0,
                },
            ),
            register_active_playback=lambda playback_id, payload: registered_playbacks.append((playback_id, payload)),
            clear_active_playback=cleared_playbacks.append,
            execute_satellite_command=_execute_satellite_command,
            close_audiobook_session=lambda session_id, **kwargs: closed_sessions.append(
                {"session_id": session_id, **kwargs}
            ),
            create_sleep_timer=lambda source, session_id, duration: {},
            defer_audible_start=True,
        )

        self.assertEqual(status, "failed")
        self.assertEqual(result["action"], "play")
        self.assertEqual(registered_playbacks[0][0], "playback-1")
        self.assertEqual(actions, ["play_longform_audio", "stop_longform_audio"])
        self.assertEqual(cleared_playbacks, ["playback-1"])
        self.assertEqual(closed_sessions[0]["session_id"], "abs-session-1")
        self.assertEqual(closed_sessions[0]["current_time"], 123.0)
        self.assertEqual(closed_sessions[0]["time_listened"], 0.0)
        self.assertEqual(closed_sessions[0]["duration"], 1000.0)
        self.assertEqual(closed_sessions[0]["user_id"], "reader_one")

    def test_sleep_timer_failure_stops_started_playback(self) -> None:
        actions: list[str] = []
        cleared_playbacks: list[str] = []

        def _execute_satellite_command(source, action, payload):
            actions.append(action)
            return {"ok": True, "state": "accepted"}

        status, result = play_selected(
            source="room_satellite",
            session_id="routine-1",
            user_id="reader_one",
            selection={"library_item_id": "book-1"},
            sleep_timer_seconds=1200,
            fetch_audiobook_item=lambda library_item_id, user_id=None: {"userMediaProgress": {}},
            open_audiobook_playback_session=lambda library_item_id, user_id=None: {
                "id": "abs-session-1",
                "libraryItemId": library_item_id,
                "duration": 1000.0,
                "currentTime": 100.0,
            },
            build_longform_payload=lambda session: (
                "playback-1",
                {
                    "playback_id": "playback-1",
                    "session_id": "abs-session-1",
                    "duration_seconds": 1000.0,
                    "start_position_seconds": 100.0,
                },
                {"playback_id": "playback-1", "abs_session_id": "abs-session-1"},
            ),
            register_active_playback=lambda playback_id, payload: None,
            clear_active_playback=cleared_playbacks.append,
            execute_satellite_command=_execute_satellite_command,
            close_audiobook_session=lambda session_id, **kwargs: None,
            create_sleep_timer=lambda source, session_id, duration: (_ for _ in ()).throw(
                RuntimeError("timer failed")
            ),
        )

        self.assertEqual(status, "failed")
        self.assertEqual(result["error"], "audiobook_sleep_timer_failed")
        self.assertEqual(result["detail"], "timer failed")
        self.assertTrue(result["playback_stopped"])
        self.assertEqual(actions, ["play_longform_audio", "stop_longform_audio"])
        self.assertEqual(cleared_playbacks, ["playback-1"])

    def test_stop_uses_start_position_when_state_query_fails(self) -> None:
        closed_sessions: list[dict[str, object]] = []
        cleared_playbacks: list[str] = []

        def _execute_satellite_command(source, action, payload):
            if action == "get_longform_state":
                raise RuntimeError("state query failed")
            return {"ok": True, "state": "stopped"}

        status, result = sync_then_control(
            source="room_satellite",
            action="stop_longform_audio",
            close_session=True,
            get_active_playback_for_source=lambda source: {
                "playback_id": "playback-1",
                "source": source,
                "abs_session_id": "abs-session-1",
                "duration_seconds": 1000.0,
                "start_position_seconds": 456.0,
                "user_id": "reader_one",
            },
            execute_satellite_command=_execute_satellite_command,
            close_audiobook_session=lambda session_id, **kwargs: closed_sessions.append(
                {"session_id": session_id, **kwargs}
            ),
            sync_audiobook_session=lambda session_id, **kwargs: None,
            clear_active_playback=cleared_playbacks.append,
        )

        self.assertEqual(status, "executed")
        self.assertEqual(result["action"], "stop")
        self.assertEqual(closed_sessions[0]["session_id"], "abs-session-1")
        self.assertEqual(closed_sessions[0]["current_time"], 456.0)
        self.assertEqual(closed_sessions[0]["time_listened"], 0.0)
        self.assertEqual(closed_sessions[0]["duration"], 1000.0)
        self.assertEqual(closed_sessions[0]["user_id"], "reader_one")
        self.assertEqual(cleared_playbacks, ["playback-1"])


if __name__ == "__main__":
    unittest.main()
