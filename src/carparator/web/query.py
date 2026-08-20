"""Turn a query string into filters, and filters into SQL. No Flask here.

Which filters exist, and which of them need an "unknown" bucket, is decided by
the schema's nullability — never by how well populated a column happened to
look in a sample. A column that is NOT NULL cannot hide a row by being absent;
every other column can, and must say so on screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Mapping

from carparator.web.normalise import DRIVETRAIN_KEY_SQL, MODEL_KEY_SQL

CHOICE = "choice"
RANGE = "range"
TEXT = "text"


@dataclass(frozen=True)
class Field:
    """One offered filter, and what it is allowed to do to the result set."""

    name: str
    param: str
    kind: str
    label: str
    nullable: bool
    columns: tuple[str, ...] = ()
    numeric: type = int
    scale: int = 1
    key_sql: str | None = None

    def __post_init__(self) -> None:
        if not self.columns:
            object.__setattr__(self, "columns", (self.name,))


# The NOT NULL columns are exactly source, source_id, brand, model,
# mileage_miles, year, price_pence, dealer_name, fuel_type, first_seen and
# last_seen. fuel_type is electric by construction, transmission is Automatic
# on every listing seen, and engine_cc is empty for EVs by nature, so none of
# the three can discriminate and none is offered.
FIELDS: tuple[Field, ...] = (
    Field("source", "source", CHOICE, "Source", nullable=False),
    Field("brand", "brand", CHOICE, "Brand", nullable=False),
    Field("model", "model", CHOICE, "Model", nullable=False, key_sql=MODEL_KEY_SQL),
    Field("year", "year", RANGE, "Year", nullable=False),
    Field("mileage_miles", "mileage", RANGE, "Mileage", nullable=False),
    Field("price_pence", "price", RANGE, "Price (£)", nullable=False, scale=100),
    Field("dealer_name", "dealer", TEXT, "Dealer", nullable=False),
    Field("battery_kwh", "battery", RANGE, "Battery (kWh)", nullable=True, numeric=float),
    Field("power_kw", "power", RANGE, "Power (kW)", nullable=True),
    Field("range_miles", "range", RANGE, "Range (miles)", nullable=True, numeric=float),
    Field("seats", "seats", CHOICE, "Seats", nullable=True),
    Field(
        "drivetrain",
        "drivetrain",
        CHOICE,
        "Drivetrain",
        nullable=True,
        key_sql=DRIVETRAIN_KEY_SQL,
    ),
    Field("body_style", "body", CHOICE, "Body style", nullable=True),
    Field("trim", "trim", TEXT, "Trim", nullable=True),
    Field("colour", "colour", TEXT, "Colour", nullable=True),
    Field(
        "location",
        "location",
        TEXT,
        "Town or postcode",
        nullable=True,
        columns=("dealer_city", "dealer_postcode"),
    ),
)

BY_NAME = {each.name: each for each in FIELDS}


@dataclass(frozen=True)
class FilterSpec:
    """What the user asked for, in stored units, already validated."""

    choices: dict[str, tuple[str, ...]] = dataclass_field(default_factory=dict)
    ranges: dict[str, tuple[Any, Any]] = dataclass_field(default_factory=dict)
    texts: dict[str, str] = dataclass_field(default_factory=dict)
    excluded_unknown: frozenset[str] = frozenset()

    def is_active(self, name: str) -> bool:
        """Whether this field is narrowing the results at all."""
        return name in self.choices or name in self.ranges or name in self.texts

    def includes_unknown(self, name: str) -> bool:
        return name not in self.excluded_unknown

    def without(self, name: str) -> "FilterSpec":
        """This spec with one field's filter and toggle taken out."""
        return FilterSpec(
            {key: value for key, value in self.choices.items() if key != name},
            {key: value for key, value in self.ranges.items() if key != name},
            {key: value for key, value in self.texts.items() if key != name},
            self.excluded_unknown - {name},
        )

    def as_query(self) -> dict[str, list[str]]:
        """The query string that reproduces this spec."""
        args: dict[str, list[str]] = {"submitted": ["1"]}
        for name, values in self.choices.items():
            args[BY_NAME[name].param] = list(values)
        for name, (low, high) in self.ranges.items():
            each = BY_NAME[name]
            for suffix, bound in (("min", low), ("max", high)):
                if bound is not None:
                    args[f"{each.param}_{suffix}"] = [_unscale(bound, each)]
        for name, text in self.texts.items():
            args[BY_NAME[name].param] = [text]
        for each in FIELDS:
            if each.nullable and self.includes_unknown(each.name):
                args[f"unknown_{each.name}"] = ["on"]
        return args


