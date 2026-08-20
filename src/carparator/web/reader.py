"""Read-only access to a scraped database. Never writes, never migrates."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from carparator.ingest import COMPLETE
from carparator.store import SCHEMA_VERSION
from carparator.web.query import FIELDS, FilterSpec, build_where, order_by, unknown_clause


NO_RUNS = "no_runs"
NO_COMPLETE_RUN = "no_complete_run"
COMPLETE_RUN = "complete"


@dataclass(frozen=True)
class SourceCoverage:
    """How far a source's stock can be trusted to be current."""

    source: str
    state: str
    completed_at: str | None = None


@dataclass(frozen=True)
class Coverage:
    sources: list[SourceCoverage]
    cars_with_no_run: int

    @property
    def is_partial(self) -> bool:
        """True when any source cannot say which of its cars have sold."""
        return any(each.state != COMPLETE_RUN for each in self.sources)


class ReaderError(RuntimeError):
    """Raised when a database cannot be read, with a message a user can act on."""


class DatabaseNotFound(ReaderError):
    """Raised when the database file does not exist."""


class SchemaMismatch(ReaderError):
    """Raised when the database was written by a different schema version."""


class Reader:
    """Reads listings from a database the scraper owns."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        # mode=ro refuses to create the file, but reports it as a bare
        # OperationalError; --db defaults to a relative path, so "wrong
        # directory" is the likeliest first-run failure and deserves saying so.
        if not self.path.exists():
            raise DatabaseNotFound(
                f"no database at {self.path}"
                " — check the --db path, or run `carparator scrape` first"
            )
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        self._check_schema_version(connection)
        return connection

    def _check_schema_version(self, connection: sqlite3.Connection) -> None:
        """Refuse a database this code cannot read.

        There are no migrations, so a version mismatch would otherwise surface
        as `no such column` at render time. Version 0 means init_schema never
        ran at all.
        """
        (version,) = connection.execute("PRAGMA user_version").fetchone()
        if version != SCHEMA_VERSION:
            connection.close()
            raise SchemaMismatch(
                f"{self.path} has schema version {version},"
                f" but this build reads version {SCHEMA_VERSION}"
                " — there are no migrations, so delete the database and re-scrape"
            )

    def cars(self) -> list[dict]:
        return self._query("SELECT * FROM cars")

    def current_stock(self) -> list[dict]:
        """The cars still believed to be for sale.

        Absence only implies "sold" across a run that completed, so each source
        is scoped by its own most recent complete run and sources without one
        keep every car they have.
        """
        clause, parameters = scope_clause(self.complete_run_floors())
        return self._query(f"SELECT * FROM cars WHERE {clause}", parameters)

    def search(self, spec: FilterSpec) -> list[dict]:
        """The current stock, narrowed by the user's filters."""
        scope, scope_parameters = scope_clause(self.complete_run_floors())
        where, parameters = build_where(spec)
        return self._query(
            f"SELECT * FROM cars WHERE ({scope}) AND ({where})"
            f" ORDER BY {order_by(spec)}",
            scope_parameters + parameters,
        )

    def unknown_counts(self, spec: FilterSpec) -> dict[str, int]:
        """Per nullable field, how many cars in scope it cannot speak for.

        Deliberately blind to the field's own filter and its own toggle: the
        number answers "how many cars would this filter hide", so it must not
        move when the user acts on the answer. Every *other* active filter does
        apply, so the number describes the result set actually on screen.
        """
        scope, scope_parameters = scope_clause(self.complete_run_floors())
        counts = {}
        for each in FIELDS:
            if not each.nullable:
                continue
            where, parameters = build_where(spec.without(each.name))
            rows = self._query(
                f"SELECT COUNT(*) AS total FROM cars"
                f" WHERE ({scope}) AND ({where}) AND ({unknown_clause(each)})",
                scope_parameters + parameters,
            )
            counts[each.name] = rows[0]["total"]
        return counts

    def coverage(self) -> Coverage:
        """What each source can honestly claim about its own stock."""
        floors = self.complete_run_floors()
        completions = self._finished_at(floors.values())
        have_run = {row["source"] for row in self._query("SELECT source FROM scrape_runs")}

        sources = []
        for source in sorted(self._sources()):
            if source in floors:
                state, completed_at = COMPLETE_RUN, completions.get(floors[source])
            elif source in have_run:
                state, completed_at = NO_COMPLETE_RUN, None
            else:
                state, completed_at = NO_RUNS, None
            sources.append(SourceCoverage(source, state, completed_at))
        return Coverage(sources, self._cars_with_no_run())

    def _finished_at(self, run_ids) -> dict[int, str | None]:
        run_ids = list(run_ids)
        if not run_ids:
            return {}
        rows = self._query(
            "SELECT id, finished_at FROM scrape_runs"
            f" WHERE id IN ({', '.join('?' * len(run_ids))})",
            run_ids,
        )
        return {row["id"]: row["finished_at"] for row in rows}

    def _sources(self) -> set[str]:
        """Never hardcoded — a source exists if either table mentions it."""
        rows = self._query(
            "SELECT source FROM scrape_runs UNION SELECT source FROM cars"
        )
        return {row["source"] for row in rows}

    def _cars_with_no_run(self) -> int:
        rows = self._query(
            "SELECT COUNT(*) AS total FROM cars WHERE last_seen_run_id IS NULL"
        )
        return rows[0]["total"]

    def complete_run_floors(self) -> dict[str, int]:
        """Per source, the id of its most recent run recorded as complete."""
        rows = self._query(
            "SELECT source, MAX(id) AS run_id FROM scrape_runs"
            " WHERE status = ? GROUP BY source",
            (COMPLETE,),
        )
        return {row["source"]: row["run_id"] for row in rows}

    def _query(self, sql: str, parameters: Sequence | dict = ()) -> list[dict]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()


def scope_clause(floors: dict[str, int]) -> tuple[str, list]:
    """SQL keeping only the cars a complete run has not proven absent.

    Expressed as an exclusion, so the default is to include: a car is dropped
    only on positive evidence that a complete run passed over it. The
    comparison is `<` against the floor rather than `>=` in the positive form,
    because presence in any *later* run — complete or not, such as a
    `--limit` one — is evidence the car exists and must win. A NULL run id is
    no evidence either way and is never dropped; SQL's NULL comparison would
    otherwise discard those rows silently.
    """
    conditions, parameters = [], []
    for source, floor in sorted(floors.items()):
        conditions.append(
            "NOT (source = ? AND last_seen_run_id IS NOT NULL AND last_seen_run_id < ?)"
        )
        parameters.extend([source, floor])
    return " AND ".join(conditions) if conditions else "1", parameters
