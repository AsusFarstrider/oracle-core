# Self-Interaction: Repeat And Help

## Boundary

Repeat and Help are deterministic Brain-owned system interactions. They use the
existing capability registry, system dispatch target, canonical reply shaper,
and source-plus-effective-session lifecycle. They do not use facts or fallback
to answer capability questions, and they do not add conversation memory.

## Repeat

`application_command.py` records a bounded `repeat_output` utility context only
after canonical reply validation. The slot contains reply text plus scalar
route/action/status/error metadata and expires or clears with the existing
90-second session. `system.repeat` renders that text; it never dispatches the
prior command.

Empty replies, Repeat itself, fallback/facts/news/Home Assistant provider text,
sensitive-looking text, cache/routine/media mutations, calendar commits, and
mutating alert acknowledgements are ineligible. A completed authenticated
reminder delivery may seed the satellite's current canonical session with the
Brain-authored reminder message so an immediate “repeat that” remains local to
the source and interaction.

## Help Catalog

`server/oracle_app/system_help.py` is the executable governed catalog. Each
entry declares its public name, aliases, truthful summary/examples, optional
configured handler owner, and supported or deferred disposition. General Help
lists only catalog entries whose canonical owners are present and enabled in
the applied application composition. Specific Help distinguishes supported,
configured-but-disabled, explicitly deferred, and unknown capabilities.

Task guidance is finite and tested against implemented Timer, Alarm, and
Reminder grammar. Failure Help uses only bounded status/error metadata from the
prior repeat-eligible reply; it does not inspect raw provider payloads or infer
new recovery behavior. Configured capability support is not represented as a
live provider-health guarantee. A failed attempt supplies contextual recovery
guidance instead of creating a second health authority.

Catalog changes are public behavior changes. Adding a capability requires its
canonical owner/configuration check, truthful examples, routing tests, and
supported/deferred disposition to change together.

## Remaining Intent Disposition

- Stopwatch and randomizer/coin/dice requests fail deterministically and remain
  deferred post-V2; they cannot enter media or facts.
- Sunrise/sunset fails deterministically and remains Stage 7 Weather scope.
- Lists/Notes remain Stage 8; calls/messages/general announcements remain V3;
  live currency conversion remains provider-backed post-V2 work.
- Exact greetings and thanks have concise deterministic replies. This preserves
  minimal courtesy without adding personality, jokes, stories, or chit-chat.
