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
from carparator.sources.cupra import CupraSource

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
