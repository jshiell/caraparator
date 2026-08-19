"""`carparator scrape` — the ingestion entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from carparator.ingest import FAILED, ingest
from carparator.sources import ListingSource
from carparator.sources.cupra import CupraSource
from carparator.sources.volkswagen import VolkswagenSource
from carparator.store import SqliteStore

SOURCES = {"cupra": CupraSource, "volkswagen": VolkswagenSource}
DEFAULT_DB = "carparator.db"


def build_sources(name: str | None) -> list[ListingSource]:
    names = [name] if name else list(SOURCES)
    return [SOURCES[each]() for each in names]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="carparator")
    subcommands = parser.add_subparsers(dest="command", required=True)

    scrape = subcommands.add_parser("scrape", help="fetch listings into the database")
    scrape.add_argument("--source", choices=sorted(SOURCES), default=None)
    scrape.add_argument("--db", default=DEFAULT_DB)
    scrape.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap listings per source; forces the run to be recorded as partial",
    )
    scrape.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    with SqliteStore(args.db) as store:
        store.init_schema()
        results = ingest(build_sources(args.source), store, limit=args.limit)

    for result in results:
        print(
            f"{result.source}: seen {result.listings_seen}"
            f"/{result.expected_total if result.expected_total is not None else '?'}"
            f" stored {result.listings_stored}"
            f" skipped {result.skipped_non_electric}"
            f" errors {result.mapping_errors}"
            f" [{result.status}]"
        )
        if result.error:
            print(f"  {result.error}", file=sys.stderr)

    return 1 if any(result.status == FAILED for result in results) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
