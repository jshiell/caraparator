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
        lambda sources, store, **kwargs: [
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


def test_report_line_states_what_the_feature_pass_did(
    tmp_path, capsys, monkeypatch
):
    from carparator import cli
    from carparator.ingest import IngestResult

    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])
    monkeypatch.setattr(
        cli,
        "ingest",
        lambda sources, store, **kwargs: [
            IngestResult(
                source="volkswagen",
                run_id=1,
                expected_total=1,
                listings_seen=1,
                listings_stored=1,
                features_fetched=1,
                feature_errors=3,
            )
        ],
    )

    main(["scrape", "--db", str(tmp_path / "cars.db")])

    report = capsys.readouterr().out
    assert "features 1" in report
    assert "feature_errors 3" in report


def ingest_kwargs(tmp_path, monkeypatch, argv):
    from carparator import cli

    seen = {}
    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])

    def spy(sources, store, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cli, "ingest", spy)
    main(["scrape", "--db", str(tmp_path / "cars.db"), *argv])
    return seen


def test_refetch_features_is_off_by_default(tmp_path, monkeypatch):
    assert ingest_kwargs(tmp_path, monkeypatch, [])["refetch_features"] is False


def test_refetch_features_is_passed_through(tmp_path, monkeypatch):
    """Detail responses are not retained, so this is the only way back from an
    extraction bug short of a full re-scrape."""
    seen = ingest_kwargs(tmp_path, monkeypatch, ["--refetch-features"])

    assert seen["refetch_features"] is True


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


def scraped(path):
    from carparator.store import SqliteStore

    with SqliteStore(path) as store:
        store.init_schema()
    return path


def test_serve_binds_the_loopback_address_only(tmp_path, monkeypatch):
    """The UI is a local tool and the Werkzeug debugger is remote code
    execution for anything that can reach the port."""
    from flask import Flask

    captured = {}
    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: captured.update(kwargs))

    assert main(["serve", "--db", str(scraped(tmp_path / "c.db")), "--port", "8123"]) == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["debug"] is False
    assert captured["use_reloader"] is False


def test_serve_without_the_web_extra_says_how_to_install_it(tmp_path, monkeypatch, capsys):
    import sys

    monkeypatch.setitem(sys.modules, "flask", None)
    monkeypatch.delitem(sys.modules, "carparator.web.app", raising=False)

    exit_code = main(["serve", "--db", str(scraped(tmp_path / "c.db"))])

    assert exit_code == 1
    assert "--extra web" in capsys.readouterr().err


def test_serve_on_a_missing_database_explains_rather_than_traces(tmp_path, capsys):
    exit_code = main(["serve", "--db", str(tmp_path / "absent.db")])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "absent.db" in error
    assert "Traceback" not in error


def test_scrape_still_runs_when_the_web_extra_is_absent(tmp_path, monkeypatch):
    import sys

    from carparator import cli

    monkeypatch.setitem(sys.modules, "flask", None)
    monkeypatch.delitem(sys.modules, "carparator.web.app", raising=False)
    monkeypatch.setattr(cli, "build_sources", lambda name: [_StubSource()])

    assert main(["scrape", "--db", str(tmp_path / "c.db")]) == 0
