# Portfolio Experiment Plan: Universe Research + CIO Allocation

Scope additions agreed 2026-08-19: X capture, external-harness sandboxing, new-scenario rubrics, and the portfolio-level experimental run.

## 1. X capture (operational, immediate)

The calgary poller runs locally: SQLite media store (`~/.tradingagents/media-poller.sqlite3`), `X_BEARER_TOKEN` from `.env`, hard budget caps (~$1.48/day, once-per-day X discipline), and `TRADINGAGENTS_POLLER_TEMPORAL_STORE` mirroring every terminal receipt into canonical temporal documents.
Deployment: a second launchd job running `python -m tradingagents.poller --once` on weekdays, offset from the 17:15 capture job so the store has one mutator at a time.
Gate: X posts appear as eligible temporal documents with correct availability clocks; budget receipts stay under the cap.

## 2. External-harness sandboxing (plan B precondition)

The MCP server enforces temporal honesty (sealed corpus, `as_of` filtering, server-side traces) but does not confine the harness.
An external arm (opencode) is valid only under all three layers:

1. Config lockdown: built-in web/fetch/shell tools disabled; MCP temporal tools only.
2. Egress enforcement: sandboxed process or container whose network allowlist is exactly the LLM provider API.
3. Transcript audit: any non-MCP retrieval call disqualifies the run.

Residual leak stays model weights (post-scenario knowledge in pretraining); handled by pinning the same model across all arms so contamination is constant.

## 3. Rubrics for the 2026-08-19 scenarios

First-pass labeling for `nvda/tsla/msft-2026-08-19` (same method as the originals: material = decision-relevant tool evidence + key documents; useful superset).
Until labeled, those scenarios support decision/stability comparison and golden replay but not coverage/grounding metrics.

## 4. The portfolio experimental run

### Why

Per-ticker ratings are unanchored: nothing prevents thirty simultaneous Overweights, nothing sizes positions, and per-ticker decision flips carry almost no statistical power.
A portfolio agent closes the loop - research becomes allocation, allocation becomes fills, fills become outcomes - and daily portfolio outcomes compound into evaluable return streams.
This also completes the long-standing roadmap item (CIO agent) and activates dormant merged infrastructure: `temporal/simulation.py` (PortfolioSimulator), calgary's `domain/portfolios.py`, `portfolio_backtest.py`, and `walkforward.py`.

### Daily loop shape (three stages)

1. **Universe sweep** - every universe ticker gets a trimmed research pass (analysts only, quick model, no debate) producing a compact brief and rating. Full debate on 30 names daily is $9-15; the sweep runs at roughly a quarter of that.
2. **Focus list** - a screener promotes the top 5-8 most actionable names (conviction, news volume, rating changes) to the full multi-agent debate graph.
3. **CIO agent** (new) - consumes all per-ticker outputs, the sealed portfolio state (positions, cash, prior targets), and explicit constraints (max position weight, gross exposure, turnover budget); emits a structured target portfolio: weights, orders, and per-position rationale carrying `[evidence:<id>]` citations. PortfolioSimulator executes against captured quotes and records fills.

Estimated daily cost: 30 sweep runs + 5-8 full graphs + 1 CIO call = **$3-6/day** at taped token rates.

### Temporal invariants (non-negotiable)

- The whole daily loop runs as one `live_capture` run: every tool call, document, and LLM call taped; the day seals as one replayable scenario.
- Portfolio state is a sealed scenario snapshot (like memory context): replay reads the seal, never ambient files. Day N+1's scenario references day N's sealed post-fill state.
- The CIO decision is scored two ways: trace metrics per ticker (coverage, grounding) and portfolio outcome metrics (return vs SPY, turnover, exposure discipline) once enough days accrue.

### What it does NOT replace

Plan A (does retrieval improve research quality) stays a per-ticker replay experiment on the existing sealed scenarios - it is ready now and needs no outcome data.
The portfolio loop is the standing daily experiment that *accumulates* outcome data; its A/B form (same CIO fed by baseline vs search-enabled research) becomes meaningful after weeks of fills, via walkforward iteration over the accumulated days.

### Build order

1. X poller launchd install (after the verification cycle completes).
2. Rubric labeling for the three new scenarios.
3. CIO agent + portfolio-state snapshot + simulator wiring + daily-loop CLI (`temporal-portfolio-run`), implemented as a fleet wave with the same integration discipline as R0-R5.
4. Scheduler extension: capture at 17:15, poller offset, portfolio run after capture.
5. Plan A experiment on existing scenarios - independent, can run any time on approval (~$10-25).
