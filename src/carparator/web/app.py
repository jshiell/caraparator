"""A read-only local web view of the scraped listings.

Binds 127.0.0.1 only. Nothing here writes: scraping stays in the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode

from flask import Flask, abort, render_template, request

from carparator.web.normalise import canonical_forms
from carparator.web.query import CHOICE, FIELDS, SORTS, parse_filters
from carparator.web.reader import COMPLETE_RUN, NO_COMPLETE_RUN, Reader

CHOICE_FIELDS = tuple(each for each in FIELDS if each.kind == CHOICE)


def create_app(reader: Reader, *, now: Callable[[], str] | None = None) -> Flask:
    """Build the app around an already-open reader, so tests can inject a clock."""
    app = Flask(__name__)
    clock = now or _utc_now
    app.jinja_env.filters["bound"] = _bound
    app.jinja_env.filters["sorted_by"] = _sorted_by

    @app.get("/")
    def index() -> str:
        spec = parse_filters(request.args.to_dict(flat=False))
        stock = reader.current_stock()
        coverage = reader.coverage()
        forms = _display_forms(stock)
        unknown = reader.unknown_counts(spec)
        return render_template(
            "list.html",
            spec=spec,
            coverage=coverage,
            banners=[_banner(each) for each in coverage.sources],
            controls=_controls(stock, forms, unknown, spec),
            cars=[_present(row, forms, clock()) for row in reader.search(spec)],
            sorts=SORTS,
            show_last_seen=coverage.is_partial,
        )

    @app.get("/car/<source>/<path:source_id>")
    def detail(source: str, source_id: str) -> str:
        row = reader.car(source, source_id)
        if row is None:
            abort(404)
        return render_template(
            "detail.html",
            car=row,
            price=_pounds(row["price_pence"]),
            image=_safe_url(row["image_url"]),
            phone=_telephone(row["dealer_phone"]),
            history=_history(reader.price_history(source, source_id)),
            stated=[(label, row[name]) for label, name in DETAIL_FIELDS if row[name] is not None],
            absent=[label for label, name in DETAIL_FIELDS if row[name] is None],
        )

    return app


DETAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("Trim", "trim"),
    ("Description", "description"),
    ("Battery (kWh)", "battery_kwh"),
    ("Range (miles)", "range_miles"),
    ("Power (kW)", "power_kw"),
    ("Power (PS)", "power_ps"),
    ("Drivetrain", "drivetrain"),
    ("Transmission", "transmission"),
    ("Body style", "body_style"),
    ("Colour", "colour"),
    ("Doors", "doors"),
    ("Seats", "seats"),
    ("AC charging (kW)", "ac_charge_kw"),
    ("DC charging (kW)", "dc_charge_kw"),
    ("Registration", "registration"),
    ("First registered", "first_registered"),
    ("Model year", "model_year"),
    ("Previous owners", "previous_owners"),
    ("VIN", "vin"),
    ("Engine (cc)", "engine_cc"),
    ("Monthly price", "monthly_price_pence"),
    ("Dealer town", "dealer_city"),
    ("Dealer postcode", "dealer_postcode"),
)

SAFE_SCHEMES = ("http://", "https://")


def _safe_url(url: str | None) -> str | None:
    """Autoescaping does not police URL schemes, and these come from a third
    party, so anything but plain http(s) is dropped rather than rendered."""
    if url and url.lower().startswith(SAFE_SCHEMES):
        return url
    return None


def _telephone(number: str | None) -> str | None:
    """A tel: target built only from the characters a phone number may hold."""
    if not number:
        return None
    dialable = "".join(each for each in number if each.isdigit() or each == "+")
    return dialable or None


def _history(observations: list[dict]) -> list[dict]:
    """Each observed price with its change from the one before it."""
    shown = []
    previous = None
    for each in observations:
        change = None if previous is None else each["price_pence"] - previous
        shown.append(
            {
                "observed_at": each["observed_at"],
                "price": _pounds(each["price_pence"]),
                "change": _change(change),
            }
        )
        previous = each["price_pence"]
    return shown


def _change(pence: int | None) -> str | None:
    if not pence:
        return None
    sign = "+" if pence > 0 else "\u2212"
    return f"{sign}{_pounds(abs(pence))}"


def _bound(value, field) -> str:
    """Put a stored bound back into the units the form asked for."""
    if value is None:
        return ""
    scaled = value / field.scale if field.scale != 1 else value
    return str(int(scaled) if scaled == int(scaled) else scaled)


def _sorted_by(spec, column: str) -> str:
    """The current view's query string, re-sorted on `column`.

    Clicking the column you are already sorted on reverses it; every other
    filter survives, so the URL stays bookmarkable.
    """
    flipped = "desc" if spec.sort == column and spec.direction == "asc" else "asc"
    args = spec.as_query()
    args["sort"], args["dir"] = [column], [flipped]
    return urlencode([(key, value) for key, values in args.items() for value in values])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _display_forms(stock: list[dict]) -> dict[str, dict[str, str]]:
    """For each faceted field, the spelling to show for each folded key."""
    return {
        each.name: canonical_forms([row[each.name] for row in stock], each.fold)
        for each in CHOICE_FIELDS
    }


def _controls(stock, forms, unknown, spec) -> list[dict]:
    """The filter form's fields, with each one's unknown count.

    A control whose count is zero is not offered: a field that happens to be
    fully populated should cost no screen space, and "include 0 unknown" is
    noise. The count itself comes from the reader, which computes it ignoring
    this field's own filter, so ticking the box never changes the number.
    """
    controls = []
    for each in FIELDS:
        options = sorted(forms.get(each.name, {}).items(), key=lambda pair: pair[1])
        controls.append(
            {
                "field": each,
                "options": options,
                "selected": spec.choices.get(each.name, ()),
                "range": spec.ranges.get(each.name, (None, None)),
                "text": spec.texts.get(each.name, ""),
                "unknown": unknown.get(each.name, 0),
                "includes_unknown": spec.includes_unknown(each.name),
            }
        )
    return controls


def _present(row: dict, forms, now: str) -> dict:
    """One table row, with folded spellings and formatted money."""
    shown = dict(row)
    for each in CHOICE_FIELDS:
        folded = each.fold(row[each.name])
        shown[each.name] = forms[each.name].get(folded, row[each.name])
    shown["price"] = _pounds(row["price_pence"])
    shown["days_ago"] = _days_between(row["last_seen"], now)
    return shown


def _pounds(pence: int) -> str:
    return f"£{pence // 100:,}"


def _days_between(then: str, now: str) -> int | None:
    """How stale a listing is. None when either timestamp is unreadable —
    an unparseable date must not be reported as "0 days ago"."""
    try:
        return (_moment(now) - _moment(then)).days
    except ValueError:
        return None


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _banner(source) -> str:
    """What this source can honestly claim about its own stock."""
    if source.state == COMPLETE_RUN:
        return (
            f"{source.source}: last complete run {source.completed_at}."
            " Cars it did not see are treated as sold and are not listed."
        )
    if source.state == NO_COMPLETE_RUN:
        return (
            f"{source.source}: no complete run yet, so no car can be inferred sold."
            " Every car ever seen is listed, however old."
        )
    return (
        f"{source.source}: never scraped in this database."
        " Every car it has is listed, however old."
    )
