"""Canary tests against the real endpoints. Opt in with `pytest -m live`."""

import itertools

import pytest

from carparator.model import FuelType
from carparator.sources.cupra import CupraSource
from carparator.sources.volkswagen import VolkswagenSource

pytestmark = pytest.mark.live


def first_page(source, count):
    return list(itertools.islice(source.fetch_raw(), count))


# Sanity ranges, not fixed totals — inventory drifts daily.
@pytest.mark.parametrize(
    "build_source, low, high",
    [
        (CupraSource, 150, 400),
        (VolkswagenSource, 700, 1500),
    ],
)
def test_the_endpoint_still_reports_a_plausible_total(build_source, low, high):
    source = build_source()

    first_page(source, 1)

    assert source.expected_total is not None, "expected-total marker disappeared"
    assert low <= source.expected_total <= high


@pytest.mark.parametrize("build_source", [CupraSource, VolkswagenSource])
def test_a_sampled_live_record_still_maps_cleanly(build_source):
    source = build_source()

    listings = first_page(source, 5)

    assert listings, "no listings returned"
    cars = [source.to_car(listing) for listing in listings]
    assert all(car is not None for car in cars), "electric filter rejected everything"
    for car in cars:
        assert car.fuel_type is FuelType.ELECTRIC
        assert car.price_pence > 0
        assert 1990 < car.year <= 2100
        assert car.dealer_name
