"""The single deterministic retrieval engine used by every temporal consumer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .clock import format_timestamp, parse_timestamp
from .models import (
    SearchManifest,
    TemporalDocument,
    TemporalSearchResponse,
    TemporalSearchResult,
    canonical_json,
)
from .ranking import RANKER_VERSION, rank

_SNIPPET_CHARS = 1_500


class TemporalRetriever:
    """Own query parsing, eligibility, ranking, pagination, and manifests."""

    def __init__(self, store):
        self.store = store

    def search(
        self, query: str, *, as_of: datetime, limit: int = 10, page: int = 1,
        date_from: str | None = None, date_to: str | None = None,
        source: str | None = None, corpus_hash_pin: str | None = None,
    ) -> TemporalSearchResponse:
        """One search, pinned to a single document generation.

        The read phases use separate connections, so a generation swap
        landing mid-search could mix index and hydration from different
        generations; when the active generation moved during the read, one
        retry re-runs the whole search against the settled generation.
        """
        for _attempt in range(2):
            generation_before = self.store.active_generation_id()
            response = self._search_once(
                query, as_of=as_of, limit=limit, page=page, date_from=date_from,
                date_to=date_to, source=source, corpus_hash_pin=corpus_hash_pin,
            )
            if self.store.active_generation_id() == generation_before:
                return response
        return response

    def _search_once(
        self, query: str, *, as_of: datetime, limit: int = 10, page: int = 1,
        date_from: str | None = None, date_to: str | None = None,
        source: str | None = None, corpus_hash_pin: str | None = None,
    ) -> TemporalSearchResponse:
        if limit < 1 or page < 1:
            raise ValueError("limit and page must be positive")
        parsed_as_of = parse_timestamp(as_of)
        start = self._date_bound(date_from)
        end = self._date_bound(date_to)
        if start and end and start > end:
            raise ValueError("date_from must be on or before date_to")
        cutoff = format_timestamp(parsed_as_of)
        generation_id = self.store.active_generation_id()
        corpus_hash = self.store.corpus_hash(as_of=parsed_as_of)
        if page > 1 and corpus_hash_pin != corpus_hash:
            raise ValueError("page > 1 requires a matching page-1 corpus_hash pin")
        index = self.store.eligible_index(cutoff=cutoff, generation_id=generation_id)

        filtered: set[str] | None = None
        if start or end or source:
            predicates = ["d.available_at <= ?"]
            parameters: list[Any] = [cutoff]
            if start:
                predicates.append("substr(d.available_at, 1, 10) >= ?")
                parameters.append(start)
            if end:
                predicates.append("substr(d.available_at, 1, 10) <= ?")
                parameters.append(end)
            if source:
                predicates.append("(d.source_domain = ? OR e.source = ? OR d.source_domain LIKE ?)")
                parameters.extend((source, source, f"%{source}%"))
            with self.store._connect() as connection:
                filtered = {row["doc_key"] for row in connection.execute(
                    "SELECT d.doc_key FROM documents d JOIN evidence e ON e.evidence_id = d.parent_evidence_id "
                    f"WHERE {' AND '.join(predicates)}", parameters,
                ).fetchall()}

        cluster_by_key = index.cluster_by_doc_key
        if not query.strip():
            with self.store._connect() as connection:
                rows = connection.execute(
                    "SELECT doc_key, cluster_key FROM documents WHERE available_at <= ? "
                    "ORDER BY available_at DESC, doc_key ASC", (cutoff,),
                ).fetchall()
            best: dict[str, dict[str, Any]] = {}
            for row in rows:
                if filtered is None or row["doc_key"] in filtered:
                    best.setdefault(row["cluster_key"], {"doc_key": row["doc_key"], "cluster_key": row["cluster_key"], "rank": 0.0})
            ordered = list(best.values())
        else:
            candidates = rank(index, query, max(limit * 8 * page, 32))
            best = {}
            for doc_key, score in candidates:
                if filtered is not None and doc_key not in filtered:
                    continue
                cluster = cluster_by_key[doc_key]
                if cluster not in best or (-score, doc_key) < (-float(best[cluster]["rank"]), best[cluster]["doc_key"]):
                    best[cluster] = {"doc_key": doc_key, "cluster_key": cluster, "rank": score}
            ordered = sorted(best.values(), key=lambda row: (-float(row["rank"]), row["doc_key"]))

        selected = ordered[(page - 1) * limit: page * limit]
        keys = [row["doc_key"] for row in selected]
        if not keys:
            hydrated = {}
            siblings = {}
        else:
            placeholders = ",".join("?" for _ in keys)
            with self.store._connect() as connection:
                hydrated_rows = connection.execute(
                    "SELECT d.*, e.*, json_extract(e.response_json, '$.metadata') AS metadata_json "
                    f"FROM documents d JOIN evidence e ON e.evidence_id = d.parent_evidence_id WHERE d.doc_key IN ({placeholders})", keys,
                ).fetchall()
                hydrated = {row["doc_key"]: row for row in hydrated_rows}
                siblings = {row["cluster_key"]: tuple(item["doc_key"] for item in connection.execute(
                    "SELECT doc_key FROM documents WHERE cluster_key=? AND doc_key<>? ORDER BY doc_key",
                    (row["cluster_key"], row["doc_key"]),
                ).fetchall()) for row in selected}

        results = tuple(TemporalSearchResult(
            evidence=self.store._search_evidence_from_row(hydrated[row["doc_key"]]),
            rank=float(row["rank"]),
            document=TemporalDocument(
                doc_key=row["doc_key"], parent_evidence_id=hydrated[row["doc_key"]]["parent_evidence_id"],
                title=hydrated[row["doc_key"]]["title"], body=hydrated[row["doc_key"]]["body"],
                source_domain=hydrated[row["doc_key"]]["source_domain"],
                canonical_url=hydrated[row["doc_key"]]["canonical_url"],
                published_at=parse_timestamp(hydrated[row["doc_key"]]["published_at"])
                if hydrated[row["doc_key"]]["published_at"] else None,
                available_at=parse_timestamp(hydrated[row["doc_key"]]["available_at"]),
                doc_kind=hydrated[row["doc_key"]]["doc_kind"],
                siblings=siblings[row["cluster_key"]],
            ),
        ) for row in selected)
        return TemporalSearchResponse(results, SearchManifest(
            query=query, as_of=parsed_as_of, ranker_version=RANKER_VERSION,
            corpus_hash=corpus_hash, evidence_ids=tuple(item.evidence.evidence_id for item in results),
            index_state_hash=index.index_state_hash, page=page, limit=limit,
            date_from=start, date_to=end, source=source, generation_id=generation_id,
        ))

    @staticmethod
    def _date_bound(value: str | None) -> str | None:
        if not value:
            return None
        return value[:10] if len(value) == 10 else parse_timestamp(value).date().isoformat()


def search_payload(response: TemporalSearchResponse) -> dict[str, Any]:
    """Serialize search results once for tools, briefs, and MCP."""
    manifest = response.manifest
    payload = {
        "results": [{
            "evidence_id": item.evidence.evidence_id, "doc_key": item.doc_key,
            "title": item.document.title if item.document else item.evidence.response.get("title"),
            "source": item.evidence.source or (item.document.source_domain if item.document else None),
            "available_at": item.evidence.available_at, "fidelity": item.evidence.fidelity,
            **_snippet(item.evidence.response),
        } for item in response.results],
        "manifest": {
            "query": manifest.query, "as_of": manifest.as_of, "ranker_version": manifest.ranker_version,
            "corpus_hash": manifest.corpus_hash, "evidence_ids": manifest.evidence_ids,
            "index_state_hash": manifest.index_state_hash, "tie_break": manifest.tie_break,
            "page": manifest.page, "limit": manifest.limit, "date_from": manifest.date_from,
            "date_to": manifest.date_to, "source": manifest.source,
            "generation_id": manifest.generation_id,
        },
    }
    return json.loads(canonical_json(payload))


def _snippet(response: Any) -> dict[str, Any]:
    text = response.get("text", "") if isinstance(response, Mapping) else ""
    text = text if isinstance(text, str) else canonical_json(response)
    return {"snippet": text[:_SNIPPET_CHARS], "truncated": len(text) > _SNIPPET_CHARS}
