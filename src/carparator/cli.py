"""The command line: `carparator scrape` ingests, `carparator serve` reads."""

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
# Never anything else: the Werkzeug debugger and an unauthenticated view of the
# database have no business on a routable address.
LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 8000


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
    scrape.add_argument(
        "--refetch-features",
        action="store_true",
        help="re-read every listing's equipment, not only listings that lack it",
    )
    scrape.add_argument("-v", "--verbose", action="store_true")

    serve = subcommands.add_parser("serve", help="browse the database in a browser")
    serve.add_argument("--db", default=DEFAULT_DB)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return COMMANDS[args.command](args)


def scrape_command(args: argparse.Namespace) -> int:
    """Fetch listings into the database."""
    with SqliteStore(args.db) as store:
        store.init_schema()
        results = ingest(
            build_sources(args.source),
            store,
            limit=args.limit,
            refetch_features=args.refetch_features,
        )

    for result in results:
        line = (
            f"{result.source}: seen {result.listings_seen}"
            f"/{result.expected_total if result.expected_total is not None else '?'}"
            f" stored {result.listings_stored}"
            f" skipped {result.skipped_non_electric}"
            f" errors {result.mapping_errors}"
            f" features {result.features_fetched}"
            f" feature_errors {result.feature_errors}"
        )
        if result.failed_pages:
            line += f" failed_pages {result.failed_pages}"
        line += f" [{result.status}]"
        print(line)
        if result.error:
            print(f"  {result.error}", file=sys.stderr)

    return 1 if any(result.status == FAILED for result in results) else 0


def serve_command(args: argparse.Namespace) -> int:
    """Serve the read-only web view on the loopback address."""
    try:
        # Imported here, not at module scope, so `scrape` still runs without
        # the web extra installed.
        from carparator.web.app import create_app
    except ImportError:
        print(
            "carparator serve needs Flask: run `uv sync --extra web`",
            file=sys.stderr,
        )
        return 1

    from carparator.web.reader import Reader, ReaderError

    reader = Reader(args.db)
    try:
        # Fail now, with a message, rather than on the first request.
        reader.coverage()
    except ReaderError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"carparator: http://{LOOPBACK}:{args.port}/  (Ctrl-C to stop)")
    create_app(reader).run(
        host=LOOPBACK, port=args.port, debug=False, use_reloader=False
    )
    return 0


COMMANDS = {"scrape": scrape_command, "serve": serve_command}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
