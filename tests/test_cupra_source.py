import pytest

from carparator.sources.cupra import parse_cupra_title


@pytest.mark.parametrize(
    "title, battery_kwh, doors",
    [
        ("CUPRA Tavascan 250kW VZ2 77kWh AWD 5dr Auto", 77.0, 5),
        ("CUPRA Born 170kW e-Boost V2 59kWh 5dr Auto", 59.0, 5),
        ("CUPRA Born 140kW V2 58kWh 5dr Auto", 58.0, 5),
        ("CUPRA Tavascan 250kW VZ2 77kWh AWD 5dr Auto *Premium Metallic*", 77.0, 5),
        # AFV titles space the unit and carry no door count.
        ("CUPRA Tavascan VZ1 77 kWh AFV 340 Auto", 77.0, None),
        ("CUPRA Tavascan V2 77 kWh AFV 286 Auto", 77.0, None),
        ("CUPRA Born VZ First Edition 79 kWh AFV 326 Auto", 79.0, None),
        # Neither figure present.
        ("CUPRA Formentor 2.5 TSI 390 VZ5 5dr DSG 4Drive", None, 5),
        ("CUPRA Leon", None, None),
    ],
)
def test_parse_cupra_title_reads_battery_and_doors(title, battery_kwh, doors):
    parsed = parse_cupra_title(title)

    assert parsed.battery_kwh == battery_kwh
    assert parsed.doors == doors


def test_power_in_kilowatts_is_never_mistaken_for_battery_capacity():
    assert parse_cupra_title("CUPRA Tavascan 250kW VZ2 AWD 5dr Auto").battery_kwh is None


import json
from pathlib import Path

from carparator.model import FuelType, RawListing
from carparator.sources.cupra import CupraSource, extract_cupra_features

FIXTURE = Path(__file__).parent / "fixtures" / "cupra_search.json"


def fixture_cars():
    payload = json.loads(FIXTURE.read_text())
    return [entry["car"] for entry in payload["results"]["result"]["cars"]]


def raw(index):
    car = fixture_cars()[index]
    return RawListing(source="cupra", source_id=car["carid"], payload=car)


@pytest.fixture
def source():
    return CupraSource()


def test_to_car_maps_the_core_listing_fields(source):
    car = source.to_car(raw(0))

    assert car.source == "cupra"
    assert car.source_id == "GBR551693296921"
    assert car.brand == "CUPRA"
    assert car.model == "Tavascan"
    assert car.fuel_type is FuelType.ELECTRIC
    assert car.registration == "VA26MVW"
    assert car.dealer_name == "Listers SEAT Worcester"


def test_mileage_and_price_come_from_raw_value_not_the_formatted_string(source):
    car = source.to_car(raw(0))

    assert car.mileage_miles == 2222
    assert car.price_pence == 4998500


def test_year_is_the_registration_year(source):
    car = source.to_car(raw(0))

    assert car.year == 2026
    assert car.first_registered == "2026-07-01"


def test_battery_and_doors_fall_back_to_the_title(source):
    car = source.to_car(raw(0))

    assert car.battery_kwh == 77.0
    assert car.doors == 5


def test_first_class_door_and_seat_fields_are_preferred_over_the_title(source):
    car = source.to_car(raw(1))

    assert car.doors == 5
    assert car.seats == 5


def test_afv_titles_yield_a_battery_but_no_door_count(source):
    car = source.to_car(raw(2))

    assert car.battery_kwh == 77.0
    assert car.doors is None


def test_range_comes_from_the_wltp_combined_figure(source):
    car = source.to_car(raw(0))

    assert car.range_miles == 298.0


def test_power_trim_drivetrain_and_colour_are_mapped(source):
    car = source.to_car(raw(0))

    assert car.power_kw == 250
    assert car.power_ps == 340
    assert car.trim == "VZ"
    assert car.drivetrain == "All-wheel drive"
    assert car.transmission == "Automatic"
    assert car.colour == "Tavascan Blue"


def test_dealer_details_are_mapped(source):
    car = source.to_car(raw(0))

    assert car.dealer_city == "Worcester"
    assert car.dealer_postcode == "WR3 7DG"
    assert car.dealer_phone == "01905 794000"
    assert car.dealer_lat == pytest.approx(52.221649)
    assert car.dealer_lon == pytest.approx(-2.228826)


def test_a_dealer_without_a_phone_number_still_maps(source):
    car = source.to_car(raw(3))

    assert car.dealer_name == "Yeomans SEAT Exeter"
    assert car.dealer_phone is None


def test_monthly_finance_payment_is_mapped_as_pence(source):
    assert source.to_car(raw(1)).monthly_price_pence == 43516


def test_no_finance_offer_leaves_the_monthly_price_unset(source):
    assert source.to_car(raw(0)).monthly_price_pence is None


def test_electric_cars_have_no_engine_capacity(source):
    assert source.to_car(raw(0)).engine_cc is None


