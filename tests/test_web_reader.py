import sqlite3
import threading
from contextlib import contextmanager

import pytest

from carparator.ingest import COMPLETE, PARTIAL
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


def test_the_database_cannot_be_written_through_a_reader(tmp_path):
    db = build(tmp_path / "cars.db", [car()])
    reader = Reader(db)

    with pytest.raises(sqlite3.OperationalError):
        reader._query("DELETE FROM cars")

    assert len(Reader(db).cars()) == 1


def test_a_query_from_another_thread_succeeds(tmp_path):
    """Flask's dev server is threaded; one shared connection would raise."""
    db = build(tmp_path / "cars.db", [car()])
    reader = Reader(db)
    reader.cars()
    outcome = []

    thread = threading.Thread(target=lambda: outcome.append(reader.cars()))
    thread.start()
    thread.join()

    assert [len(each) for each in outcome] == [1]


WHEN = "2026-08-01T00:00:00Z"


@contextmanager
def writing(path):
    with SqliteStore(path) as store:
        store.init_schema()
        yield store


def add_run(store, source, status, at=WHEN):
    run_id = store.start_run(source, started_at=at)
    store.finish_run(
        run_id,
        finished_at=at,
        expected_total=1,
        listings_seen=1,
        listings_stored=1,
        skipped_non_electric=0,
        mapping_errors=0,
        status=status,
    )
    return run_id


def ids(cars):
    return sorted(each["source_id"] for each in cars)


def test_a_source_with_no_complete_run_keeps_every_car(tmp_path):
    db = tmp_path / "cars.db"
    with writing(db) as store:
        old = add_run(store, "cupra", PARTIAL)
        new = add_run(store, "cupra", PARTIAL)
        store.upsert_car(car(source_id="old"), observed_at=WHEN, run_id=old)
        store.upsert_car(car(source_id="new"), observed_at=WHEN, run_id=new)

    assert ids(Reader(db).current_stock()) == ["new", "old"]


def test_a_complete_run_drops_the_cars_it_did_not_see(tmp_path):
    db = tmp_path / "cars.db"
    with writing(db) as store:
        old = add_run(store, "cupra", COMPLETE)
        current = add_run(store, "cupra", COMPLETE)
        store.upsert_car(car(source_id="sold"), observed_at=WHEN, run_id=old)
        store.upsert_car(car(source_id="listed"), observed_at=WHEN, run_id=current)

    assert ids(Reader(db).current_stock()) == ["listed"]


def test_a_partial_run_after_a_complete_one_still_proves_a_car_exists(tmp_path):
    """`carparator scrape --limit 20` is partial by construction. Its cars are
    the most recently confirmed to exist, so they must not be hidden."""
    db = tmp_path / "cars.db"
    with writing(db) as store:
        complete = add_run(store, "cupra", COMPLETE)
        limited = add_run(store, "cupra", PARTIAL)
        store.upsert_car(car(source_id="seen-fully"), observed_at=WHEN, run_id=complete)
        store.upsert_car(car(source_id="seen-limited"), observed_at=WHEN, run_id=limited)

    assert ids(Reader(db).current_stock()) == ["seen-fully", "seen-limited"]


def test_each_source_is_scoped_by_its_own_latest_complete_run(tmp_path):
    db = tmp_path / "cars.db"
    with writing(db) as store:
        stale_cupra = add_run(store, "cupra", COMPLETE)
        vw = add_run(store, "volkswagen", PARTIAL)
        fresh_cupra = add_run(store, "cupra", COMPLETE)
        store.upsert_car(car("cupra", "cupra-sold"), observed_at=WHEN, run_id=stale_cupra)
        store.upsert_car(car("cupra", "cupra-listed"), observed_at=WHEN, run_id=fresh_cupra)
        store.upsert_car(car("volkswagen", "vw-old"), observed_at=WHEN, run_id=vw)

    assert ids(Reader(db).current_stock()) == ["cupra-listed", "vw-old"]


def test_a_car_with_no_run_recorded_is_never_dropped(tmp_path):
    """NULL >= 5 is NULL, so a bare comparison discards these rows silently."""
    db = tmp_path / "cars.db"
    with writing(db) as store:
        complete = add_run(store, "cupra", COMPLETE)
        store.upsert_car(car(source_id="listed"), observed_at=WHEN, run_id=complete)
        store.upsert_car(car(source_id="unattributed"), observed_at=WHEN, run_id=None)

    assert ids(Reader(db).current_stock()) == ["listed", "unattributed"]
