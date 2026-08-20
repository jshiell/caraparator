import pytest

from carparator.ingest import COMPLETE, PARTIAL
from carparator.model import Car, FuelType
from carparator.store import SqliteStore
from carparator.web.app import create_app
from carparator.web.reader import Reader

WHEN = "2026-08-01T00:00:00Z"


def car(source="cupra", source_id="a", **overrides):
    fields = dict(
        source=source,
        source_id=source_id,
        brand="CUPRA",
        model="Born",
        mileage_miles=10_000,
        year=2023,
        price_pence=3_000_000,
        dealer_name="A Dealer",
        fuel_type=FuelType.ELECTRIC,
    )
    return Car(**{**fields, **overrides})


def client(tmp_path, cars=(), *, runs=(), now="2026-08-11T00:00:00Z"):
    db = tmp_path / "c.db"
    with SqliteStore(db) as store:
        store.init_schema()
        for source, status in runs:
            run_id = store.start_run(source, started_at=WHEN)
            store.finish_run(
                run_id,
                finished_at=WHEN,
                expected_total=1,
                listings_seen=1,
                listings_stored=1,
                skipped_non_electric=0,
                mapping_errors=0,
                status=status,
            )
        for each in cars:
            store.upsert_car(each, observed_at=WHEN, run_id=None)
    app = create_app(Reader(db), now=lambda: now)
    app.config["TESTING"] = True
    return app.test_client()


def body(response):
    return response.get_data(as_text=True)


def test_the_list_shows_the_cars(tmp_path):
    page = client(tmp_path, [car(dealer_name="Swansway Chester")]).get("/")

    assert page.status_code == 200
    assert "Swansway Chester" in body(page)
    assert "£30,000" in body(page)


def test_the_list_applies_the_filters_in_the_query_string(tmp_path):
    web = client(
        tmp_path,
        [
            car(source_id="a", dealer_name="Swansway Chester"),
            car(source_id="b", dealer_name="Marshall Oxford"),
        ],
    )

    page = body(web.get("/?dealer=Chester"))

    assert "Swansway Chester" in page
    assert "Marshall Oxford" not in page


def test_markup_in_a_dealer_name_is_shown_not_run(tmp_path):
    """Dealer names, trims and colours are third-party free text."""
    web = client(tmp_path, [car(dealer_name="<script>alert(1)</script>")])

    page = body(web.get("/"))

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_the_result_count_is_shown(tmp_path):
    web = client(tmp_path, [car(source_id="a"), car(source_id="b")])

    assert "2 cars" in body(web.get("/"))


def test_the_banner_says_when_a_source_last_completed_a_run(tmp_path):
    web = client(tmp_path, [car()], runs=[("cupra", COMPLETE)])

    assert "complete" in body(web.get("/")).lower()


def test_the_banner_admits_when_a_source_has_never_completed_a_run(tmp_path):
    web = client(tmp_path, [car()], runs=[("cupra", PARTIAL)])

    page = body(web.get("/"))

    assert "no complete run" in page.lower()
    assert "sold" in page.lower()


def test_the_banner_admits_when_a_source_has_never_run_at_all(tmp_path):
    web = client(tmp_path, [car()])

    assert "never" in body(web.get("/")).lower()


def test_a_source_without_complete_coverage_shows_how_stale_each_car_is(tmp_path):
    """Without a complete run nothing can be called sold, so say how cold it is."""
    web = client(tmp_path, [car()], runs=[("cupra", PARTIAL)], now="2026-08-11T00:00:00Z")

    assert "10 days ago" in body(web.get("/"))


def test_an_active_filter_discloses_how_many_cars_it_cannot_speak_for(tmp_path):
    web = client(
        tmp_path,
        [car(source_id="far", range_miles=250), car(source_id="a"), car(source_id="b")],
    )

    assert "2 unknown" in body(web.get("/?range_min=200"))


def test_a_field_with_nothing_unknown_offers_no_toggle(tmp_path):
    web = client(tmp_path, [car(range_miles=250)])

    assert 'name="unknown_range_miles"' not in body(web.get("/?range_min=200"))


def test_filter_options_are_offered_once_per_folded_spelling(tmp_path):
    web = client(
        tmp_path,
        [car(source_id="a", model="ID.3"), car(source_id="b", model="Id.3")],
    )

    assert body(web.get("/")).count('value="id.3"') == 1
