# Retrieval Substrate Plan: Temporal Search for Agentic Research Backtesting

Audited 2026-08-19 by two independent reviews (code-groundedness and adversarial architecture); all findings integrated.
The material corrections from the audit: measure before building (benchmark moved first), fix a lookahead leak in bm25 term statistics before any ranking work, separate the document layer from raw evidence with stable keys, and gate ranking quality on held-out scenarios so tuning cannot certify memorization.

## Thesis

The novel asset is not the agent benchmark; it is the database/system that honestly answers broad, search-shaped tool calls against the world as it was at time T.
Every experiment so far bottlenecked on retrieval quality, not on agents.
Retrieval quality is a pure function of (eligible corpus, query, ranker version), so it is measured and tuned offline at zero LLM cost.
LLM arm benchmarks are deferred until the retriever clears an offline gate, then run once instead of five times.

## Design invariants (every phase preserves these)

1. `available_at <= as_of` filters every retrieval path - including `temporal_fetch` (its `get_evidence` path has no as_of filter today, so the tool enforces it) and including ranking statistics (see the bm25 leak below).
2. The evidence/document boundary: **evidence = bytes observed from the world** (tool tapes, raw API responses, raw filings); **documents = a rebuildable derivative** with a stable key `(parent_evidence_id, logical_position)` independent of extractor version. `corpus_hash` covers evidence only (already true: `store.corpus_hash` hashes evidence rows), so rebuilding documents never drifts sealed scenarios. Rubrics label stable document keys, not re-mintable IDs.
3. Ranking is a deterministic pure function of the *eligible* corpus: versioned ranker string in every manifest, evidence-id tie-break, an index-state hash in the manifest as a drift tripwire, and pagination pinned to the page-1 corpus hash (page-2+ requests carry it; the retriever refuses on mismatch).
4. Replay makes zero network calls. Entity aliases come from a static table in the store (seeded from the universe file and captured fundamentals), never live yfinance - the existing `historical` bypass in `_run_graph` already establishes this convention.
5. One retrieval engine serves every consumer: the in-graph tools, the evidence brief, and the MCP surface. The existing `TemporalSearch` facade in `temporal/search.py` is subsumed by it, not left as a fourth path.

## Known defects the plan must clear, not inherit (audit findings)

- **bm25 lookahead leak (blocker):** FTS5 computes IDF and average document length over the entire index, not the `as_of`-eligible subset. Post-`as_of` documents silently reweight historical rankings, and the same query at the same `as_of` returns different results as the live store grows - while the manifest's corpus hash still claims "same world." Fix in R3: rank against an eligible-only materialized chunk index (cached per corpus-hash + as_of; sub-second at this scale), making ranking a true pure function.
- **Index rebuild on every store open:** `_initialize` runs `DELETE FROM evidence_fts` + full re-insert on every `TemporalStore()` construction (`store.py:200`). This is a write on open, races the launchd capture against concurrent replay processes, and scales with corpus size. R2 replaces it with `temporal-reindex` plus incremental maintenance in `record()`; store open becomes read-safe.
- **Search materializes full documents:** `store.search` json-parses entire `response_json` bodies (up to 12.8 MB SEC filings) to produce 1,500-char snippets. R2 projects normalized fields only; full bodies load only in `temporal_fetch`.
- **Syndication duplicates:** GDELT discovery yields many URL variants of one story and daily captures re-mint unchanged content; duplicate-filled top-k corrupts both metrics and agent context. Dedup (canonical-URL + title-similarity clustering, rank the cluster representative) belongs in the document layer, not a follow-up.

## R0 (immediate, no retrieval dependency): citation propagation

Trader and portfolio-manager prompts carry `[evidence:<id>]` citations from analyst reports into `final_trade_decision`, with one test asserting citations survive.
This un-nulls the citation-grounding metric for every future experiment and is a one-prompt-plus-one-test change.

