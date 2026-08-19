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
