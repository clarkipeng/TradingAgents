# Sol invariant review — `cdt/8224-ta-audit-lead`

Reviewed integrated head `78308b7`, focused on `0cc2963`, `84744bc`, `604b42e`, `13272c3`, `febc449`, `78eb030`, `a13e710`, and `3b86a5f`.

## Findings

1. **P0 — A healthy portfolio owner can be replaced before its allowed deadline.** `tradingagents/temporal/store.py:43,400-417` makes a claim stale after 3,600 seconds, while `tradingagents/portfolio_run.py:45,273-279` permits provider work for 7,200 seconds and never renews/rechecks the claim. Minimal counterexample: owner A is still researching at 60m01s; scheduler B claims the same day and starts the same paid sweep; A later loses the completion CAS. This violates single ownership and duplicates spend during an explicitly healthy run.

2. **P0 — Research calls bypass the new durable model budget.** `tradingagents/portfolio_run.py:260-263` attaches the ledger only to the parent context, but `tradingagents/portfolio_run.py:559-566` creates each research context without copying `model_budget`; the reservation hook is conditional on that field at `tradingagents/temporal_adapters/langchain.py:300-309`. Minimal counterexample: a one-ticker research graph makes any number of model calls under `worker_context`; every call sees `model_budget is None`, so the day ledger remains empty and neither token nor money ceiling can stop the dominant spend.

3. **P0 — Even budgeted calls reserve zero dollars.** `tradingagents/temporal_adapters/langchain.py:306-309` always passes `estimated_cost="0"`, although the day advertises a `$100` ceiling at `tradingagents/portfolio_run.py:49-50`. Minimal counterexample: 101 CIO/provider calls costing $1 each but fitting the token ceiling all reserve `$0` and pass the money check. The money invariant is therefore not enforced even on the parent stream.

4. **P1 — Replay accepts count-equivalent but different streams and omits failed research streams.** `tradingagents/portfolio_replay.py:18-26,44-50` seals stream digests, but `tradingagents/portfolio_replay.py:108-121` validates only callback-reported counts; additionally `tradingagents/portfolio_run.py:432-435` builds membership from successful `ratings`, not the requested ticker set or every started child stream. Minimal counterexamples: replay a different one-call tape and return the expected counts—it passes despite a digest mismatch; or let ticker A make one taped call then fail while ticker B succeeds—the sealed manifest contains only B, so “complete portfolio replay” never replays A's captured failure.

## Approved scope

Apart from the findings above, the focused clock, execution, state-completion CAS, verified-snapshot taping, analysts-only memory isolation, and temporal-store lookup/transaction changes preserve their stated invariants. The focused suite passed: `28 passed` via `uv run --frozen --extra dev pytest -q tests/test_portfolio_replay.py tests/test_model_budget.py tests/test_portfolio_run.py tests/test_temporal_core.py`.
