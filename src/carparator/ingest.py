"""Drives sources into the store and records what each run actually saw."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from carparator.sources import ListingSource
from carparator.store import SqliteStore

logger = logging.getLogger(__name__)

COMPLETE = "complete"
PARTIAL = "partial"
FAILED = "failed"


@dataclass
class IngestResult:
    source: str
    run_id: int
    expected_total: int | None
    listings_seen: int = 0
    listings_stored: int = 0
    skipped_non_electric: int = 0
    mapping_errors: int = 0
    failed_pages: int = 0
    status: str = COMPLETE
    error: str | None = None


def ingest(
    sources: Iterable[ListingSource],
    store: SqliteStore,
    *,
    limit: int | None = None,
) -> list[IngestResult]:
    """Run every source, isolating each so one failure cannot abort the rest."""
    return [_ingest_one(source, store, limit=limit) for source in sources]


def _ingest_one(
    source: ListingSource, store: SqliteStore, *, limit: int | None
) -> IngestResult:
    now = _timestamp()
    run_id = store.start_run(source.name, started_at=now)
    result = IngestResult(source=source.name, run_id=run_id, expected_total=None)

    try:
        with store.transaction():
            for raw in source.fetch_raw():
                if limit is not None and result.listings_seen >= limit:
                    break
                result.listings_seen += 1
                store.store_raw(
                    raw.source, raw.source_id, json.dumps(raw.payload), fetched_at=now
                )
                try:
                    car = source.to_car(raw)
                except Exception:
                    # One unmappable record must not cost us the rest of the run;
                    # the raw payload is already stored, so it can be remapped
                    # later.
                    logger.exception(
                        "%s: could not map %s", source.name, raw.source_id
                    )
                    result.mapping_errors += 1
                    continue
                if car is None:
                    result.skipped_non_electric += 1
                    continue
                store.upsert_car(car, observed_at=now, run_id=run_id)
                result.listings_stored += 1
    except Exception as error:
        logger.exception("%s: run failed", source.name)
        result.status = FAILED
        result.error = f"{type(error).__name__}: {error}"

    result.expected_total = getattr(source, "expected_total", None)
    _retain_failed_pages(source, store, result)
    if result.status != FAILED:
        result.status = COMPLETE if _is_complete(result, limit) else PARTIAL

    store.finish_run(
        run_id,
        finished_at=_timestamp(),
        expected_total=result.expected_total,
        listings_seen=result.listings_seen,
        listings_stored=result.listings_stored,
        skipped_non_electric=result.skipped_non_electric,
        mapping_errors=result.mapping_errors,
        status=result.status,
        error=result.error,
    )
    return result


def _retain_failed_pages(
    source: ListingSource, store: SqliteStore, result: IngestResult
) -> None:
    """Persist any pages a source couldn't parse, so an operator can inspect them.

    Not every source retains failed pages (CupraSource doesn't, and the
    ListingSource protocol doesn't require it), so both attributes are read
    defensively.
    """
    pages = getattr(source, "failed_pages", [])
    bodies = getattr(source, "failed_page_bodies", [])
    result.failed_pages = len(pages)
    if not bodies:
        return

    directory = Path(f"{store.path}.failed-pages")
    directory.mkdir(parents=True, exist_ok=True)
    for page, body in zip(pages, bodies):
        path = directory / f"{source.name}-page{page}.html"
        path.write_text(body)
        logger.warning("%s: wrote failed page %d to %s", source.name, page, path)


def _is_complete(result: IngestResult, limit: int | None) -> bool:
    """Only a full, unlimited pass may be trusted to infer that a listing is gone."""
    if limit is not None:
        return False
    if result.expected_total is None:
        return True
    return result.listings_seen >= result.expected_total


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
