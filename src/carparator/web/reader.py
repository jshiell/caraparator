"""Read-only access to a scraped database. Never writes, never migrates."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from carparator.store import SCHEMA_VERSION


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

    def _query(self, sql: str, parameters: Sequence | dict = ()) -> list[dict]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()
