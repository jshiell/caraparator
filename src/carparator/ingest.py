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

    stored_ids: list[str] = []
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
                # Held as IDs rather than payloads: the feature pass needs
                # nothing else, and ~1000 vehicle dicts would otherwise stay
                # resident for the length of the run.
                stored_ids.append(raw.source_id)
    except Exception as error:
        logger.exception("%s: run failed", source.name)
        result.status = FAILED
        result.error = _describe_error(error)

    result.expected_total = getattr(source, "expected_total", None)
    _retain_failed_pages(source, store, result)
    if result.status != FAILED:
        result.status = COMPLETE if _is_complete(result, limit) else PARTIAL

    _fetch_features(source, store, stored_ids, fetched_at=now)

    try:
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
    except Exception as error:
        # The store itself is what's broken here, so there's no further store
        # call worth making. Keep the failure inside this source: the caller
        # still gets an accurate IngestResult, and the next source can run.
        logger.exception("%s: failed to record run outcome", source.name)
        result.status = FAILED
        result.error = _describe_error(error, cause=result.error)
    return result


def _fetch_features(
    source: ListingSource,
    store: SqliteStore,
    source_ids: list[str],
    *,
    fetched_at: str,
) -> None:
    """Fetch each stored listing's equipment, once the listing work is durable.

    A second pass on purpose. Folding a detail fetch per listing into the
    listing transaction would stretch one commit from seconds to minutes, so a
    kill mid-run would cost every listing rather than none, and would widen the
    concurrent-scrape lock window by the same factor. Here each listing commits
    on its own, and a kill costs at most one listing's features — which are
    re-fetchable by definition.

    Not every source can report features, and the ListingSource protocol does
    not require it, so the method is read defensively.
    """
    fetch = getattr(source, "fetch_features", None)
    if fetch is None:
        return

    for source_id in source_ids:
        features = fetch(source_id)
        store.store_features(source.name, source_id, features, fetched_at=fetched_at)


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
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for page, body in zip(pages, bodies):
            path = directory / f"{source.name}-page{page}.html"
            path.write_text(body, encoding="utf-8")
            logger.warning(
                "%s: wrote failed page %d to %s", source.name, page, path
            )
    except OSError:
        logger.warning(
            "%s: could not retain failed page bodies", source.name, exc_info=True
        )


def _is_complete(result: IngestResult, limit: int | None) -> bool:
    """Only a full, unlimited pass may be trusted to infer that a listing is gone."""
    if limit is not None:
        return False
    if result.expected_total is None:
        # No total means no yardstick: a run cut short by a site change cannot be
        # told apart from a full one. Withhold 'complete' rather than license the
        # sold-listing inference on a run whose coverage is unknown.
        return False
    return result.listings_seen >= result.expected_total


def _describe_error(error: BaseException, *, cause: str | None = None) -> str:
    """Render an error for storage without losing an earlier root cause.

    A later store-layer failure (e.g. a commit raising while unwinding a
    fetch failure, or finish_run itself failing) must not silently replace
    the reason the run actually failed.
    """
    message = f"{type(error).__name__}: {error}"
    if cause is None:
        context = error.__context__
        if context is not None and context is not error:
            cause = f"{type(context).__name__}: {context}"
    if cause and cause not in message:
        message = f"{message} (cause: {cause})"
    return message


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
