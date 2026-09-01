# Oracle System Utilities Contract

## Purpose And Status

This contract defines the ratified V2 Stage 6 behavior and authority boundary
for Time/Date, Timers, Alarms, Reminders, Math, Conversions, Repeat, and Help.

Status: ratified target contract. Private development planning records track
slice completion; a requirement in this document is not a claim that the
current runtime already implements it. This contract is the reusable behavioral
law and does not depend on private roadmap artifacts at runtime.

Implementation note: Stage 6 Slices 2 through 11 now provide the shared strict
value parsers, bounded typed session/pending-context seam, alert-aware route
precedence, owner-validated fallback re-entry, deterministic Time/Date, Math,
Conversion, Timer, Alarm, Reminder, Repeat, and truthful Help owners, plus the
focused satellite alert runtime/UI. Slice 12 integrated release verification
and promotion remain incomplete.

## Shared Authority Rules

- The Brain owns recognition, semantic parsing, clarification, scheduling,
  calculation, conversion, target and recipient resolution, mutation, and
  canonical replies.
- Satellites own local capture, display, touch, audio effects, and actual
  interruption outcome. They do not reinterpret alert or utility semantics.
- Supported Stage 6 requests are deterministic. They must not depend on LLM
  fallback for parameters, answers, recurrence, targets, or mutation.
- The existing capability registry, dispatch boundary, canonical interaction
  session, household configuration, Memory, foreground-audio authority, and
  satellite source authentication remain the only authorities for their
  respective concerns.
- A shared parser or formatter may serve several utilities, but it cannot
  become a second router or a universal open-ended language engine.
- Unknown, unsupported, incompatible, or materially ambiguous requests clarify
  or fail honestly. Oracle must not strip unknown text into a different valid
  command.

## Deterministic Routing And Fallback

Deterministic capability handling retains priority. Recognition and semantic
parsing must agree on one owner-backed interpretation before execution.

Fallback may propose only a supported domain and bounded normalized text, plus
the existing advisory user field. The Brain must re-enter the deterministic
owner and repeat its ordinary parsing, validation, identity, authorization,
and execution checks. Fallback cannot:

- compute time, math, or conversion answers;
- invent dates, durations, units, recurrence rules, alert names, recipients,
  target sets, occurrence scope, or acknowledgement state;
- turn an unsupported request into a mutation;
- bypass clarification or failure produced by the deterministic owner; or
- answer directly on behalf of a Stage 6 utility.

For the shared `system` route, deterministic re-entry requires the fallback
text to equal the normalized original request. This deliberately limits
fallback to classification for Stage 6 utilities and makes semantic-value
invention mechanically non-executable, not merely prompt-prohibited.

Active pending clarification and actively ringing alert context may refine
otherwise ambiguous words such as `stop`, `cancel`, `dismiss`, or `snooze`.
They do not erase existing media and Home Assistant collision rules.

## Canonical Session Context

Stage 6 extends the existing effective interaction session. It does not create
a parallel conversation-memory store.

The session may retain bounded typed references to:

- the prior calculation value;
- the prior conversion value, dimension, source unit, and target unit;
- the prior temporal subject;
- a selected alert schedule, occurrence, or timer by stable scalar identity;
- an explicitly established canonical user; and
- the last repeat-eligible Oracle reply.

Typed context follows the existing source plus effective-session identity,
90-second inactive lifecycle, synchronization, reset, and snapshot rules.
Pending clarifications use the existing 30-second pending lifecycle. Context
must clear or narrow after expiry, reset, explicit topic change, ambiguity,
terminal destructive action, or invalid referenced state.

Context may store identifiers and immutable typed summaries. It must not store
live alert objects, global alert collections, provider payloads, credentials,
playback truth, or a second copy of durable schedule state.

Examples such as “divide that by four,” “what about 12 miles?”, “add five
minutes to it,” and “skip the next one” execute only when typed context makes
the subject and operation unambiguous.

## Time, Date, Timezones, And Holidays

- Household-local time and schedules use the canonical household IANA timezone,
  never the host, process, browser, or inferred geography timezone.
- World time supports a governed bounded set of common city, region, country,
  and timezone aliases plus explicit IANA timezone names.
- The governed alias surface and ambiguity set are recorded in
  `docs/reference/timezone-alias-catalog.md`; its executable owner is the
  temporal domain, and catalog expansion is a reviewed behavior change.
- Genuinely ambiguous aliases clarify; unsupported locations fail honestly.
- Stage 6 adds no external geocoding provider.
- Relative dates and date arithmetic use typed temporal values and explicit
  timezone semantics.
- The configured calendar feed or feeds of kind `holidays` are the sole holiday
  authority. A holiday absent from that configured authority is unknown; Oracle
  must not use a hardcoded holiday catalog, ordinary personal calendar feed,
  fallback answer, or hidden external source.
