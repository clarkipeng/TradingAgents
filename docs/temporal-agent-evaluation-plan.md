# Temporal Research Environment

## In one sentence

Build a time machine for an agent's public research environment: the same tools work in real time, capture what they see, and later replay or re-search only what was available at a chosen historical moment.

This is not a static sentiment dataset, an entire-web clone, or HFT infrastructure.
It is a hedge-fund-style public research desk made reproducible for agents.

```text
archives + current public world ──► collectors ──► sealed evidence corpus
                                                            │
new or old agent ◄────── temporal search/tools at time T ──┘
        │
        └──► research trace ──► trace metrics + later simulated trade outcome
```

## What it proves

TradingAgents is the first proof: change its prompts, research flow, browser, search, RAG, or NLP/sentiment stack and test whether it did better research and made better decisions without giving the new version future information.
The shared core later works for any tool-using agent.

The question is:

> Given this time, this public evidence, and this tool environment, did agent A research and decide better than agent B?

## Scope

| In | Out for the first proof |
|---|---|
| Daily market state, corporate actions, public financial news and filings | Private messages, broker/order flow, proprietary alt data |
| Company releases, public web sources, public discussion | Exchange packets, co-location, microsecond execution research |
| Raw source artifacts, provenance, search/tool traces, LLM call traces | A claim to reproduce unrecorded Google/X searches |
| Archive backfill for immediate historical coverage | Live broker trading |

Every scenario declares its basis: `forward-captured`, `licensed-historical`, or `archive-reconstructed`.
Forward capture establishes the exact tool-visible world; archive reconstruction trades some fidelity for immediate historical depth.
Both are first-class, and the label keeps the difference honest.

## The data contract

```text
An agent tool may return evidence only when evidence.available_at <= as_of.
```

`available_at` is not merely an article publication date.
Retain the relevant clocks: `event_at`, `source_published_at`, `observed_at`, `available_at`, and `ingested_at`.
The schema keeps full-precision timestamps even though capture cadence is daily; tightening granularity later is a collector change, not a migration.

Store the richest permitted raw observation - page/API response, HTML/JSON/PDF, rendered assets, request context, links, search order/snippets, and source/time metadata - content-addressed and immutable.

Everything interpretive is a replaceable derivative: extracted text, chunks, entities, embeddings, query rewriting, sentiment, summaries, and features.
Each derivative records its input hashes and code/model/prompt version.
This lets a new framework be judged against the same historical world, not frozen labels.

## Two levels of replay

Tool replay makes the environment deterministic, but the LLM still samples.
Record both, and distinguish two replay modes:

| Replay level | Tools | LLM calls | Use |
|---|---|---|---|
| Full-trace replay | From tape | From tape | Exact reproduction: debugging, CI regression tests, golden scenarios |
| Evidence replay | From tape / temporal search | Live | Experiments: new prompts, graphs, or retrieval stacks against the same eligible world |

Every run records its tool tape and its LLM call tape (request, response, model, params) as part of the trace.
A full replay names the captured run it follows: tool calls must match that
run's sequence and canonical request, and LLM calls are selected from that
same run. It never quietly substitutes a later matching observation.
That capture run can be sealed into the scenario manifest, allowing a full
tool/LLM replay to be reconstructed from the scenario ID alone; evidence
replay intentionally does not opt into that tape.

Mutable inputs are scenario inputs too. Capture seals named snapshots (for
example, prior decision memory) before the graph starts; replay reads those
snapshots and never ambient files from the host workspace.

## Recreating search honestly

Source availability alone does not recreate search.
A page can be public but not yet visible to a particular query.

| Search mode | Claim | Required record |
|---|---|---|
| External snapshot replay | "What did this provider return to this call?" | Captured query, filters, identity/locale, pagination, ranked results/snippets, errors, opened artifacts |
| Owned temporal search | "What would our research tool return to this new query at T?" | Versioned corpus, versioned ranker, deterministic tie-break |

