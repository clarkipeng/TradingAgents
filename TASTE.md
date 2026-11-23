# Taste

Read `~/.agentworld/lab/taste.md` first; this file refines it for this repo.

## Scope

- Trade individual stocks only, never index funds.
- Adopt Atrioc's viewpoints.

## Decisions

- Every decision comes with an LLM-written reason and shows what the model saw.
- The bot only recommends, and its caps are advisory.
- The PI approves trade batches in a UI with bar charts.

## Measurement

- Optimize Sharpe net of the real risk-free rate, tax and costs.
- Measure against the S&P and bonds.
- Backtests use only data the live pipeline could have had, and are reproducible from it.
