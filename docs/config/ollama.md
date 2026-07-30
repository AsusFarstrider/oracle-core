# Ollama Configuration

Ollama is an optional external inference provider. It is not required by the
minimal provider-free Brain profile, and the standard installer does not
install or manage it.

Canonical configuration selects the provider definition and consuming domain
roles. The immutable applied runtime view supplies the provider endpoint,
model and bounded request settings, while credentials (if required) resolve
only from the selected secret generation. Clean runtime code has no
environment/local-file precedence and no compiled-in household provider
choice.

When Ollama-backed fallback routing, Suggestions, or facts summarization is
disabled, those surfaces report intentional unavailability rather than making
the minimal Brain unhealthy. When enabled, readiness validates the selected
bridge, configuration, Oracle-side dependencies, reachability, authentication,
and compatibility. Provider failure does not alter deterministic routing or
built-in provider-free capabilities.
