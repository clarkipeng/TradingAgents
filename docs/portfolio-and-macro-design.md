# Design: Portfolio-Level Management + Macro/Global News Coverage

**Status:** Draft / proposal — not yet implemented
**Author:** (origamiresearch)
**Date:** 2026-06-28

## Summary

Two related upgrades to TradingAgents:

1. **Macro & global-news coverage** — strengthen the news layer so macro-sensitive
   names (semiconductors like **NVDA**/**MU**, which trade on China export controls,
   tariffs, the Fed path, and AI-capex cycles) are analyzed against the global/political
   backdrop *and* their own ticker news — and so that macro context is computed **once
   per run** and shared, instead of being re-derived per ticker.

2. **Portfolio-level decisioning** — today the system analyzes **one ticker in isolation**
   (`TradingAgentsGraph.propagate(ticker, date)`). We want it to manage a **whole book**:
   analyze every held position, analyze a set of candidate tickers, then have a **meta-agent**
   weigh them against each other, the current holdings, cash, and risk limits, and emit
   **portfolio actions** (add / trim / open / close, with sizes).

These compose: the meta-agent reasons better when every ticker shares a consistent macro
view, and macro themes (e.g. "semi export ban") are precisely where cross-ticker
correlation matters (NVDA and MU move together on the same headline).

---

## Part A — Macro & Global News Coverage

### What exists today

The **News Analyst** ([news_analyst.py](../tradingagents/agents/analysts/news_analyst.py)) already
has a macro layer:

- `get_global_news` — macro headlines driven by a fixed query list in
  [default_config.py:116-122](../tradingagents/default_config.py#L116-L122)
  (Fed/inflation, S&P/GDP, geopolitical risk, central banks, oil/commodities).
- `get_macro_indicators` — FRED series ([macro_data_tools.py](../tradingagents/agents/utils/macro_data_tools.py)).
- `get_prediction_markets` — Polymarket implied probabilities for forward events.
- `get_news` — ticker-specific headlines; `get_insider_transactions`.
- New: [scripts/media_poller.py](../scripts/media_poller.py) accumulates ticker-keyed social +
  news-RSS history into SQLite for backtesting.

### Gaps

1. **Macro is generic and ticker-agnostic.** The global queries are the same for every run;
   a semiconductor name gets no extra weight on China/export-control/AI-capex news.
2. **No ticker→theme mapping.** Nothing tells the system that NVDA and MU are both
   *semiconductor + China-exposed + AI-capex* names, so their macro exposure is rediscovered
   (inconsistently) every run.
3. **Macro is re-fetched per ticker.** In a portfolio run that's N× the FRED/Polymarket/global
   calls for identical data, and N inconsistent macro narratives.
4. **News search is bare-ticker or cashtag only** (see the poller's query design note) — no
   "ticker OR company-name OR theme" expansion.

### Proposed changes

**A1. Macro theme registry** — a config-driven map of themes → (search queries, FRED series,
Polymarket topics). Example:

```python
MACRO_THEMES = {
  "semiconductors": {
    "queries": ["semiconductor export controls China", "CHIPS Act subsidy",
                "TSMC capacity", "AI accelerator demand"],
    "fred": ["INDPRO"], "polymarket": ["China Taiwan", "chip export ban"],
  },
  "rates":  {"queries": ["Federal Reserve rate decision", ...], "fred": ["FEDFUNDS","DGS10"]},
  "trade":  {"queries": ["tariffs trade war sanctions", ...]},
  "ai_capex": {"queries": ["hyperscaler capex data center buildout", ...]},
}
```

**A2. Ticker→theme sensitivity map** — declares which themes a ticker loads on, e.g.
`{"NVDA": ["semiconductors","ai_capex","trade"], "MU": ["semiconductors","trade","rates"]}`.
Start as a curated config; optionally let an LLM classify unknown tickers once and cache the
result (mirrors how `resolve_instrument_identity` caches ticker identity).

**A3. Shared macro brief.** Compute a single **macro context** per portfolio run (global news +
FRED + Polymarket for the union of active themes), summarized once, and inject it into every
ticker's state as a new `macro_context` field — exactly parallel to today's `instrument_context`
in [agent_states.py:50](../tradingagents/agents/utils/agent_states.py#L50). The per-ticker News
Analyst then only fetches **ticker-specific** news + that ticker's theme deltas, and reasons
against the shared brief. Cuts redundant calls and guarantees a consistent worldview across the book.

**A4. Query expansion.** News/social searches become `"$TICKER" OR "<CompanyName>" OR <theme terms>`
using the already-resolved instrument identity, improving recall on the macro-driven headlines that
rarely cashtag the stock.

---

## Part B — Portfolio-Level Management

### Today

`propagate(company_name, trade_date)` runs the full graph for **one** ticker and returns a
`PortfolioDecision` (Buy/Overweight/Hold/Underweight/Sell + sizing prose) — see
[trading_graph.py:321](../tradingagents/graph/trading_graph.py#L321). There is no notion of a book,
cash, existing positions, or cross-ticker tradeoffs.

> **Naming:** the existing `portfolio_manager` node
> ([portfolio_manager.py](../tradingagents/agents/managers/portfolio_manager.py)) makes the
> **per-ticker** final call. To avoid collision, the new book-level meta-agent in this design is the
> **Chief Investment Officer (CIO)** agent. The per-ticker `portfolio_manager` is unchanged.

### Target architecture

A new orchestrator, `PortfolioGraph.analyze_portfolio(holdings, candidates, trade_date, constraints)`,
that runs in three phases:

```
                         ┌─────────────────────────────────────┐
   Phase 0 (once)        │  Shared macro brief (A3)             │
                         │  global news + FRED + Polymarket     │
                         │  over union of active themes         │
                         └──────────────────┬──────────────────┘
                                            │ macro_context
        ┌───────────────────────────────────┼───────────────────────────────────┐
        ▼                                   ▼                                    ▼
   Phase 1: per-ticker analysis (REUSES the existing single-ticker graph, in parallel)
   ┌──────────────┐  ┌──────────────┐            ┌──────────────┐  ┌──────────────┐
   │ HELD: NVDA   │  │ HELD: MU     │   ...      │ CAND: AMD    │  │ CAND: TSM    │
   │ propagate()  │  │ propagate()  │            │ propagate()  │  │ propagate()  │
   │ → Decision   │  │ → Decision   │            │ → Decision   │  │ → Decision   │
   └──────┬───────┘  └──────┬───────┘            └──────┬───────┘  └──────┬───────┘
          └─────────────────┴──────────┬────────────────┴─────────────────┘
                                        ▼
   Phase 2: cross-ticker analysis (NEW)
   correlation / sector & factor concentration / shared-catalyst clustering
                                        ▼
   Phase 3: CIO meta-agent (NEW)
   inputs: all per-ticker PortfolioDecisions + reports, current holdings + cash,
           constraints (max position %, sector caps, cash floor, long-only?),
           cross-ticker analysis, shared macro brief, portfolio memory
   output: PortfolioPlan = ranked, sized, cash-aware actions across the whole book
```

**Why reuse `propagate` per ticker:** the per-ticker pipeline (analysts → bull/bear → research
manager → trader → risk debate → portfolio_manager) is already strong and well-tested. Phase 1 is
"fan out the existing graph over the universe"; only Phases 0/2/3 are new. Phase 1 parallelizes
cleanly (independent runs), bounded by API rate limits.

### New components

| Component | Role | Mirrors / reuses |
|---|---|---|
| `PortfolioGraph` (new) | Orchestrates phases 0–3 | wraps `TradingAgentsGraph` |
| Shared macro brief (A3) | One macro view per run | `resolve_instrument_context` pattern |
| `PortfolioState` (new) | Holds per-ticker decisions, holdings, cash, constraints, macro_context, cross-ticker stats | `AgentState` ([agent_states.py](../tradingagents/agents/utils/agent_states.py)) |
| Cross-ticker analyst (new) | Correlation/concentration/catalyst clustering | new analyst node |
| **CIO agent** (new) | Final book-level allocation | structurally like `portfolio_manager` but over the whole book |
| `PortfolioPlan` schema (new) | Structured actions | extends [schemas.py](../tradingagents/agents/schemas.py) `PortfolioDecision` |
| Portfolio memory (new) | Tracks **portfolio** return/alpha + reflections | extends `TradingMemoryLog` ([memory.py](../tradingagents/agents/utils/memory.py)) |

### Proposed schemas (sketch)

```python
class Holding(BaseModel):
    ticker: str
    shares: float
    cost_basis: Optional[float]

class PortfolioInput(BaseModel):
    holdings: list[Holding]
    cash: float
    candidates: list[str]              # tickers to consider adding
    constraints: PortfolioConstraints  # max_position_pct, sector_caps, cash_floor, long_only

class PortfolioAction(BaseModel):
    ticker: str
    action: Literal["open","add","trim","close","hold"]
    target_weight: float               # desired % of portfolio post-trade
    size_note: str
    rationale: str
    conviction: Literal["low","medium","high"]

class PortfolioPlan(BaseModel):
    actions: list[PortfolioAction]
    cash_after: float
    portfolio_rationale: str           # how macro + cross-ticker view shaped allocation
    key_risks: list[str]
    turnover_estimate: Optional[float]
```

---

## Backtesting implications

Ties directly into the media poller already built:

- **Portfolio returns, not per-ticker.** Extend the reflection layer
  ([reflection.py](../tradingagents/graph/reflection.py) / `_fetch_returns` in
  [trading_graph.py](../tradingagents/graph/trading_graph.py)) to weight each ticker's realized
  return by `target_weight` and compare the **book** vs benchmark (SPY).
- **Look-ahead safety extends to the book.** Each ticker's Phase-1 run already pins `trade_date`;
  the shared macro brief and cross-ticker stats must also use only data `<= trade_date`. The
  poller's `window()` slices are already look-ahead-safe.
- **Walk-forward loop.** A backtest drives `analyze_portfolio` over a date series, feeding the prior
  step's `PortfolioPlan` (post-trade holdings + cash) into the next — a true simulated book, vs.
  today's stateless single-decision runs.

---

## Phased implementation plan

1. **Macro brief (A3) + theme registry (A1).** Highest ROI, no portfolio plumbing needed; immediately
   improves single-ticker runs for macro names. Add `macro_context` to state + propagation.
2. **Ticker→theme map (A2) + query expansion (A4).**
3. **`PortfolioGraph` Phase 1** — fan `propagate` over a holdings+candidates list, collect decisions.
   (Usable on its own: "analyze my whole watchlist.")
4. **CIO agent + `PortfolioPlan` (Phase 3)** with simple equal/conviction weighting and constraints.
5. **Cross-ticker analyst (Phase 2)** — correlation/concentration to refine CIO allocation.
6. **Portfolio memory + walk-forward backtest** over poller data.

---

## Open decisions (need your call)

| # | Decision | Options | Default I'd pick |
|---|---|---|---|
| 1 | **Position sizing** | equal-weight / conviction-weighted / risk-parity | conviction-weighted, capped by constraints |
| 2 | **Constraints** | max position %, sector caps, cash floor, long-only vs short | long-only, 25% max position, 10% cash floor |
| 3 | **Candidate universe** | static watchlist / screener / news-driven discovery | static watchlist (config) to start |
| 4 | **Holdings + cash input** | manual JSON portfolio file / broker API | JSON file (no broker dependency yet) |
| 5 | **Rebalance cadence & costs** | every run / threshold-based; model transaction costs? | threshold-based + flat per-trade cost in backtest |
| 6 | **Theme map source** | curated config / LLM-classified + cached | curated for top names, LLM fallback cached |

---

## Out of scope (for now)

Live broker execution, intraday/real-time portfolio updates, options/derivatives, and tax-lot
optimization. This design produces a **proposed** `PortfolioPlan`; execution stays manual, consistent
with the project's research framing.