Owned temporal search supports arbitrary new queries.
At this corpus scale it is deliberately simple: a full-text query filtered by `available_at <= T` with a deterministic ranking and tie-break rule, plus a result manifest listing corpus state, document IDs, filters, and ranker version.

Sealing a scenario records the eligible corpus hash at its `as_of` time. A
later archive import that would change historical search results is therefore
corpus drift to surface and decide about, never an invisible rewrite of an
evaluation. The TradingAgents evidence-replay adapter refuses a drifted
scenario until it is deliberately resealed as a new evaluation world.

```sql
SELECT ... FROM evidence_fts
WHERE evidence_fts MATCH :query AND available_at <= :as_of
ORDER BY rank, doc_id;
```

Index-event streams, index manifests, and modeled indexing lag are deferred until the corpus is large enough to need them or an experiment depends on indexing latency.
The claim stays "what our versioned research tool returns," never "what Google returned."

## One tool in real time and replay

```json
research.search({
  "query": "NVDA supply constraints",
  "as_of": "now",
  "mode": "live_capture"
})
```

| Mode | Result | Persistence / network |
|---|---|---|
| `live` | Current corpus or approved provider | Fast path; persistence optional |
| `live_capture` | Current corpus plus approved discovery sources | Seal query/result/evidence before return; becomes future replay evidence |
| `replay` | Evidence eligible at `as_of` only | No external network; missing data is explicit |

The tool is a citation-rich interface across public news, filings, web, discussion, macro, and market context.
Expose it as an SDK first; HTTP/MCP surfaces come later.
`live_capture` is the default so the corpus compounds from day one.

TradingAgents keeps the same explicit `temporal=` API for applications, and
also accepts an opt-in normal graph configuration for unattended jobs. Its
default mode is `live`, so existing calls remain unchanged:

```python
config["temporal"] = {
    "mode": "replay",
    "store": ".tradingagents/temporal",
    "scenario_id": "nvda-q4",
    "search_enabled": True,
}
graph.propagate("NVDA", "2024-02-21")
```

For exact golden replay add `"use_capture_tape": True`; it then follows the
capture run named by the sealed scenario. A replay without a scenario must
provide an explicit timezone-aware `as_of` boundary.
`search_enabled` adds `temporal_search` to the market, news, and fundamentals
analysts. Its store and time are resolved only at call time from the active
run, then the returned manifest is persisted in that run's trace. It is off by
default so existing tool selection and prompts do not change.

The LangChain adapter exposes the owned search as an optional
`temporal_search` tool. It reads `as_of` only from the active temporal context
and returns evidence IDs, availability clocks, fidelity, and the query's corpus
manifest; existing TradingAgents tools remain unchanged until a graph opts in.
Agents cite a result as `[evidence:<id>]`; the first-pass evaluator extracts
explicitly cited report lines while leaving richer claim extraction replaceable.

For the first operating loop, the repository exposes two non-interactive
commands that a scheduler can call:

```bash
tradingagents temporal-import historical-news.jsonl --store .tradingagents/temporal
tradingagents temporal-capture --tickers NVDA,MSFT,AAPL --full-surface --store .tradingagents/temporal
tradingagents temporal-sec-import --cik 1045810 --user-agent "Research team contact@example.com" --store .tradingagents/temporal
tradingagents temporal-wayback-import --url 'https://investor.example.com/*' --from 2024-01-01 --to 2024-03-31 --store .tradingagents/temporal
tradingagents temporal-gdelt-import --query NVDA --from 2024-02-01 --to 2024-02-29 --store .tradingagents/temporal
tradingagents temporal-hn-import --query NVDA --from 2024-02-01 --to 2024-02-29 --store .tradingagents/temporal
tradingagents temporal-reddit-import --ticker NVDA --from 2024-02-01 --to 2024-02-29 --store .tradingagents/temporal
tradingagents temporal-media-import --from 2024-02-01 --to 2024-02-29 --sources x,reddit --store .tradingagents/temporal
tradingagents temporal-scenario --id nvda-q4 --as-of 2024-02-21T17:02:03Z --basis archive-reconstructed --metadata '{"ticker":"NVDA"}'
tradingagents temporal-rubric --id nvda-q4 --material evidence-id-1 --useful evidence-id-1,evidence-id-2 --store .tradingagents/temporal
tradingagents temporal-score-run --run-id replay-run-id --id nvda-q4 --store .tradingagents/temporal
```

