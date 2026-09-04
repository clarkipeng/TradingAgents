"""Offline, read-only benchmark for the current temporal evidence index."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents.temporal.ranking import build_eligible_index, rank


def _fts_query(query: str) -> str:
    return " OR ".join(f'"{token}"' for token in re.findall(r"[\w]+", query, re.UNICODE))


@dataclass(frozen=True)
class Case:
    query: str
    expected: tuple[str, ...]
    kind: str
    as_of: str


def _parse_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _document_key(evidence_id: str) -> str:
    """R1 key: evidence IDs are the stable document identity before R2."""
    return evidence_id


def _cases(spec: dict[str, Any], traces: list[tuple[str, str, list[str]]] = ()) -> list[Case]:
    default_as_of = spec.get("as_of")
    if not default_as_of and traces:
        default_as_of = traces[0][1]
    if not default_as_of:
        default_as_of = "9999-12-31T23:59:59Z"
    result = [
        Case(
            query=item["query"],
            expected=tuple(dict.fromkeys(item["expected_document_keys"])),
            kind=item.get("kind", "known-item"),
            as_of=_parse_time(item.get("as_of", default_as_of)),
        )
        for item in spec.get("queries", [])
    ]
    selector = spec.get("topic_from_search_traces")
    if selector:
        limit = int(selector.get("limit", 0)) or len(traces)
        for query, as_of, expected in traces[:limit]:
            targets = tuple(dict.fromkeys(expected))
            if targets:
                result.append(Case(query, targets, "topic", _parse_time(as_of)))
    return result


def _fixture_connection(spec: dict[str, Any]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, available_at TEXT NOT NULL)")
    connection.execute("CREATE VIRTUAL TABLE evidence_fts USING fts5(evidence_id UNINDEXED, content)")
    for item in spec.get("documents", []):
        key = item["document_key"]
        connection.execute("INSERT INTO evidence VALUES (?, ?)", (key, _parse_time(item["available_at"])))
        connection.execute("INSERT INTO evidence_fts VALUES (?, ?)", (key, item.get("text", "")))
    return connection


def _read_connection(store: Path) -> sqlite3.Connection:
    database = store / "temporal.sqlite3" if store.is_dir() else store
    if not database.is_file():
        raise FileNotFoundError(f"temporal database not found: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _trace_cases(connection: sqlite3.Connection) -> list[tuple[str, str, list[str]]]:
    rows = connection.execute(
        """SELECT t.query, t.as_of, r.material_evidence_ids_json
           FROM search_traces AS t
           JOIN scenario_rubrics AS r ON r.scenario_id = t.scenario_id
           WHERE t.scenario_id IS NOT NULL
           ORDER BY t.trace_id"""
    ).fetchall()
    has_documents = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if has_documents:
        evidence_to_document = dict(connection.execute("SELECT parent_evidence_id, doc_key FROM documents"))
        document_keys = set(evidence_to_document.values())
    else:
        evidence_to_document = {}
        document_keys = {row[0] for row in connection.execute("SELECT evidence_id FROM evidence WHERE tool = 'corpus.document'")}
    return [
        (query, as_of, [evidence_to_document.get(key, key) for key in json.loads(ids) if key in evidence_to_document or key in document_keys])
        for query, as_of, ids in rows
    ]


def _search(connection: sqlite3.Connection, case: Case, limit: int, index_cache: dict[tuple[str, str], Any] | None = None) -> list[str]:
    query = _fts_query(case.query)
    if not query:
        return []
    has_documents = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if has_documents:
        cutoff = case.as_of
        cache = index_cache if index_cache is not None else {}
        index = cache.get(cutoff)
        if index is None:
            index = build_eligible_index(connection, as_of=cutoff)
            cache[cutoff] = index
        return [key for key, _score in rank(index, case.query, limit)]
    return [row[0] for row in connection.execute(
        """SELECT f.evidence_id FROM evidence_fts AS f
           JOIN evidence AS e ON e.evidence_id = f.evidence_id
           WHERE evidence_fts MATCH ? AND e.available_at <= ?
           ORDER BY bm25(evidence_fts) ASC, f.evidence_id ASC LIMIT ?""",
        (query, case.as_of, limit),
    )]


def _query_metrics(ranked: list[str], expected: tuple[str, ...], k: int) -> dict[str, Any]:
    expected_set = set(expected)
    top = ranked[:k]
    hits = [index + 1 for index, key in enumerate(top) if key in expected_set]
    dcg = sum(1 / math.log2(rank + 1) for rank in hits)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(expected)) + 1))
    return {
        "success": bool(hits),
        "recall": len(hits) / len(expected) if expected else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
        "mrr": 1 / hits[0] if hits else 0.0,
        "misses": [key for key in expected if key not in set(top)],
        "ranked_document_keys": top,
    }


def run_benchmark(bench: str | Path, store: str | Path | None = None, k: int = 10) -> dict[str, Any]:
    spec = json.loads(Path(bench).read_text(encoding="utf-8"))
    if k < 1:
        raise ValueError("k must be positive")
    connection = _fixture_connection(spec) if "documents" in spec else _read_connection(Path(store or ""))
    try:
        traces = _trace_cases(connection) if "topic_from_search_traces" in spec else []
        cases = _cases(spec, traces)
        if "documents" not in spec:
            has_documents = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
            if has_documents:
                mapping = dict(connection.execute("SELECT parent_evidence_id, doc_key FROM documents"))
                document_keys = set(mapping.values())
                evidence_keys = set(mapping)
                normalized_cases = []
                for case in cases:
                    unknown = sorted(set(case.expected) - evidence_keys - document_keys)
                    if unknown:
                        raise ValueError(f"benchmark target is neither an evidence ID nor document key: {unknown[0]}")
                    normalized_cases.append(
                        Case(case.query, tuple(mapping.get(key, key) for key in case.expected), case.kind, case.as_of)
                    )
                cases = normalized_cases
        rows = []
        index_cache: dict[tuple[str, str], Any] = {}
        for case in cases:
            evaluated_k = max(k, 2 * len(case.expected))
            metrics = _query_metrics(_search(connection, case, evaluated_k, index_cache), case.expected, evaluated_k)
            rows.append({
                "query": case.query,
                "kind": case.kind,
                "expected_count": len(case.expected),
                "evaluated_k": evaluated_k,
                **metrics,
            })
        averages = {
            "success@k": sum(row["success"] for row in rows) / len(rows) if rows else 0.0,
            "recall@k": sum(row["recall"] for row in rows) / len(rows) if rows else 0.0,
            "nDCG@k": sum(row["ndcg"] for row in rows) / len(rows) if rows else 0.0,
            "MRR": sum(row["mrr"] for row in rows) / len(rows) if rows else 0.0,
        }
        floors = spec.get("floors", {})
        failures = [name for name, floor in floors.items() if averages.get(name, 0.0) < float(floor)]
        return {"k": k, "query_count": len(rows), "metrics": averages, "queries": rows, "floor_failures": failures}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", help="temporal store directory (opened read-only)")
    parser.add_argument("--bench", required=True, help="benchmark JSON file")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.bench, args.store, args.k)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"queries={result['query_count']} k={result['k']}")
        for name, value in result["metrics"].items():
            print(f"{name}={value:.4f}")
        print(f"floor_failures={len(result['floor_failures'])}")
    return 1 if result["floor_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
