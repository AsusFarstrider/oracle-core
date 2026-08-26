from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from oracle_app.application_playback import deferred_resume
from oracle_app.brain_application_composition import CanonicalBrainApplicationComposition
from oracle_app.configuration.request_source_resolution import ResolvedRequestSource
from oracle_app.music_runtime.control import ControlPlaneError
from oracle_app.schemas import VoiceDeferredResumeRequest
from oracle_app.satellite_playback_routes import register_satellite_playback_routes


def _token(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _client(*, authentication: str = "satellite_credential") -> tuple[TestClient, Mock, Mock]:
    resolver = Mock()
    resolver.resolve.return_value = ResolvedRequestSource(
        request_source_id="credential-bound-source",
        kind="stable",
        authentication=authentication,
    )
    fleet = SimpleNamespace(
        satellite_for_source=lambda source_id: (
            SimpleNamespace(playback_capable=True)
            if source_id == "credential-bound-source"
            else None
        )
    )
    composition = object.__new__(CanonicalBrainApplicationComposition)
    object.__setattr__(composition, "request_source_resolver", resolver)
    object.__setattr__(composition, "runtime", SimpleNamespace(satellites=fleet))
    deferred_resume = Mock(return_value={"ok": True})
    app = FastAPI()
    app.state.brain_application_composition = composition
    register_satellite_playback_routes(app, deferred_resume=deferred_resume)
    return TestClient(app), resolver, deferred_resume


def test_deferred_resume_requires_projection_bearer_credential() -> None:
    client, _resolver, deferred_resume = _client()

    response = client.post(
        "/api/satellite/deferred-resume",
        json={"source": "claimed-source", "continuation_token": "opaque"},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    deferred_resume.assert_not_called()


def test_deferred_resume_uses_credential_bound_source_and_typed_continuation() -> None:
    client, resolver, deferred_resume = _client()
    continuation = {
        "kind": "audiobook",
        "backend_type": "oracle_audiobook",
        "session_id": "book-session",
        "resume_action": "resume_longform_audio",
    }

    response = client.post(
        "/api/satellite/deferred-resume",
        json={"source": "claimed-source", "continuation_token": _token(continuation)},
        headers={"Authorization": "Bearer projection-secret"},
    )

    assert response.status_code == 200
    resolver.resolve.assert_called_once_with(
        claimed_source_id="claimed-source",
        credential="projection-secret",
        peer_address="testclient",
    )
    request = deferred_resume.call_args.args[0]
    assert request.source == "credential-bound-source"
    assert request.deferred_session == continuation


def test_deferred_resume_rejects_non_satellite_credentials() -> None:
    client, _resolver, deferred_resume = _client(authentication="source_credential")

    response = client.post(
        "/api/satellite/deferred-resume",
        json={"source": "claimed-source", "continuation_token": "opaque"},
        headers={"Authorization": "Bearer source-secret"},
    )

    assert response.status_code == 403
    deferred_resume.assert_not_called()


def test_deferred_resume_rejects_malformed_typed_continuation() -> None:
    client, _resolver, deferred_resume = _client()
    continuation = {
        "kind": "audiobook",
        "backend_type": "oracle_audiobook",
        "session_id": "book-session",
        "resume_action": "play_media",
    }

    response = client.post(
        "/api/satellite/deferred-resume",
        json={"source": "claimed-source", "continuation_token": _token(continuation)},
        headers={"Authorization": "Bearer projection-secret"},
    )

    assert response.status_code == 400
    deferred_resume.assert_not_called()


def _application_execution(*, admitted: bool = True, result: object | None = None) -> Mock:
    execution = Mock()
    execution.settings.playback_target.side_effect = (
        lambda source_id: object() if admitted and source_id == "credential-bound-source" else None
    )
    if isinstance(result, Exception):
        execution.execute_satellite_command.side_effect = result
    else:
        execution.execute_satellite_command.return_value = result or {"ok": True, "state": "playing"}
    return execution


def _application_request(
    *,
    source: str = "credential-bound-source",
    action: str = "resume_longform_audio",
    resume_args: dict[str, object] | None = None,
) -> VoiceDeferredResumeRequest:
    continuation: dict[str, object] = {
        "kind": "audiobook" if action == "resume_longform_audio" else "music",
        "backend_type": "oracle_audiobook" if action == "resume_longform_audio" else "plexamp",
        "session_id": "playback-session",
        "resume_action": action,
    }
    if resume_args is not None:
        continuation["resume_args"] = resume_args
    return VoiceDeferredResumeRequest(source=source, deferred_session=continuation)


def test_application_deferred_resume_validates_source_action_and_music_arguments() -> None:
    with pytest.raises(HTTPException, match="Unsupported deferred resume action"):
        deferred_resume(_application_request(action="stop"))
    with pytest.raises(HTTPException, match="requires resume_args"):
        deferred_resume(_application_request(action="play_media"))


def test_application_deferred_resume_rejects_unadmitted_target() -> None:
    execution = _application_execution(admitted=False)
    composition = SimpleNamespace(music_execution=execution, audiobook_execution=None)
    with (
        patch("oracle_app.application_playback.brain_application_composition", return_value=composition),
        pytest.raises(HTTPException, match="not an admitted canonical audio target"),
    ):
        deferred_resume(_application_request())


def test_application_deferred_resume_executes_typed_audiobook_and_music_actions() -> None:
    audiobook = _application_execution()
    music = _application_execution()
    with patch(
        "oracle_app.application_playback.brain_application_composition",
        return_value=SimpleNamespace(music_execution=None, audiobook_execution=audiobook),
    ):
        audiobook_result = deferred_resume(_application_request())
    with patch(
        "oracle_app.application_playback.brain_application_composition",
        return_value=SimpleNamespace(music_execution=music, audiobook_execution=None),
    ):
        music_result = deferred_resume(
            _application_request(
                action="play_media",
                resume_args={"media_url": "https://media.invalid/item"},
            )
        )

    assert audiobook_result["ok"] is True
    assert music_result["ok"] is True
    music.execute_satellite_command.assert_called_once_with(
        "credential-bound-source",
        "play_media",
        {"media_url": "https://media.invalid/item"},
    )
    audiobook.execute_satellite_command.assert_called_once_with(
        "credential-bound-source",
        "resume_longform_audio",
        None,
    )


def test_application_deferred_resume_preserves_control_plane_failure_fields() -> None:
    failure = ControlPlaneError(
        "satellite unavailable",
        failure_class="transport_failure",
        owning_component="satellite.control_service",
        error_code="control_unreachable",
    )
    execution = _application_execution(result=failure)
    composition = SimpleNamespace(music_execution=None, audiobook_execution=execution)
    with patch("oracle_app.application_playback.brain_application_composition", return_value=composition):
        result = deferred_resume(_application_request())

    assert result["ok"] is False
    assert result["source"] == "credential-bound-source"
    assert result["result"] == {
        "action": "resume_longform_audio",
        "error": "satellite_command_failed",
        "detail": "satellite unavailable",
        "failure_class": "transport_failure",
        "owning_component": "satellite.control_service",
        "control_error": "control_unreachable",
    }
