import pytest

from carparator.web.query import FIELDS, FilterSpec, parse_filters


def test_an_empty_query_string_filters_nothing():
    spec = parse_filters({})

    assert spec.choices == {}
    assert spec.ranges == {}
    assert spec.texts == {}


def test_a_choice_filter_is_read_from_the_query_string():
    spec = parse_filters({"source": ["cupra", "volkswagen"]})

    assert spec.choices["source"] == ("cupra", "volkswagen")


def test_a_range_filter_is_read_as_a_pair_of_bounds():
    spec = parse_filters({"year_min": ["2022"], "year_max": ["2024"]})

    assert spec.ranges["year"] == (2022, 2024)


def test_one_sided_ranges_are_allowed():
    assert parse_filters({"mileage_max": ["30000"]}).ranges["mileage_miles"] == (
        None,
        30000,
    )


def test_prices_are_entered_in_pounds_and_held_in_pence():
    assert parse_filters({"price_min": ["25000"]}).ranges["price_pence"] == (
        2_500_000,
        None,
    )


def test_a_malformed_number_is_ignored_rather_than_raising():
    spec = parse_filters({"year_min": ["not a year"], "year_max": ["2024"]})

    assert spec.ranges["year"] == (None, 2024)


def test_a_blank_value_is_not_a_filter():
    assert parse_filters({"colour": [""], "source": [""]}) == parse_filters({})


def test_an_unrecognised_parameter_is_ignored():
    assert parse_filters({"favourite_colour": ["red"]}) == parse_filters({})


def test_unknown_values_are_included_until_the_form_says_otherwise():
    assert parse_filters({"range_min": ["200"]}).excluded_unknown == frozenset()


def test_an_unticked_box_on_a_submitted_form_excludes_that_field_s_unknowns():
    """An unticked checkbox submits nothing, so the form marks itself submitted."""
    spec = parse_filters({"submitted": ["1"], "range_min": ["200"]})

    assert "range_miles" in spec.excluded_unknown


def test_a_ticked_box_keeps_unknowns_in():
    spec = parse_filters(
        {"submitted": ["1"], "range_min": ["200"], "unknown_range_miles": ["on"]}
    )

    assert "range_miles" not in spec.excluded_unknown


def test_only_nullable_fields_have_an_unknown_bucket():
    """A NOT NULL column can never hide a row for absence, so it offers no toggle."""
    nullable = {field.name for field in FIELDS if field.nullable}

    assert "year" not in nullable
    assert "price_pence" not in nullable
    assert {"battery_kwh", "power_kw", "trim"} <= nullable

    location = next(field for field in FIELDS if field.name == "location")
    assert location.nullable
    assert location.columns == ("dealer_city", "dealer_postcode")


def test_fields_the_sources_cannot_discriminate_on_are_not_offered():
    offered = {field.name for field in FIELDS}

    assert "fuel_type" not in offered
    assert "transmission" not in offered
    assert "engine_cc" not in offered


def test_a_filter_spec_round_trips_through_its_query_string():
    spec = parse_filters({"submitted": ["1"], "source": ["cupra"], "year_min": ["2022"]})

    assert parse_filters(spec.as_query()) == spec
