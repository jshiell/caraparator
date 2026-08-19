"""SQLite persistence. Hand-written DDL; no migrations — see README."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from carparator.model import Car

SCHEMA_VERSION = 1


class TransactionError(RuntimeError):
    """Raised when a store transaction is used in an unsupported way."""


_DDL = """
CREATE TABLE IF NOT EXISTS cars (
    source            TEXT    NOT NULL,
    source_id         TEXT    NOT NULL,
    brand             TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    battery_kwh       REAL,
    doors             INTEGER,
    mileage_miles     INTEGER NOT NULL,
    year              INTEGER NOT NULL,
    registration      TEXT,
    price_pence       INTEGER NOT NULL,
    dealer_name       TEXT    NOT NULL,
    fuel_type         TEXT    NOT NULL,

    trim                TEXT,
    description         TEXT,
    range_miles         REAL,
    power_kw            INTEGER,
    power_ps            INTEGER,
    engine_cc           INTEGER,
    drivetrain          TEXT,
    transmission        TEXT,
    colour              TEXT,
    seats               INTEGER,
    first_registered    TEXT,
    monthly_price_pence INTEGER,
    dealer_city         TEXT,
    dealer_postcode     TEXT,
    dealer_phone        TEXT,
    dealer_lat          REAL,
    dealer_lon          REAL,
    image_url           TEXT,
    body_style          TEXT,
    ac_charge_kw        REAL,
    dc_charge_kw        REAL,
    vin                 TEXT,
    previous_owners     INTEGER,
    model_year          INTEGER,

    first_seen        TEXT    NOT NULL,
    last_seen         TEXT    NOT NULL,
    last_seen_run_id  INTEGER,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    source      TEXT    NOT NULL,
    source_id   TEXT    NOT NULL,
    observed_at TEXT    NOT NULL,
    price_pence INTEGER NOT NULL,
    PRIMARY KEY (source, source_id, observed_at)
);

CREATE TABLE IF NOT EXISTS raw_listings (
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                   INTEGER PRIMARY KEY,
    source               TEXT NOT NULL,
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    expected_total       INTEGER,
    listings_seen        INTEGER NOT NULL DEFAULT 0,
    listings_stored      INTEGER NOT NULL DEFAULT 0,
    skipped_non_electric INTEGER NOT NULL DEFAULT 0,
    mapping_errors       INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    error                TEXT
);

CREATE INDEX IF NOT EXISTS cars_price      ON cars (price_pence);
CREATE INDEX IF NOT EXISTS cars_make_model ON cars (brand, model);
CREATE INDEX IF NOT EXISTS cars_battery    ON cars (battery_kwh);
CREATE INDEX IF NOT EXISTS cars_last_seen  ON cars (last_seen);
"""


class SqliteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._in_transaction = False

    def init_schema(self) -> None:
        with self.connection:
            self.connection.executescript(_DDL)
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Scope a batch of writes (e.g. one source's listings) to one commit.

        Commits once the block exits, whether cleanly or via an exception, so
        that a mid-run failure still leaves the listings seen so far durable.

        Not re-entrant: nesting a transaction() inside another raises
        TransactionError. Without this, the inner block's exit would clear
        the transaction flag and commit early, silently degrading the outer
        block's remaining writes to per-write autocommit.
        """
        if self._in_transaction:
            raise TransactionError(
                "transaction() is not re-entrant"
                " — a nested call would commit early and break the outer"
                " transaction's atomicity"
            )
        self._in_transaction = True
        try:
            yield
        finally:
            self._in_transaction = False
            self.connection.commit()

    def _write(self, fn) -> None:
        """Run fn's writes as part of an open transaction, else commit alone."""
        if self._in_transaction:
            fn()
        else:
            with self.connection:
                fn()

    def upsert_car(self, car: Car, *, observed_at: str, run_id: int | None) -> None:
        """Insert or refresh a listing, preserving first_seen."""
        values = car.model_dump()
        values["fuel_type"] = car.fuel_type.value
        columns = list(values)
        placeholders = ", ".join(f":{name}" for name in columns)
        updates = ", ".join(f"{name} = excluded.{name}" for name in columns)

        def _do() -> None:
            self.connection.execute(
                f"INSERT INTO cars ({', '.join(columns)},"
                " first_seen, last_seen, last_seen_run_id)"
                f" VALUES ({placeholders}, :observed_at, :observed_at, :run_id)"
                " ON CONFLICT (source, source_id) DO UPDATE SET"
                f" {updates}, last_seen = excluded.last_seen,"
                " last_seen_run_id = excluded.last_seen_run_id",
                {**values, "observed_at": observed_at, "run_id": run_id},
            )
            self._record_price(car, observed_at)

        self._write(_do)

    def store_raw(
        self, source: str, source_id: str, payload: str, *, fetched_at: str
    ) -> None:
        """Keep the untouched payload so the mapper can change without re-scraping."""

        def _do() -> None:
            self.connection.execute(
                "INSERT OR REPLACE INTO raw_listings"
                " (source, source_id, fetched_at, payload) VALUES (?, ?, ?, ?)",
                (source, source_id, fetched_at, payload),
            )

        self._write(_do)

    def _record_price(self, car: Car, observed_at: str) -> None:
        """Write history on the first sighting, and thereafter only on a change."""
        latest = self.connection.execute(
            "SELECT price_pence FROM price_history"
            " WHERE source = ? AND source_id = ?"
            " ORDER BY observed_at DESC LIMIT 1",
            (car.source, car.source_id),
        ).fetchone()
        if latest is not None and latest[0] == car.price_pence:
            return
        self.connection.execute(
            "INSERT OR REPLACE INTO price_history"
            " (source, source_id, observed_at, price_pence) VALUES (?, ?, ?, ?)",
            (car.source, car.source_id, observed_at, car.price_pence),
        )

    def start_run(self, source: str, *, started_at: str) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO scrape_runs (source, started_at, status)"
                " VALUES (?, ?, 'running')",
                (source, started_at),
            )
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: str,
        expected_total: int | None,
        listings_seen: int,
        listings_stored: int,
        skipped_non_electric: int,
        mapping_errors: int,
        status: str,
        error: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE scrape_runs SET finished_at = ?, expected_total = ?,"
                " listings_seen = ?, listings_stored = ?, skipped_non_electric = ?,"
                " mapping_errors = ?, status = ?, error = ? WHERE id = ?",
                (
                    finished_at,
                    expected_total,
                    listings_seen,
                    listings_stored,
                    skipped_non_electric,
                    mapping_errors,
                    status,
                    error,
                    run_id,
                ),
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
