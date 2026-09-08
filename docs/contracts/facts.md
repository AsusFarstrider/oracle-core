# Facts Domain Contract

Status: ratified and implemented through V2 Stage 7 Slice 5. Existing provider-
neutral retrieval, cache, deterministic degradation, and evidence authority
remain in force.

## Purpose and ownership

The `facts` domain owns bounded factual and explanatory lookup from configured
evidence providers. It is not general Conversation, a creative-writing surface,
a control path, or a provider-specific API.

Facts owns question interpretation, provider-neutral request construction,
provider selection/orchestration, normalized result handling, evidence-aware
presentation, bounded follow-up, and final Oracle reply shaping. Fact provider
bridges own provider-specific retrieval, payloads, identifiers, URLs, provenance
normalization, transport, and provider-error translation.

Domain code must not depend on raw provider response shapes. An inference model
is a presentation helper, never the facts provider or factual authority.

## Route scope

Facts may handle evidence-backed requests such as:

- "What is a black hole?"
- "Who wrote Frankenstein?"
- "What is the largest animal?"
- "How does photosynthesis work?"
- "Explain the Roman Republic."

Facts does not own device/service control, network diagnostics, Calendar,
Weather, News, media playback, home automation, runbooks, arbitrary geocoding,
current operational data owned elsewhere, creative generation, role-play, jokes,
stories, or open-ended conversation. Deterministic owners win before Facts.
Unsupported neighboring capabilities must fail truthfully rather than being
silently stretched into a Facts lookup.

## Provider-neutral states

Every Facts provider bridge normalizes to one of:

- `answered`;
- `evidence_only`;
- `no_result`;
- `provider_error`; or
- `disabled`.

These states remain distinct. `no_result` is not a provider outage;
`provider_error` is not lack of evidence; and `disabled` does not activate a
legacy or alternate authority.

## Evidence and presentation

The normalized result preserves its original answer, evidence, provider,
retrieval method, provenance, and meaningful source/retrieval timestamps. Facts
answers the exact question first and may then identify its evidence. Related but
insufficient evidence must produce an honest insufficiency response rather than
a generic article introduction or a model-authored guess.

Facts summarization is optional and provider/model independent. It receives only
the normalized answer, bounded evidence snippets, provenance needed for faithful
presentation, and the user query. Stage 7 remains evidence-only: a model may not
supplement the packet from training knowledge or undisclosed retrieval.

A valid model result is either a supported concise `summary` or `insufficient`.
Valid insufficiency is semantic success and must not trigger another inference
provider. Operational or contract-invalid output may use the next provider in
the Facts-specific configured order under
[`ollama-policy.md`](ollama-policy.md). A successful summary is stored separately
and marked as model-presented; it never replaces the provider-normalized fields
or enters the Facts retrieval cache.

When summarization is disabled, unavailable, or exhausted, Oracle remains useful
deterministically. For `answered`, it selects a concise relevant sentence from
the normalized answer. For `evidence_only`, it uses bounded evidence when useful
or reports that it found related information without a reliable answer.
`no_result`, `provider_error`, and `disabled` each receive their own concise
truthful response. An inference failure is not rewritten as a facts-provider
failure.

An enabled voice Facts lookup emits its normal acknowledgement immediately when
the Facts handler accepts execution, before retrieval begins. This is provider-
independent interaction UX: direct routing and valid fallback re-entry behave
the same, and cache state, inference availability/provider, or predicted latency
do not change it. The final answer queues behind an acknowledgement already in
progress; it never interrupts or overlaps it. The event is source/session
scoped, is not part of final `reply_text`, and contains only the fixed message
and safe routing fields—never payloads, prompts, evidence, provenance, tokens,
or secrets. Provenance/age responses that do not perform a lookup and admin
diagnostic lookup do not emit this voice event.

## Bounded informational follow-up

Facts may retain a typed, expiring informational subject in the canonical
effective session so a bounded same-source follow-up can reuse the subject or
ask for a supported clarification. It must not retain unbounded conversation,
raw provider payloads, or hidden model history. Explicit new wording, pending
clarification/confirmation, reset, expiry, ambiguity, source/user isolation, and
topic changes retain the existing session precedence rules. Repeat eligibility
does not expand to arbitrary provider text.

## Provider-neutral cache

Facts lookup caching stores only normalized provider results. It does not store
model summaries, raw provider payloads, credentials, or secrets. Cache identity
includes normalized query, provider, provider-relevant settings, and request
evidence options.

`answered`, `evidence_only`, and `no_result` may be cached. `provider_error` and
`disabled` must not be cached. Expired, corrupt, malformed, or incompatible
entries are misses. Cache failure is not provider failure. Maintenance prunes
invalid/expired entries, rejects old versions, retains at most the 512 newest
valid entries, and writes with locked atomic replacement. Cache hits preserve evidence and
provenance and may add retrieval notes so diagnostics can distinguish reuse from
live retrieval.

## Admin diagnostics

A read-only admin Facts endpoint may expose normalized lookup, provider
selection, evidence/provenance, cache behavior, inference outcome class, and
safe provider/model identity. It uses the same domain execution dependency and
must not expose raw provider payloads, prompts, tokens, credentials, or
provider-specific internals; emit voice events; dispatch actions; or change the
canonical command reply path.

## Provider bridges

Current bridges include static fixtures and the Wikipedia API. Future Wikidata
or Alexandria bridges remain possible without changing Facts semantics.

The static bridge supports explicit `answered`, `evidence_only`, `no_result`,
and `provider_error` fixtures and honors provider-neutral evidence options.

The Wikipedia bridge executes a Facts-owned search plan, retrieves candidates
and summaries, and normalizes provider-specific title, language, URL, answer,
evidence, and provenance into Oracle's result. Search/summary misses become
`no_result`; timeouts, transport failures, malformed JSON, and malformed payloads
become `provider_error`.

## Configuration and current compatibility

Facts is explicitly enabled and provider-selected in the fixed `facts` section
of canonical `domains/information.yaml`. Its retrieval/cache settings and its
inference enablement/provider order are typed domain-owned choices. Credential
presence, environment variables, and local compatibility files do not enable
Facts or inference and cannot override canonical selection.

Canonical composition constructs one immutable Facts execution dependency used
by voice dispatch and diagnostics. Physical grouping with News and Suggestions
does not merge their runtime, provider, cache, inference, or failure contracts.
An absent/disabled Facts capability remains disabled and never falls back to
`chat` or another authority.

Facts now executes its owned exact `summary|insufficient` contract through the
configured Luna/Ollama order. Valid insufficiency is terminal; operational and
contract failure alone may advance. The prompt and validator prohibit outside
model knowledge, invented numbers and malicious evidence instructions. A model
summary is never inserted into the retrieval cache.

The handler retains only a bounded subject, evidence/source identifiers and
retrieval timestamp in canonical informational context. Obvious pronoun,
provenance and lookup-age follow-ups use that same-source context; ambiguity
requires clarification. Wikipedia date questions may use the existing page-
extract path when the selected summary lacks a date, with the requested date
qualifier preserved to prevent unrelated page dates from becoming answers.
Normalized retrieval, cache behavior and deterministic no-inference replies
remain the compatibility baseline.