def test_non_electric_listings_are_skipped_but_the_engine_maps_when_present(source):
    petrol = fixture_cars()[4]

    assert source.to_car(raw(4)) is None
    assert source.map_car(petrol).engine_cc == 2480
    assert source.map_car(petrol).fuel_type is FuelType.PETROL


def test_image_url_is_the_first_dealer_photo(source):
    car = source.to_car(raw(0))

    assert car.image_url.startswith("https://")


import httpx


def paged_transport(pages, recorder):
    """Serve one canned page per X-Page, recording the requests made."""

    def handler(request):
        recorder.append(request)
        page = int(request.headers["X-Page"])
        cars = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(
            200,
            json={
                "criteria": {
                    "search": {
                        "criterias": [
                            {"criteria": {"key": "t_color"}, "selectedItems": []},
                            {
                                "criteria": {"key": "t_petr"},
                                "selectedItems": [{"key": "E", "number": 248}],
                            },
                        ]
                    }
                },
                "results": {"result": {"cars": [{"car": car} for car in cars]}},
            },
        )

    return httpx.MockTransport(handler)


def source_over(pages, recorder=None):
    recorder = recorder if recorder is not None else []
    client = httpx.Client(transport=paged_transport(pages, recorder))
    return CupraSource(client=client, request_delay=0), recorder


def test_fetch_raw_pages_until_an_empty_page(source):
    cars = fixture_cars()
    paging, requests = source_over([cars[:2], cars[2:4], cars[4:]])

    listings = list(paging.fetch_raw())

    assert [listing.source_id for listing in listings] == [c["carid"] for c in cars]
    assert [r.headers["X-Page"] for r in requests] == ["1", "2", "3", "4"]


def test_fetch_raw_reports_the_facet_count_as_the_expected_total(source):
    paging, _ = source_over([fixture_cars()[:1]])

    list(paging.fetch_raw())

    assert paging.expected_total == 248


def test_fetch_raw_requests_electric_only_as_a_matrix_parameter(source):
    paging, requests = source_over([fixture_cars()[:1]])

    list(paging.fetch_raw())

    assert requests[0].url.path.endswith("/search/car;t_petr=E")
    assert requests[0].headers["X-Pattern"] == "cuprawebfe"
    assert requests[0].headers["Accept-Language"] == "en-GB"


def test_an_immediately_empty_first_page_yields_nothing(source):
    paging, _ = source_over([])

    assert list(paging.fetch_raw()) == []


def test_fetch_raw_carries_the_untouched_payload(source):
    paging, _ = source_over([fixture_cars()[:1]])

    listing = next(iter(paging.fetch_raw()))

    assert listing.payload["carid"] == "GBR551693296921"
    assert paging.to_car(listing).model == "Tavascan"


def test_a_page_past_the_end_omits_the_cars_key_entirely():
    # Live behaviour: beyond the last page the response drops "cars" rather than
    # returning it empty.
    def handler(request):
        if request.headers["X-Page"] == "1":
            return httpx.Response(
                200,
                json={
                    "results": {
                        "result": {"cars": [{"car": fixture_cars()[0]}]},
                    }
                },
            )
        return httpx.Response(200, json={"results": {"result": {}}})

    paging = CupraSource(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay=0
    )

    listings = list(paging.fetch_raw())

    assert [listing.source_id for listing in listings] == ["GBR551693296921"]


DETAIL_FIXTURE = Path(__file__).parent / "fixtures" / "cupra_detail.json"


@pytest.fixture
def detail():
    return json.loads(DETAIL_FIXTURE.read_text(encoding="utf-8"))


def test_reads_both_equipment_lists_off_a_real_detail_response(detail):
    features = extract_cupra_features(detail)

    assert len(features.standard) == 56
    assert len(features.optional) == 29


def test_equipment_groups_are_flattened_in_the_order_they_arrive(detail):
    features = extract_cupra_features(detail)

    assert features.standard[0] == "Four-wheel drive"
    assert features.standard[-1] == "Tire pressure monitoring system"
    assert features.optional[0] == (
        "Exterior mirrors with memory feature,"
        " power-folding/adjustable, separately heated"
    )
    assert features.optional[1] == "Glass roof"
    assert features.optional[-1] == "Tires 225/50 R17 94Y ULET"


def test_insurance_type_classes_are_not_mistaken_for_equipment():
    """special_equip holds insurance data, and its entries are shaped differently."""
    features = extract_cupra_features(
        {
            "serie_equip": [{"key": "e", "values": [{"value": "Heat pump"}]}],
            "special_equip": [
                {
                    "key": "special.additional.insurance",
                    "values": [
                        {"key": "full", "value": "Fully comprehensive cover"}
                    ],
                }
            ],
        }
    )

    assert features.standard == ("Heat pump",)
    assert features.optional == ()


def test_a_response_with_no_standard_equipment_is_a_failure_not_an_empty_car():
    """Every real listing has a standard list, so an empty one means drift."""
    assert extract_cupra_features({"equip": [{"values": [{"value": "Tow bar"}]}]}) is None
    assert extract_cupra_features({"serie_equip": []}) is None
    assert extract_cupra_features({}) is None