## R1: offline retrieval benchmark (measure first)

Built against the *current* index so the present failure becomes the recorded baseline; R2/R3 must then show the delta.

- Benchmark format: per scenario, `{query, expected_document_keys, kind}` with two tiers. `known-item` queries are hand-written (the 10-Q, the $500B financing story). `topic` queries are seeded verbatim from the agent queries already recorded in `search_traces` during the first experiment - ecologically valid phrasing the ranker was not reverse-engineered from.
- Rubric partition: material IDs that are tool-surface evidence (price CSVs, fundamentals) are excluded from search targets - search only ranks documents, so including them caps recall structurally and poisons the gate.
- Metrics: per-query success@k tables (not one aggregate), recall@k with k >= 2x the target count, nDCG@k, MRR, and per-query miss listings for debuggability.
- CLI `temporal-retrieval-bench --store ... --bench <file>`; two tiers: a synthetic-corpus fixture asserting metric floors in CI, and the real-corpus bench run locally for tuning.

Gate: the bench runs in seconds on the live corpus and records the current near-zero rubric recall as the score to beat.

## R2: the document layer

- `documents` derivative table with stable keys per invariant 2: `{doc_key, parent_evidence_id, title, body, source_domain, published_at, available_at, doc_kind, extractor_version}`. Per-source extractors: GDELT (text is the title; domain from `metadata.article`), HN (title/body split on the existing `"title\n\nstory_text"` layout - the raw JSON is already segregated in `metadata.story`), SEC (strip SGML headers and encoded exhibits, keep text sections), Wayback (existing text derivative).
- Dedup clustering at build time (canonical URL + title similarity); cluster representative is ranked, siblings listed on the result.
- `document_chunks` FTS5 table over ~1,500-token chunks with overlap, using FTS5 external-content mode so large bodies are not stored twice; chunk rows are the future embedding unit (stable chunk_id, versioned chunker) so semantic ranking later is one added table.
- `temporal-reindex` CLI rebuilds documents + chunks idempotently; `record()` maintains them incrementally for new `corpus.document` evidence so the daily capture never leaves the index stale; the rebuild-on-open behavior is deleted.
- `store.search` switches to chunk-hit aggregation (best chunk per document) projecting normalized fields; ranker version bumps (two existing test assertions hardcode the old string and are updated with it).
- Migration: the one existing scenario's rubric is resealed onto document keys (cheap now; impossible later).

Gate: R1 bench shows the delta from R2 alone; reindex leaves every scenario's `verify_scenario_corpus` true; store open performs no writes.

## R3: eligible-corpus hybrid ranking

Entry condition (pulled forward from corpus ops): 2 to 3 additional sealed scenarios with rubrics on other tickers/windows, so tuning and gating use different scenarios.

- Eligible-only ranking per the blocker fix: materialize (and cache by corpus-hash + as_of) a chunk index over eligible documents; bm25 statistics computed on that subset; index-state hash recorded in every manifest.
- Hybrid, deterministic scoring tuned against the R1 bench: title-over-body field weights, ticker/alias filter-boosts from the static alias table, a fixed documented recency decay relative to `as_of`; ranker version `temporal-hybrid-v3`.
- Embeddings stay deferred - the observed failure was normalization and chunking, not lexical-vs-semantic, and at this scale the deferral is a determinism/simplicity call, not a cost call. They enter later as a versioned derivative behind the same interface only if the bench shows a lexical ceiling.

Gate: on the held-out scenario(s) never used during tuning - success@10 for a majority of known-item queries and material-document recall at k = 2x target count meaningfully above the R2 baseline; identical results across repeated runs against a frozen store; zero results differ when post-`as_of` documents are added (the leak test, now a permanent regression test).

## R4: a search surface shaped like real research (parallel with R3)

