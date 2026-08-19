import logging
import sqlite3

import pytest

from carparator.model import Car, FuelType, RawListing
from carparator.ingest import ingest
from carparator.store import SqliteStore


@pytest.fixture
def store(tmp_path):
    with SqliteStore(tmp_path / "test.db") as store:
        store.init_schema()
        yield store


def a_car(source, source_id, **overrides):
    fields = dict(
        source=source,
        source_id=source_id,
        brand="CUPRA",
        model="Born",
        mileage_miles=100,
        year=2024,
        price_pence=1000000,
        dealer_name="A Dealer",
        fuel_type=FuelType.ELECTRIC,
    )
    return Car(**{**fields, **overrides})


class FakeSource:
    """A source whose pages, totals and failures are dictated by the test."""

    def __init__(self, name, ids, expected_total=None, non_electric=(), broken=(),
                 fail_after=None):
        self.name = name
        self._ids = ids
        self.expected_total = expected_total if expected_total is not None else len(ids)
        self._non_electric = set(non_electric)
        self._broken = set(broken)
        self._fail_after = fail_after

    def fetch_raw(self):
        for index, source_id in enumerate(self._ids):
            if self._fail_after is not None and index == self._fail_after:
                raise RuntimeError(f"{self.name} endpoint went away")
            yield RawListing(source=self.name, source_id=source_id,
                             payload={"id": source_id})

    def to_car(self, raw):
        if raw.source_id in self._broken:
            raise ValueError("unmappable record")
        if raw.source_id in self._non_electric:
            return None
        return a_car(self.name, raw.source_id)


class FakeSourceWithFailedPages(FakeSource):
    """A FakeSource that also retains failed-page bodies, like VolkswagenSource."""

    def __init__(self, *args, failed_pages=(), failed_page_bodies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.failed_pages = list(failed_pages)
        self.failed_page_bodies = list(failed_page_bodies)


def runs(store):
    store.connection.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in store.connection.execute(
            "SELECT * FROM scrape_runs ORDER BY id")]
    finally:
        store.connection.row_factory = None


def car_count(store, source):
    return store.connection.execute(
        "SELECT COUNT(*) FROM cars WHERE source = ?", (source,)).fetchone()[0]


def test_ingest_stores_every_listing_a_source_yields(store):
    ingest([FakeSource("cupra", ["a", "b", "c"])], store)

    assert car_count(store, "cupra") == 3


def test_a_complete_run_is_recorded_with_its_counts(store):
    ingest([FakeSource("cupra", ["a", "b", "c"], expected_total=3)], store)

    (run,) = runs(store)
    assert run["source"] == "cupra"
    assert run["status"] == "complete"
    assert run["expected_total"] == 3
    assert run["listings_seen"] == 3
    assert run["listings_stored"] == 3
    assert run["finished_at"] is not None


def test_non_electric_skips_are_counted_separately_from_mapping_errors(store):
    ingest(
        [FakeSource("cupra", ["a", "b", "c", "d"], non_electric=["b"], broken=["c"])],
        store,
    )

    (run,) = runs(store)
    assert run["skipped_non_electric"] == 1
    assert run["mapping_errors"] == 1
    assert run["listings_seen"] == 4
    assert run["listings_stored"] == 2


def test_a_short_run_is_marked_partial(store):
    ingest([FakeSource("cupra", ["a", "b"], expected_total=10)], store)

    assert runs(store)[0]["status"] == "partial"


def test_a_limited_run_is_marked_partial(store):
    ingest([FakeSource("cupra", ["a", "b", "c"], expected_total=3)], store, limit=2)

    (run,) = runs(store)
    assert run["status"] == "partial"
    assert run["listings_seen"] == 2
    assert car_count(store, "cupra") == 2


def test_a_source_that_fails_mid_run_is_recorded_as_failed_with_its_error(store):
    ingest([FakeSource("cupra", ["a", "b", "c"], fail_after=2)], store)

    (run,) = runs(store)
    assert run["status"] == "failed"
    assert "endpoint went away" in run["error"]
    assert run["listings_seen"] == 2
    assert car_count(store, "cupra") == 2


