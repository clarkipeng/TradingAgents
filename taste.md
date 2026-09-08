# Engineering taste

Updated: 2026-09-06.
This is the repository's short review standard, distilled from the audit and the existing cross-repo engineering taste canon.
Detailed remediation belongs in [the audit fix plan](docs/audit-fix-plan-2026-09-06.md), not in an expanding rulebook here.

- **Name the invariant before the patch.** Prefer one rule that makes the whole class of mistakes impossible. A longer timeout does not establish ownership; a day owner that must still hold its claim before spending and committing does.
- **Prove the user's outcome at the real boundary.** Recorded calls are not proof of replay. Replay the production day through its actual entrypoint with external access disabled. A backup is proven by restoring it with its artifacts. For example, test that a denied model invocation makes zero provider calls.
- **Keep one authority for each fact.** Derive CLI exits, alerts, reports, and `doing.md` from durable outcomes. Keep historical commentary out of current status. Distinguish implemented, locally verified, deployed, and operationally verified.
- **Separate reality from simulation.** Observation time, publication time, decision cutoff, and execution time have different meanings. A historical run cannot stamp today's live response with yesterday's clock. Replayability alone does not prove a valid backtest.
- **Keep immutable evidence; version its interpretations.** Indexes, clusters, prompts, and execution policies can evolve. Existing seals retain their original meaning and required inputs. Delete obsolete code after checking readers; delete data only after proving it is unreferenced or safely archived.
- **Own the whole lifecycle.** The owner starts, supervises, cancels, reaps, and recovers its work. Stopping a scheduler includes its persistent configuration. A paid retry keeps the original spend ledger, even on a copied store.
- **Distinguish unavailable input from broken execution.** Validate external data once at its boundary. Tolerate optional missing fields without inventing facts; isolate a bad item without losing healthy ones. Missing tapes, exhausted budgets, lost ownership, and corrupt state must never become successful fallback decisions.
- **Preserve the contract when optimizing.** A cache key names every input that changes the answer, including its vintage. A performance change proves equivalent results; a change to model-visible information gets a version and a quality comparison.
- **Keep slow work outside shared locks.** Fetch and compute first, validate again, then publish in a short transaction. Reuse existing types and boundaries; add a service or abstraction only when an exercised caller needs it.
- **Measure the workload that will run.** Check the real entrypoint at representative corpus size and concurrency. Account for CPU, memory, lock time, temporary disk, and provider cost together. A per-call saving is not automatically a wall-clock saving.
- **Make failure cheap and visible.** Reserve cost before a paid request, retry the failed stage within that reservation policy, and report fixed error codes. Debug with stored evidence and isolated probes before buying another run. Never expose secrets in diagnostics.
- **Close the change at its consumers.** A filename, schema, date policy, or default changes the producer, reader, migration, verification, and owning doc together. Run focused checks, then the required integration gate once; repeat only for a new change or unresolved failure.

For a substantive change, the review should answer three questions: What invariant changes? What observation could disprove the fix? Which consumers and retained records depend on the old behavior?
Keep the answers in the change description with the relevant evidence, rather than creating another checklist system.

The cross-repo source read for this distillation was `~/.claude/engineering-taste.md` (2026-08-31).
This portable repository copy adds the TradingAgents lessons; it does not change global agent settings.
