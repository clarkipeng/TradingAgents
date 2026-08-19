# Temporal Agent Evaluation: Existing Building Blocks

## Bottom line

The components exist. The gap is their temporal composition: a tool boundary
that preserves the public evidence an agent saw, exposes it in real time, and
later replays or re-searches the same eligible world.

## Closest existing solutions

| Category | What it already does | Role in this plan | What it does not replace |
|---|---|---|---|
| Market data: Databento, exchange/vendor feeds | Historical prices and market timing; Databento exposes event, capture-receive, and gateway-output clocks | Market-state connector | Public research corpus or agent search/tool replay ([Databento](https://databento.com/docs/api-reference-historical/helpers/request_symbology)) |
| Financial news: Polygon, Benzinga, LSEG, FactSet, RavenPack | Licensed/historical financial-news access at different depth and rights | News connectors | Unified agent evidence/tape model |
| Web capture: WARC, Browsertrix, WACZ, pywb | Preserve web/browser resources and replay pages for people | Raw public-web capture and inspection | Agent-facing temporal search and arbitrary tool replay ([Browsertrix](https://crawler.docs.browsertrix.com/user-guide/), [pywb](https://pywb.readthedocs.io/en/docs/manual/usage.html)) |
| Archive/backfill: Wayback/Memento, Common Crawl, GDELT, EDGAR | Historic pages, broad crawl data, public filings/news datasets | Seed/reconstructed corpus | Exact original search result or tool visibility ([Memento](https://mementoweb.org/guide/quick-intro/), [Common Crawl](https://commoncrawl.org/overview)) |
| Live search/extraction: Exa, Firecrawl, browser tools | Find and parse the current web | Approved real-time discovery sources | Historical state/replay ([Exa MCP](https://exa.ai/docs/reference/exa-mcp), [Firecrawl](https://docs.firecrawl.dev/introduction)) |
| Agent traces/evals: LangSmith, OpenTelemetry | Record runs and compare datasets/experiments | Export target for our traces/manifests | Durable historical external environment ([LangSmith](https://docs.langchain.com/langsmith/evaluation-concepts), [OpenTelemetry](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)) |
| Tool transport: MCP | Let clients invoke server tools | A delivery surface for temporal tools | Storage, search state, or replay semantics ([MCP](https://modelcontextprotocol.io/specification/draft/server/tools)) |

## What public trading/research systems typically keep

Research-oriented firms generally license historical market/news data and
collect their own source feeds. They retain market and public-source events,
reference-data versions, and their own orders/decisions—not the whole web.
The common pattern is durable ingestion → live/intraday access → partitioned
historical research store. KX documents this as feed handlers, tickerplant,
real-time database, and historical database. [KX architecture](https://code.kx.com/q/architecture/)

For this project, take the discipline—not the HFT complexity: append-only raw
evidence, independent derived indexes, source timing, content hashes, and a
reproducible run manifest. Market data supplies minute-level context; public
news and web evidence supply the research world.

## The integrity limit

No product can recreate an unrecorded historical Google/X result merely from a
page timestamp. Exact provider replay requires the original captured query and
ranked response. An owned temporal index enables arbitrary new historical
queries, but its claim is “what our versioned research tool returns,” not
“what Google returned.” This limitation makes fidelity visible instead of
manufacturing certainty.
