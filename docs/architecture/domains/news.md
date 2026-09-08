# News

The `news` domain is a route target with execution centered in `server/oracle_app/handlers/news.py`.

## Structure

The current domain is split across:

- `server/oracle_app/news.py` for request parsing, source selection, and result construction
- `server/oracle_app/news_context.py` for bounded selected-story follow-up state
- `server/oracle_app/provider_bridges/rss_news.py` for RSS fetch and parse mechanics
- `server/oracle_app/handlers/news.py` for dispatch-target execution and error shaping
- capability routing in `server/oracle_app/capabilities/information.py`, which recognizes news requests and routes them to the `news` target

## Responsibilities

The current domain is responsible for:

- identifying news requests
- parsing the requested configured source and bounded topic when present
- combining configured sources for a general request
- selecting the active news provider mechanism for the chosen source
- assigning stable Oracle article identities and retaining source/publication/retrieval evidence
- resolving ordinal selection, provenance, publication-time and explicit-update follow-ups
- executing the request through the news handler

## Data Shapes

The current domain centers on:

- `NewsQuery` as the parsed request shape
- headline and selected-article result payloads containing source availability,
  stable article IDs, publication time, retrieval time and freshness
- an expiring News informational subject in the shared interaction session,
  containing only bounded Oracle-owned IDs and evidence fields

## Provider Surface

The current implementation reads configured news sources from config/domain state.

The active bridge is:

- `RssNewsBridge`

The current split is:

- domain/config owns source catalog and source selection
- the RSS bridge owns feed fetch, RSS/XML parsing, headline normalization, and
  selected HTML article retrieval/excerpt extraction

This is one bounded RSS bridge, not a general web or provider framework.

## Current Surface

The current News surface includes general and source-specific headlines, bounded
topic filtering over RSS title/summary evidence, ordinal story selection,
selected-article excerpts, provenance/publication-time follow-up and explicit
checks for newer related source evidence. It has:

- a five-minute fresh cache per configured feed and selected article;
- bounded stale-on-error reuse for at most 30 minutes for successful reads;
- independent per-source availability, explicit partial/stale wording and no
  caching of fetch, parse or article failures;
- narrow high-confidence disclosure when configured sources publish closely
  matching headlines with explicit lexical contradiction; Oracle does not
  adjudicate which source is correct.

Article retrieval is confined to the selected RSS link. Each source may list
exact additional `article_hosts`; the feed host is also admitted. Initial URLs,
every redirect and the final URL must use HTTP(S), match that exact allowlist,
use a standard port, contain no credentials, and resolve only to public network
addresses. Responses must be HTML and are size-bounded. Oracle extracts a
deterministic bounded excerpt and never follows page instructions, bypasses a
paywall, opens arbitrary web destinations, or invokes inference.

## Boundary

Publication time remains source evidence about when the article was published;
it is never presented as proof of when the reported event happened. Retrieval
time separately records Oracle's read. News resolves on the Brain and returns
structured evidence through the dedicated handler and canonical reply surface.

## V2 Configuration Reconciliation

News remains a separate runtime domain, but its configuration is the fixed
`news` section of `domains/information.yaml`. The section has independent
explicit enablement, provider selection, source catalog, and cache/freshness
policy; neighboring facts and Suggestions sections cannot enable or override it.

The canonical runtime seam constructs news only when this section is enabled.
It retains the explicit selected provider while binding each source to its own
typed referenced RSS definition and indexing the source ID, display name, and
aliases. Dormant definitions and neighboring sections do not become fallback.

The canonical Brain binds that view to one immutable news execution dependency.
Route parsing, pending-calendar collision checks, dispatch, per-source RSS
fetching, fresh/stale cache policy, and health reporting consume the typed source
and provider records directly. Canonical parsing recognizes only configured
source IDs, display names, and aliases; the legacy built-in source vocabulary
remains confined to the explicit V1 path. A disabled or absent canonical news
section cannot fall back to legacy feeds.
