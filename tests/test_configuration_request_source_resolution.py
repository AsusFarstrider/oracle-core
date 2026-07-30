from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from starlette.requests import Request

from oracle_app import api, state

from oracle_app.configuration.request_source_resolution import (
    EPHEMERAL_HTTP_SOURCE_ID,
    CanonicalRequestSourceResolver,
    RequestSourceAuthenticationError,
    ResolvedRequestSource,
)
from oracle_app.schemas import UiContextStartRequest


class CanonicalRequestSourceResolverTests(unittest.TestCase):
    def test_uncredentialed_ingress_receives_unassociated_ephemeral_source(self) -> None:
        runtime = MagicMock()
        projections = MagicMock()

        resolved = CanonicalRequestSourceResolver(runtime, projections).resolve(
            claimed_source_id="living_room_voice",
            credential=None,
        )

        self.assertEqual(resolved.request_source_id, EPHEMERAL_HTTP_SOURCE_ID)
        self.assertEqual(resolved.kind, "ephemeral")
        self.assertEqual(resolved.authentication, "none")
        runtime.satellites.satellite_for_source.assert_not_called()
        runtime.access.authenticate_source_credential.assert_not_called()
        projections.authenticate_activation.assert_not_called()

    def test_satellite_claim_is_authenticated_against_applied_activation(self) -> None:
        runtime = MagicMock()
        satellite = MagicMock(
            satellite_id="living_room_satellite",
            projection_activation_id="sat_activation_applied",
        )
        runtime.satellites.satellite_for_source.return_value = satellite
        projections = MagicMock()

        resolved = CanonicalRequestSourceResolver(runtime, projections).resolve(
            claimed_source_id="living_room_voice",
            credential="satellite-token",
        )

        self.assertEqual(resolved.request_source_id, "living_room_voice")
        self.assertTrue(resolved.stable)
        self.assertEqual(resolved.authentication, "satellite_credential")
        projections.authenticate_activation.assert_called_once_with(
            "living_room_satellite",
            "sat_activation_applied",
            "satellite-token",
        )
        runtime.access.authenticate_source_credential.assert_not_called()

    def test_non_satellite_credential_is_the_source_authority(self) -> None:
        runtime = MagicMock()
        runtime.satellites.satellite_for_source.return_value = None
        runtime.access.authenticate_source_credential.return_value = "wall_kiosk"
        projections = MagicMock()

        resolved = CanonicalRequestSourceResolver(runtime, projections).resolve(
            claimed_source_id="untrusted-payload-name",
            credential="kiosk-token",
        )

        self.assertEqual(resolved.request_source_id, "wall_kiosk")
        self.assertEqual(resolved.authentication, "source_credential")
        projections.authenticate_activation.assert_not_called()

    def test_invalid_presented_credential_never_falls_back_to_ephemeral(self) -> None:
        runtime = MagicMock()
        runtime.satellites.satellite_for_source.return_value = None
        runtime.access.authenticate_source_credential.return_value = None

        with self.assertRaises(RequestSourceAuthenticationError):
            CanonicalRequestSourceResolver(runtime, MagicMock()).resolve(
                claimed_source_id="wall_kiosk",
                credential="wrong-token",
            )

    def test_browser_ui_context_uses_ingress_source_and_separate_action_target(self) -> None:
        session_id = "ui-search-source-boundary"
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/ui/context/start",
                "query_string": b"",
                "headers": [],
                "app": api.app,
            }
        )
        payload = UiContextStartRequest(
            action="music_search",
            client_id="living-room-browser",
            ui_session_id=session_id,
            target_source_id="living_room_satellite",
        )

        try:
            with (
                patch(
                    "oracle_app.api._canonical_http_request_source",
                    return_value=ResolvedRequestSource(
                        request_source_id=EPHEMERAL_HTTP_SOURCE_ID,
                        kind="ephemeral",
                        authentication="none",
                    ),
                ),
                patch(
                    "oracle_app.api._resolve_ui_audio_source",
                    return_value=("living_room_satellite", ["living_room_satellite"]),
                ),
            ):
                response = api._ui_context_start_impl(payload, request)

            pending = state.load_pending_ui_context(EPHEMERAL_HTTP_SOURCE_ID, session_id)
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending["target_source_id"], "living_room_satellite")
            self.assertIsNone(state.load_pending_ui_context("living_room_satellite", session_id))
            self.assertEqual(response["source_id"], EPHEMERAL_HTTP_SOURCE_ID)
            self.assertEqual(response["target_source_id"], "living_room_satellite")

            with patch(
                "oracle_app.api._ui_audio_search_impl",
                return_value={
                    "ok": True,
                    "kind": "music",
                    "query": "black magic",
                    "results": [],
                    "result_count": 0,
                },
            ) as search:
                command_response = api._handle_pending_ui_context(
                    "black magic",
                    EPHEMERAL_HTTP_SOURCE_ID,
                    session_id,
                    audio_search=api._ui_audio_search_impl,
                )

            self.assertIsNotNone(command_response)
            assert command_response is not None
            self.assertEqual(command_response.dispatch.hook, "ui_context.handle_pending")
            self.assertEqual(command_response.dispatch.result["action"], "music_search")
            search_request = search.call_args.args[0]
            self.assertEqual(search_request.source, "living_room_satellite")
            self.assertEqual(search_request.query, "black magic")
        finally:
            state.clear_pending_ui_context(EPHEMERAL_HTTP_SOURCE_ID, session_id)
            state.clear_pending_ui_context("living_room_satellite", session_id)

    def test_satellite_ui_sends_canonical_context_session_and_target_fields(self) -> None:
        client = (Path(__file__).resolve().parents[1] / "satellite_ui" / "app.js").read_text(encoding="utf-8")
        start = client.index("async function startUiContext")
        end = client.index("function wireActionButtons", start)
        context_client = client[start:end]

        self.assertIn("ui_session_id: state.voice.sessionId", context_client)
        self.assertIn("target_source_id: state.sourceId", context_client)
        self.assertNotIn("source: state.sourceId", context_client)
        self.assertNotIn("\n    session_id: state.voice.sessionId", context_client)


if __name__ == "__main__":
    unittest.main()
