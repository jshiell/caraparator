"""Read-only access to a scraped database. Never writes, never migrates."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence


class Reader:
    """Reads listings from a database the scraper owns."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
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
