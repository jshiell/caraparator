import json
from pathlib import Path

import pytest

from carparator.sources.volkswagen import (
    extract_vw_detail_urls,
    extract_vw_features,
    extract_vw_vehicles,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vw_srp_page1.html"


@pytest.fixture(scope="module")
def page():
    return FIXTURE.read_text(encoding="utf-8")


VDP_FIXTURE = Path(__file__).parent / "fixtures" / "vw_vdp.html"


@pytest.fixture(scope="module")
def detail_page():
    return VDP_FIXTURE.read_text(encoding="utf-8")


def test_extracts_every_vehicle_on_a_real_search_results_page(page):
    vehicles = extract_vw_vehicles(page)

    assert len(vehicles) == 20
    assert all("ID" in vehicle for vehicle in vehicles)


def test_every_vehicle_on_the_page_carries_a_detail_url(page):
    """The SRP's JSON-LD keys on the same ID the vehicle payload does."""
    urls = extract_vw_detail_urls(page)

    assert set(urls) == {str(vehicle["ID"]) for vehicle in extract_vw_vehicles(page)}


def test_a_detail_url_has_its_escaped_slashes_restored(page):
    assert extract_vw_detail_urls(page)["R7EC4DX"] == (
        "https://usedcars.volkswagen.co.uk/en/vehicle_search/volkswagen/golf"
        "/golf-99kw-e-golf-35kwh-5dr-auto-r7ec4dx"
        "?view=list&FUEL_TYPE_LST=ELECTRIC"
    )


def test_reads_both_equipment_lists_off_a_real_detail_page(detail_page):
    features = extract_vw_features(detail_page)

    assert len(features.standard) == 132
    assert len(features.optional) == 5


def test_a_feature_is_captured_exactly_as_the_page_fragments_it(detail_page):
    """VW splits one comma-separated feature across several <li>. Don't reassemble."""
    features = extract_vw_features(detail_page)

    assert features.standard[:4] == (
        "'Lights On' Reminder warning buzzer",
        "ACC - Adaptive cruise control with front assist",
        "forward collision warning",
        "distance monitoring",
    )


def test_a_glossary_popup_does_not_leak_into_the_feature_it_annotates(detail_page):
    """The glossary div is a sibling of the label span, and repeats its text."""
    assert extract_vw_features(detail_page).optional[2] == "Adaptive Cruise Control"


def test_a_listing_with_no_optional_extras_is_not_an_error():
    """The optional heading is legitimately absent on some real listings."""
    features = extract_vw_features(
        '<div class="technical__equipment"><h4>Fitted as standard</h4>'
        '<ul><li><span class="label">Heat pump</span></li></ul></div>'
        '<div class="technical__specification"></div>'
    )

    assert features.standard == ("Heat pump",)
    assert features.optional == ()


def test_a_page_without_an_equipment_region_yields_nothing():
    assert extract_vw_features("<html><body>This listing has sold</body></html>") is None


def test_an_empty_standard_list_is_a_failure_rather_than_a_car_with_no_features():
    """If the label markup moves, storing zero features would never be retried."""
    assert (
        extract_vw_features(
            '<div class="technical__equipment"><h4>Fitted as standard</h4>'
            "<ul><li><em>Heat pump</em></li></ul>"
            "<h4>Fitted optional extras</h4>"
            '<ul><li><span class="label">Tow bar</span></li></ul></div>'
            '<div class="technical__specification"></div>'
        )
        is None
    )


def test_vehicles_are_deduplicated_on_id(page):
    vehicles = extract_vw_vehicles(page)

    assert len({vehicle["ID"] for vehicle in vehicles}) == len(vehicles)


def test_a_repeated_embedding_does_not_double_the_results(page):
    doubled = page + page

    assert len(extract_vw_vehicles(doubled)) == 20


def test_a_value_containing_a_quote_paren_does_not_truncate_the_array():
    blob = json.dumps([{"ID": "1", "NOTE": "call us (weekdays')"}, {"ID": "2"}])
    html = f"<script>let vehicles = JSON.parse('{blob}')\nvehicles.forEach(f)</script>"

    vehicles = extract_vw_vehicles(html)

    assert [vehicle["ID"] for vehicle in vehicles] == ["1", "2"]
    assert vehicles[0]["NOTE"] == "call us (weekdays')"


def test_a_page_with_no_vehicles_yields_nothing():
    assert extract_vw_vehicles("<html><body>no results</body></html>") == []


def test_apostrophes_inside_values_are_parsed_as_delivered(page):
    vehicles = extract_vw_vehicles(page)

    assert any("'" in str(value) for vehicle in vehicles for value in vehicle.values())


from carparator.model import FuelType, RawListing
from carparator.sources.volkswagen import VolkswagenSource


@pytest.fixture(scope="module")
def vehicles(page):
    return extract_vw_vehicles(page)


@pytest.fixture
def source():
    return VolkswagenSource()


def raw(vehicles, index):
    vehicle = vehicles[index]
    return RawListing(source="volkswagen", source_id=vehicle["ID"], payload=vehicle)


def test_to_car_maps_the_core_listing_fields(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.source == "volkswagen"
    assert car.source_id == "R7EC4DX"
    assert car.brand == "Volkswagen"
    assert car.model == "Golf"
    assert car.trim == "e-Golf"
    assert car.fuel_type is FuelType.ELECTRIC
    assert car.mileage_miles == 61490
    assert car.price_pence == 880000
    assert car.doors == 5
    assert car.battery_kwh == 35.8


def test_year_is_the_registration_year_not_the_model_year(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.first_registered == "2019-01-17"
    assert car.year == 2019
    assert car.model_year == 2019


def test_dealer_name_is_stripped_of_trailing_whitespace(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.dealer_name == "Marshall Volkswagen (South Oxford)"
    assert car.dealer_city == "Abingdon"
    assert car.dealer_postcode == "OX14 4TX"


def test_volkswagen_records_carry_no_dealer_coordinates(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.dealer_lat is None
    assert car.dealer_lon is None


def test_sentinel_engine_capacity_of_one_is_discarded(source, vehicles):
    assert vehicles[0]["CAPACITY_CYLINDER_CCA_FLT"] == 1

    assert source.to_car(raw(vehicles, 0)).engine_cc is None


def test_power_is_recorded_in_kilowatts(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.power_kw == 101
    assert car.power_ps is None


def test_electric_specific_fields_are_mapped(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.range_miles == 186
    assert car.ac_charge_kw == 7.2
    assert car.dc_charge_kw == 50


def test_volkswagen_only_fields_are_mapped(source, vehicles):
    car = source.to_car(raw(vehicles, 0))

    assert car.vin == "WVWZZZAUZKW906234"
    assert car.previous_owners == 2
    assert car.body_style == "Hatchback"
    assert car.colour == "Atlantic Blue metallic"
    assert car.drivetrain == "Front wheel drive"
    assert car.transmission == "Automatic"
    assert car.monthly_price_pence == 17074


def test_absent_optional_fields_map_to_none(source, vehicles):
    without_seats = source.to_car(raw(vehicles, 0))
    without_owner = source.to_car(raw(vehicles, 3))
    without_range = source.to_car(raw(vehicles, 9))

    assert without_seats.seats is None
    assert without_owner.previous_owners is None
    assert without_range.range_miles is None
    assert without_range.monthly_price_pence is None


def test_seats_are_mapped_when_present(source, vehicles):
    assert source.to_car(raw(vehicles, 2)).seats == 5


def test_image_url_is_built_from_the_picserver_path(source, vehicles):
    assert source.to_car(raw(vehicles, 0)).image_url == (
        "http://picserver.vwguk.mdxprod.io/userdata/47/11307/fIw4MFmF/1_1024.jpg"
    )


def test_no_main_image_leaves_the_image_url_unset(source, vehicles):
    assert source.to_car(raw(vehicles, 2)).image_url is None


def test_non_electric_listings_are_skipped(source, vehicles):
    petrol = dict(vehicles[0], FUEL_TYPE_LST="Petrol")

    assert source.to_car(RawListing(source="volkswagen", source_id="x", payload=petrol)) is None


def test_a_listing_missing_the_model_is_a_mapping_error(source, vehicles):
    document = dict(vehicles[0])
    document.pop("MODEL_TEXT_STR")

    with pytest.raises(ValueError, match="R7EC4DX"):
        source.to_car(RawListing(source="volkswagen", source_id="x", payload=document))


def test_a_listing_missing_the_mileage_is_a_mapping_error(source, vehicles):
    document = dict(vehicles[0])
    document.pop("MILEAGE_MIL_INT")

    with pytest.raises(ValueError, match="R7EC4DX"):
        source.to_car(RawListing(source="volkswagen", source_id="x", payload=document))


def test_a_listing_missing_the_dealer_name_is_a_mapping_error(source, vehicles):
    document = dict(vehicles[0])
    document.pop("POOL_NAME1_STR")

    with pytest.raises(ValueError, match="R7EC4DX"):
        source.to_car(RawListing(source="volkswagen", source_id="x", payload=document))


def test_a_listing_with_genuinely_zero_mileage_still_maps_to_zero(source, vehicles):
    document = dict(vehicles[0], MILEAGE_MIL_INT=0)

    car = source.to_car(RawListing(source="volkswagen", source_id="x", payload=document))

    assert car.mileage_miles == 0


def test_every_vehicle_on_the_page_maps_without_error(source, vehicles):
    cars = [source.to_car(raw(vehicles, i)) for i in range(len(vehicles))]

    assert all(car is not None for car in cars)
    assert len({car.source_id for car in cars}) == 20


import httpx


def vw_transport(pages, recorder, details=None):
    """Serve one canned page per /pageN path segment, recording the requests.

    Detail pages are served by exact URL, as the source only ever asks for one
    the search page named.
    """
    details = details or {}

    def handler(request):
        recorder.append(request)
        url = str(request.url)
        if url in details:
            return httpx.Response(200, text=details[url])
        number = int(request.url.path.rsplit("page", 1)[1])
        body = pages[number - 1] if number <= len(pages) else EMPTY_PAGE
        return httpx.Response(200, text=body)

    return httpx.MockTransport(handler)


def detail_url(source_id):
    return f"https://usedcars.volkswagen.co.uk/en/vehicle_search/vw/m/car-{source_id}"


def page_of(vehicles, total=1019):
    blob = json.dumps(vehicles)
    # The real page names each detail URL in JSON-LD, with its slashes escaped.
    linked = "".join(
        '{"sku":"%s","description":"a car","url":"%s"}'
        % (vehicle["ID"], detail_url(vehicle["ID"]).replace("/", "\\/"))
        for vehicle in vehicles
    )
    return (
        f'<html><body><script type="application/ld+json">{linked}</script>'
        f"<script>'numberOfResults': '{total}'\n"
        f"let vehicles = JSON.parse('{blob}')\n</script></body></html>"
    )


EQUIPPED_PAGE = (
    '<div class="technical__equipment"><h4>Fitted as standard</h4>'
    '<ul><li><span class="label">Heat pump</span></li></ul>'
    "<h4>Fitted optional extras</h4>"
    '<ul><li><span class="label">Glass roof</span></li></ul></div>'
    '<div class="technical__specification"></div>'
)


EMPTY_PAGE = page_of([])


def source_over(pages, recorder=None):
    recorder = recorder if recorder is not None else []
    client = httpx.Client(transport=vw_transport(pages, recorder))
    return VolkswagenSource(client=client, request_delay=0), recorder


def test_fetch_raw_walks_pages_until_one_comes_back_empty(vehicles):
    paging, requests = source_over(
        [page_of(vehicles[:2]), page_of(vehicles[2:4]), page_of(vehicles[4:6])]
    )

    listings = list(paging.fetch_raw())

    assert [listing.source_id for listing in listings] == [
        v["ID"] for v in vehicles[:6]
    ]
    assert [r.url.path.rsplit("/", 1)[1] for r in requests] == [
        "page1",
        "page2",
        "page3",
        "page4",
    ]


def test_the_page_number_is_a_path_segment_and_the_filter_is_uppercase(vehicles):
    paging, requests = source_over([page_of(vehicles[:1])])

    list(paging.fetch_raw())

    assert requests[0].url.path.endswith("/all-brands/all-models/page1")
    assert requests[0].url.params["FUEL_TYPE_LST"] == "ELECTRIC"
    assert requests[0].url.params["view"] == "list"
    assert "page" not in requests[0].url.params


def test_fetch_raw_reads_the_result_count_as_the_expected_total(vehicles):
    paging, _ = source_over([page_of(vehicles[:1], total=1019)])

    list(paging.fetch_raw())

    assert paging.expected_total == 1019


def test_a_page_that_returns_no_vehicles_terminates_regardless_of_status(vehicles):
    # /page99 answers 200 with zero vehicles and nonsense metadata.
    paging, requests = source_over([])

    assert list(paging.fetch_raw()) == []
    assert len(requests) == 1


def test_a_page_that_fails_to_parse_does_not_abort_the_remaining_pages(vehicles):
    paging, _ = source_over(
        [page_of(vehicles[:1]), "<html>totally unexpected</html>", page_of(vehicles[1:2])]
    )

    listings = list(paging.fetch_raw())

    assert [listing.source_id for listing in listings] == [
        vehicles[0]["ID"],
        vehicles[1]["ID"],
    ]
    assert paging.failed_pages == [2]


def test_a_page_that_fails_to_parse_retains_its_raw_html(vehicles):
    paging, _ = source_over(
        [page_of(vehicles[:1]), "<html>totally unexpected</html>", page_of(vehicles[1:2])]
    )

    list(paging.fetch_raw())

    assert paging.failed_page_bodies == ["<html>totally unexpected</html>"]


def test_retained_failed_page_bodies_are_capped_regardless_of_total_failures(vehicles):
    paging, _ = source_over(
        [
            "<html>bad1</html>",
            page_of(vehicles[:1]),
            "<html>bad2</html>",
            page_of(vehicles[1:2]),
            "<html>bad3</html>",
            page_of(vehicles[2:3]),
            "<html>bad4</html>",
        ]
    )

    list(paging.fetch_raw())

    assert paging.failed_pages == [1, 3, 5, 7]
    assert paging.failed_page_bodies == [
        "<html>bad1</html>",
        "<html>bad2</html>",
        "<html>bad3</html>",
    ]


def test_fetch_features_reads_the_detail_page_the_search_page_named(vehicles):
    vehicle = vehicles[0]
    requests = []
    client = httpx.Client(
        transport=vw_transport(
            [page_of([vehicle])],
            requests,
            {detail_url(vehicle["ID"]): EQUIPPED_PAGE},
        )
    )
    source = VolkswagenSource(client=client, request_delay=0)
    list(source.fetch_raw())

    features = source.fetch_features(vehicle["ID"])

    assert features.standard == ("Heat pump",)
    assert features.optional == ("Glass roof",)
    assert str(requests[-1].url) == detail_url(vehicle["ID"])


def test_fetch_features_for_a_listing_it_never_saw_costs_no_request(vehicles):
    requests = []
    source, _ = source_over([page_of(vehicles[:1])], requests)
    list(source.fetch_raw())
    already_made = len(requests)

    assert source.fetch_features("NOSUCHID") is None
    assert len(requests) == already_made
