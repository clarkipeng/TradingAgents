"""Conservative SEC EDGAR archive collector for public filing evidence."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from typing import Any

import requests

from tradingagents.temporal import TemporalStore

_ACCEPTANCE_TIMESTAMP = re.compile(r"ACCEPTANCE-DATETIME:\s*(\d{14})")
_DEFAULT_FORMS = frozenset({"10-K", "10-Q", "8-K"})


@dataclass(frozen=True)
class SecEdgarImportResult:
    requested: int
    imported: int
    evidence_ids: tuple[str, ...]
    failures: tuple[str, ...]


def import_sec_edgar_filings(
    store: TemporalStore,
    *,
    cik: str | int,
    user_agent: str,
    start_date: str | None = None,
    end_date: str | None = None,
    forms: Iterable[str] = _DEFAULT_FORMS,
    max_filings: int = 25,
    request_delay_seconds: float = 0.1,
    session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SecEdgarImportResult:
    """Import public EDGAR filing documents with conservative availability clocks.

    The SEC submission index provides document locations. When a filing header
    exposes ``ACCEPTANCE-DATETIME``, that becomes both publication and
    availability time. Older/unusual documents without that header are admitted
    only at the end of their filing date, preventing same-day lookahead. Every
    imported document is explicitly labelled ``archive-reconstructed``.
    """
    if not user_agent.strip():
        raise ValueError("SEC requests require an identifying user_agent")
    if max_filings < 1:
        raise ValueError("max_filings must be positive")
    if request_delay_seconds < 0.1:
        raise ValueError("request_delay_seconds must be at least 0.1")

    normalized_cik = str(int(str(cik))).zfill(10)
    wanted_forms = {form.upper() for form in forms}
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    client = session or requests.Session()
    submissions_url = f"https://data.sec.gov/submissions/CIK{normalized_cik}.json"
    response = client.get(submissions_url, headers=headers, timeout=30)
    response.raise_for_status()
    recent = response.json().get("filings", {}).get("recent", {})

    filings = []
    for index, form in enumerate(recent.get("form", ())):
        filing_date = _column(recent, "filingDate", index)
        if form.upper() not in wanted_forms or not _within(filing_date, start_date, end_date):
            continue
        accession = _column(recent, "accessionNumber", index)
        primary_document = _column(recent, "primaryDocument", index)
        if accession and primary_document:
            filings.append(
                {
                    "accession": accession,
                    "filing_date": filing_date,
                    "form": form,
                    "primary_document": primary_document,
                    "report_date": _column(recent, "reportDate", index),
                }
            )
        if len(filings) >= max_filings:
            break

    evidence_ids: list[str] = []
    failures: list[str] = []
    for position, filing in enumerate(filings):
        if position:
            sleep(request_delay_seconds)
        source_url = _submission_url(normalized_cik, filing["accession"])
        try:
            document_response = client.get(source_url, headers=headers, timeout=30)
            document_response.raise_for_status()
            document = document_response.text
            accepted_at, availability_basis = _availability_time(document, filing["filing_date"])
            report_date = filing["report_date"]
            event_at = (
                datetime.combine(datetime.fromisoformat(report_date).date(), clock_time(), tzinfo=timezone.utc)
                if report_date
                else None
            )
            record = store.record(
                "corpus.document",
                {
                    "source_url": source_url,
                    "external_id": filing["accession"],
                    "source": "sec-edgar",
                },
                {
                    "text": document,
                    "metadata": {
                        "cik": normalized_cik,
                        "accession_number": filing["accession"],
                        "form": filing["form"],
                        "filing_date": filing["filing_date"],
                        "report_date": report_date,
                        "availability_basis": availability_basis,
                    },
                },
                available_at=accepted_at,
                observed_at=accepted_at,
                event_at=event_at,
                source_published_at=accepted_at,
                fidelity="archive-reconstructed",
                source=source_url,
            )
        except requests.RequestException as error:
            failures.append(f"{filing['accession']}:{type(error).__name__}")
        else:
            evidence_ids.append(record.evidence_id)
    return SecEdgarImportResult(
        requested=len(filings),
        imported=len(evidence_ids),
        evidence_ids=tuple(evidence_ids),
        failures=tuple(failures),
    )


def _column(columns: dict[str, list[str]], key: str, index: int) -> str:
    values = columns.get(key, ())
    return values[index] if index < len(values) else ""


def _within(filing_date: str, start_date: str | None, end_date: str | None) -> bool:
    return bool(filing_date) and (start_date is None or filing_date >= start_date) and (
        end_date is None or filing_date <= end_date
    )


def _submission_url(cik: str, accession: str) -> str:
    """Return EDGAR's complete submission text, including the acceptance header."""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}.txt"
    )


def _availability_time(document: str, filing_date: str) -> tuple[datetime, str]:
    match = _ACCEPTANCE_TIMESTAMP.search(document[:50_000])
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc), "acceptance"
    filing_day = datetime.fromisoformat(filing_date).date()
    return datetime.combine(filing_day, clock_time(23, 59, 59), tzinfo=timezone.utc), "filing-date-end"
