# News

The `news` domain is a route target with execution centered in `server/oracle_app/handlers/news.py`.

## Structure

The current domain is split across:

- `server/oracle_app/news.py` for request parsing, source selection, and result construction
- `server/oracle_app/provider_bridges/rss_news.py` for RSS fetch and parse mechanics
- `server/oracle_app/handlers/news.py` for dispatch-target execution and error shaping
- capability routing in `server/oracle_app/capabilities/plugins.py`, which recognizes news requests and routes them to the `news` target

## Responsibilities

The current domain is responsible for:

- identifying news requests
- parsing the requested source from the query when present
- selecting the current configured source
- selecting the active news provider mechanism for the chosen source
- executing the request through the news handler

## Data Shapes

The current domain centers on:

- `NewsQuery` as the parsed request shape
- a headlines result payload containing source metadata and headline items

## Provider Surface

The current implementation reads configured news sources from config/domain state.

The active bridge is:

- `RssNewsBridge`

The current split is:

- domain/config owns source catalog and source selection
- the RSS bridge owns feed fetch, RSS/XML parsing, and headline normalization

This preserves room for future non-RSS news mechanisms without introducing multi-bridge orchestration now.

## Current Surface

The current news surface includes general and source-specific headline requests
for configured providers, with:

- a five-minute fresh cache per configured source;
- bounded stale-on-error reuse for at most 30 minutes;
- explicit freshness metadata and plain-language stale wording;
- no caching of fetch or RSS parse failures.

## Boundary

The news domain resolves headline requests on the brain and returns structured headline results through the dedicated news handler.

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