def test_one_source_failing_does_not_abort_the_other(store):
    results = ingest(
        [
            FakeSource("cupra", ["a", "b", "c"], fail_after=1),
            FakeSource("volkswagen", ["x", "y"]),
        ],
        store,
    )

    assert car_count(store, "volkswagen") == 2
    assert [result.status for result in results] == ["failed", "complete"]
    assert {run["source"]: run["status"] for run in runs(store)} == {
        "cupra": "failed",
        "volkswagen": "complete",
    }


def test_raw_payloads_are_kept_for_every_listing_seen(store):
    ingest([FakeSource("cupra", ["a", "b"], broken=["b"])], store)

    stored = store.connection.execute(
        "SELECT source_id, payload FROM raw_listings ORDER BY source_id").fetchall()
    assert [row[0] for row in stored] == ["a", "b"]
    assert '"id": "b"' in stored[1][1]


def test_stored_cars_are_tagged_with_the_run_that_last_saw_them(store):
    ingest([FakeSource("cupra", ["a"])], store)

    run_id = runs(store)[0]["id"]
    assert store.connection.execute(
        "SELECT last_seen_run_id FROM cars").fetchone()[0] == run_id


def test_a_second_run_updates_rather_than_duplicates(store):
    ingest([FakeSource("cupra", ["a", "b"])], store)

    ingest([FakeSource("cupra", ["a", "b"])], store)

    assert car_count(store, "cupra") == 2
    assert len(runs(store)) == 2


def test_a_multi_listing_source_commits_the_listing_work_once(store):
    statements = []
    store.connection.set_trace_callback(statements.append)

    ingest([FakeSource("cupra", ["a", "b", "c", "d", "e"])], store)

    store.connection.set_trace_callback(None)
    commit_count = statements.count("COMMIT")
    # start_run + one commit for the whole listing loop + finish_run: not one
    # per listing.
    assert commit_count == 3
    assert car_count(store, "cupra") == 5


def test_retained_failed_page_bodies_are_written_to_disk(store, tmp_path):
    source = FakeSourceWithFailedPages(
        "volkswagen", ["a"],
        failed_pages=[3, 7], failed_page_bodies=["<html>3</html>", "<html>7</html>"],
    )

    ingest([source], store)

    directory = tmp_path / "test.db.failed-pages"
    assert (directory / "volkswagen-page3.html").read_text() == "<html>3</html>"
    assert (directory / "volkswagen-page7.html").read_text() == "<html>7</html>"


def test_only_the_retained_bodies_are_written_when_more_pages_failed_than_were_kept(
    store, tmp_path
):
    source = FakeSourceWithFailedPages(
        "volkswagen", ["a"],
        failed_pages=[3, 7, 9], failed_page_bodies=["<html>3</html>"],
    )

    ingest([source], store)

    directory = tmp_path / "test.db.failed-pages"
    assert [path.name for path in directory.iterdir()] == ["volkswagen-page3.html"]


def test_writing_a_failed_page_logs_the_path_it_was_written_to(store, tmp_path, caplog):
    source = FakeSourceWithFailedPages(
        "volkswagen", ["a"], failed_pages=[7], failed_page_bodies=["<html>7</html>"]
    )
    written = tmp_path / "test.db.failed-pages" / "volkswagen-page7.html"

    with caplog.at_level(logging.WARNING):
        ingest([source], store)

    assert str(written) in caplog.text


def test_ingest_result_counts_failed_pages(store):
    source = FakeSourceWithFailedPages(
        "volkswagen", ["a"], failed_pages=[3, 7], failed_page_bodies=["x"]
    )

    (result,) = ingest([source], store)

    assert result.failed_pages == 2


def test_a_source_without_failed_page_attributes_creates_no_failed_pages_directory(
    store, tmp_path
):
    ingest([FakeSource("cupra", ["a"])], store)

    assert not (tmp_path / "test.db.failed-pages").exists()


def test_a_clean_run_creates_no_failed_pages_directory(store, tmp_path):
    ingest([FakeSourceWithFailedPages("volkswagen", ["a"])], store)

    assert not (tmp_path / "test.db.failed-pages").exists()