For a running poller, set `TRADINGAGENTS_POLLER_TEMPORAL_STORE` to the same
store directory. It projects media rows only after their poller terminal receipt
commits, preserving the poller's existing request-budget and failure semantics.
Rubrics are immutable scenario labels: both experimental arms are scored against
the exact same material/useful evidence set.

## Smallest architecture that works

```text
archive backfill ─┐
                  ├─► collectors ─► content-addressed filesystem store
daily capture ────┘                          │
                                             ▼
                              SQLite: provenance + FTS5 temporal search
                                             │
                                             ▼
                                     Temporal Gateway
                                             │
                                             ▼
                         agents + trace metrics + evaluation runner
```

- **Collectors:** the only networked components; a daily cron over a fixed ticker universe (20-50 symbols) captures the existing price, news, Stocktwits, and Reddit tools, alongside archive importers (EDGAR full-text, Wayback, and GDELT discovery now; historical news APIs and Reddit dumps next). The Wayback importer retains original HTML bytes as an artifact and indexes a text derivative with the archive-capture clock. GDELT retains its full query response and each result's source URL/title/seen clock, but deliberately marks original article content as not fetched.
- **Evidence store:** filesystem with content-hash paths; artifacts deduplicate by hash.
- **Provenance + search:** one SQLite database for sources, clocks, artifacts, scenarios, traces, and FTS5 full-text search. The schema is designed so promotion to Postgres/S3 is a configuration change, not a redesign.
- **Temporal Gateway:** the sole agent-facing boundary; enforces time, mode, citations, and no-network replay.
- **Evaluation:** compare sealed research traces first; a portfolio simulator comes later and stays separate from research data.

## TradingAgents: preserve, do not rewrite

The shared core has no TradingAgents imports.
This repository gets a small, opt-in adapter:

1. Intercept `route_to_vendor()` at its existing dataflow boundary.
2. Move direct Reddit/StockTwits/Polymarket calls behind that boundary.
3. Add run-level `live`, `live_capture`, and `replay` context, plus LLM call recording.
4. Write evidence and trace records beside existing reports; preserve live tools and default behavior.

### Repo mesh and implementation waves

Keep the core in `tradingagents/temporal/`, with no graph, vendor, or finance
imports:

| Module | Responsibility |
|---|---|
| `models.py` / `clock.py` | Canonical request, evidence, clocks, scenario, trace types |
| `store.py` | Content-addressed filesystem artifacts and SQLite schema |
| `runtime.py` | Run-scoped `ContextVar` for mode, `as_of`, scenario, store, and optional source tape |
| `gateway.py` | Live-capture/replay selection, provenance envelopes, no-network rule |
| `search.py` | SQLite FTS5 temporal search and result manifests |

`tradingagents/temporal_adapters/langchain.py` is a separate adapter. It owns
LangChain callbacks and any tape-backed chat-model integration for golden
replay; the temporal core never imports LangChain or LangGraph.

The thin repository changes are confined to:

- `dataflows/interface.py`: factor the existing live router into a private
  function and wrap its public entrypoint with the Temporal Gateway.
- `agents/analysts/sentiment_analyst.py`: route its direct Reddit/StockTwits
  prefetches through the same gateway.
- `graph/trading_graph.py`: enter the run-scoped temporal context in
  `propagate()` and connect the optional LangChain adapter to the existing
  callback list.
- `default_config.py`: add opt-in temporal configuration; default remains
  today's live behavior.

Implement in narrow waves, merging and testing each independently:

1. **Foundation:** core types, SQLite/filesystem store, virtual clock, and unit
   tests. No graph or vendor changes.
2. **Tool tape:** wrap `route_to_vendor`, then the two direct social calls.
   Prove evidence replay causes zero *data-source* network calls and live mode
   returns the exact existing result shape.
