# Portfolio TradingAgents: Macro-Grounded, Cross-Asset Multi-Agent LLM Trading at the Book Level

**Author:** (origamiresearch) — placeholder

**Affiliation:** placeholder

**Preprint — draft. Built on TradingAgents (Xiao et al., 2025; arXiv:2412.20138).**

---

## Abstract

TradingAgents (Xiao et al., 2025) decomposes equity analysis into a team of
LLM agents — fundamental, sentiment, news, and technical analysts; a bull/bear
research debate; a research manager; a trader; a risk-management debate; and a
portfolio manager — and produces a single rating for a single ticker on a single
date. While effective as a per-name analyst, this design has two structural
blind spots. First, it analyzes each ticker *in isolation*: it has no notion of a
held book, of cash, or of the cross-asset tradeoffs that determine real
allocation decisions. Second, its macro layer is generic and re-derived
independently for every ticker, so macro-sensitive names — semiconductors such
as NVDA and MU, which co-move on shared catalysts like China export controls,
tariffs, the Fed path, and the AI-capex cycle — are assessed against
*inconsistent* and *redundant* worldviews.

We propose **Portfolio TradingAgents**, an extension that addresses both gaps
while preserving the original per-ticker pipeline intact. The first contribution
is *macro theme grounding*: a configurable theme registry maps macro themes to
news queries, FRED series, and prediction-market topics, and a ticker→theme
sensitivity map declares which themes each name loads on. A **shared macro
brief** is computed *once per run* over the union of active themes and injected
into every ticker's state as a `macro_context` field, guaranteeing a consistent
worldview across the book at a fraction of the data-fetch cost. The second
contribution is a *portfolio graph* that runs in four phases: a Phase-0 macro
brief, a Phase-1 fan-out that reuses the existing single-ticker graph over
holdings and candidates, a Phase-2 cross-ticker analysis (correlation,
concentration, shared-catalyst clustering), and a Phase-3 **Chief Investment
Officer (CIO)** meta-agent that emits a sized, cash-aware `PortfolioPlan` subject
to explicit constraints. A portfolio-level memory tracks *book* return and alpha
versus a benchmark and reflects on allocation decisions, rather than per-ticker
calls. We describe the method in full and lay out a planned walk-forward
backtest protocol built on an already-deployed media-collection pipeline. We do
**not** report empirical returns: all evaluation is described as forthcoming,
and the results tables are left as templates to be populated under a fixed,
look-ahead-safe protocol.

---

## 1. Introduction

Large language models have made it practical to build *agentic* analysts that
read filings, news, social chatter, and price action and reason toward a trading
view in natural language. TradingAgents (Xiao et al., 2025) is a prominent
instance of this idea: it mirrors the division of labor inside a trading firm,
assigning specialized roles to distinct LLM agents and orchestrating them as a
graph. Its central call, `propagate(ticker, date)`, runs the full pipeline for a
single instrument on a single trade date and returns a five-tier rating (Buy /
Overweight / Hold / Underweight / Sell) with supporting prose.

This single-ticker framing is also its principal limitation. A practitioner does
not hold one stock; they hold a *book*. The decision that matters is not "is NVDA
a Buy?" in the abstract but "given that I already hold NVDA and MU, have a 10%
cash floor, a 25% single-position cap, and a candidate list of AMD and TSM,
*what should I do across all of them this week*?" Answering that requires
reasoning about cash, existing positions, position sizing, and — critically —
the *interactions* between names. Two independently strong "Buy" calls on NVDA
and MU may, taken together, constitute an over-concentrated bet on a single
catalyst (a semiconductor export ban) that no per-ticker analysis can see.

