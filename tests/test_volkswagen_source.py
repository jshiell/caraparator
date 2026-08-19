import json
from pathlib import Path

import pytest

from carparator.sources.volkswagen import extract_vw_vehicles

FIXTURE = Path(__file__).parent / "fixtures" / "vw_srp_page1.html"


@pytest.fixture(scope="module")
def page():
    return FIXTURE.read_text(encoding="utf-8")


def test_extracts_every_vehicle_on_a_real_search_results_page(page):
    vehicles = extract_vw_vehicles(page)

    assert len(vehicles) == 20
    assert all("ID" in vehicle for vehicle in vehicles)


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


def test_every_vehicle_on_the_page_maps_without_error(source, vehicles):
    cars = [source.to_car(raw(vehicles, i)) for i in range(len(vehicles))]

    assert all(car is not None for car in cars)
    assert len({car.source_id for car in cars}) == 20