- `temporal_search(query, limit, page, date_from, date_to, source)` returning `(title, snippet, source, available_at, doc_key)` rows plus manifest; empty query with filters is defined as "list by recency within filters" (calendar-shaped browsing); pagination pinned per invariant 3.
- `temporal_fetch(doc_key, page)`: bounded ~4k-char sequential pages of the normalized body with provenance clocks; enforces `available_at <= as_of` itself; every fetch records a tool trace so deep reading counts as surfaced evidence.
- A trivial corpus-overview tool (source counts, date span at `as_of`) so agents calibrate what the store can answer.
- Both registered via `_configured_analyst_extra_tools` with the same live-mode guard; the prompt hint in `agent_utils.py` updated to describe search-then-fetch and the citation contract.
- Stated consequence: ranking/tool-shape changes invalidate `use_capture_tape` golden replay of previously sealed capture runs (byte-exact prompt verification). Evidence replay and all metrics are unaffected; new experiments seal new tapes. Accepted and documented, not silent.

Gate: an agent locates the NVDA 10-Q via search and reads its financials section via fetch, fully offline in replay.

## R5: one retriever, three consumers

- `TemporalRetriever` in `tradingagents/temporal/` owns query parsing, eligibility, ranking, pagination, manifests; `TemporalStore.search` delegates to it; the `TemporalSearch` facade is folded in.
- Evidence brief: `build_evidence_brief(store, ticker, as_of, k)` - a pure function assembling a deterministic top-k pack (titles, snippets, doc keys) from the same retriever. Injection point is `propagator.create_initial_state` (the seam shared by `propagate()` and the CLI path - injecting in `_run_graph` would miss CLI runs), behind `temporal.evidence_brief`, off by default.
- MCP server (`tradingagents temporal-mcp --store ... --scenario ...`) exposing search/fetch/overview scoped to one scenario over stdio, behind an `[mcp]` extra; server-side trace recording makes any external client a scoreable arm.

Gate: tool, brief, and MCP return identical results for identical queries; an MCP client run appears in the store with full traces.

## Follow-up plan A: mesh into TradingAgents (after R3)

1. Evidence-brief arm: paired experiment fixed-feeds vs brief injection (cheapest treatment: deterministic, no extra tool calls).
2. Agent-search arm rerun with the R3/R4 retriever (search-then-fetch), citation grounding now measurable thanks to R0.
Gate: non-null grounding numbers; per-arm metric differences attributable to retrieval policy.

## Follow-up plan B: external harness arm (after R5)

1. Headless runner launching the external harness (opencode first; MCP makes alternatives drop-in) with the scenario-scoped MCP config; final report recorded via `record_research_run`.
2. Four-arm benchmark - fixed feeds / brief / agent search / external harness - N repetitions over the sealed scenario set, run only after a concrete cost quote is approved.
Gate: a non-TradingAgents consumer uses the temporal core unchanged and is scored by the same rubric - the framework-neutral proof.

## Follow-up plan C: corpus operations (continuous)

1. The 2-3 extra scenario rubrics move up as R3's entry condition; further breadth (more windows/tickers) continues in parallel.
2. Weekly GDELT-to-Wayback body bridge retries (provider was rate-limiting) so headline discoveries gain readable bodies.
3. Launchd capture accrues; weekly error-log review (Reddit RSS 429s already visible). Note: per-search `corpus_hash` is O(evidence count) - acceptable now, flagged as the latency floor to revisit as the corpus grows.

## Order

R0 immediately; R1 → R2 → R3 sequential (measure, structure, rank); R4 parallel with R3; R5 after R3/R4; plans A/B/C trigger as gated above.
No new dependencies before the `[mcp]` extra; everything through R5 is SQLite + stdlib.

## Open decisions (none block this work)

1. Four-arm LLM benchmark budget - a concrete quote will be presented for approval at plan B step 2.
2. External harness choice - defaulting to opencode; swappable at plan B time.
3. Embeddings - deferred by design; revisited only with bench evidence of a lexical ceiling.
