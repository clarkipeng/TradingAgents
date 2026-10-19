# Luna P4 successor handoff

Date: 2026-09-07  
Base: `543a370` (working branch `cdt/bf9a-ta-luna-p4-resum`)

## Commit

- `532af46a86d503dd2b2a8caee39842eff44deccb` — generation-pinned retrieval
  tables and candidate policy; safe legacy generation fallback; immutable
  post-publication document snapshots; retirement of new `evidence_fts`
  writes; non-destructive trace/artifact retention plan; focused proofs.

## Implemented contract

`temporal_index_generations` resolves a search/fetch to one retained document,
chunk, and FTS generation. Pagination and fetch can carry `generation_id`, so
publication cannot mix candidates, hydration, or siblings. Pre-versioning
stores continue reading canonical generation 0 tables. After a generation is
published, new document evidence remains durable but is indexed only by the
next rebuild; pinned readers therefore cannot drift. Cluster candidate cutoff
is fixed rather than corpus-size-derived, preserving policy semantics as the
corpus grows. New stores no longer create or write the unused full-payload
`evidence_fts` copy; existing tables are left intact for rollback/legacy
readers.

`retention_plan()` is a dry-run contract: it reports candidate trace rows,
protected run IDs, inline LLM payloads that already have artifacts, and the
artifact reachability report. It performs no deletion or rewriting and never
age-prunes sealed evidence.

## Verification

- `uv run --with pytest pytest -q -W error::ResourceWarning tests/test_temporal_core.py tests/test_temporal_documents.py tests/test_temporal_retention.py tests/test_temporal_search.py tests/test_temporal_r4_tool_surface.py tests/test_temporal_evaluation.py tests/test_temporal_archive_graph_replay.py`
  — **55 passed**.
- `git diff --check` and Python compilation passed.

## Residuals / integration

- Capture/import callers still hold `store.write_lock()` around provider work
  in out-of-scope adapter/CLI files; parent/CLI owner must split those locks.
- No CLI interface was changed or required by this slice.
- Parent should integrate this commit, then exercise archive/restore and any
  production retention executor against a copied store before reclaiming data.
