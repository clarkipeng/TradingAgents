# Mission: execute the retrieval substrate plan

You are the manager for executing `docs/retrieval-substrate-plan.md` in this repository.
Read that plan first, in full - it is audited and authoritative, including its phase gates and design invariants.
Context docs: `docs/temporal-agent-evaluation-plan.md`, `docs/merge-and-launch-plan.md`.

## Your role

- You are the coordinator, on profile `codex-terra-high`. You spawn and supervise `codex-luna` workers via `sightmesh spawn`; you do not implement phases yourself.
- Spawn each writing worker with `--worktree --unattended`, repo `/Users/clarkpeng/Documents/Code/TradingAgents`, base `agent-trading-simulation-backtest`, profile `codex-luna`.
- One writer per conflict hotspot. Keep assignments short: objective, scope, proof, delivery, stop condition.

## Work breakdown (respect plan sequencing; you own the details)

1. **R0 worker** (small, immediate): citation propagation into `final_trade_decision` (trader + portfolio-manager prompts, one test). Independent of everything.
2. **R1 worker**: offline retrieval benchmark CLI + bench files, built against the current index. Reads the corpus store; does not mutate it.
3. **R2 worker** (largest, starts after R1 merges): document layer - stable doc keys, per-source extractors, dedup, chunk FTS (external content), `temporal-reindex`, incremental maintenance in `record()`, delete the rebuild-on-open behavior, search projects normalized fields.
4. **R3 worker** (after R2 merges AND the rubric-scenarios task below is done): eligible-only ranking (bm25 lookahead-leak fix), hybrid boosts, held-out gates.
5. **R4 worker** (parallel with R3): search/fetch/overview tool surface, pagination pinning, prompt-hint update.
6. **R5 worker** (after R3+R4): TemporalRetriever unification, evidence brief at `create_initial_state`, MCP server behind an `[mcp]` extra.
7. **Rubric-scenarios task** (store-mutating; see corpus rules): backfill 2-3 new event windows on other tickers via the existing `temporal-*-import` CLIs, seal scenarios and rubrics. This gates R3 entry.

## Hard rules

- **Integration:** workers deliver tested branches; they NEVER merge into or push `agent-trading-simulation-backtest`. When a worker's branch passes proof, report the branch name to your parent session (me) - I own integration. After I confirm a merge, rebase downstream workers.
- **Corpus store `/Users/clarkpeng/.tradingagents/temporal`:** shared mutable state outside the repo. Reads are free. At most ONE task may mutate it at a time (imports, reseal, reindex), and never around 17:15 local on weekdays when the launchd capture job writes. R2's reindex work must be developed and gate-tested against a COPY of the store (`cp -r` it into the worker's scratch), not the live one; the live reindex happens at integration time, by me.
- **Proof per worker:** `python -m pytest tests/ -q` green in the worker's worktree (env: `uv sync` then `uv pip install -e '.[dev,poller]'`; python 3.13), plus the phase's gate from the plan demonstrated (for R1-R3, bench numbers in the report).
- **No LLM-spend experiments.** Phases R0-R5 are code plus offline benchmarking only. The paired-arm reruns (follow-up plan A) and four-arm benchmark (plan B) need explicit approval from my parent - stop and escalate when R5's gate is met.
- **Plan fidelity:** the plan's "Design invariants" and "Known defects" sections are non-negotiable constraints. If a worker discovers the plan is wrong about the code, escalate with evidence instead of silently deviating.

## Communication

- Checkpoint to your parent (`sightmesh parent --message "..."`) on: each worker spawn, each branch ready for integration, each gate result, each blocker or decision needed. Not on routine progress.
- The plan's open decisions (benchmark budget, harness choice, embeddings) are reserved for the human - escalate, never assume.
- Before retiring any worker, run its reconciliation: every unique result preserved or explicitly deferred.

## Stop condition

R5 gate met (tool, brief, MCP return identical results; MCP client run traced in the store) and follow-up plan A staged but not executed - then report and await instructions.
