import sqlite3

import pytest

from carparator.web.normalise import (
    DRIVETRAIN_KEY_SQL,
    MODEL_KEY_SQL,
    canonical_forms,
    drivetrain_key,
    model_key,
)


def test_hyphenated_and_spaced_drivetrains_fold_together():
    assert drivetrain_key("Rear-wheel drive") == drivetrain_key("Rear wheel drive")


def test_genuinely_different_drivetrains_stay_apart():
    assert drivetrain_key("Rear-wheel drive") != drivetrain_key("Four-wheel drive")


def test_model_case_variants_fold_together():
    assert model_key("ID.3") == model_key("Id.3")


def test_different_models_stay_apart():
    assert model_key("ID.3") != model_key("ID.4")


def test_the_most_common_spelling_becomes_the_display_form():
    forms = canonical_forms(["ID.3", "Id.3", "ID.3"], model_key)

    assert forms[model_key("Id.3")] == "ID.3"


def test_lower_case_model_names_are_not_shouted_at():
    """Folding must pick a spelling the source actually used, never invent one."""
    forms = canonical_forms(["e-up!", "e-up!", "E-UP!"], model_key)

    assert forms[model_key("E-UP!")] == "e-up!"


def test_a_tie_folds_the_same_way_whatever_the_order():
    assert canonical_forms(["Id.3", "ID.3"], model_key) == canonical_forms(
        ["ID.3", "Id.3"], model_key
    )


def test_absent_values_are_not_offered_as_an_option():
    assert canonical_forms([None, "ID.3"], model_key) == {model_key("ID.3"): "ID.3"}


@pytest.mark.parametrize(
    "column, expression, key, values",
    [
        ("model", MODEL_KEY_SQL, model_key, ["ID.3", "Id.3", " ID.4 ", "e-up!"]),
        (
            "drivetrain",
            DRIVETRAIN_KEY_SQL,
            drivetrain_key,
            ["Rear-wheel drive", "Rear wheel drive", "Four-wheel drive", " FRONT "],
        ),
    ],
)
def test_the_sql_key_matches_the_python_key(column, expression, key, values):
    """Filtering happens in SQL and display in Python; they must agree."""
    connection = sqlite3.connect(":memory:")
    connection.execute(f"CREATE TABLE cars ({column} TEXT)")
    connection.executemany(f"INSERT INTO cars VALUES (?)", [(each,) for each in values])

    rows = connection.execute(f"SELECT {column}, {expression} FROM cars").fetchall()

    assert [(raw, key(raw)) for raw, _ in rows] == [(raw, folded) for raw, folded in rows]
