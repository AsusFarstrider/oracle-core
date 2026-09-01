# Timers

Timers are the first complete family built on the shared Brain-owned alert
lifecycle. They do not own a store, scheduler, target registry, or playback
authority of their own.

## Deterministic owner

`server/oracle_app/timers.py` owns strict duration parsing, names, target
semantics, selection, status, adjustment, cancellation, dismissal, response
metadata, and compact state projection after the canonical system route selects
Alerts. It reuses `deterministic_values.parse_duration`.

A timer is one one-time schedule and one occurrence. Local, room, and household
scope live on the schedule; the shared coordinator projects the occurrence to
one or more source delivery rows. A household timer therefore remains logically
singular even though several satellites sound it.

Names are selectors, not unique keys. A unique name, original duration, or typed
90-second alert-subject context may select among multiple timers. Remaining
ambiguity uses the existing 30-second utility clarification state. Explicit
cancel-all uses the existing confirmation authority. Adjustments that move a
deadline to now or the past reject instead of causing unexpected immediate
audio.

## Delivery and foreground lifecycle

The shared coordinator projects a timer only during its fixed ten-minute late
window. Satellite runtime acceptance moves the occurrence from `due` to
`ringing` without treating runtime receipt as human dismissal. After the window,
an unresolved timer becomes `missed` and cannot create surprise audio.

The satellite runtime holds one borrowing foreground handoff across all active
timer occurrences on that destination. It pauses suitable media, explicitly
sounds and identifies each arriving timer, reasserts unresolved timers, and
restores the interrupted session only after the final active timer becomes
terminal. Overlapping expiries reuse that handoff rather than overwriting or
dropping one another.

## APIs and UI

Credential-bound satellite runtime surfaces are:

- `POST /api/satellite/alerts/claim`
- `POST /api/satellite/alerts/{alert_id}/acknowledge`
- `GET /api/satellite/alerts/state`
- `POST /api/satellite/alerts/{occurrence_id}/action`

The satellite browser uses source-bound `GET /api/ui/timer/state` for compact
active refresh and `POST /api/ui/timer/action` for typed cancel or dismiss. The
ordinary home snapshot includes initial timer state, but active countdown
refresh does not accelerate unrelated home providers.

The permanent cross-family scheduling page and active fullscreen takeover are
owned by Slice 10. Slice 7 supplies the dynamic home presence and typed Timer
seams on which that later focused UI builds.