The problem is sharpest precisely for macro-sensitive names. Semiconductors
trade on a shared backdrop: export-control policy, tariffs, the rate path, and
the hyperscaler capital-expenditure cycle. When a single headline about chip
export restrictions hits, NVDA and MU move together. A system that analyzes each
name separately (a) rediscovers this macro backdrop independently for each
ticker, wasting identical data calls and producing N subtly different macro
narratives, and (b) has no place to register that the resulting positions are
correlated. The first is an *efficiency and consistency* failure; the second is
a *portfolio-construction* failure. They are linked: cross-ticker reasoning is
only trustworthy when every ticker was analyzed against the same macro view.

This paper contributes:

1. **Macro theme grounding (Section 3.2).** A theme registry and a ticker→theme
   sensitivity map that turn the generic, ticker-agnostic macro layer into a
   targeted one — a semiconductor name is explicitly weighted toward
   export-control, trade, and AI-capex signals — plus query expansion that
   searches on company name and theme terms, not just the bare cashtag.

2. **A shared macro brief (Section 3.3).** A single macro context computed once
   per run and injected into every ticker, parallel to the existing
   deterministic `instrument_context`. This makes the macro worldview consistent
   across the book and removes redundant data fetches.

3. **A portfolio graph with a CIO meta-agent (Section 3.4).** A four-phase
   orchestrator that reuses the proven single-ticker graph as its Phase-1
   workhorse, adds a cross-ticker analysis phase, and culminates in a book-level
   meta-agent that emits a sized, constrained `PortfolioPlan`.

4. **Portfolio memory and reflection (Section 3.5).** A memory that tracks book
   return and alpha and reflects on allocation, extending the per-ticker
   reflection loop to the book.

5. **A planned, honest evaluation (Section 4).** A walk-forward backtest protocol
   on look-ahead-safe data, with metrics, baselines, and explicitly *empty*
   results templates. We fabricate no returns.

Throughout, we keep the original per-ticker pipeline unchanged; the contributions
are additive layers. This is a deliberate design stance — it lets the new system
inherit the analyst quality of the existing one and isolates the new components
for ablation.

---

## 2. Related Work

**LLM agents for finance.** A growing line of work applies LLMs to financial
reasoning, from sentiment extraction and event detection to end-to-end trading
agents (e.g., [Author, 2023]; [Author, 2024]). These systems demonstrate that
LLMs can synthesize heterogeneous textual signals — filings, news, social posts —
into directional views, but most operate at the level of a single instrument and
a single decision.

**Multi-agent debate and role specialization.** Decomposing a task across
multiple LLM agents that critique and refine one another's outputs improves
reasoning quality relative to a single monolithic prompt ([Author, 2023];
[Author, 2024]). Adversarial structures — a proposer and a critic, or a bull and
a bear — surface considerations that a single agent tends to omit. TradingAgents
adopts this pattern in two places: a bull/bear research debate and a
risk-management debate among aggressive, conservative, and neutral agents.

**TradingAgents.** Xiao et al. (2025; arXiv:2412.20138) introduced the
multi-agent trading framework this work extends. Its pipeline runs four analysts
(fundamentals, sentiment, news, technical), a bull/bear researcher debate
adjudicated by a research manager, a trader that proposes a transaction, a
three-way risk debate, and a portfolio manager that issues the final per-ticker
rating. The framework grounds price and identity claims deterministically and
supports a reflection loop that injects realized-return lessons into later runs.
Our work treats this pipeline as a fixed subroutine and builds book-level
structure around it.

**Portfolio optimization.** Classical portfolio construction — mean–variance
optimization ([Markowitz, 1952]), risk-parity allocation ([Author, year]), and
factor-based concentration control — provides the formal language for turning a
set of views into weights subject to constraints. We do not replace these methods;
rather, the CIO meta-agent reasons over the same primitives (target weights, a
cash floor, position and sector caps) in natural language, optionally informed by
cross-ticker correlation statistics. Bridging LLM-generated views and quantitative
allocation is an open area ([Author, year]); our contribution is a concrete,
constraint-aware, auditable instantiation within an agentic framework.

