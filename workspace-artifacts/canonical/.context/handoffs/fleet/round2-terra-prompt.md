# Assignment: fleet round two - portfolio correctness and scale (terra)

Objective: implement the accepted audit findings as committed work based on `agent-trading-simulation-backtest` at f039695. You coordinate; luna workers implement; the owner gates the final merge.

Read first, in this directory: `audit-report.md` (findings 1, 2, 4, 5, 6, 8, 10 are the scope; their "smallest robust fix" paragraphs are the design), `system-map.md` (guardrails are law), `taste-canon.md` (review standard).

Spawn three luna workers (`--profile codex-luna --worktree --unattended`); stream A is one worker doing two stages sequentially, streams B and C run in parallel with A:

## Stream A - `luna-day`: atomic day lifecycle, then the fast sweep (findings 1, 2, 5, 6)
Stage 1: give a portfolio day one owner with an atomic claim -> complete lifecycle.
- A durable day record (new evidence tool or table per the finding's prescription) with a unique day identity and states claimed/completed/failed; claiming is atomic so two invocations can never both run.
- Portfolio state, scenario seal, and research-run record commit together at completion; a crash leaves a failed claim that is explicitly resumable and never becomes prior state.
- `portfolio_state_asof` and `portfolio_report` read only completed days.
Stage 2: the sweep contract.
- Research becomes analysts-only (no debate or risk phases) producing a validated compact brief per ticker; the CIO stays the sole cross-sectional judge.
- Bounded parallelism (4 workers, each with its own graph instance and tape identity; do not share a `TradingAgentsGraph`).
- A checked-in run policy: wall-clock deadline, per-day research-call ceiling (already present), minimum ticker coverage to seal (default 0.8), complete quote coverage for held symbols; breach ends the day failed-unsealed with a sanitized fixed-vocabulary outcome.
- The day summary reports coverage, spend proxy (call counts), and elapsed seconds as first-class fields.

## Stream B - `luna-reindex`: generational document rebuild (finding 4)
- Rebuild documents, chunks, and FTS into versioned shadow tables from a pinned evidence high-water mark in bounded transactions, catch up the delta, then swap generations in one short atomic transaction.
- Ingestion and readers never wait on the rebuild; readers see only complete generations.
- Record the active generation identifier so search traces can pin it.
- Prove equivalence: post-swap assignments equal the current full `reindex_documents` output on the same corpus (adapt the existing equivalence tests).
- Update the CLI `temporal-reindex` to use the generational path and note in the summary that the 21:30 launchd job stays unloaded until the owner re-arms it.

## Stream C - `luna-honesty`: tape latch and clocks (findings 8, 10)
- `LangChainTapeRecorder`: any persistence failure latches the active temporal run invalid; a latched run cannot seal its scenario and surfaces one sanitized error class. TDD the latch through the portfolio day path.
- GDELT importer: keep the real fetch receipt as `observed_at`, name `seendate` a provider-availability estimate in metadata, and leave `available_at` policy explicit per the finding.

Merge discipline, unchanged from round one and non-negotiable: review each diff against the taste canon, run `python -m pytest -q` at every integration point, integrate one stream at a time into branch `fleet/round2` in your worktree, never push the base branch, commit style `type: what and why` with no co-author lines. Round-one lesson: you shipped a constant that would have bricked the scheduled day (cap 25 below planned 31) and missed audit finding 7 - re-read each finding's failure scenario against your final diff before calling a stream done.

Deliver: branch `fleet/round2`, plus `round2-terra-summary.md` in this directory with per-stream outcomes, deferred items with reasons, and exact verification commands. Message your parent when integration completes or when blocked more than 30 minutes.

Hard guardrails: never write `~/.tradingagents/*`; never run pollers, launchctl, deploys, or `temporal-portfolio-run`; never spend provider or LLM budgets; tests use tmp dirs only.
