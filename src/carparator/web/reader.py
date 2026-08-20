"""Read-only access to a scraped database. Never writes, never migrates."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence


class ReaderError(RuntimeError):
    """Raised when a database cannot be read, with a message a user can act on."""


class DatabaseNotFound(ReaderError):
    """Raised when the database file does not exist."""


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
        return connection

    def cars(self) -> list[dict]:
        return self._query("SELECT * FROM cars")

    def _query(self, sql: str, parameters: Sequence | dict = ()) -> list[dict]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()
