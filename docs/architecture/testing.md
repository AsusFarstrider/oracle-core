# Testing

This document records the current test entrypoint and the broad structure of the active test tree.

For the conceptual test taxonomy, see `docs/architecture/test-taxonomy.md`.

## Test Command

Run from the project root:

```bash
python -m pytest
```

Pytest is the sole canonical Python test runner. Install the tracked test
dependency from `requirements-dev.txt`; production runtime dependencies remain
separate in `server/requirements.txt`. The repository configuration fixes the
test root and Brain import path and rejects unknown configuration and markers.

The canonical collection size is reported by each validation run rather than
embedded here, because it changes as coverage grows. Pytest also reports the
unittest-compatibility subtests exercised by the run.

`unittest discover` remains usable as a partial developer convenience, but it
does not collect module-level pytest tests and is not a validation gate.

## Coverage Contract

Branch coverage is measured for every tracked Python module under `server/`
and `satellite/`. The authoritative ownership and ratchet policy is
`tests/coverage-policy.json`; an exclusion must name one exact tracked module
and record why measuring it is inappropriate. Generated coverage data is a
temporary validation artifact and is not committed.

Run the measured suite and reconcile it from the project root:

```bash
python -m pytest --cov --cov-branch --cov-report=json:/tmp/oracle-coverage.json
python scripts/coverage_gate.py /tmp/oracle-coverage.json
```

The gate reports Brain and satellite totals separately, rejects an unmeasured
tracked production module, and enforces the reviewed per-surface ratchets.
Ratchets may initially be null only while establishing the first clean measured
baseline; they are then set from that evidence rather than from an arbitrary
target percentage.

The initial reviewed floors use the lower of the complete private-repository
and independently materialized clean-core measurements, so private-only tests
cannot overstate reusable-core coverage. Clean-core CI runs its complete test
and skip-baseline gate once with `--coverage-json` and enforces the same policy
from that single measured pass.

## Current Test Tree Shape

The current test tree is a flat `tests/` directory with module-per-surface test files.

The suite is organized primarily by implementation surface and feature area rather than by nested directory taxonomy.

## Broad Coverage Areas

Current coverage areas include:

- API and command-path tests
- routing tests
- dispatch and handler tests
- state, session, and context tests
- media tests
- satellite and runtime tests
- config and health tests
- smoke and regression tests

## Representative Modules

Representative modules by area include:

### API and Command Path

- `tests/test_api_command.py`

### Routing

- `tests/test_routing_capabilities.py`
- `tests/test_system_intents.py`

### Dispatch and Handlers

- `tests/test_dispatch_handlers.py`
- `tests/test_ollama_handler.py`

### State, Session, and Context

- `tests/test_session_state.py`
- `tests/test_state.py`
- `tests/test_audiobook_state.py`
- `tests/test_ui_calendar_drafts.py`
- `tests/test_ui_snapshot_cache.py`
- `tests/test_conversation.py`
- `tests/test_room_context.py`
- `tests/test_user_context.py`
- `tests/test_phase_g_audiobook_user_scope.py`

### Media

- `tests/test_music_matching.py`
- `tests/test_audiobook_matching.py`
- `tests/test_audiobook_stream_api.py`
- `tests/test_audiobook_sleep_timer.py`
- `tests/test_audiobook_state.py`
- `tests/test_media_rescue_policy.py`

### Satellite and Runtime

- `tests/test_control_service_request_state_cache_server.py`
- `tests/test_control_service_interruption_authority.py`
- `tests/test_control_service_longform_local_playback.py`
- `tests/test_control_service_plexamp_queue_transport.py`
- `tests/test_control_service_volume.py`
- `tests/test_control_service_logging.py`
- `tests/test_satellite_cli.py`
- `tests/test_satellite_foreground_handoff.py`
- `tests/test_satellite_capture_request_pipeline.py`
- `tests/test_satellite_reply_followup_runtime.py`
- `tests/test_satellite_runtime_config_audio_capture.py`
- `tests/test_satellite_resume_duck_policy.py`
- `tests/test_satellite_reply_fallback.py`
- `tests/test_wake_capture.py`
- `tests/test_wake_tuning.py`

### Config and Health

- `tests/test_config.py`
- `tests/test_config_validation.py`
- `tests/test_health_config.py`
- `tests/test_sync_home_assistant.py`

### Smoke and Regression

- `tests/test_smoke_flows.py`
- `tests/test_phase_a_failures.py`
- `tests/test_utterance_ledger_schema.py`
- `tests/test_utterance_ledger_execution.py`

## Cross-Surface Smoke Coverage

Some smoke tests span both server and satellite surfaces within a single test module.

`tests/test_smoke_flows.py` is the clearest example of this cross-surface structure.
