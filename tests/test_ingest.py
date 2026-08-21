import logging
import os
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import contextmanager

import pytest

from carparator.model import Car, FuelType, ListingFeatures, RawListing
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


class FakeSourceWithFeatures(FakeSource):
    """A source that can also report a listing's equipment.

    Deliberately a subclass rather than a change to FakeSource: ingest() reads
    fetch_features defensively, and the plain FakeSource is what proves a source
    without it still works.
    """

    def __init__(self, *args, features=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._features = features or {}
        self.feature_requests = []

    def fetch_features(self, source_id):
        self.feature_requests.append(source_id)
        return self._features.get(source_id)


def features_of(store, kind, source="cupra", source_id="a"):
    return [
        row[0]
        for row in store.connection.execute(
            "SELECT feature FROM car_features"
            " WHERE source = ? AND source_id = ? AND kind = ?"
            " ORDER BY position",
            (source, source_id, kind),
        )
    ]


def some_features(*standard, optional=()):
    return ListingFeatures(standard=standard, optional=optional)


class FakeSourceWithFailedPages(FakeSource):
    """A FakeSource that also retains failed-page bodies, like VolkswagenSource."""

    def __init__(self, *args, failed_pages=(), failed_page_bodies=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.failed_pages = list(failed_pages)
        self.failed_page_bodies = list(failed_page_bodies)


class FakeSourceWithoutTotal(FakeSource):
    """A source that cannot say how many listings it should have found."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_total = None


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


def test_a_finish_run_failure_for_one_source_does_not_abort_the_next(store, monkeypatch):
    original_finish_run = store.finish_run
    calls = []

    def flaky_finish_run(run_id, **kwargs):
        calls.append(run_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return original_finish_run(run_id, **kwargs)

    monkeypatch.setattr(store, "finish_run", flaky_finish_run)

    results = ingest(
        [
            FakeSource("cupra", ["a", "b"]),
            FakeSource("volkswagen", ["x", "y"]),
        ],
        store,
    )

    assert [result.source for result in results] == ["cupra", "volkswagen"]
    assert results[0].status == "failed"
    assert "disk I/O error" in results[0].error
    assert results[1].status == "complete"
    assert car_count(store, "cupra") == 2
    assert car_count(store, "volkswagen") == 2


def test_a_commit_failure_unwinding_a_fetch_failure_keeps_the_original_cause(
    store, monkeypatch
):
    source = FakeSource("cupra", ["a", "b", "c"], fail_after=1)

    @contextmanager
    def flaky_transaction():
        store._in_transaction = True
        try:
            yield
        finally:
            store._in_transaction = False
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "transaction", flaky_transaction)

    ingest([source], store)

    (run,) = runs(store)
    assert run["status"] == "failed"
    assert "endpoint went away" in run["error"]
    assert "disk I/O error" in run["error"]


def test_partial_listings_from_a_failed_source_are_actually_committed(store):
    ingest([FakeSource("cupra", ["a", "b", "c"], fail_after=2)], store)

    with sqlite3.connect(store.path) as second_connection:
        count = second_connection.execute(
            "SELECT COUNT(*) FROM cars WHERE source = 'cupra'"
        ).fetchone()[0]
    assert count == 2


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


def test_a_failed_page_retention_error_does_not_abort_the_run_or_the_next_source(
    store, tmp_path
):
    # Occupy the .failed-pages path with a file, so mkdir() inside retention
    # raises instead of creating a directory.
    (tmp_path / "test.db.failed-pages").write_text("occupied")

    source = FakeSourceWithFailedPages(
        "volkswagen", ["a"], failed_pages=[3], failed_page_bodies=["<html>3</html>"]
    )

    results = ingest([source, FakeSource("cupra", ["x"])], store)

    assert [result.source for result in results] == ["volkswagen", "cupra"]
    assert results[0].status != "failed"
    assert results[0].failed_pages == 1
    assert car_count(store, "cupra") == 1
    assert {run["source"]: run["status"] for run in runs(store)} == {
        "volkswagen": "complete",
        "cupra": "complete",
    }


def test_failed_page_bodies_are_retained_whatever_the_locale(tmp_path):
    """Scrapes run from cron and systemd, where the default encoding is ASCII.

    Every Volkswagen page carries a price in pounds, so retention that leans
    on the locale default would fail precisely when it is needed.
    """
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from types import SimpleNamespace
        from carparator.ingest import IngestResult, _retain_failed_pages

        store = SimpleNamespace(path=Path({str(tmp_path / "test.db")!r}))
        source = SimpleNamespace(
            name="volkswagen",
            failed_pages=[7],
            failed_page_bodies=["<html>price \\u00a34,995</html>"],
        )
        _retain_failed_pages(
            source, store, IngestResult("volkswagen", run_id=1, expected_total=None)
        )
        """
    )
    ascii_locale = dict(
        os.environ, LC_ALL="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=ascii_locale,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    retained = tmp_path / "test.db.failed-pages" / "volkswagen-page7.html"
    assert retained.read_text(encoding="utf-8") == "<html>price \u00a34,995</html>"


def test_a_run_that_cannot_know_its_total_is_never_complete(store):
    """`complete` licenses the sold-listing inference, so it has to be earned.

    A source reporting no expected total leaves the run nothing to be short of,
    so a pass truncated by a site change would otherwise be indistinguishable
    from a full one — and every listing it never reached would look sold.
    """
    ingest([FakeSourceWithoutTotal("volkswagen", ["a", "b"])], store)

    assert [run["status"] for run in runs(store)] == ["partial"]


def test_a_stored_listings_features_are_fetched_and_kept(store):
    source = FakeSourceWithFeatures(
        "cupra",
        ["a"],
        features={"a": some_features("Heat pump", optional=("Tow bar",))},
    )

    ingest([source], store)

    assert features_of(store, "standard") == ["Heat pump"]
    assert features_of(store, "optional") == ["Tow bar"]


def test_a_source_that_cannot_report_features_still_ingests(store):
    ingest([FakeSource("cupra", ["a", "b"])], store)

    assert car_count(store, "cupra") == 2


class FeatureSourceWatchingDurability(FakeSourceWithFeatures):
    """Reports, from a second connection, what was durable when it was asked."""

    def __init__(self, *args, db_path, **kwargs):
        super().__init__(*args, **kwargs)
        self._db_path = db_path
        self.cars_durable_when_asked = []

    def fetch_features(self, source_id):
        with sqlite3.connect(self._db_path) as elsewhere:
            (count,) = elsewhere.execute("SELECT COUNT(*) FROM cars").fetchone()
        self.cars_durable_when_asked.append(count)
        return super().fetch_features(source_id)


def test_features_are_fetched_only_once_the_listing_work_is_committed(store, tmp_path):
    """Folding a 13-minute detail pass into the listing transaction would put
    every listing at risk of a mid-run kill, rather than none of them."""
    source = FeatureSourceWatchingDurability(
        "cupra",
        ["a", "b"],
        db_path=tmp_path / "test.db",
        features={"a": some_features("Heat pump"), "b": some_features("Glass roof")},
    )

    ingest([source], store)

    assert source.cars_durable_when_asked == [2, 2]
