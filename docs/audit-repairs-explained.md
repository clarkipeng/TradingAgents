# What changed, and why

These fixes are integrated in the London workspace and checked locally. Nothing has been deployed. The full audit remains in progress; [doing.md](../doing.md) is the current status, and [the fix plan](audit-fix-plan-2026-09-06.md) retains all 33 findings and C1–C4.

The most important example is a simulated trade. Previously, research could finish in the evening but be priced as though it had traded at an earlier closing price. Now the decision is saved first, then a separate job executes it using the next eligible exchange session’s raw opening data. Weekends, holidays, price gaps, fees, and held-position corporate actions have explicit handling. This is a versioned simulation policy; it is not evidence of a live broker fill or investment performance.

| Work completed locally | Why it matters |
| --- | --- |
| Separate saved decisions from next-session execution; connect the morning scheduler and CLI. | A saved decision actually reaches execution. A restart or duplicate invocation cannot apply the same decision twice. An expired session or changed starting portfolio fails visibly. |
| Use raw execution prices, preserve the research cutoff and actual response time, and label return policies and valuation dates. | Adjusted analyst prices cannot be mislabeled as raw fills. Later information cannot be silently backdated. Reports do not combine incompatible old and new return calculations. |
| Replay the actual two-ticker, four-analyst graph, CIO, and execution with external access disabled. Record missing-input outcomes too. | We can reproduce what the agents saw and did. Changing a report or price, or deleting a required recording, is detected. Missing input remains distinguishable from a broken recording. |
| Reserve model spending before calls, share the allowance across retries and workers, settle returned usage, and preserve uncertain spend. | A limit can stop the provider call before it costs money. An ambiguous failure cannot reset the allowance. The ledger is policy accounting, not a claim about the provider’s final bill. |
| Publish storage generations atomically; retain pending decisions and required recordings; back up the database, artifacts, and spending ledger together. | A failed rebuild cannot leave half-published indexes. Cleanup cannot remove an unfinished decision’s inputs. Restore recovers a complete unit rather than only the database file. |
| Reuse identical adjusted-history requests within one run, with one fetch shared by concurrent callers. Keep brief comparison offline. | Avoid duplicate provider work without changing the analytical price contract or reusing an old observation in a new run. The comparison tool measures frozen inputs; it does not enable unproven prompt compression. |
| Remove duplicate interpreter startup in deployment-test fakes and isolate a CLI fixture from shadow collection. | The original timeout and deployment assertions can pass without hiding a slow test behind a larger timeout or letting a unit test reach unrelated providers. |

The integration review found several of these connections missing from the worker deliveries. Those deliveries were corrected before acceptance; worker-reported passing helper tests were not treated as sufficient proof.

## Mistakes we keep making

The recurring issue is fixing a component while leaving the whole workflow unproven. A replay helper can pass while production replay fails; a backup writer can work while restore misses files; a decision can be saved while nothing schedules execution. The prevention is to test the user’s actual entrypoint and include its readers, restart path, and retained data in the change.

Another pattern is mixing facts with different meanings: publication versus observation time, research cutoff versus execution time, raw versus adjusted prices, and local tests versus operational proof. Preserve those distinctions explicitly instead of repairing individual exceptions later.

Optimizations also need to preserve their contract. A faster cache that changes data vintage, or a shorter prompt that loses useful evidence, is a behavior change requiring separate evidence. The rejected cache attempts and later corrections are why lower per-call model cost alone does not establish a cheaper project.

These lessons are already captured in [taste.md](../taste.md): prove the outcome at the real boundary, close changes at their consumers, preserve contracts when optimizing, and minimize total work including coordination and correction. The user’s existing edits to that file and AGENTS.md were preserved.

## Parallel work and wakeups

Sightmesh workers handled separate implementation slices; London reviewed and integrated their changes. All assigned implementation workers are currently idle. Their branches and handoffs are preserved. Idle-worker wakeups are suppressed while London owns the remaining work, so the timer does not repeatedly buy an unchanged status report. Six guard fixtures verify the wake behavior. There is no verified automatic push into this Conductor chat.

We have no reliable combined token or dollar total for implementation, management, correction, audits, and wakeups, so no numerical savings are claimed.

## Verification

The final service-independent suite passed **1,782 tests and 71 subtests**. The deployment-wrapper suite passed **102 tests** with its original timeouts. Repository-wide Ruff passes. One optional Bedrock dependency test was skipped; 14 service-backed tests were excluded. The combined source is committed through `d51e422`. These are local checks, not deployed-system evidence.

## Still open

Remaining work includes scheduler dependency and missed-job policy, stable retrieval-world membership and storage cleanup/restore proof, representative performance measurements, fresh model quality/cost comparisons before prompt changes, and operational cutover/rollback/capacity evidence. Existing historical seals still need individual validity assessments. Local tests do not close those gates.
