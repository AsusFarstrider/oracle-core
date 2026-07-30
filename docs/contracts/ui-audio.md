# `/api/ui/audio` Contract

## Purpose

`GET /api/ui/audio` returns the Alpha/Beta House Mode Audio page snapshot.

Audio is now a household audio remote surface. It is centered on choosing a user, choosing content, choosing a playback target, optionally choosing a sleep timer, and sending playback to that target.

This remains a structured `/api/ui` surface. It is not a chat prompt, Plex replacement, Audiobookshelf replacement, raw provider browser, or client-side orchestration layer.

## Current Implementation Status

Implemented now:

- page snapshot at `GET /api/ui/audio`
- structured search at `POST /api/ui/audio/search`
- structured playback launch at `POST /api/ui/audio/play`
- structured playback controls at `POST /api/ui/audio/control`
- audiobook sleep timer operations at `POST /api/ui/audio/sleep-timer`
- user list and selected user for audiobook context
- playback targets with user-facing labels and stable internal source ids
- current playback/now-playing summary from playback authority
- current audiobook summary for the selected user
- result-card scaffold populated by the selected user's current audiobook when available
- audiobook search result cards
- music search result cards for tracks and albums
- satellite-targeted audiobook/music playback
- inline audiobook sleep timer on play
- normalized Oracle-proxied artwork URLs where provider art is available
- capabilities flags that tell the UI which Audio behaviors are ready

Not implemented yet:

- browser-as-playback-target semantics
- music sleep timers
- artist/playlist music browsing

Those missing pieces require dedicated `/api/ui/audio` contract work before they are wired. The UI must not fake them through `/command`, `/api/voice`, or provider-specific browser calls.

## Playback Authority Rule

UI-initiated playback is externally targeted playback.

Rules:

- the UI sends a stable Oracle target/source id
- Oracle sends the existing satellite control command to that target
- the selected satellite remains the playback authority owner for local session truth
- internal source ids do not change
- voice command behavior does not change

Important distinction:

- voice-originated satellite playback may use deferred audible start so Oracle can speak before media resumes
- `/api/ui/audio/play` must not use that voice-only deferred audible start path
- UI playback should start audibly on the selected target after the satellite accepts the command
- UI music playback must stop and sync any active Oracle audiobook on the selected target before starting music, so playback authority does not enter a dual-active music/audiobook state

This is an internal call-mode difference, not a change to the public playback authority contract.

## Request

Method:

- `GET`

Optional canonical query fields:

- `source_id`
- `user_id`

### `source_id`

- optional for the read-only snapshot
- identifies the playback-capable Oracle source/target the client wants to inspect
- must remain a stable internal id, not a display label

Alpha/Beta rule:

- read-only snapshots may default to a configured playback-capable source when one exists
- source-scoped write actions still require explicit source/target context

### `user_id`

- optional
- selects the user whose audiobook context should be used
- currently applies narrowly to Audio audiobook behavior, not global UI identity

Rules:

- unknown users should fail with an app-safe `404`
- if omitted, Oracle may choose the configured default user
- music behavior may ignore user context until a provider needs it

## Required Response Shape

Required top-level fields:

- `generated_at`
- `users`
- `selected_user`
- `selected_source_id`
- `targets`
- `playback`
- `now_playing`
- `audiobook`
- `current_audiobook`
- `results`
- `selected_result`
- `sleep_timer`
- `actions`
- `capabilities`
- `refresh_after_seconds`

Bounded compatibility:

- the current House and satellite UIs consume `source`, `selected_target`,
  `available_sources`, `targets[].source`, write field `target`, and
  `client_id`;
- Stage 3 retains those aliases only until both UIs migrate and their
  characterization tests pass;
- canonical clients use `selected_source_id`, `targets[].source_id`, and
  `target_source_id`, plus `ui_session_id` for temporary UI state; and
- the unconsumed `default_room` target field is removed rather than retained
  for tests.

## Field Requirements

### `users`

Rules:

- always an array
- items are app-safe user summaries
- user ids are stable internal ids
- display names may be shown to the household user

Recommended item fields:

- `user_id`
- `label`
- `is_default`
- `audiobook_enabled`

### `targets`

Rules:

- always an array
- items identify playback-capable Oracle sources the UI may target
- `source_id` is the stable request-source ID sent back to Oracle
- `label` is the user-facing display name

Recommended item fields:

- `source_id`
- `label`

Labels are derived from canonical display/room presentation and do not rename
the source ID used by playback authority or voice flows.

