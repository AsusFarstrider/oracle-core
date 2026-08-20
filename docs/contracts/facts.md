# Facts Domain Contract

Status: V1 implementation contract.

The `facts` domain owns factual and informational lookup behavior inside
Oracle. It replaces the removed user-facing `chat` target for factual and
informational questions, but it must not become a control path or a provider-specific API
surface.

## Ownership

The facts domain owns:

- user-facing factual lookup semantics;
- provider-neutral request construction;
- provider selection and orchestration;
- normalized response handling;
- final Oracle reply shaping.

Fact provider bridges own provider-specific retrieval details, including
Wikipedia page shapes, Wikidata identifiers, future Alexandria payloads, static
fixture formats, URLs, and provenance normalization.

Oracle domain code must not depend on raw provider response shapes.

## Route Scope

Facts may handle questions such as:

- "What is a black hole?"
- "Who wrote Frankenstein?"
- "What is the largest animal?"
- "How does photosynthesis work?"
- "Explain the Roman Republic."

Facts must not handle:

- device control;
- service control;
- network diagnostics;
- calendar lookup;
- music or audiobook playback control;
- home automation;
- runbook execution.

If a deterministic Oracle domain owns the request, that domain wins before
facts.

## Provider-Neutral States

Every facts provider bridge must normalize into one of these states:

- `answered`;
- `evidence_only`;
- `no_result`;
- `provider_error`;
- `disabled`.

Facts replies must fail gracefully for `no_result`, `provider_error`, and
`disabled`.

## Model Boundary

The local model may later summarize normalized facts payloads, but it is not the
facts provider and must not be treated as factual authority.

When summarization is enabled, the model is the primary presentation layer for
facts replies. It receives only the normalized provider answer, evidence
snippets, provenance, and the user query. It must answer only from that supplied
evidence.

Summarization is optional and controlled by `ORACLE_FACTS_SUMMARIZER_ENABLED` /
`facts_summarizer_enabled`.

Summarization applies only to:

- `answered`;
- `evidence_only`.

The dispatch result must preserve the original provider-normalized `answer`,
`evidence`, `provider`, and `retrieval` fields. A successful summarization adds
a separate `summary` field and sets `summarized_by_model = true`.

Summaries must answer the exact user question. For specific-property questions
such as lifespan, author, date, or location, a generic article introduction is
not acceptable unless it directly answers the requested property. If the
provider evidence is related but insufficient, the model should say Oracle could
not find a reliable answer from the supplied evidence.

If summarization fails or produces an invalid/generic answer, Oracle must fall
back to the provider-normalized result and keep `summarized_by_model = false`.
A summarizer failure is not a provider failure.

When `ORACLE_FACTS_ACK_ENABLED` / `facts_ack_enabled` is enabled, Oracle may
emit a source/session-scoped interim `facts_summarizer_ack` command event
immediately before calling the local model for facts summarization. This event
is latency-cover feedback for voice clients. It is not part of final
`reply_text`, and it must not be emitted for facts states that do not invoke the
summarizer.

Voice satellites may poll `/api/voice/command-events` while their `/command`
request is in flight and play a matching `facts_summarizer_ack` event once.
Final answer playback still comes from canonical `/command` `reply_text`.

The interim event must contain only safe routing/presentation fields such as
event type, source, session id, domain, message, and timestamp. It must not
include provider raw payloads, prompts, evidence blobs, provenance details,
tokens, or secrets.

When summarization is disabled or unavailable, Oracle may shape `reply_text`
from the normalized provider answer by selecting a concise sentence that matches
the user's question intent. This is presentation-only behavior: the dispatch
payload must still retain the full normalized provider answer and evidence for
UI, diagnostics, provenance, and later summarization.

## Admin Diagnostics

Oracle may expose a read-only admin facts lookup endpoint for debugging facts
quality, provider selection, evidence, provenance, cache behavior, and
summarizer behavior.

The diagnostic surface must use the same provider-neutral facts request/result
shape as the normal facts domain. It must not expose raw provider payloads,
prompts, tokens, secrets, credentials, or provider-specific API internals.

Diagnostic summarization is presentation/debug visibility only. It must not
emit voice interim acknowledgment events, dispatch actions, or change the
canonical `/command` reply path.

## Provider-Neutral Cache

Facts lookup caching is optional and controlled by `ORACLE_FACTS_CACHE_ENABLED`
/ `facts_cache_enabled` plus `ORACLE_FACTS_CACHE_TTL_SECONDS` /
`facts_cache_ttl_seconds`.