**Macro and event grounding.** Tying equity views to macro state — rates,
inflation, policy, and prediction-market-implied event probabilities — is standard
in discretionary research. TradingAgents already exposes FRED indicators
(`get_macro_indicators`), global-news search (`get_global_news`), and Polymarket
probabilities (`get_prediction_markets`) to its news analyst. Our theme registry
makes this macro layer *ticker-aware* and *shared*, which to our knowledge has
not been done in prior agentic-trading work.

*(Citations above are placeholders in [Author, Year] form except for the
real TradingAgents reference; see References.)*

---

## 3. Method

### 3.1 Background: the single-ticker TradingAgents pipeline

We briefly recap the pipeline we extend; see Xiao et al. (2025) for the full
description. A run is parameterized by a ticker $c$ and a trade date $t$. Before
any agent executes, the ticker's identity is resolved deterministically into an
`instrument_context` string and pinned into the shared `AgentState`
(`agent_states.py`). The graph then executes, conceptually:

1. **Analyst team** — four analysts populate the state:
   *Fundamentals* (financials, valuation), *Sentiment* (StockTwits / Reddit /
   news mood, emitted as a structured `SentimentReport` with a band and a 0–10
   score), *News* (global news plus FRED macro indicators and prediction-market
   probabilities), and *Technical* (indicators such as MACD/RSI).

2. **Researcher debate** — a bull and a bear researcher argue over the analyst
   reports across a configurable number of rounds; a **research manager**
   adjudicates and emits a structured `ResearchPlan` (a five-tier recommendation
   plus rationale and strategic actions).

3. **Trader** — turns the plan into a `TraderProposal` (Buy/Hold/Sell, with
   optional entry, stop-loss, and sizing prose).

4. **Risk debate** — aggressive, conservative, and neutral risk agents debate the
   proposal.

5. **Portfolio manager** — issues the final per-ticker `PortfolioDecision`
   (a `PortfolioRating`: Buy / Overweight / Hold / Underweight / Sell, with an
   executive summary, investment thesis, and optional price target / horizon).

After the run, a reflection layer fetches the realized raw and alpha return over
a holding window (alpha measured against a market benchmark, SPY by default;
`_fetch_returns` in `trading_graph.py`), writes a one-paragraph reflection to a
memory log, and injects recent same-ticker decisions and cross-ticker lessons
into the next run's portfolio-manager prompt via the `past_context` field.

Formally, the pipeline is a function

$$
\textsc{Propagate}(c, t)\ \longrightarrow\ d_c = (\text{rating}_c,\ \text{thesis}_c,\ \ldots),
$$

a per-ticker `PortfolioDecision` $d_c$. The rest of this section builds macro
grounding and a book-level orchestrator around this function.

### 3.2 Macro theme grounding

The existing macro layer issues a *fixed* global-news query list and the same
FRED/Polymarket calls for every ticker. This is both generic (a semiconductor
name gets no extra weight on export-control news) and redundant in a portfolio
setting (N identical macro fetches, N inconsistent macro narratives). We
introduce two configuration artifacts.

**Theme registry.** A map $\mathcal{R}$ from a macro theme $\theta$ to the data
sources that define it: a set of news queries, a set of FRED series, and a set of
prediction-market topics.

```python
MACRO_THEMES = {
  "semiconductors": {
    "queries": ["semiconductor export controls China", "CHIPS Act subsidy",
                "TSMC capacity", "AI accelerator demand"],
    "fred": ["INDPRO"],
    "polymarket": ["China Taiwan", "chip export ban"],
  },
  "rates":    {"queries": ["Federal Reserve rate decision", ...],
               "fred": ["FEDFUNDS", "DGS10"]},
  "trade":    {"queries": ["tariffs trade war sanctions", ...]},
  "ai_capex": {"queries": ["hyperscaler capex data center buildout", ...]},
}
```

**Ticker→theme sensitivity map.** A map $\sigma$ from a ticker $c$ to the themes
it loads on:

$$
\sigma(\text{NVDA}) = \{\text{semiconductors},\,\text{ai\_capex},\,\text{trade}\},
\qquad
\sigma(\text{MU}) = \{\text{semiconductors},\,\text{trade},\,\text{rates}\}.
$$