In canonical mode the complete Audio read/write surface consumes one installed
application snapshot. Target membership is the union of the enabled music and
audiobook playback policies; user summaries, source associations, defaults, and
labels come from `household.yaml`. Search, current progress, playback authority,
play, transport, and sleep-timer operations receive the selected typed domain
executions explicitly. A disabled domain fails closed for its own operations
and never falls back to V1 registries, provider settings, or control targets.
Legacy aliases listed above remain bounded UI compatibility output; they are not
configuration authority.

### `playback` and `now_playing`

`playback` is the app-safe playback authority summary.

Required `playback` fields:

- `ok`
- `active`
- `active_sessions`
- `output_owner`

Recommended fields:

- `degraded_state`
- `degraded_reasons`
- `detail`

`now_playing` should mirror the selected target's current output owner when available.

Rules:

- do not expose a raw backend-specific authority dump
- do not let the browser infer playback ownership from provider internals

### `audiobook` and `current_audiobook`

Required `audiobook` fields:

- `ok`
- `resume_available`
- `current`

Recommended `current` fields:

- `library_item_id`
- `title`
- `author`
- `current_time_seconds`
- `duration_seconds`

`current_audiobook` should mirror `audiobook.current` for the selected user.

Rule:

- current audiobook is user-scoped because Audiobookshelf progress is user-scoped
- this does not make all of `/api/ui` fully user-aware

### `results`

Rules:

- always an array
- result cards are normalized and app-safe
- provider-specific search payloads must be normalized before reaching the UI

Recommended item fields:

- `result_id`
- `type`
- `title`
- `subtitle`
- `source`
- `library_item_id`
- `position_seconds`
- `duration_seconds`
- `art_url`

Current implementation:

- may include one result for the selected user's current audiobook
- search results are returned by the dedicated structured search contract
- `art_url`, when present, points to an Oracle `/api/ui/audio/art/...` proxy URL rather than provider-specific browser logic

### `sleep_timer`

Required fields:

- `supported`
- `selected_minutes`
- `options_minutes`

Alpha/Beta rule:

- sleep timer is target-scoped behavior
- if `supported` is false, the UI may show timer choices as disabled but must not pretend they work
- `options_minutes` are suggested quick choices, not the only valid values
- clients may send a custom `sleep_timer_minutes` value within the schema bounds when the UI exposes a custom timer field

### `capabilities`

Required fields:

- `library_search`
- `music_search`
- `current_audiobook`
- `target_selection`
- `sleep_timer`
- `structured_play`

Rules:

- clients must use these flags to avoid presenting unsupported actions as active
- disabled affordances may remain visible when they explain a near-term Beta capability

## Future Endpoint Direction

Dedicated endpoints:

- `POST /api/ui/audio/search`
- `POST /api/ui/audio/play`
- `POST /api/ui/audio/control`
- `POST /api/ui/audio/sleep-timer`

These remain structured and explicit. They must not accept raw natural-language commands, and they must not expose provider-specific payloads.

## `POST /api/ui/audio/search`

Purpose:

- search one explicit Audio domain and return normalized result cards

Canonical request fields:

- `ui_session_id`
- `kind`: `audiobook` or `music`
- `query`
- `user_id`, optional and currently meaningful for audiobooks
- `limit`, optional

Rules:

- empty query is rejected
- `Get Current Audiobook` remains separate from search
- audiobook ambiguity is resolved by showing ranked result cards, not by UI clarification state
- music search is first-pass tracks and albums only

Example:

```json
{
  "ui_session_id": "example-session",
  "kind": "audiobook",
  "query": "dune",
  "user_id": "resident_one",
  "limit": 12
}
```

## `POST /api/ui/audio/play`

Purpose:

- play a selected normalized result on a selected satellite target

Request fields:

- `ui_session_id`
- `target_source_id`
- `result`
- `user_id`, optional and currently meaningful for audiobooks
- `sleep_timer_minutes`, optional and audiobook-only

Rules:

- `target_source_id` must be a playback-capable Oracle source ID
- `result.type` must be `audiobook` or `music`
- audiobook results must include `library_item_id`
- music results must be `track` or `album`
- sleep timer is accepted inline for audiobook playback only
- custom sleep timer values are allowed within the `UiAudioPlayRequest.sleep_timer_minutes` bounds
- browser playback target is deferred
- when music starts on a target with an active Oracle audiobook, Oracle stops/syncs that audiobook first instead of relying on the browser to orchestrate cleanup

Example:

```json
{
  "ui_session_id": "example-session",
  "target_source_id": "living_room_voice",
  "user_id": "resident_one",
  "result": {
    "result_id": "audiobook:book-1",
    "type": "audiobook",
    "library_item_id": "book-1"
  },
  "sleep_timer_minutes": 30
}
```

## `POST /api/ui/audio/control`

Purpose:

- pause, resume, or stop active/resumable playback on a selected target

Canonical request fields:

