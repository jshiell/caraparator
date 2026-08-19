"""SQLite persistence. Hand-written DDL; no migrations — see README."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