- Sunrise and sunset remain Stage 7 Weather scope.
- Local wall-clock gap/fold resolution is a reusable temporal-domain primitive;
  later alert recurrence must call that owner rather than independently
  reproducing DST policy.

## Recurrence And Daylight-Saving Time

Recurring alarm and reminder schedules preserve intended local wall-clock time
in their explicit schedule timezone.

- If a local time does not exist during a forward transition, that occurrence
  uses the first valid local instant after the gap.
- If a local time occurs twice during a backward transition, that occurrence
  uses the earlier instant and fires exactly once.
- Later occurrences return to their ordinary intended local wall-clock time.
- Recurrence expansion must be bounded and idempotent; restart cannot create a
  second occurrence for the same schedule instance.
- Occurrence overrides, skip-next, and snooze do not silently rewrite the
  recurring series.
- Material ambiguity between editing an occurrence and editing a series must
  clarify. Explicit occurrence or series wording controls the scope.

## Shared Alert Lifecycle

Timers, alarms, and reminders share one Brain-owned semantic lifecycle with
four distinct concepts:

1. a definition or schedule records durable user intent;
2. an occurrence records one due instance and its exception/missed state;
3. a delivery projection records presentation work for one canonical
   destination; and
4. an acknowledgement records the actor and meaning of a transition.

The families may have different policies, but they must not acquire separate
semantic stores, schedulers, recurrence engines, target registries, or
acknowledgement authorities. Existing source-scoped leased alert rows may serve
as a compatible delivery projection. They are not the Stage 6 schedule or
logical-occurrence authority.

Runtime delivery acceptance, local display dismissal, person acknowledgement,
and logical completion are distinct transitions. A retry, restart, or
multi-target fan-out must not conflate them.

Configuration activation does not itself create, reschedule, acknowledge, or
delete alerts. Future occurrence projection may resolve destinations from the
new applied configuration as explicitly allowed for reminders.

## Target And Recipient Resolution

Alert target resolution is centralized:

- `local` means the requesting authenticated stable satellite;
- a room means its currently enabled eligible alert-capable satellites;
- whole-house means one logical occurrence projected to the currently enabled
  eligible household destinations; and
- a reminder recipient is a canonical person, resolved independently from
  physical destinations.

For satellites, `associated_user_id` is the canonical default-user
relationship: the person who normally uses the satellite. A satellite has zero
or one default user; a user may be the default user for multiple satellites.
This relationship remains context and destination policy, not authentication,
authorization, speaker proof, or a permission grant.

Personal reminders for a user project to all currently enabled and eligible
satellites whose `associated_user_id` is that user. A common satellite has no
associated user and does not receive personal reminder text or a generic
private-reminder indicator. No reminder-specific identity or destination
registry is permitted.

For the reminder pronoun “me,” recipient resolution is:

1. explicitly established current canonical session user;
2. requesting satellite's default user; and
3. clarification.

The general household-default-user fallback is forbidden for this reminder
decision. On a common satellite, an omitted recipient naturally clarifies
between everyone and a specific person. Explicit named-person or everyone
wording does not clarify unnecessarily.

“Everyone” means every enabled real household user. Stage 6 has no guest-user
design, and service, technical, or system identities are excluded. Each person
owns an independent occurrence/acknowledgement state. A person with no eligible
destination remains truthfully undelivered and outstanding.

Recurring reminders freeze semantic recipients. Each future occurrence
resolves eligible physical destinations from the then-current applied
configuration and records enough configuration revision context to explain the
projection. Already-created occurrences and delivery history are not rewritten.

An explicit everyone-reminder may also project one household/common copy to
eligible common screens. That copy may display the reminder text and household
outstanding state. Hiding or dismissing it affects only the common projection;
it cannot acknowledge any person's occurrence.

## Timer Policy

Timers support the creation, naming, coexistence, status, adjustment,
cancellation, expiry, identification, and dismissal behavior in the Stage 6
brief. A room or whole-house timer remains one logical timer with multiple
deliveries.

Explicit cancel-all requires confirmation through the canonical pending
confirmation lifecycle. A timer selection may use a unique name, duration, or
typed session subject; multiple remaining matches clarify and are never chosen
arbitrarily. An adjustment that would produce a zero or past deadline fails
without creating immediate surprise audio.

If a target is unavailable at expiry, the timer has a fixed 10-minute
late-delivery grace period. A late delivery must identify that it is late. After
10 minutes Oracle marks/surfaces the timer as expired or missed and cannot emit
surprise audio.

Timer runtime acceptance begins or maintains logical ringing but is not user
dismissal. A participating destination's authenticated dismissal completes the
logical occurrence and converges every delivery. Suitable interrupted media is
restored only when the final timer in the destination's shared foreground
interaction ends.

## Alarm Policy

