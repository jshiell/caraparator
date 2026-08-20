"""A read-only local web view of the scraped listings.

Binds 127.0.0.1 only. Nothing here writes: scraping stays in the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlencode

from flask import Flask, render_template, request

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
            cars=[_present(row, forms, coverage, clock()) for row in reader.search(spec)],
            sorts=SORTS,
            show_last_seen=coverage.is_partial,
        )

    return app


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
        each.name: canonical_forms(
            [row[each.columns[0]] for row in stock], each.fold
        )
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


def _present(row: dict, forms, coverage, now: str) -> dict:
    """One table row, with folded spellings and formatted money."""
    shown = dict(row)
    for each in CHOICE_FIELDS:
        folded = each.fold(row[each.columns[0]])
        shown[each.name] = forms[each.name].get(folded, row[each.columns[0]])
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