def parse_filters(args: Mapping[str, list[str]]) -> FilterSpec:
    """Read a query string, discarding anything malformed rather than failing.

    A filter form is a URL a user can hand-edit or bookmark; nonsense in it
    should narrow nothing, not return a 500.
    """
    choices, ranges, texts = {}, {}, {}
    for each in FIELDS:
        if each.kind == CHOICE:
            values = tuple(value for value in args.get(each.param, []) if value.strip())
            if values:
                choices[each.name] = values
        elif each.kind == RANGE:
            bounds = (
                _number(args.get(f"{each.param}_min"), each),
                _number(args.get(f"{each.param}_max"), each),
            )
            if bounds != (None, None):
                ranges[each.name] = bounds
        else:
            text = _first(args.get(each.param)).strip()
            if text:
                texts[each.name] = text
    return FilterSpec(choices, ranges, texts, _excluded_unknown(args))


def _excluded_unknown(args: Mapping[str, list[str]]) -> frozenset[str]:
    """Unknowns are in by default; only a submitted form can take them out.

    An unticked checkbox submits nothing at all, so without the form's own
    marker a first visit would be indistinguishable from every box unticked.
    """
    if not args.get("submitted"):
        return frozenset()
    return frozenset(
        each.name
        for each in FIELDS
        if each.nullable and not args.get(f"unknown_{each.name}")
    )


def _first(values: list[str] | None) -> str:
    return values[0] if values else ""


def _number(values: list[str] | None, each: Field) -> Any:
    try:
        parsed = float(_first(values))
    except ValueError:
        return None
    scaled = parsed * each.scale
    return int(round(scaled)) if each.numeric is int else scaled


def _unscale(bound: Any, each: Field) -> str:
    value = bound / each.scale if each.scale != 1 else bound
    return str(int(value) if each.numeric is int and value == int(value) else value)


def build_where(spec: FilterSpec) -> tuple[str, list]:
    """The filter half of the WHERE clause. Scoping to stock is the reader's."""
    conditions, parameters = [], []
    for name, values in spec.choices.items():
        each = BY_NAME[name]
        expression = each.key_sql or each.columns[0]
        clause = f"{expression} IN ({', '.join('?' * len(values))})"
        _add(conditions, parameters, spec, each, clause, list(values))
    for name, (low, high) in spec.ranges.items():
        each = BY_NAME[name]
        bounds = [(bound, sign) for bound, sign in ((low, ">="), (high, "<=")) if bound is not None]
        clause = " AND ".join(f"{each.columns[0]} {sign} ?" for _, sign in bounds)
        _add(conditions, parameters, spec, each, clause, [bound for bound, _ in bounds])
    for name, text in spec.texts.items():
        each = BY_NAME[name]
        clause = " OR ".join(f"{column} LIKE ?" for column in each.columns)
        _add(conditions, parameters, spec, each, clause, [f"%{text}%"] * len(each.columns))
    return " AND ".join(conditions) if conditions else "1", parameters


def _add(conditions, parameters, spec: FilterSpec, each: Field, clause, values) -> None:
    """Add one field's predicate, widened to keep its unknowns if asked.

    Only fields the user actually filtered on get here, which is what makes the
    unknown toggle inert on its own: it discloses what a filter would hide, so
    with no filter set there is nothing for it to hide.
    """
    if each.nullable and spec.includes_unknown(each.name):
        absent = " AND ".join(f"{column} IS NULL" for column in each.columns)
        clause = f"({clause}) OR ({absent})"
    conditions.append(f"({clause})")
    parameters.extend(values)


def unknown_clause(each: Field) -> str:
    """True for the rows this field cannot speak for."""
    return " AND ".join(f"{column} IS NULL" for column in each.columns)
