# R3 eligible-only hybrid ranking gate

Run date: 2026-08-19. All runs used a private `cp -r` copy of
`/Users/clarkpeng/.tradingagents/temporal`; the live store was not mutated,
reindexed, or resealed.

Ranker: `temporal-hybrid-v3`. Fixed constants: title/body weights `2.0/1.0`,
static ticker-family boost `1.25`, and exponential recency decay with a
30-day half-life (`published_at`, falling back to `available_at`). The
eligible chunk index is cached by `(corpus_hash, as_of)` and manifests include
its `index_state_hash` plus the `evidence_id/doc_key` tie-break declaration.

## Held-out gate (k=10)

| scenario | query | success | recall | nDCG | MRR |
|---|---|---:|---:|---:|---:|
| TSLA | Tesla reduces Model 3 and Model Y prices | 1 | 1.000 | 1.000 | 1.000 |
| TSLA | TSLA January 2023 vehicle price cuts | 1 | 1.000 | 0.431 | 0.250 |
| MSFT | Microsoft investment partnership announcement OpenAI | 1 | 1.000 | 0.500 | 0.333 |
| MSFT | MSFT OpenAI investment January 2023 8-K | 1 | 1.000 | 0.500 | 0.333 |
| **mean (4 queries)** | | **1.000** | **1.000** | **0.608** | **0.479** |

The held-out mean beats the recorded R2 chunk baseline at k=10:
`success .500 / recall .369 / nDCG .346 / MRR .393`.

The NVDA tuning run (`nvda-2026-08-18-v3`) measured success `1.000`, recall
`0.567`, nDCG `0.511`, and MRR `0.611`.

Repeated frozen-store queries returned identical ordered `(doc_key, rank)`
pairs, corpus hash, and index-state hash. Adding a document after `as_of`
left the complete result and manifest identical; this is permanently tested
in `tests/test_temporal_search.py`.