The map starts as a curated config for the most common names; for an unknown
ticker, an LLM classifies it once against the registry and the result is cached —
mirroring how the framework already caches deterministic instrument identity.

**Active theme set.** For a universe $U$ (defined in 3.4), the *active* themes are
the union over the universe:

$$
\Theta(U) = \bigcup_{c \in U} \sigma(c).
$$

This is the precise set of macro topics the book is exposed to, and the only set
the shared brief needs to gather.

**Query expansion.** Per-ticker news and social searches are widened from the
bare cashtag to

$$
q(c) = \texttt{"\$}c\texttt{"} \ \lor\ \texttt{"<company name>"} \ \lor\ \langle\text{theme terms for } \sigma(c)\rangle,
$$

using the already-resolved instrument identity. Macro-driven headlines rarely
cashtag the stock; expansion materially improves recall on exactly the catalysts
that move these names.

### 3.3 Shared macro brief

Rather than re-derive macro state per ticker, we compute it **once per run**. For
universe $U$ on date $t$, gather global news, FRED series, and prediction-market
probabilities over the active themes $\Theta(U)$, all restricted to data with
timestamp $\le t$, and summarize them into a single brief:

$$
M_t \;=\; \textsc{MacroBrief}\big(\Theta(U),\, t\big).
$$

$M_t$ is injected into every ticker's `AgentState` as a new `macro_context`
field — exactly parallel to the existing `instrument_context`. The per-ticker
news analyst then fetches only *ticker-specific* news plus that ticker's
*theme deltas*, and reasons against the shared brief instead of rebuilding the
macro picture from scratch.

Two arguments motivate this design:

- **Consistency.** Every ticker is analyzed against an identical macro worldview.
  This is a precondition for trustworthy cross-ticker reasoning in Phase 2: a
  correlation claim between NVDA and MU is meaningful only if both were assessed
  against the same export-control narrative.

- **Efficiency.** A naive portfolio run re-issues the macro fetches $|U|$ times.
  Sharing the brief reduces the macro data cost from $O(|U|)$ to $O(1)$ — the
  expensive FRED/Polymarket/global-news calls are made once, regardless of book
  size — leaving only the (necessarily per-ticker) instrument-specific fetches to
  scale with the universe.

### 3.4 Portfolio graph

We introduce an orchestrator
`PortfolioGraph.analyze_portfolio(holdings, candidates, trade_date, constraints)`
that wraps the existing `TradingAgentsGraph`. Define the **universe** as the union
of currently held names and candidate names under consideration:

$$
U \;=\; H \cup C,\qquad
H = \{\text{held tickers}\},\quad C = \{\text{candidate tickers}\}.
$$

A book state carries holdings, available cash $K$, and constraints. The graph runs
in four phases.

```
                         +-------------------------------------+
   Phase 0 (once)        |  Shared macro brief  M_t  (3.3)     |
                         |  global news + FRED + Polymarket    |
                         |  over active themes  Theta(U)       |
                         +------------------+------------------+
                                            | macro_context (M_t)
        +-----------------------------------+-----------------------------------+
        v                                   v                                   v
   Phase 1: per-ticker analysis  (REUSES the single-ticker graph; fan out, parallel)
   +--------------+  +--------------+        +--------------+  +--------------+
   | HELD: NVDA   |  | HELD: MU     |  ...   | CAND: AMD    |  | CAND: TSM    |
   | Propagate()  |  | Propagate()  |        | Propagate()  |  | Propagate()  |
   |  -> d_NVDA   |  |  -> d_MU     |        |  -> d_AMD    |  |  -> d_TSM    |
   +------+-------+  +------+-------+        +------+-------+  +------+-------+
          +-----------------+-------------+----------+-----------------+
                                          v
   Phase 2: cross-ticker analysis  (NEW)
   correlation  C  |  sector / factor concentration  |  shared-catalyst clustering
                                          v
   Phase 3: CIO meta-agent  (NEW)
   inputs:  { d_c }_{c in U},  holdings H,  cash K,  constraints,
            cross-ticker stats,  macro brief M_t,  portfolio memory
   output:  PortfolioPlan  =  ranked, sized, cash-aware actions over the whole book
```