The cache stores only normalized `FactsProviderResult` payloads. It must not
store local-model summaries, raw provider payloads, provider credentials, or
Oracle secrets.

Cache keys must include:

- normalized query text;
- provider id;
- provider-relevant settings such as Wikipedia language;
- request evidence options that affect the normalized payload.

Cached results must preserve provider, evidence, provenance, retrieval method,
and detail fields. A cache hit may add retrieval notes such as `cache_hit` and
`cached_at=<timestamp>` so diagnostics can distinguish live provider retrieval
from cached normalized results.

`answered`, `evidence_only`, and `no_result` results may be cached.
`provider_error` and `disabled` results must not be cached.

Expired, corrupt, malformed, or unreadable cache entries are misses. Cache
failure must not become a facts provider failure.

The facts owner prunes expired and malformed entries, rejects older cache
versions, and retains at most 512 newest valid entries. Maintenance runs at
Brain startup and opportunistically after cache writes. Writes use locked
atomic replacement so concurrent requests cannot lose an otherwise valid
entry or expose partial JSON. Cache diagnostics are read-only and do not make
the cache provider truth.

## V1 Provider Shape

Temporary providers may include:

- static fixtures;
- Wikipedia API;
- Wikidata API;
- future Alexandria API.

These are bridges. Adding or replacing a provider must not require rewriting the
facts domain.

## Wikipedia API Provider

`wikipedia_api` is a temporary facts provider bridge.

The facts domain owns question-intent shaping, search-plan construction,
candidate scoring, and lifespan-evidence selection. The bridge:

- executes the domain-provided Wikipedia search plan and retrieves candidate
  page titles;
- uses the Wikipedia REST page summary payload for answer/evidence;
- normalizes the result into Oracle's `FactsProviderResult`;
- keeps Wikipedia-specific URL, title, language, and payload details inside the
  provider bridge.

Configuration:

```env
ORACLE_FACTS_PROVIDER=wikipedia_api
ORACLE_FACTS_WIKIPEDIA_LANGUAGE=en
ORACLE_FACTS_WIKIPEDIA_TIMEOUT_SECONDS=8
```

Search misses and page-summary 404s normalize to `no_result`. Transport
failures, timeouts, malformed JSON, or malformed summary payloads normalize to
`provider_error`.

## Static Provider Fixtures

The static provider is the V1 test and development bridge. It reads configured
`facts_static_items` and normalizes them into the same provider-neutral result
shape as any future provider.

Fixture items may explicitly exercise all required result states:

```json
{
  "id": "largest_animal",
  "status": "answered",
  "queries": ["what is the largest animal"],
  "answer": {"text": "The blue whale is the largest animal known to have ever existed."},
  "evidence": [
    {
      "title": "Blue whale",
      "snippet": "The blue whale is the largest animal known to have ever existed.",
      "source_name": "Static Fixture",
      "source_type": "static",
      "provenance": {"url": "https://example.invalid/static/blue-whale"}
    }
  ]
}
```

Supported fixture `status` values:

- `answered`;
- `evidence_only`;
- `no_result`;
- `provider_error`.

If `status` is omitted, a fixture with answer text becomes `answered`, a fixture
with evidence but no answer becomes `evidence_only`, and a non-match becomes
`no_result`.

The static provider must honor provider-neutral request options such as
`include_evidence` and `max_evidence_items`.

## Current Compatibility

Explicit facts routing is opt-in through `ORACLE_FACTS_ENABLED` /
`facts_enabled` while the provider bridge rollout is incomplete. Fallback
interpretation may still propose `facts`; with facts disabled, the facts domain
returns the canonical disabled response instead of falling back to `chat`.

In V2, current environment/local-file selection is importer-only after cutover.
Facts configuration is the fixed `facts` section of
`domains/information.yaml`, with explicit enablement and domain-owned provider
selection. The physical grouping with news and Suggestions does not merge their
runtime behavior or failure contracts.

The canonical Brain constructs one immutable facts execution dependency from
that section. Route enablement, voice dispatch, static or Wikipedia provider
selection, cache policy, summarizer acknowledgement, and the read-only admin
lookup consume that dependency directly. They do not reconstruct a compatibility
settings dictionary or consult compatibility getters. An absent or disabled
canonical facts capability remains disabled; it never falls back to another
authority.
