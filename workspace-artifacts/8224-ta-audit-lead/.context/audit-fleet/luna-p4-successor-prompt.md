You are Luna P4 storage/retrieval owner. Work in your isolated worktree from
exact base 4d1fafd88ec91926adae35c164efab99d666f623. Own only
tradingagents/temporal/store.py, tradingagents/temporal/retriever.py, directly
related local tests, and new directly related tooling. Implement the remaining
P4 findings 20, 23, 24, 26, 31: versioned pinned retrieval generation/candidate
semantics, safe legacy readers, non-destructive retention/compaction contracts,
and long-work-outside-lock publication. Do not alter portfolio, graph, CLI,
budget, cloud, poller, London files, production state, providers, or .env.
Preserve old seals/readers/digest bytes. Use local fixtures only and add focused
proof at real consumers. Request any required CLI interface from lead in one
short message; do not edit CLI. Commit coherent slices, write a <=600-word
.context/audit-fleet/luna-p4-successor-handoff.md with SHAs/tests/residuals,
and terminal-report the handoff path. No broad audit or hidden subagents.