- `ui_session_id`
- `target_source_id`
- `operation`: `pause`, `resume`, or `stop`
- `media_kind`, optional: `audiobook` or `music`

Rules:

- `target_source_id` must be a playback-capable Oracle source ID
- clients should send `media_kind` from the current `now_playing` or `playback.output_owner` summary when available
- if `media_kind` is omitted, Oracle may inspect playback authority to choose the active media kind
- audiobook pause/stop uses Oracle longform control and sync behavior
- audiobook resume uses Oracle longform playback control on the selected target
- audiobook stop closes/syncs the Audiobookshelf session and cancels target-scoped audiobook sleep timers
- music pause/resume/stop uses the selected satellite's music control command
- this endpoint is structured UI control, not a natural-language command path

Example:

```json
{
  "ui_session_id": "example-session",
  "target_source_id": "living_room_voice",
  "operation": "pause",
  "media_kind": "audiobook"
}
```

## `POST /api/ui/audio/sleep-timer`

Purpose:

- manage audiobook sleep timers for a selected target

Canonical request fields:

- `ui_session_id`
- `target_source_id`
- `operation`: `set`, `cancel`, or `status`
- `minutes`, required for `set`

Rules:

- sleep timers are source/target-scoped
- sleep timers are audiobook-only in this pass
- broader music timers are deferred

## Failure And Partial Data Behavior

Recommended behavior:

- prefer a usable page snapshot when one subsection is degraded
- represent playback authority failures inside `playback`
- represent audiobook progress failures inside `audiobook`
- keep `results` as an empty array when no normalized result is available
- use `capabilities` to mark missing backend support rather than hiding all of Audio

## Freshness Expectations

Beta expectation:

- fetch on page load
- poll while visible because playback state changes quickly
- refresh immediately after successful audio actions once structured actions exist

Recommended default polling:

- 5 seconds

## Example

```json
{
  "generated_at": "2026-04-21T13:05:00Z",
  "users": [
    {
      "user_id": "resident_one",
      "label": "Resident One",
      "is_default": true,
      "audiobook_enabled": true
    }
  ],
  "selected_user": "resident_one",
  "selected_source_id": "living_room_voice",
  "targets": [
    {
      "source_id": "living_room_voice",
      "label": "Living Room"
    }
  ],
  "playback": {
    "ok": true,
    "active": true,
    "active_sessions": [
      {
        "backend_type": "oracle_audiobook",
        "media_kind": "audiobook",
        "state": "playing",
        "title": "Dune",
        "artist_or_author": "Frank Herbert",
        "position_seconds": 320.0,
        "duration_seconds": 5400.0,
        "resumable": true
      }
    ],
    "output_owner": {
      "backend_type": "oracle_audiobook",
      "media_kind": "audiobook",
      "state": "playing",
      "title": "Dune",
      "artist_or_author": "Frank Herbert",
      "position_seconds": 320.0,
      "duration_seconds": 5400.0,
      "resumable": true
    },
    "degraded_state": false,
    "degraded_reasons": []
  },
  "now_playing": {
    "backend_type": "oracle_audiobook",
    "media_kind": "audiobook",
    "state": "playing",
    "title": "Dune",
    "artist_or_author": "Frank Herbert",
    "position_seconds": 320.0,
    "duration_seconds": 5400.0,
    "resumable": true
  },
  "audiobook": {
    "ok": true,
    "resume_available": true,
    "current": {
      "library_item_id": "book-1",
      "title": "Dune",
      "author": "Frank Herbert",
      "current_time_seconds": 320.0,
      "duration_seconds": 5400.0
    }
  },
  "current_audiobook": {
    "library_item_id": "book-1",
    "title": "Dune",
    "author": "Frank Herbert",
    "current_time_seconds": 320.0,
    "duration_seconds": 5400.0
  },
  "results": [
    {
      "result_id": "audiobook:book-1",
      "type": "audiobook",
      "title": "Dune",
      "subtitle": "Frank Herbert",
      "source": "current_audiobook",
      "library_item_id": "book-1",
      "position_seconds": 320.0,
      "duration_seconds": 5400.0,
      "art_url": "/api/ui/audio/art/audiobook/book-1?user_id=resident_one"
    }
  ],
  "selected_result": null,
  "sleep_timer": {
    "supported": true,
    "selected_minutes": 0,
    "options_minutes": [0, 15, 30, 45, 60]
  },
  "actions": [
    {
      "action_id": "pause_audiobook",
      "label": "Pause Audiobook",
      "type": "button",
      "icon": "pause",
      "requires_confirmation": false
    }
  ],
  "capabilities": {
    "library_search": true,
    "music_search": true,
    "current_audiobook": true,
    "target_selection": true,
    "sleep_timer": true,
    "structured_play": true
  },
  "refresh_after_seconds": 5
}
```
