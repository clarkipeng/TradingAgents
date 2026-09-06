# Luna store handoff

Date: 2026-09-06  
Base: `4d1fafd88ec91926adae35c164efab99d666f623`

## Commits

- `7a209a913791a6bf075c39583d2e7e945994e4a2` — additive lookup indexes and
  removal of the unreachable duplicate `TemporalStore.search` implementation
  (P3 findings 15, 16, 29, 30).
- `d1dae4005718944bdeca7beb2e9370e9237d8f99` — thread-owned SQLite
  connections, atomic per-run trace sequence allocation, media lookup index,
  SQL corpus overview aggregation, same-connection LLM return reads, and
  generation-read reduction (P3 findings 19, 21, 22, 25).
- `a08f6e699a602ad1aa135f8f41d59fd78112b33a` — explicit lifecycle correction:
  all registered thread-owned connections close through `TemporalStore.close()`
  or `with TemporalStore(...)`, with a focused lifecycle test.

## Verification evidence

`uv run --with pytest pytest -q -W error::ResourceWarning tests/test_temporal_core.py tests/test_temporal_documents.py tests/test_temporal_r4_tool_surface.py tests/test_x_posts_tool.py tests/test_temporal_evaluation.py`

Result: **46 passed**. `git diff --check`, Python bytecode compilation, and a
 SQLite smoke check also passed. The smoke check confirmed the new evidence,
 document-cluster, and media indexes; redundant trace indexes are absent; trace
  sequences remain 1, 2. The focused suite was run with
  `-W error::ResourceWarning`; no unclosed SQLite connection warnings occurred.

## Contract requests for lead

- Run `EXPLAIN QUERY PLAN` against representative production-sized fixtures
  for corpus freshness, sibling lookup, and `media_posts_asof`; retain the
  before/after rows-visited and lock-duration evidence.
- Exercise store construction from a pre-migration database that lacks
  `evidence.is_error`, confirming the post-alter media index migration path.
- Confirm every long-lived caller uses the explicit `TemporalStore.close()` or
  context-manager lifecycle; `__del__` remains only a short-lived/test fallback.
- Checkpoint/replay trace readers against the additive `trace_sequences` table;
  old trace rows seed their next sequence lazily under the publication
  transaction.
- Do not begin P4 retrieval/storage versioning until lead transfers ownership
  after the replay manifest definition.

P4 was not started. No CLI, portfolio/replay/model path, or `doing.md` was
edited.
