import sqlite3

import pytest

from carparator.model import Car, FuelType
from carparator.store import SCHEMA_VERSION, SqliteStore, TransactionError


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


def test_the_schema_version_is_two_now_that_features_are_stored(store):
    """car_features and cars.features_fetched_at make a v1 database unreadable."""
    assert SCHEMA_VERSION == 2


def test_init_schema_creates_the_car_features_table(store):
    assert columns(store, "car_features") == {
        "source",
        "source_id",
        "kind",
        "position",
        "feature",
    }


def test_car_features_admits_only_the_two_kinds_a_source_publishes(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "INSERT INTO car_features (source, source_id, kind, position, feature)"
            " VALUES ('cupra', 'x', 'desirable', 0, 'Glass roof')"
        )


def test_cars_records_whether_features_have_been_fetched(store):
    assert "features_fetched_at" in columns(store, "cars")


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


def test_store_exposes_the_path_it_was_opened_with(tmp_path):
    path = tmp_path / "test.db"
    with SqliteStore(path) as store:
        assert store.path == path


def a_car(**overrides):
    fields = dict(
        source="cupra",
        source_id="GBR1",
        brand="CUPRA",
        model="Tavascan",
        battery_kwh=77.0,
        doors=5,
        mileage_miles=2222,
        year=2026,
        registration="VA26MVW",
        price_pence=4998500,
        dealer_name="Listers SEAT Worcester",
        fuel_type=FuelType.ELECTRIC,
    )
    return Car(**{**fields, **overrides})


def stored_cars(store):
    store.connection.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in store.connection.execute("SELECT * FROM cars")]
    finally:
        store.connection.row_factory = None


def history(store):
    return store.connection.execute(
        "SELECT observed_at, price_pence FROM price_history ORDER BY observed_at"
    ).fetchall()


def test_upsert_car_inserts_a_new_listing(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    (row,) = stored_cars(store)
    assert row["source_id"] == "GBR1"
    assert row["battery_kwh"] == 77.0
    assert row["fuel_type"] == "electric"
    assert row["first_seen"] == "2026-08-19T10:00:00Z"
    assert row["last_seen"] == "2026-08-19T10:00:00Z"
    assert row["last_seen_run_id"] == 1


def test_first_observation_always_records_a_price_history_row(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    assert history(store) == [("2026-08-19T10:00:00Z", 4998500)]


def test_re_upsert_updates_the_same_row_and_preserves_first_seen(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    store.upsert_car(
        a_car(mileage_miles=2500), observed_at="2026-08-20T10:00:00Z", run_id=2
    )

    (row,) = stored_cars(store)
    assert row["first_seen"] == "2026-08-19T10:00:00Z"
    assert row["last_seen"] == "2026-08-20T10:00:00Z"
    assert row["last_seen_run_id"] == 2
    assert row["mileage_miles"] == 2500


def test_unchanged_price_adds_no_history_row(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    store.upsert_car(a_car(), observed_at="2026-08-20T10:00:00Z", run_id=2)

    assert history(store) == [("2026-08-19T10:00:00Z", 4998500)]


def test_price_change_adds_a_history_row(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    store.upsert_car(
        a_car(price_pence=4750000), observed_at="2026-08-20T10:00:00Z", run_id=2
    )

    assert history(store) == [
        ("2026-08-19T10:00:00Z", 4998500),
        ("2026-08-20T10:00:00Z", 4750000),
    ]


def test_price_returning_to_an_earlier_value_is_recorded_as_a_change(store):
    store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)
    store.upsert_car(
        a_car(price_pence=4750000), observed_at="2026-08-20T10:00:00Z", run_id=2
    )

    store.upsert_car(a_car(), observed_at="2026-08-21T10:00:00Z", run_id=3)

    assert history(store)[-1] == ("2026-08-21T10:00:00Z", 4998500)


def test_store_raw_persists_the_untouched_payload(store):
    store.store_raw("cupra", "GBR1", '{"carid": "GBR1"}', fetched_at="2026-08-19T10:00:00Z")

    assert store.connection.execute(
        "SELECT payload, fetched_at FROM raw_listings"
    ).fetchall() == [('{"carid": "GBR1"}', "2026-08-19T10:00:00Z")]


def test_store_raw_overwrites_on_a_later_run(store):
    store.store_raw("cupra", "GBR1", "old", fetched_at="2026-08-19T10:00:00Z")

    store.store_raw("cupra", "GBR1", "new", fetched_at="2026-08-20T10:00:00Z")

    assert store.connection.execute(
        "SELECT payload, fetched_at FROM raw_listings"
    ).fetchall() == [("new", "2026-08-20T10:00:00Z")]


def test_transaction_commits_once_for_a_single_level_block(store):
    with store.transaction():
        store.upsert_car(a_car(), observed_at="2026-08-19T10:00:00Z", run_id=1)

    (row,) = stored_cars(store)
    assert row["source_id"] == "GBR1"


def test_nested_transaction_raises_instead_of_silently_degrading(store):
    with store.transaction():
        with pytest.raises(TransactionError):
            with store.transaction():
                pass
