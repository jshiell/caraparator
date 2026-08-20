import pytest

from carparator.ingest import COMPLETE
from carparator.model import Car, FuelType
from carparator.store import SqliteStore
from carparator.web.query import FIELDS, FilterSpec, parse_filters
from carparator.web.reader import Reader


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


def stocked(path, cars):
    with SqliteStore(path) as store:
        store.init_schema()
        for each in cars:
            store.upsert_car(each, observed_at=WHEN, run_id=None)
    return Reader(path)


def found(reader, query):
    return sorted(each["source_id"] for each in reader.search(parse_filters(query)))


def test_no_filters_returns_every_car(tmp_path):
    reader = stocked(tmp_path / "c.db", [car(source_id="a"), car(source_id="b")])

    assert found(reader, {}) == ["a", "b"]


def test_a_choice_filter_keeps_only_the_chosen_values(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car("cupra", "a"), car("volkswagen", "b", brand="Volkswagen")],
    )

    assert found(reader, {"source": ["cupra"]}) == ["a"]


def test_several_choices_of_one_field_are_an_either_or(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car("cupra", "a"), car("volkswagen", "b", brand="Volkswagen")],
    )

    assert found(reader, {"source": ["cupra", "volkswagen"]}) == ["a", "b"]


def test_choices_of_different_fields_narrow_together(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car("cupra", "a"), car("cupra", "b", brand="Volkswagen")],
    )

    assert found(reader, {"source": ["cupra"], "brand": ["Volkswagen"]}) == ["b"]


def test_a_model_choice_ignores_the_spelling_the_source_used(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="upper", model="ID.3"), car(source_id="mixed", model="Id.3")],
    )

    assert found(reader, {"model": ["id.3"]}) == ["mixed", "upper"]


def test_a_range_filter_keeps_the_bounds_themselves(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="early", year=2021),
            car(source_id="in", year=2022),
            car(source_id="late", year=2025),
        ],
    )

    assert found(reader, {"year_min": ["2022"], "year_max": ["2022"]}) == ["in"]


def test_a_price_range_is_compared_in_pence(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="cheap", price_pence=2_499_900),
            car(source_id="dear", price_pence=2_500_100),
        ],
    )

    assert found(reader, {"price_max": ["25000"]}) == ["cheap"]


def test_a_dealer_search_matches_part_of_the_name(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="a", dealer_name="Swansway Chester"),
            car(source_id="b", dealer_name="Marshall Oxford"),
        ],
    )

    assert found(reader, {"dealer": ["chester"]}) == ["a"]


def test_filters_never_reach_outside_the_current_stock(tmp_path):
    db = tmp_path / "c.db"
    with SqliteStore(db) as store:
        store.init_schema()
        old = store.start_run("cupra", started_at=WHEN)
        store.finish_run(
            old,
            finished_at=WHEN,
            expected_total=1,
            listings_seen=1,
            listings_stored=1,
            skipped_non_electric=0,
            mapping_errors=0,
            status=COMPLETE,
        )
        current = store.start_run("cupra", started_at=WHEN)
        store.finish_run(
            current,
            finished_at=WHEN,
            expected_total=1,
            listings_seen=1,
            listings_stored=1,
            skipped_non_electric=0,
            mapping_errors=0,
            status=COMPLETE,
        )
        store.upsert_car(car(source_id="sold"), observed_at=WHEN, run_id=old)
        store.upsert_car(car(source_id="listed"), observed_at=WHEN, run_id=current)

    assert found(Reader(db), {"source": ["cupra"]}) == ["listed"]


def test_a_range_on_a_nullable_column_keeps_the_unknowns_by_default(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="far", range_miles=250),
            car(source_id="near", range_miles=120),
            car(source_id="unstated", range_miles=None),
        ],
    )

    assert found(reader, {"range_min": ["200"]}) == ["far", "unstated"]


def test_unticking_the_box_drops_the_unknowns(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="far", range_miles=250), car(source_id="unstated")],
    )

    assert found(reader, {"submitted": ["1"], "range_min": ["200"]}) == ["far"]


def test_unticking_a_box_for_a_field_nobody_filtered_on_removes_nothing(tmp_path):
    """The toggle discloses what a filter would hide. With no filter, it hides
    nothing — it must not become a filter in its own right."""
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="stated", range_miles=250), car(source_id="unstated")],
    )

    assert found(reader, {"submitted": ["1"], "year_min": ["2020"]}) == [
        "stated",
        "unstated",
    ]


def test_a_drivetrain_choice_ignores_hyphenation(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="cupra-spelling", drivetrain="Rear-wheel drive"),
            car(source_id="vw-spelling", drivetrain="Rear wheel drive"),
            car(source_id="other", drivetrain="Four-wheel drive"),
        ],
    )

    assert found(reader, {"drivetrain": ["rear wheel drive"]}) == [
        "cupra-spelling",
        "vw-spelling",
    ]


def test_a_location_search_matches_either_the_town_or_the_postcode(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="by-town", dealer_city="Chester", dealer_postcode="CH1 4QJ"),
            car(source_id="by-postcode", dealer_city="Leeds", dealer_postcode="CH1 9AA"),
            car(source_id="elsewhere", dealer_city="Leeds", dealer_postcode="LS1 1AA"),
        ],
    )

    assert found(reader, {"location": ["CH1"]}) == ["by-postcode", "by-town"]


def test_a_car_with_no_location_at_all_counts_as_unknown(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="located", dealer_city="Chester"),
            car(source_id="unstated"),
        ],
    )

    assert found(reader, {"location": ["Chester"]}) == ["located", "unstated"]
    assert found(reader, {"submitted": ["1"], "location": ["Chester"]}) == ["located"]


