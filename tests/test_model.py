import pytest
from pydantic import ValidationError

from carparator.model import Car, FuelType


def a_car(**overrides):
    fields = dict(
        source="cupra",
        source_id="GBR551693296921",
        brand="CUPRA",
        model="Tavascan",
        battery_kwh=77.0,
        doors=5,
        mileage_miles=2222,
        year=2026,
        registration="VA26MVW",
        price_pence=4998500,
        dealer_name="Listers SEAT Worcester",
        fuel_type=FuelType.ELECTRIC,
    )
    return Car(**{**fields, **overrides})


def test_car_carries_the_core_listing_fields():
    car = a_car()

    assert car.source_id == "GBR551693296921"
    assert car.model == "Tavascan"
    assert car.price_pence == 4998500
    assert car.fuel_type is FuelType.ELECTRIC


def test_optional_fields_default_to_none():
    car = a_car(battery_kwh=None, doors=None, registration=None)

    assert car.battery_kwh is None
    assert car.doors is None
    assert car.registration is None


def test_car_is_immutable():
    car = a_car()

    with pytest.raises(ValidationError):
        car.price_pence = 1


@pytest.mark.parametrize(
    "value",
    ["electric", "petrol", "diesel", "hybrid", "plug_in_hybrid"],
)
def test_fuel_type_covers_the_values_both_sources_emit(value):
    assert FuelType(value).value == value


def test_model_does_not_reject_non_electric_cars():
    car = a_car(fuel_type=FuelType.PETROL)

    assert car.fuel_type is FuelType.PETROL
