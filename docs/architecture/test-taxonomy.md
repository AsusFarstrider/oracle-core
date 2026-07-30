# Oracle Test Taxonomy

## Purpose

This document defines the conceptual taxonomy for understanding and grouping tests, not a file-level source of truth.

It defines:

- the accepted test taxonomy for the cleaned architecture
- where the current test files fit in that taxonomy
- the current high-level test surface mapping
- the current regression priorities

It is a conceptual and maintenance document, not a test-runner change by itself.

For deploy-vs-repo environment expectations, use `docs/runbooks/deploy-test-parity.md`.

## Accepted Taxonomy

The accepted Oracle test taxonomy is:

- routing tests
- handler/domain tests
- state/context tests
- satellite/runtime/control tests
- config/health tests
- smoke/regression tests

## Current Test Mapping

### Routing Tests

- `tests/test_routing_capabilities.py`
- `tests/test_system_intents.py`

Primary coverage:

- route target selection
- bare transport route refinement
- pending clarification routing
- system intent classification

### Handler / Domain Tests

- `tests/test_dispatch_handlers.py`
- `tests/test_music_matching.py`
- `tests/test_audiobook_matching.py`
- `tests/test_ollama_handler.py`
- `tests/test_alerts.py`
- `tests/test_calculations.py`
- `tests/test_weather_formatting.py`
- `tests/test_audiobook_sleep_timer.py`
- `tests/test_audiobook_stream_api.py`
- `tests/test_media_rescue_policy.py`
- `tests/test_reply_text.py`

Primary coverage:

- handler execution behavior
- media matching and clarification behavior
- reply shaping
- alert logic
- weather/calculation formatting
- audiobook stream behavior

### State / Context Tests

- `tests/test_state.py`
- `tests/test_session_state.py`
- `tests/test_conversation.py`
- `tests/test_room_context.py`
- `tests/test_user_context.py`
- `tests/test_phase_g_audiobook_user_scope.py`

Primary coverage:

- scoped pending state
- conversation and session state
- room and user context behavior
- active media state and user-scoped audiobook context

### Satellite / Runtime / Control Tests

- `tests/test_control_service.py`
- `tests/test_control_service_logging.py`
- `tests/test_satellite_cli.py`
- `tests/test_satellite_playback_resume.py`
- `tests/test_satellite_reply_fallback.py`
- `tests/test_longform_player.py`
- `tests/test_wake_capture.py`
- `tests/test_wake_tuning.py`
- `tests/test_audio_playback_retry.py`

Primary coverage:

- local control-service command contract
- control-service tracing
- satellite CLI/runtime behavior
- follow-up listen behavior
- playback interruption / resume behavior
- production Pi reply fallback contract
- long-form playback and wake-capture runtime surfaces

### Config / Health Tests

- `tests/test_config.py`
- `tests/test_config_validation.py`
- `tests/test_health_config.py`

Primary coverage:

- configuration loading
- configuration validation
- health-surface configuration reporting

### Smoke / Regression Tests

- `tests/test_api_command.py`
- `tests/test_smoke_flows.py`
- `tests/test_phase_a_failures.py`
- `tests/test_phase_c_utterance_bank.py`
- `tests/test_phase_c_utterance_execution.py`

Primary coverage:

- ignored-command response shape
- `/command` trace logging on the brain path
- grouped end-to-end smoke coverage for key routing, dispatch, and reply flows
- regression-oriented utterance and failure surfaces

## Current Test Surfaces

At a high level, the current repo test tree covers these surfaces:

- brain-side routing and dispatch selection
- domain and handler execution behavior
- conversation, session, room, and user context state
- satellite runtime, control service, playback, and wake-capture surfaces
- configuration and health-reporting surfaces
- smoke and regression coverage that spans multiple layers in one flow

## Current Regression Priorities

The current regression priorities include:

- preserving key end-to-end route -> dispatch -> reply paths
- preserving scoped pending clarification and confirmation behavior
- preserving transport-control behavior when media is already active
- preserving long-form audiobook playback and stream behavior
- preserving canonical Brain-owned reply text and retained satellite fallback boundaries
- preserving deploy-vs-repo expectations documented separately in `docs/runbooks/deploy-test-parity.md`

## Regression Priorities

The current regression priorities include:

1. bare transport commands
   - plain `stop`, `pause`, `resume`, `next`, `previous` should still target active local playback correctly
   - currently covered mainly in `tests/test_routing_capabilities.py`
2. music clarification follow-ups
   - short follow-ups such as `the second one` and differentiator phrases should remain scoped by `source + session_id`
   - currently covered in `tests/test_routing_capabilities.py` and `tests/test_dispatch_handlers.py`
3. audiobook clarification follow-ups
   - short follow-ups such as `the first one` and differentiator phrases should remain scoped by `source + session_id`
   - currently covered in `tests/test_routing_capabilities.py` and `tests/test_dispatch_handlers.py`
4. `play dune`
   - weak Plex hits must not win over the intended audiobook clarification path
   - currently covered in `tests/test_dispatch_handlers.py`
5. same-session `yes` after pending prompt
   - same-session pending confirmation / clarification flow must remain functional
   - currently covered explicitly in `tests/test_smoke_flows.py` and in deeper handler/routing tests
6. audiobook range streaming behavior
   - byte-range passthrough and partial-content behavior must remain intact
   - currently covered in `tests/test_smoke_flows.py` and `tests/test_audiobook_stream_api.py`
7. canonical `reply_text`
   - the brain must remain the normal owner of spoken reply shaping, and the production Pi runtime must not rebuild domain replies locally
   - currently covered in `tests/test_smoke_flows.py`, `tests/test_reply_text.py`, and `tests/test_satellite_reply_fallback.py`

## V2 Configuration Coverage

Config/health coverage expands to restricted parsing, schema fragments,
normalization/golden hashes, cross-file validation, secret presence/redaction,
candidate concurrency, generations, activation/rollback, migrations/equivalence,
source vocabulary, projection compatibility, offline restart, and selected
versus applied reporting. Tests use the same engine as runtime and CLI.