3. **LLM tape adapter:** record calls through the existing callback seam; add a
   tape-backed LangChain chat model and prove one golden graph scenario follows
   its sealed tool/LLM tapes and scenario snapshot with no external calls.
   Callback recording alone is not full replay.
4. **Corpus:** import one archive-backed scenario and run the daily collector
   for the small ticker universe. This supplies history now while forward
   capture compounds quality.
5. **Temporal search and A/B runner:** add FTS5 search, then paired trace
   metrics. Keep portfolio simulation out until these results are credible.

This is one vertical slice at a time, not a platform rewrite: after wave 2 the
current graph already gains a useful capability—captured live research that can
be rerun without touching a data vendor.

## Evaluating research traces

A trade outcome is one noisy bit per scenario; trace quality is dense signal available immediately.
The A/B harness runs paired scenarios - same scenario set, same pinned model and parameters, per-scenario diffs - and scores:

- **Evidence coverage:** of the material items eligible at `as_of`, which did the agent surface?
- **Citation grounding:** does every factual claim trace to an eligible evidence item, or did it come from nowhere?
- **Retrieval efficiency:** queries issued vs. useful evidence retrieved; token and tool cost.
- **Decision stability:** same scenario, N seeds - does the decision flip randomly?

The repeated paired harness runs both arms for each repetition, reports the
mean of the three trace metrics, and reports each arm's modal-decision share
as decision stability. The runner receives the repetition number so its owner
can pin or vary seeds explicitly; the harness never hides that choice.

The harness is deliberately framework-neutral: each arm supplies a recorded
tool run and an optional claim list; the shared scenario rubric is the only
source of material/useful evidence labels. TradingAgents is one runner, not a
special case in the evaluator.

Model-weight contamination (the LLM "remembering" post-`as_of` outcomes) is an evaluation-stage concern, handled there, not in data construction: pin the same model across both arms so leakage is constant, use citation grounding to flag unsupported claims, and prefer post-training-cutoff windows for absolute claims.
Outcome-based (P&L) evaluation arrives with the simulator, once the corpus and ticker universe give it statistical power.

## Delivery and decision gates

| Step | Deliverable | Gate |
|---|---|---|
| 1. Core | Canonical requests, evidence records, clocks, content-addressed store, SQLite schema | Same request selects the same eligible evidence deterministically |
| 2. Tool tape | Tool tape at `route_to_vendor` and the direct social paths | Evidence replay makes zero data-source network calls; live behavior unchanged |
| 3. LLM tape adapter | Callback recorder plus tape-backed chat model | One golden graph scenario supports full-trace replay with zero external calls |
| 4. Corpus | Archive backfill + daily capture cron over the fixed universe | A historical scenario (basis: `archive-reconstructed`) replays end to end |
| 5. Search | FTS5 temporal search over the corpus | New query at T cannot retrieve a later document; every result has a manifest |
| 6. Evaluation | Paired A/B harness with trace metrics | A prompt/retrieval change produces an attributable, per-scenario metric difference |
| 7. Simulation | Cash/positions/orders/fills model | Information replay and market timestamps are validated; outcome metrics join trace metrics |

Steps 1-6 are all small; nothing waits on months of forward capture.
Do not extract a standalone product until a non-finance adapter uses the same core unchanged.
Stop or narrow if coverage cannot support the claimed fidelity or the proof does not beat a simpler static dataset plus tracing.

## Why it is different

| Existing category | Strength | Missing piece supplied here |
|---|---|---|
| Market-data vendors | Accurate historical prices/events | Multi-source public research and agent tool trajectory |
| Web archives/crawlers | Raw pages/browser capture | Agent-ready temporal search and per-call provenance |
| Live search/scrape APIs | Current discovery/extraction | Historical replay and a deterministic source boundary |
| Agent eval/tracing | Compare model/prompt/tool runs | The changing external world those runs observed |

The asset compounds: every real-time `live_capture` interaction improves the future backtesting corpus.
The product is not "we crawl the web"; it is **reproducible, auditable external state for agents acting on the public world**.