Alarms support persistent one-time and recurring schedules, series and
occurrence edits, enable/disable/delete, occurrence exceptions, skip-next,
snooze, missed state, and the bounded ringing behavior in the Stage 6 brief.

An alarm due at most 20 minutes before recovery may ring late. An older alarm is
missed and visible but cannot surprise-ring. Ringing and reassertion are one
logical occurrence state, not repeated alert creation.

Explicit wording such as “tomorrow's alarm” edits an occurrence; wording such
as “weekday alarm” edits a schedule. Context may establish “next.” Otherwise a
material occurrence-versus-series ambiguity clarifies.

The deterministic Alarm family owner is `server/oracle_app/alarms.py`; it uses
the shared alert schedule, occurrence, target, delivery, acknowledgement, and
foreground authorities. Source-bound satellite and browser state/action
surfaces may project and manage that state, but they are not additional Alarm
schedulers or stores. A ringing browser projection requests display attention
and offers typed snooze/dismiss actions; runtime acceptance alone never counts
as dismissal.

## Reminder Policy

Reminders are persistent person- or household-directed obligations, not alarms
with different reply text. They support one-time and recurring schedules,
outstanding/overdue state, snooze, dismissal, and independent recipient
acknowledgement.

A reminder due at most 20 minutes before recovery may deliver late. An older
reminder becomes overdue and visible but cannot blurt automatically. Person
acknowledgement converges that person's eligible deliveries only. A common
surface can never impersonate or acknowledge a person.

The deterministic Reminder family owner is `server/oracle_app/reminders.py`.
It reuses the canonical alert recurrence, schedule, occurrence, target,
delivery, and actor-typed acknowledgement authorities. Semantic recipient IDs
are stored independently of source IDs. Source-bound satellite and browser
state/actions are projections of that owner, never reminder schedulers or
identity registries.

## Math And Conversions

Math and conversions use strict typed deterministic evaluation. Unknown text
must reject rather than be removed. Math supports the bounded arithmetic,
fractions, percentages, roots/powers, aggregation, proportions, geometry,
money arithmetic, precision, and follow-up surface in the Stage 6 brief.

Conversions use a governed unit and dimension catalog, validate dimensional
compatibility, handle affine units such as temperature correctly, and format
human-useful precision. Timezone conversion belongs to Time/Date. Live currency
conversion is not Stage 6 behavior.

## Repeat, Help, And Interaction Style

Repeat returns the most recent repeat-eligible Oracle-authored output in the
current canonical session. It does not replay the prior command or mutation and
does not reach across session/source boundaries. Credentials, internal traces,
raw provider/tool payloads, and unsafe content are ineligible.

A successfully spoken reminder may establish the authenticated satellite's
current interaction session and store only its Brain-authored reminder message
for immediate Repeat. Delivery acceptance remains a delivery transition, not a
person acknowledgement or a second reminder authority.

Help must be truthful to implemented and applied capabilities. It may provide
general capability help, task guidance, and useful failure/recovery guidance.
It cannot claim disabled, deferred, or unimplemented behavior.

The executable Help catalog is `server/oracle_app/system_help.py`. Optional
availability derives from the installed canonical handler composition; recent
failure guidance derives from bounded typed session metadata. Help does not
probe providers, treat configuration as a health guarantee, or maintain a
parallel capability/health registry.

Stage 6 is not a personality or chit-chat expansion. Oracle may remain pleasant,
natural, clear, and concise, but it does not introduce unsolicited banter,
jokes, trivia, follow-up chatter, personality interjections, or unnecessary
verbal flourishes.

Stopwatch and randomizer/coin/dice behavior are deferred post-V2. Existing
behavior need not be broken, but Stage 6 does not expand or formalize it.

## Satellite UI, Audio, And Display Attention

Stage 6 UI/runtime work is limited to the alert experience: dynamic alert
summary, scheduling/management, active occurrence presentation, authenticated
typed touch actions, compact state refresh, foreground audio coordination, and
truthful degraded/readiness state.

The existing foreground-audio authority owns pause, interruption outcome,
resume, replacement, and overlap behavior. Alert code and UI may not create a
competing playback owner.

Physical display wake/attention must be implemented and proven for the current
Windows Surface satellite fleet. The current Linux display does not sleep and
requires no new wake mechanism. Stage 6 must not build a universal abstraction
for hypothetical platforms, but readiness must truthfully report whether an
enabled display-capable satellite supports required attention behavior.

Broad House Mode/UI redesign, mobile/general responsive cleanup, generalized
runtime decomposition, and packaging remain later-stage work.

## Durable Deferrals

- Lists and Notes, including grocery/shared-list behavior: Stage 8 V2.
- Communications and Messaging: V3.
- Sunrise/sunset: Stage 7 Weather.
- Stopwatch and randomizer/coin/dice: post-V2 with no V2 assignment.
- Personality/chit-chat expansion: not a Stage 6 product goal.
