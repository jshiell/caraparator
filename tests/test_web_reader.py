import sqlite3

import pytest

from carparator.model import Car, FuelType
from carparator.store import SqliteStore
from carparator.web.reader import DatabaseNotFound, Reader, SchemaMismatch


def car(source="cupra", source_id="a", **overrides):
    fields = dict(
        source=source,
        source_id=source_id,
        brand="CUPRA",
        model="Born",
        mileage_miles=1000,
        year=2024,
        price_pence=2_500_000,
        dealer_name="A Dealer",
        fuel_type=FuelType.ELECTRIC,
    )
    return Car(**{**fields, **overrides})


def build(path, cars=(), *, observed_at="2026-08-01T00:00:00Z", run_id=None):
    """Write a database and close it, leaving no WAL sidecars behind."""
    with SqliteStore(path) as store:
        store.init_schema()
        for each in cars:
            store.upsert_car(each, observed_at=observed_at, run_id=run_id)
    return path


def test_reader_returns_the_stored_cars(tmp_path):
    db = build(tmp_path / "cars.db", [car(source_id="a"), car(source_id="b")])

    listed = Reader(db).cars()

    assert sorted(each["source_id"] for each in listed) == ["a", "b"]


def test_reader_opens_a_database_whose_wal_sidecars_are_absent(tmp_path):
    db = build(tmp_path / "cars.db", [car()])
    assert not (tmp_path / "cars.db-wal").exists()
    assert not (tmp_path / "cars.db-shm").exists()

    assert len(Reader(db).cars()) == 1


def test_reader_exposes_every_car_column(tmp_path):
    db = build(tmp_path / "cars.db", [car(battery_kwh=77.0, colour="Aurora Blue")])

    (only,) = Reader(db).cars()

    assert only["battery_kwh"] == 77.0
    assert only["colour"] == "Aurora Blue"
    assert only["last_seen"] == "2026-08-01T00:00:00Z"


def test_a_missing_database_names_the_path_it_looked_for(tmp_path):
    reader = Reader(tmp_path / "absent.db")

    with pytest.raises(DatabaseNotFound) as raised:
        reader.cars()

    assert "absent.db" in str(raised.value)
    assert "carparator scrape" in str(raised.value)


@pytest.mark.parametrize("version", [0, 2])
def test_a_foreign_schema_version_is_refused(tmp_path, version):
    db = build(tmp_path / "cars.db", [car()])
    with sqlite3.connect(db) as connection:
        connection.execute(f"PRAGMA user_version = {version}")

    with pytest.raises(SchemaMismatch) as raised:
        Reader(db).cars()

    assert str(version) in str(raised.value)
    assert "re-scrape" in str(raised.value)


def test_the_current_schema_version_is_accepted(tmp_path):
    db = build(tmp_path / "cars.db", [car()])

    assert len(Reader(db).cars()) == 1
