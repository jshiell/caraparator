import sqlite3

import pytest

from carparator.cli import build_sources, main


def test_defaults_to_both_sources():
    assert [source.name for source in build_sources(None)] == ["cupra", "volkswagen"]


@pytest.mark.parametrize("name", ["cupra", "volkswagen"])
def test_a_single_source_can_be_selected(name):
    assert [source.name for source in build_sources(name)] == [name]


def test_an_unknown_source_is_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["scrape", "--source", "tesla"])


def test_scrape_creates_the_database_and_reports_per_source(tmp_path, capsys, monkeypatch):
    from carparator import cli

    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])
    db = tmp_path / "cars.db"

    exit_code = main(["scrape", "--db", str(db)])

    assert exit_code == 0
    report = capsys.readouterr().out
    assert "stub" in report
    assert "complete" in report
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM cars").fetchone()[0] == 1


def test_a_failing_source_exits_non_zero(tmp_path, monkeypatch):
    from carparator import cli

    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource(broken=True)])

    assert main(["scrape", "--db", str(tmp_path / "cars.db")]) == 1


def test_report_line_mentions_failed_pages_when_a_source_dropped_any(
    tmp_path, capsys, monkeypatch
):
    from carparator import cli
    from carparator.ingest import IngestResult

    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])
    monkeypatch.setattr(
        cli,
        "ingest",
        lambda sources, store, limit=None: [
            IngestResult(
                source="volkswagen",
                run_id=1,
                expected_total=1,
                listings_seen=1,
                listings_stored=1,
                failed_pages=2,
            )
        ],
    )

    main(["scrape", "--db", str(tmp_path / "cars.db")])

    report = capsys.readouterr().out
    assert "failed_pages 2" in report


def test_report_line_omits_failed_pages_when_none_were_dropped(
    tmp_path, capsys, monkeypatch
):
    from carparator import cli

    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])

    main(["scrape", "--db", str(tmp_path / "cars.db")])

    report = capsys.readouterr().out
    assert "failed_pages" not in report


class _StubSource:
    name = "stub"
    expected_total = 1

    def __init__(self, broken=False):
        self._broken = broken

    def fetch_raw(self):
        if self._broken:
            raise RuntimeError("boom")
        from carparator.model import RawListing

        yield RawListing(source="stub", source_id="a", payload={})

    def to_car(self, raw):
        from carparator.model import Car, FuelType

        return Car(
            source="stub",
            source_id=raw.source_id,
            brand="CUPRA",
            model="Born",
            mileage_miles=1,
            year=2024,
            price_pence=1,
            dealer_name="d",
            fuel_type=FuelType.ELECTRIC,
        )
