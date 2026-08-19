"""CUPRA approved-used listings, via the private VTP search API."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from carparator.model import Car, FuelType, RawListing
from carparator.sources import REQUEST_DELAY_SECONDS, build_client, get_with_retry

# ";t_petr=E" is a matrix path parameter — the "?t_petr=E" query form is silently
# ignored and returns unfiltered results.
SEARCH_URL = "https://vtpapi.seat.com/restapi/v1/cuukgwb/search/car;t_petr=E"
PAGE_SIZE = 100

_FUEL_TYPES = {
    "electric": FuelType.ELECTRIC,
    "petrol": FuelType.PETROL,
    "diesel": FuelType.DIESEL,
    "hybrid": FuelType.HYBRID,
    "plug-in hybrid": FuelType.PLUG_IN_HYBRID,
}

# Anchored on the unit so a "250kW" power figure can never be read as a battery.
_BATTERY = re.compile(r"(\d+(?:\.\d+)?)\s*kWh\b", re.IGNORECASE)
_DOORS = re.compile(r"\b(\d)\s*dr\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedTitle:
    battery_kwh: float | None
    doors: int | None


def parse_cupra_title(title: str) -> ParsedTitle:
    """Recover battery size and door count from the marketing title.

    Most records carry neither as a first-class field, so the title is the only
    source; both spellings ("77kWh" and the AFV format's "77 kWh") appear live.
    """
    battery = _BATTERY.search(title)
    doors = _DOORS.search(title)
    return ParsedTitle(
        battery_kwh=float(battery.group(1)) if battery else None,
        doors=int(doors.group(1)) if doors else None,
    )


def _by_key(entries: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in entries or []}


def _number(text: str | None) -> float | None:
    """CUPRA formats numbers for display ("2,480"); strip the separators."""
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _wltp_combined(techdata: list[dict[str, Any]], group_key: str) -> float | None:
    """Pull one WLTP group's combined figure out of its deeply nested envelope."""
    for block in techdata or []:
        if block.get("key") != "WLTP":
            continue
        for group in block["techDataType"].get("groups", []):
            if group["key"] != group_key:
                continue
            for datum in group["techDataGroup"].get("data", []):
                for variant in datum["techData"].get("values", []):
                    for value in variant.get("values", []):
                        if value["key"] == "COMBINED":
                            return _number(value.get("value"))
    return None


def _exterior_colour(colour_item: dict[str, Any] | None) -> str | None:
    """Prefer the marketing name ("Tavascan Blue") over the generic one ("Blue")."""
    exterior = _by_key(colour_item.get("values") if colour_item else None).get("exterior")
    if exterior is None:
        return None
    facets = _by_key(exterior.get("values"))
    marketing = _by_key(facets.get("marketing", {}).get("values")).get("out")
    if marketing and marketing.get("value"):
        return marketing["value"]
    return facets.get("generic", {}).get("value")


def _first_image(images: list[dict[str, Any]] | None) -> str | None:
    for group in images or []:
        for image in group.get("imageGroup", {}).get("images", []):
            href = image.get("image", {}).get("href")
            if href:
                return href
    return None


def _monthly_pence(financing: list[dict[str, Any]] | None) -> int | None:
    for offer in financing or []:
        rate = _by_key(offer.get("financingData", {}).get("items")).get("Rate")
        if rate and rate.get("unit") == "GBP":
            amount = _number(rate.get("value"))
            if amount is not None:
                return round(amount * 100)
    return None


class CupraSource:
    """Reads CUPRA UK approved-used stock from the private VTP search API."""

    name = "cupra"

    def __init__(
        self,
        client: httpx.Client | None = None,
        request_delay: float = REQUEST_DELAY_SECONDS,
    ):
        self._client = client or build_client()
        self._request_delay = request_delay
        self.expected_total: int | None = None

    def fetch_raw(self) -> Iterator[RawListing]:
        """Walk X-Page until a page comes back empty.

        Past the last page the API answers 200 but omits the `cars` key, so an
        empty (or absent) car list — not the status code — is the terminator.
        """
        page = 1
        while True:
            if page > 1 and self._request_delay:
                time.sleep(self._request_delay)
            payload = get_with_retry(
                self._client,
                SEARCH_URL,
                headers={
                    "X-Pattern": "cuprawebfe",
                    "Accept-Language": "en-GB",
                    "X-Page": str(page),
                    "X-Page-Items": str(PAGE_SIZE),
                },
            ).json()
            if self.expected_total is None:
                self.expected_total = _electric_facet_count(payload)
            # Past the last page the API drops "cars" rather than emptying it.
            cars = payload["results"]["result"].get("cars") or []
            if not cars:
                return
            for entry in cars:
                car = entry["car"]
                yield RawListing(source=self.name, source_id=car["carid"], payload=car)
            page += 1

    def to_car(self, raw: RawListing) -> Car | None:
        car = self.map_car(raw.payload)
        if car.fuel_type is not FuelType.ELECTRIC:
            return None
        return car

    def map_car(self, payload: dict[str, Any]) -> Car:
        """Map a record without filtering, so non-electric mapping stays testable."""
        items = _by_key(payload["items"])
        motor = _by_key(items["motor"].get("values"))
        title = items.get("localCarTitle", {}).get("value", "")
        parsed = parse_cupra_title(title)

        mileage = items["mileage"]
        if mileage.get("unit") != "MI":
            raise ValueError(f"unexpected mileage unit {mileage.get('unit')!r}")
        price = _by_key(items["prices"].get("values"))["sale"]

        registered = items.get("initialreg", {}).get("value", "")
        dealer = _by_key(payload["hypermediadealer"]["dealer"]["items"])
        position = _by_key(dealer.get("position", {}).get("values"))

        doors = items.get("doors", {}).get("value")
        seats = items.get("seat", {}).get("value")

        return Car(
            source=self.name,
            source_id=payload["carid"],
            brand=items["manuf"]["value"],
            model=items["model"]["value"],
            battery_kwh=parsed.battery_kwh,
            doors=int(doors) if doors else parsed.doors,
            mileage_miles=int(mileage["raw_value"]),
            year=int(registered[:4]),
            registration=items.get("numberplate", {}).get("value"),
            price_pence=round(price["raw_value"] * 100),
            dealer_name=dealer["name"]["value"],
            fuel_type=_FUEL_TYPES[motor["fuel"]["value"].strip().lower()],
            trim=items.get("smod", {}).get("value"),
            description=title or None,
            range_miles=_wltp_combined(payload.get("hypermediatechdata", []), "range"),
            power_kw=_as_int(motor.get("power.kw", {}).get("value")),
            power_ps=_as_int(motor.get("power.ps", {}).get("value")),
            engine_cc=_as_int(motor.get("capacity", {}).get("value")),
            drivetrain=items.get("drive", {}).get("value"),
            transmission=items.get("gear", {}).get("value"),
            colour=_exterior_colour(items.get("color")),
            seats=int(seats) if seats else None,
            first_registered=registered[:10] or None,
            monthly_price_pence=_monthly_pence(payload.get("hypermediafinancing")),
            dealer_city=dealer.get("city", {}).get("value"),
            dealer_postcode=dealer.get("zip", {}).get("value"),
            dealer_phone=dealer.get("phone", {}).get("value"),
            dealer_lat=_number(position.get("latitude", {}).get("value")),
            dealer_lon=_number(position.get("longitude", {}).get("value")),
            image_url=_first_image(payload.get("images")),
        )


def _as_int(text: str | None) -> int | None:
    value = _number(text)
    return None if value is None else int(value)


def _electric_facet_count(payload: dict[str, Any]) -> int | None:
    """The t_petr facet reports how many electric cars the run should find."""
    for criteria in payload.get("criteria", {}).get("search", {}).get("criterias", []):
        if criteria.get("criteria", {}).get("key") != "t_petr":
            continue
        for selected in criteria.get("selectedItems", []):
            if selected.get("key") == "E":
                return selected.get("number")
    return None
