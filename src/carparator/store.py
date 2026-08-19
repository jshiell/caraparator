"""SQLite persistence. Hand-written DDL; no migrations — see README."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from carparator.model import Car

SCHEMA_VERSION = 1

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
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        with self.connection:
            self.connection.executescript(_DDL)
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def upsert_car(self, car: Car, *, observed_at: str, run_id: int | None) -> None:
        """Insert or refresh a listing, preserving first_seen."""
        values = car.model_dump()
        values["fuel_type"] = car.fuel_type.value
        columns = list(values)
        placeholders = ", ".join(f":{name}" for name in columns)
        updates = ", ".join(f"{name} = excluded.{name}" for name in columns)
        with self.connection:
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

    def store_raw(
        self, source: str, source_id: str, payload: str, *, fetched_at: str
    ) -> None:
        """Keep the untouched payload so the mapper can change without re-scraping."""
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO raw_listings"
                " (source, source_id, fetched_at, payload) VALUES (?, ?, ?, ?)",
                (source, source_id, fetched_at, payload),
            )

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

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
