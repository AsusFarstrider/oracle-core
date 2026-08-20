# Dispatch And Reply Characterization

This reference freezes the pre-decomposition Stage 5 dispatch/reply seam. It
describes behavior that later typed target models and Brain-owned reply shapers
must preserve unless an explicitly approved correctness repair says otherwise.

## Shared Envelope

`DispatchPlan` remains the serialized command envelope:

- `target`: one current `RouteTarget`;
- `hook`: the selected handler seam;
- `payload`: the target-owned request object, currently represented as a dict;
- `status`: `planned`, `pending_integration`, `pending_confirmation`,
  `pending_clarification`, `executed`, or `failed`;
- `result`: the target-owned outcome object or null.

The public command response continues to expose that envelope plus the
Brain-owned `reply_text`, requested `session_id`, and effective session ID.
Typed target models introduced later must round-trip to the same JSON objects;
they do not authorize a second public representation.

Pending confirmation and clarification are shared status primitives. Their
result key is `prompt`, and they take reply precedence over target shaping.
Shared failures retain `error` and optional `detail`; domain-specific evidence
stays in the domain outcome.

## Target Matrix

| Target owner | Representative payload keys | Success outcome keys/actions | Failure keys and codes consumed by replies |
| --- | --- | --- | --- |
| Home automation | `text`, source/session and resolved room context | nested `response.speech.plain.speech` | `error`; `pending_state_requires_context`, `home_room_unresolved`, `retired_home_room_name`, `home_assistant_target_unavailable` plus `unavailable_targets`, `home_assistant_state_verification_failed` plus `verification_failed_targets` |
| Calendar | `text` or `action`, source/session context | `action`: `find_event`, `list_events`, `commit_event`; `query`, `events`, `speech`, `stale_notice` | `error`; `calendar_query_failed`, `calendar_write_unavailable`, `calendar_write_failed` |
| Facts | `query`, source/session context | `facts_status`: `answered`, `evidence_only`, `no_result`, `disabled`, `provider_error`; `query`, `answer`, `evidence`, `summary`, `summarized_by_model` | provider failure is expressed through `facts_status` and/or failed dispatch status |
| Fallback router | `query` plus request context | proposal/result data is interpreted by Brain orchestration before any second dispatch | failed or unresolved fallback always uses the established misunderstanding reply |
| Audiobook | normalized text/action, source/session, effective user and playback-target context | `action`: `play`, `resume_current`, `pause`, `resume`, `stop`, `what_is_playing`, `series_lookup`, `sleep_timer`; `selected`, `now_playing`, `match`, `ordinal`, `sleep_timer`, operation/count/due fields | `error`; `unknown_user`, `audiobook_user_not_configured`, `pending_state_requires_context`, `audiobook_not_found`, `audiobook_search_failed`, `satellite_command_failed`, `no_active_audiobook`; optional requested user fields |
| News | `text` and selected source | `headlines`, `source_label`, `stale_notice` | failed status maps to the established headline-unavailable reply |
| Network | `action`, normalized text and execution context | normalized `speech` plus action-specific diagnostic/control fields | failed status maps to the established network-unavailable reply |
| System | `action`, normalized text and source/session context | `action`: `ignore`, `refresh_cache`, `cancel_pending`, `switch_user`, `calculation`, `alerts`, current time/date actions, `confirm_pending`; `speech`, display/user fields, or `confirmed_dispatch` | `error`, including `unknown_user`; failed confirmation may contain a complete nested `confirmed_dispatch` whose target reply remains authoritative |
| Weather | `action`, normalized text and remote location/date context | `action`: current/remote current weather, local/remote forecast, weather history; normalized `speech` | `error` and optional `detail`; `weather_unavailable`, `remote_weather_location_unresolved`, forecast range/unavailable variants, remote unavailable, and history unavailable |
| Music | normalized text/action, source/session and playback-target context | `action`: `play`, `what_is_playing`, `lookup_album`, transport actions, `set_volume`, volume up/down; `selected`, `now_playing`, `reply`, satellite outcome, and degraded dual-active evidence | `error`; `pending_state_requires_context`, `music_not_found`, `satellite_command_failed`, `music_search_failed`, `plex_search_failed` |

No target may infer authority from keys belonging to another target. The common
context and failure primitives will be typed once; the rows above become
target-owned executable models in the ordered DTO migration.

## Brain Pipeline Side Effects

Reply shaping is pure with respect to providers and sessions, but command
processing around it currently owns these ordered effects:

1. normalize request and effective interaction/session identity;
2. route and construct the dispatch payload;
3. add effective user, room, source, and playback-target context where owned;
4. execute and validate the target result or validate/re-dispatch a fallback
   proposal;
5. apply pending/session transitions and deferred playback metadata;
6. build the exact Brain reply;
7. emit tracing, command events, and Memory observations;
8. assemble the public command response.

Structural extraction must keep the externally observable ordering, session
refresh decisions, intentional silence, deferred-audible-start behavior, and
nested confirmation behavior stable.

## Executable Evidence

`tests/fixtures/dispatch_reply_characterization.json` provides representative
success, failure, pending, nested-confirmation, degraded-media, and intentional-
silence envelopes across every current target. Its test requires exact Pydantic
round-trip shape and byte-exact reply text. Existing focused reply, handler,
command API, session, tracing, Memory, and smoke tests remain the detailed
evidence for branches not duplicated in that representative matrix.