**Phase 0 — Macro brief.** Compute $M_t$ as in 3.3.

**Phase 1 — Per-ticker analysis (reuse).** For each $c \in U$, run the existing
pipeline with the shared brief injected:

$$
d_c \;=\; \textsc{Propagate}(c, t \mid M_t),\qquad c \in U.
$$

This is the design's leverage point: Phase 1 is simply *fan out the proven
single-ticker graph over the universe*. The runs are independent and parallelize
cleanly, bounded only by API rate limits. Only Phases 0, 2, and 3 are new code;
the analyst→debate→trader→risk→PM machinery is untouched and reused verbatim.

**Phase 2 — Cross-ticker analysis (new).** A new analyst node consumes the
per-ticker reports and computes book-level structure that no single-ticker run can
see: pairwise return **correlation** $\mathbf{C} = [\rho_{ij}]$ over a trailing
window (data $\le t$), **sector/factor concentration**, and **shared-catalyst
clustering** — grouping names that load on the same active theme (e.g., NVDA and
MU both on `semiconductors`). The output is a compact statistical and narrative
summary that flags hidden concentration (e.g., "two independent Buys constitute a
single 40% bet on export-control policy").

**Phase 3 — CIO meta-agent (new).** A book-level meta-agent — the **Chief
Investment Officer (CIO)** — produces the final allocation. (We name it CIO to
avoid colliding with the existing per-ticker `portfolio_manager` node, which is
unchanged; the CIO is structurally analogous but reasons over the whole book.)
Its inputs are the per-ticker decisions $\{d_c\}_{c\in U}$ and their reports, the
current holdings $H$ and cash $K$, the constraints, the Phase-2 cross-ticker
statistics, the shared brief $M_t$, and portfolio memory. Its output is a
`PortfolioPlan`.

**Allocation objective and constraints.** Let $w_i$ be the post-trade target
weight of ticker $i$, $v_i \in [-1,1]$ a conviction-scaled view derived from
$d_i$ (rating and stated conviction), and $\hat r_i$ the expected excess return
implied by $d_i$. The CIO is asked to choose weights that trade off view-weighted
return against concentration risk,

$$
\max_{\mathbf{w}}\quad \sum_{i \in U} v_i\,\hat r_i\, w_i \;-\; \lambda\, \mathbf{w}^\top \mathbf{C}\, \mathbf{w},
$$

subject to the explicit book constraints:

$$
\begin{aligned}
&\textstyle\sum_{i} w_i \;+\; \frac{K_{\text{after}}}{V} \;=\; 1
   &&\text{(fully accounted: positions + cash)}\\[2pt]
&0 \le w_i \le w_{\max}
   &&\text{(long-only, single-position cap)}\\[2pt]
&\textstyle\sum_{i \in s} w_i \;\le\; \kappa_s,\ \ \forall\, \text{sectors } s
   &&\text{(sector caps)}\\[2pt]
&\frac{K_{\text{after}}}{V} \;\ge\; \phi
   &&\text{(cash floor)}
\end{aligned}
$$

where $V$ is total book value, $w_{\max}$ the max position fraction (default
25%), $\kappa_s$ the per-sector cap, and $\phi$ the cash floor (default 10%). The
risk-aversion term $\lambda\,\mathbf{w}^\top\mathbf{C}\,\mathbf{w}$ penalizes the
correlated-concentration that Phase 2 surfaces. The CIO does not solve this as a
numerical program; it reasons over these primitives in natural language and emits
target weights, with the cross-ticker statistics and constraints in its prompt.
This keeps the allocation *auditable* (every weight comes with a rationale) while
respecting the same structure a quantitative optimizer would. Default sizing is
conviction-weighted and capped by the constraints; equal-weight and risk-parity
are configurable alternatives.

**`PortfolioPlan` / `PortfolioAction` schema.** The plan extends the existing
`PortfolioDecision` to the book level (proposed Pydantic sketch):

```python
class Holding(BaseModel):
    ticker: str
    shares: float
    cost_basis: Optional[float]

class PortfolioConstraints(BaseModel):
    max_position_pct: float      # w_max, e.g. 0.25
    sector_caps: dict[str, float] # kappa_s
    cash_floor: float            # phi, e.g. 0.10
    long_only: bool

class PortfolioInput(BaseModel):
    holdings: list[Holding]
    cash: float                  # K
    candidates: list[str]        # tickers to consider adding
    constraints: PortfolioConstraints

class PortfolioAction(BaseModel):
    ticker: str
    action: Literal["open", "add", "trim", "close", "hold"]
    target_weight: float         # w_i: desired % of book post-trade
    size_note: str
    rationale: str
    conviction: Literal["low", "medium", "high"]

class PortfolioPlan(BaseModel):
    actions: list[PortfolioAction]
    cash_after: float            # K_after
    portfolio_rationale: str     # how macro + cross-ticker view shaped allocation
    key_risks: list[str]
    turnover_estimate: Optional[float]
```

A new `PortfolioState` (mirroring `AgentState`) carries the per-ticker decisions,
holdings, cash, constraints, `macro_context`, and cross-ticker statistics through
the phases.

### 3.5 Portfolio memory and reflection

The per-ticker reflection loop fetches each name's realized raw and alpha return
and writes lessons keyed by ticker. We lift this to the book. Given a
`PortfolioPlan` executed at $t$ with target weights $w_i$, the realized book
return over the holding window is

$$
R^{\text{book}}_{t \to t+h} \;=\; \sum_{i \in U} w_i \, r_{i,\,t \to t+h},
$$

and book alpha is measured against the benchmark $B$ (SPY by default):

$$
\alpha^{\text{book}} \;=\; R^{\text{book}}_{t \to t+h} \;-\; r_{B,\,t \to t+h}.
$$

The reflection layer (`reflection.py` / `_fetch_returns`) is extended to
weight each ticker's realized return by its `target_weight` and to compare the
*book* against the benchmark rather than reflecting on isolated calls. A new
**portfolio memory** (extending the existing `TradingMemoryLog`) stores book
return, book alpha, the executed plan, and a one-paragraph reflection on the
*allocation* decision — e.g., "the export-control thesis dominated two
correlated names; trimming MU into the cash floor would have reduced drawdown
without sacrificing upside." These lessons are injected into the CIO's prompt on
the next run, closing the loop at the level where allocation decisions are
actually made.

---

## 4. Experimental Design

This section describes the **planned** evaluation. We deliberately report
**no empirical numbers**: returns, Sharpe ratios, and drawdowns depend on the
backbone model, temperature, date range, data quality, and LLM sampling, and the
framework is a research scaffold rather than a fixed, replicable strategy. All
results below are labeled *forthcoming*, and the results table (Table 1) is left
as an empty template to be populated under the protocol described here.

### 4.1 Data pipeline

Most social and news sources cannot be queried historically — StockTwits exposes
only the latest messages per symbol, and Reddit/Bluesky/X searches return only
recent windows. The only way to obtain a historical sentiment series is to
capture it as it happens. We use an already-deployed harness,
`scripts/media_poller.py`, as the data pipeline. It polls each source on a fixed
cadence (hourly by default) and appends every *new* item to a local SQLite store
(`media_posts`), deduplicating on each item's stable provider ID so overlapping
polls never double-count. Sources are:

| Source | Access | Notes |
|---|---|---|
| `stocktwits` | keyless cashtag stream | carries Bullish/Bearish user labels |
| `reddit` | keyless RSS search | finance subreddits (wallstreetbets, stocks, investing) |
| `bluesky` | keyless AT-Protocol search | public AppView |
| `news` | keyless RSS | Yahoo Finance + Google News company headlines |
| `truthsocial` | Mastodon v2 search | needs `TRUTHSOCIAL_TOKEN` (Cloudflare-gated) |
| `x` | X/Twitter API v2 | needs paid `X_BEARER_TOKEN`; off by default |

Each row stores the *original* post timestamp (`created_utc`) separately from the
poll time (`fetched_utc`). This is what makes the store backtest-safe: a loader
can reconstruct the exact window each analyst would have seen on any historical
trade date. The harness's `window(ticker, end, days)` returns precisely the items
whose original post time falls in $[\,t - d,\ t\,]$ — the look-ahead-safe slice.
For the portfolio extension, the same window query is run per ticker across the
universe, and the shared macro brief and cross-ticker statistics are restricted
to the same $\le t$ horizon.

### 4.2 Walk-forward backtest protocol

We evaluate via a **walk-forward** simulation of a managed book, in contrast to
the original framework's stateless single-decision runs. Over a sequence of trade
dates $t_0 < t_1 < \cdots < t_n$ (e.g., weekly rebalances):

1. At $t_k$, call `analyze_portfolio(holdings, candidates, t_k, constraints)`
   using only data with timestamp $\le t_k$.
2. Apply the resulting `PortfolioPlan` to obtain post-trade holdings and
   $K_{\text{after}}$ (subtracting modeled transaction costs).
3. Carry the post-trade book forward as the input holdings/cash for $t_{k+1}$.

This produces a *true simulated book*: the prior step's plan is the next step's
state. Look-ahead safety extends to the book — each Phase-1 run already pins
`trade_date`, and Phase-0/Phase-2 inputs are clipped to $\le t_k$.

### 4.3 Metrics

The book's realized weighted-return series (Section 3.5) is evaluated on:

- **Cumulative return** over the backtest horizon.
- **Annualized Sharpe ratio** of the book return series.
- **Maximum drawdown.**
- **Alpha versus SPY**, the framework's default benchmark, using the same
  raw/alpha decomposition as the per-ticker reflection layer.
- Secondary: **turnover** (from `turnover_estimate` vs. realized) and
  **constraint adherence** (fraction of plans satisfying all caps and the cash
  floor).

### 4.4 Baselines

- **Buy-and-hold** of the initial universe.
- **Equal-weight** rebalanced on the same cadence.
- **Single-ticker TradingAgents** — the original framework run per name, with
  positions sized by a naive mapping from rating to weight (no cross-ticker
  reasoning, no shared macro brief). This is the key ablation isolating the
  portfolio and macro contributions.
- **60/40** stock/bond reference portfolio.

We additionally plan **ablations** of the two contributions independently:
(a) shared macro brief on/off (with per-ticker macro re-derivation as the off
condition), and (b) Phase-2 cross-ticker analysis on/off (CIO with vs. without
correlation/concentration inputs).

### 4.5 Results (forthcoming)

Results are forthcoming. Table 1 is the template to be populated under the
protocol above; all cells are intentionally blank.

**Table 1.** Walk-forward backtest results (forthcoming; to be populated).
Numbers will depend on backbone model, temperature, date range, and data quality,
and are not guaranteed to replicate. *No values are reported in this draft.*

| Strategy | Cum. return | Ann. Sharpe | Max drawdown | Alpha vs SPY | Turnover |
|---|---|---|---|---|---|
| Buy-and-hold | — | — | — | — | — |
| Equal-weight (rebalanced) | — | — | — | — | — |
| 60/40 | — | — | — | — | — |
| Single-ticker TradingAgents (naive sizing) | — | — | — | — | — |
| Portfolio TradingAgents (− macro brief) | — | — | — | — | — |
| Portfolio TradingAgents (− cross-ticker) | — | — | — | — | — |
| **Portfolio TradingAgents (full)** | — | — | — | — | — |

---

## 5. Limitations

**No live execution.** The system produces a *proposed* `PortfolioPlan`;
execution remains manual. There is no broker integration, no intraday or
real-time portfolio updates, and no options, derivatives, or tax-lot handling.
Backtests apply plans against historical prices and cannot capture live fills,
slippage, or borrow constraints.

**LLM non-determinism.** The pipeline is LLM-driven, so two runs of the same
universe and date can differ. Reasoning models — the default configuration —
vary the most and largely ignore temperature, so byte-identical reproducibility
is not achievable. Reported metrics, when populated, will be point estimates of a
distribution; multi-seed reporting is advisable.

**Social-data coverage gaps.** The media pipeline only contains history from when
polling began; there is no way to retroactively recover pre-poller windows. Some
sources are token-gated (X, Truth Social) and may be empty in a given backtest.
Coverage is uneven across tickers and time, which can bias the sentiment signal.

**Look-ahead risk in macro data.** Although the social/news store records
original post timestamps and the `window()` slice is look-ahead-safe, macro
inputs require care: FRED series are subject to revision (the value of an
indicator "as known at $t$" can differ from its later-revised value), and
prediction-market and global-news queries must be clipped to $\le t$. The shared
macro brief inherits these hazards and must be validated against point-in-time
data to avoid subtle leakage.

**Transaction costs and capacity.** The backtest models costs as a flat per-trade
charge; real costs (spread, market impact, financing) are state- and
size-dependent and are not modeled. Turnover from frequent rebalancing can erode
the alpha the CIO appears to add; threshold-based rebalancing is intended to
mitigate this but is itself a hyperparameter.

**Universe and theme curation.** The candidate universe is a static, curated
watchlist, and the ticker→theme map is partly hand-curated (with an LLM fallback).
Both encode human priors and may miss emergent themes or mis-classify names.

---

## 6. Conclusion

TradingAgents established that a team of specialized LLM agents can analyze a
single instrument with the structure of a real trading desk. We argued that two
of its limitations are linked: it analyzes names in isolation, and it re-derives
a generic macro view per name. Portfolio TradingAgents addresses both with
additive layers that leave the proven per-ticker pipeline intact. A theme
registry and ticker→theme map make the macro layer targeted; a shared macro
brief, computed once and injected as `macro_context`, makes the worldview
consistent across the book and removes redundant data cost. A four-phase
portfolio graph reuses the single-ticker graph as a fan-out workhorse, adds a
cross-ticker analysis phase that surfaces hidden correlated concentration, and
culminates in a CIO meta-agent that emits a sized, constraint-aware
`PortfolioPlan`. A portfolio memory closes the reflection loop at the book level,
on book return and alpha.

We have deliberately reported no empirical results. The evaluation is laid out as
a planned, look-ahead-safe walk-forward backtest on an already-deployed media
pipeline, with concrete metrics, baselines, and ablations — and an explicitly
empty results template. Populating that table under the stated protocol, and
ablating the macro and cross-ticker contributions independently, is the immediate
next step.

---

## References

- Xiao, Y., Sun, E., Luo, D., and Wang, W. (2025). *TradingAgents: Multi-Agents
  LLM Financial Trading Framework.* arXiv:2412.20138 [q-fin.TR].
- Markowitz, H. (1952). *Portfolio Selection.* [Author, Year placeholder].
- [Author, Year]. *LLM agents for financial reasoning and trading.* (placeholder)
- [Author, Year]. *Multi-agent debate improves LLM reasoning.* (placeholder)
- [Author, Year]. *Adversarial proposer–critic structures for decision-making.* (placeholder)
- [Author, Year]. *Risk-parity and constraint-aware portfolio construction.* (placeholder)
- [Author, Year]. *Bridging LLM-generated views and quantitative allocation.* (placeholder)
- [Author, Year]. *Event and macro grounding for equity analysis.* (placeholder)

*Citation keys other than TradingAgents are placeholders to be resolved before
submission; no arXiv identifiers are invented.*
