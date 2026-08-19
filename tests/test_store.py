import sqlite3

import pytest

from carparator.model import Car, FuelType
from carparator.store import SCHEMA_VERSION, SqliteStore


@pytest.fixture
def store(tmp_path):
    with SqliteStore(tmp_path / "test.db") as store:
        store.init_schema()
        yield store


def columns(store, table):
    return {row[1] for row in store.connection.execute(f"PRAGMA table_info({table})")}


def test_init_schema_creates_the_four_tables(store):
    tables = {
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    assert {"cars", "price_history", "raw_listings", "scrape_runs"} <= tables


def test_cars_table_holds_the_car_fields_plus_bookkeeping(store):
    assert {"source", "source_id", "brand", "model", "price_pence"} <= columns(
        store, "cars"
    )
    assert {"first_seen", "last_seen", "last_seen_run_id"} <= columns(store, "cars")


def test_cars_is_keyed_on_source_and_source_id(store):
    store.connection.execute(
        "INSERT INTO cars (source, source_id, brand, model, mileage_miles, year,"
        " price_pence, dealer_name, fuel_type, first_seen, last_seen)"
        " VALUES ('cupra', 'x', 'CUPRA', 'Born', 1, 2024, 1, 'd', 'electric', 'n', 'n')"
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "INSERT INTO cars (source, source_id, brand, model, mileage_miles, year,"
            " price_pence, dealer_name, fuel_type, first_seen, last_seen)"
            " VALUES ('cupra', 'x', 'CUPRA', 'Born', 1, 2024, 1, 'd', 'electric', 'n', 'n')"
        )


def test_init_schema_records_the_schema_version(store):
    (version,) = store.connection.execute("PRAGMA user_version").fetchone()

    assert version == SCHEMA_VERSION


def test_database_uses_write_ahead_logging(store):
    (mode,) = store.connection.execute("PRAGMA journal_mode").fetchone()

    assert mode == "wal"


def test_init_schema_is_idempotent(store):
    store.init_schema()

    assert "cars" in {
        row[0]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
