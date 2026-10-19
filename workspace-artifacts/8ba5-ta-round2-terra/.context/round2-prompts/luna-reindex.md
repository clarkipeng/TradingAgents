# Stream B: generational document rebuild

Base: `fleet/round2` at `f039695`.

Read the system map and taste canon at `/Users/clarkpeng/conductor/workspaces/TradingAgents/london/.context/handoffs/fleet/` before editing.

Implement finding 4.

- Rebuild documents, chunks, and FTS into versioned shadow tables from a pinned evidence high-water mark in bounded transactions.
- Catch up the delta and swap generations in one short atomic transaction.
- Ingestion and readers must not wait for rebuild work; readers see complete generations only.
- Persist an active generation identifier usable in search traces.
- Adapt the existing equivalence tests to prove that post-swap assignments equal `reindex_documents` on the same corpus.
- Update the `temporal-reindex` CLI to use the generational path and clearly state in its summary that the 21:30 launchd job remains unloaded until the owner re-arms it.

Keep the smallest robust architecture and do not retain a legacy reindex path unless a live consumer requires it.

Do not write `~/.tradingagents`, run pollers, launchctl, deploys, `temporal-portfolio-run`, provider calls, or LLM calls.

Run affected tests and `python -m pytest -q`.

Commit with `type: what and why`, no co-author lines, and report commit SHA, changed files, test commands/results, and risks to the coordinator via `sightmesh parent --message`.
