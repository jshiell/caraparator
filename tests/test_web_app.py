import re

import pytest

from carparator.ingest import COMPLETE, PARTIAL
from carparator.model import Car, FuelType, ListingFeatures
from carparator.store import SqliteStore
from carparator.web.app import create_app
from carparator.web.reader import Reader

WHEN = "2026-08-01T00:00:00Z"
SOURCE = "cupra"


def car(source=SOURCE, source_id="a", **overrides):
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


def client(tmp_path, cars=(), *, runs=(), features=(), now="2026-08-11T00:00:00Z"):
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
                features_fetched=0,
                feature_errors=0,
                status=status,
            )
        for each in cars:
            store.upsert_car(each, observed_at=WHEN, run_id=None)
        for source_id, listing_features in features:
            store.store_features(SOURCE, source_id, listing_features, fetched_at=WHEN)
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


def test_the_list_shows_each_car_trim(tmp_path):
    page = client(tmp_path, [car(trim="V2")]).get("/")

    assert "V2" in body(page)


def test_a_car_with_no_trim_shows_the_em_dash(tmp_path):
    page = body(client(tmp_path, [car(trim=None)]).get("/"))

    assert "\u2014" in page
    assert "None" not in page


@pytest.mark.parametrize("runs", [(), (("cupra", COMPLETE),)])
def test_every_body_row_has_a_cell_for_every_column_header(tmp_path, runs):
    """The header loops over SORTS but the cells are written out by hand, so a
    column added to one and not the other would silently shift every value."""
    page = body(client(tmp_path, [car()], runs=runs).get("/"))

    table = re.search(r"<table>.*</table>", page, re.S).group()
    header, *body_rows = re.split(r"<tr>", table)[1:]
    assert body_rows
    for row in body_rows:
        assert row.count("<td>") == header.count("<th>")


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_sorting_by_trim_puts_the_cars_with_none_last(tmp_path, direction):
    """Ascending is the case that bites: SQLite orders NULLs first, which would
    present every car whose trim nobody stated as though it sorted before V1."""
    web = client(tmp_path, [car(source_id="a", trim=None), car(source_id="b", trim="V2")])

    page = body(web.get(f"/?sort=trim&dir={direction}"))

    assert page.index("/car/cupra/b") < page.index("/car/cupra/a")


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


def detail(web, source="cupra", source_id="a"):
    return web.get(f"/car/{source}/{source_id}")


def test_the_detail_page_shows_the_car(tmp_path):
    web = client(tmp_path, [car(trim="V2", colour="Aurora Blue", vin="ABC123")])

    page = body(detail(web))

    assert "Aurora Blue" in page
    assert "ABC123" in page


def test_the_detail_page_lists_the_optional_features(tmp_path):
    web = client(
        tmp_path,
        [car()],
        features=[("a", ListingFeatures(standard=("Alloy wheels",), optional=("Winter pack",)))],
    )

    page = body(detail(web))

    assert "Optional features" in page
    assert "<li>Winter pack</li>" in page


def test_an_unknown_car_is_a_404(tmp_path):
    web = client(tmp_path, [car("cupra", "a")])

    assert detail(web, "cupra", "missing").status_code == 404
    assert detail(web, "volkswagen", "a").status_code == 404


def test_a_source_id_containing_a_slash_still_resolves(tmp_path):
    web = client(tmp_path, [car(source_id="a/b")])

    assert detail(web, "cupra", "a/b").status_code == 200


def test_the_detail_page_shows_the_price_history_with_its_changes(tmp_path):
    db = tmp_path / "c.db"
    with SqliteStore(db) as store:
        store.init_schema()
        store.upsert_car(car(price_pence=3_000_000), observed_at=WHEN, run_id=None)
        store.upsert_car(
            car(price_pence=2_800_000), observed_at="2026-08-05T00:00:00Z", run_id=None
        )
    app = create_app(Reader(db))
    app.config["TESTING"] = True

    page = body(app.test_client().get("/car/cupra/a"))

    assert "£30,000" in page
    assert "£28,000" in page
    assert "−£2,000" in page


def test_fields_the_source_never_provides_are_named_once_together(tmp_path):
    web = client(tmp_path, [car()])

    page = body(detail(web))

    assert page.count("Not provided by this listing") == 1
    assert "VIN" in page


def test_no_link_to_the_original_listing_is_invented(tmp_path):
    """Neither source exposes a listing URL, so there is nothing to link to."""
    web = client(tmp_path, [car()])

    page = body(detail(web)).lower()

    assert "cupra.co.uk" not in page
    assert "volkswagen.co.uk" not in page


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html;base64,PHN2Zz4=", "file:///etc/passwd"]
)
def test_an_image_url_with_an_unexpected_scheme_is_not_loaded(tmp_path, url):
    web = client(tmp_path, [car(image_url=url)])

    assert "<img" not in body(detail(web))


def test_an_ordinary_image_url_is_loaded(tmp_path):
    web = client(tmp_path, [car(image_url="https://images.example/car.jpg")])

    assert 'src="https://images.example/car.jpg"' in body(detail(web))


def test_the_list_links_to_each_car(tmp_path):
    web = client(tmp_path, [car(source_id="a/b")])

    assert "/car/cupra/a/b" in body(web.get("/"))


def tel_href(page: str) -> str | None:
    """The dialable target of the phone link, or None if there isn't one."""
    match = re.search(r'href="tel:([^"]*)"', page)
    return match.group(1) if match else None


@pytest.mark.parametrize(
    "raw",
    [
        '<script>alert(1)</script>01244555555',
        '" onmouseover="alert(1)01244555555',
        "javascript:alert(1)//+441244555555",
    ],
)
def test_a_phone_number_carrying_markup_or_a_scheme_does_not_reach_the_tel_href(
    tmp_path, raw
):
    """dealer_phone is third-party free text rendered into a tel: href. Only
    digits and `+` may reach it, so markup or a non-dial scheme must not
    survive into the link target."""
    web = client(tmp_path, [car(dealer_phone=raw)])

    href = tel_href(body(detail(web)))

    assert href is not None
    assert re.fullmatch(r"[\d+]*", href)
    assert "script" not in href
    assert "javascript" not in href
    assert '"' not in href


def test_an_ordinary_phone_number_still_produces_a_working_tel_link(tmp_path):
    web = client(tmp_path, [car(dealer_phone="01244 555 555")])

    assert tel_href(body(detail(web))) == "01244555555"


def test_the_list_view_does_not_mention_drivetrain(tmp_path):
    """It neither columns nor filters on drivetrain; the detail page still states it."""
    web = client(tmp_path, [car(drivetrain="Rear-wheel drive")])

    page = body(web.get("/"))

    assert "Rear-wheel drive" not in page
    assert "Drivetrain" not in page
    assert "Rear-wheel drive" in body(detail(web))