def ranged(reader, query):
    return reader.unknown_counts(parse_filters(query))["range_miles"]


def test_the_unknown_count_is_the_cars_a_filter_would_hide(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="far", range_miles=250),
            car(source_id="near", range_miles=120),
            car(source_id="a", range_miles=None),
            car(source_id="b", range_miles=None),
        ],
    )

    assert ranged(reader, {"range_min": ["200"]}) == 2


def test_the_unknown_count_does_not_change_when_the_box_is_unticked(tmp_path):
    """Otherwise the control reads "include 0 unknown" the moment you untick it."""
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="far", range_miles=250), car(source_id="a"), car(source_id="b")],
    )

    ticked = {"submitted": ["1"], "range_min": ["200"], "unknown_range_miles": ["on"]}
    unticked = {"submitted": ["1"], "range_min": ["200"]}

    assert ranged(reader, ticked) == ranged(reader, unticked) == 2


def test_the_unknown_count_ignores_the_field_s_own_range(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="a"), car(source_id="b")],
    )

    assert ranged(reader, {"range_min": ["200"]}) == ranged(reader, {"range_max": ["1"]})


def test_the_unknown_count_respects_every_other_filter(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car("cupra", "a"),
            car("cupra", "b"),
            car("volkswagen", "c", brand="Volkswagen"),
        ],
    )

    assert ranged(reader, {"range_min": ["200"], "source": ["cupra"]}) == 2


def test_the_unknown_count_stays_inside_the_current_stock(tmp_path):
    db = tmp_path / "c.db"
    with SqliteStore(db) as store:
        store.init_schema()
        runs = []
        for _ in range(2):
            run_id = store.start_run("cupra", started_at=WHEN)
            store.finish_run(
                run_id,
                finished_at=WHEN,
                expected_total=1,
                listings_seen=1,
                listings_stored=1,
                skipped_non_electric=0,
                mapping_errors=0,
                status=COMPLETE,
            )
            runs.append(run_id)
        store.upsert_car(car(source_id="sold"), observed_at=WHEN, run_id=runs[0])
        store.upsert_car(car(source_id="listed"), observed_at=WHEN, run_id=runs[1])

    assert ranged(Reader(db), {"range_min": ["200"]}) == 1


def test_the_buckets_account_for_every_car_in_scope(tmp_path):
    """unknown + known-matching + known-not-matching == the scoped total."""
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id=f"far{n}", range_miles=250) for n in range(3)]
        + [car(source_id=f"near{n}", range_miles=120) for n in range(2)]
        + [car(source_id=f"none{n}") for n in range(4)],
    )
    excluding_unknown = {"submitted": ["1"], "range_min": ["200"]}

    unknown = ranged(reader, excluding_unknown)
    known_matching = len(reader.search(parse_filters(excluding_unknown)))

    assert len(reader.current_stock()) == 9
    assert unknown == 4
    assert known_matching == 3
    assert 9 - unknown - known_matching == 2


def test_a_fully_populated_field_reports_no_unknowns(tmp_path):
    reader = stocked(tmp_path / "c.db", [car(range_miles=250)])

    assert ranged(reader, {}) == 0


def ordered(reader, query):
    return [each["source_id"] for each in reader.search(parse_filters(query))]


def test_results_are_cheapest_first_by_default(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="dear", price_pence=4_000_000),
            car(source_id="cheap", price_pence=1_000_000),
        ],
    )

    assert ordered(reader, {}) == ["cheap", "dear"]


def test_a_column_and_direction_can_be_chosen(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [car(source_id="old", year=2020), car(source_id="new", year=2024)],
    )

    assert ordered(reader, {"sort": ["year"], "dir": ["desc"]}) == ["new", "old"]


@pytest.mark.parametrize(
    "query",
    [
        {"sort": ["dealer_phone; DROP TABLE cars"]},
        {"sort": ["not_a_column"]},
        {"sort": [""]},
    ],
)
def test_an_unrecognised_column_never_reaches_the_database(tmp_path, query):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="dear", price_pence=4_000_000, year=2020),
            car(source_id="cheap", price_pence=1_000_000, year=2024),
        ],
    )

    assert ordered(reader, query) == ["cheap", "dear"]


def test_an_unrecognised_direction_falls_back_without_losing_the_column(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="dear", price_pence=4_000_000, year=2020),
            car(source_id="cheap", price_pence=1_000_000, year=2024),
        ],
    )

    assert ordered(reader, {"sort": ["year"], "dir": ["sideways"]}) == ["dear", "cheap"]


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_cars_with_no_value_sort_last_whichever_way_round(tmp_path, direction):
    """Sorting by range ascending must not present 800 unknowns as the worst."""
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="unstated"),
            car(source_id="far", range_miles=250),
            car(source_id="near", range_miles=120),
        ],
    )

    assert ordered(reader, {"sort": ["range_miles"], "dir": [direction]})[-1] == "unstated"


def test_equal_values_keep_a_stable_order_so_a_bookmark_still_works(tmp_path):
    reader = stocked(
        tmp_path / "c.db",
        [
            car("volkswagen", "b", brand="Volkswagen"),
            car("cupra", "c"),
            car("cupra", "a"),
        ],
    )

    assert ordered(reader, {"sort": ["price_pence"]}) == ["a", "c", "b"]


def test_a_normalised_column_sorts_on_the_value_that_is_displayed(tmp_path):
    """Raw text sort puts ID.3 and Id.3 either side of e-up!, undoing the fold."""
    reader = stocked(
        tmp_path / "c.db",
        [
            car(source_id="upper", model="ID.3"),
            car(source_id="mixed", model="Id.3"),
            car(source_id="lower", model="e-up!"),
        ],
    )

    assert ordered(reader, {"sort": ["model"]}) == ["lower", "mixed", "upper"]
