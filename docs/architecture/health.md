# Oracle Health Surface

This document describes the current implementation shape of Oracle's health surface.

For the canonical health contract, see
[health-ownership.md](../contracts/health-ownership.md).

## Current State Summary

Current health behavior is split across multiple layers:

- `server/oracle_app/api.py` owns the HTTP routes for `/health*`
- `server/oracle_app/dispatch.py` currently owns several probe wrappers and some direct probe logic
- domain modules such as `audiobook.py`, `calendar.py`, `music.py`, and `news.py` already own some health-building logic
- STT and TTS providers expose `status()` methods that return provider-level readiness detail

This means health is partially centralized, but the ownership boundary is not explicit and the semantics are not fully uniform.

## Current Endpoint Shape

### `GET /health`

Current behavior:

- current `/health` returns basic brain liveness plus a small configured/not-configured summary for Home Assistant and Ollama

### `GET /health/<subsystem>`

Current behavior:

- current subsystem endpoints do not expose configured, reachable, and usable concepts uniformly yet

## Current Endpoint Semantics By Subsystem

### Home Assistant

Current behavior:

- probe lives in `dispatch.py`
- endpoint checks configuration by calling `get_home_assistant_settings()`
- endpoint then performs a live HTTP request to `/api/`

Current semantic shape:

- configured if URL + token can be resolved
- reachable if HTTP succeeds
- usable is implied, not explicitly separated

### Ollama

Current behavior:

- probe lives in `dispatch.py`
- endpoint checks configuration by calling `get_ollama_settings()`
- endpoint then requests `/api/version`

Current semantic shape:

- configured if URL + model resolve
- reachable if HTTP succeeds
- usable is implied, not explicitly separated

### TTS

Current behavior:

- probe lives in `dispatch.py`
- provider object supplies `status()` with `configured` and `available`

Current semantic shape:

- `configured` is explicit
- `available` approximates usable
- reachable is not a separate concept because this is mostly a local provider surface

### STT

Current behavior:

- probe lives in `dispatch.py`
- provider object supplies `status()` with `configured` and `available`

Current semantic shape:

- `configured` is explicit
- `available` approximates usable
- reachable is not a separate concept because this is mostly a local provider surface

### Calendar

Current behavior:

- probe lives in `calendar.py`
- checks whether feed config exists
- fetches the ICS payload and parses events

Current semantic shape:

- `calendar_configured` is explicit
- reachable and usable are effectively merged into one successful parse result

### Music

Current behavior:

- probe lives in `music.py`
- only reports whether Plex config exists and which satellites are configured
- does not perform live Plex or satellite reachability checks

Current semantic shape:

- config-present only
- reachable and usable are not currently probed

## V2 Configuration Reconciliation

Health remains separate from deterministic configuration validation. V2 adds a
third explicit surface, activation compatibility, for runtime/schema support,
required secret presence, and projection compatibility. Reports keep validation
findings, activation blockers, and operational readiness separate.

### Audiobook

Current behavior:

- probe lives in `audiobook.py`
- checks configuration and pings Audiobookshelf

Current semantic shape:

- config-present is explicit
- reachable is explicit through `/ping`
- usable is implied, not explicitly separated

### News

Current behavior:

- probe lives in `news.py`
- only reports whether sources are configured
- does not perform a live feed fetch

Current semantic shape:

- config-present only
- reachable and usable are not currently probed
