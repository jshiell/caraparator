"""Volkswagen UK approved-used listings, from the Solr documents in the SRP."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Iterator

import httpx

from carparator.model import Car, FuelType, ListingFeatures, RawListing
from carparator.sources import REQUEST_DELAY_SECONDS, build_client, get_with_retry

SEARCH_URL = "https://usedcars.volkswagen.co.uk/en/vehicle_search/all-brands/all-models"
MAX_PAGES = 200
MAX_CONSECUTIVE_FAILED_PAGES = 3
MAX_RETAINED_FAILED_PAGE_BODIES = 3

_RESULT_COUNT = re.compile(r"'numberOfResults'\s*:\s*'(\d+)'")

logger = logging.getLogger(__name__)

_FUEL_TYPES = {
    "electric": FuelType.ELECTRIC,
    "petrol": FuelType.PETROL,
    "diesel": FuelType.DIESEL,
    "hybrid": FuelType.HYBRID,
    "plug-in hybrid": FuelType.PLUG_IN_HYBRID,
    "hybrid petrol/electric plug-in": FuelType.PLUG_IN_HYBRID,
    "hybrid petrol/electric": FuelType.HYBRID,
    "hybrid diesel/electric": FuelType.HYBRID,
}

# A cylinder capacity of 1 is the platform's "not applicable" filler, not an engine.
_ENGINE_CC_SENTINEL = 1

# The whole vehicle array arrives as one JSON literal inside an inline script.
_ANCHOR = "let vehicles = JSON.parse('"

# Each vehicle's detail page is named in the SRP's JSON-LD, whose "sku" is the
# same ID the vehicle payload carries. Reading it here costs no extra request.
_JSON_STRING = r'(?:[^"\\]|\\.)*'
_DETAIL_URL = re.compile(
    rf'"sku"\s*:\s*"({_JSON_STRING})"\s*,'
    rf'\s*"description"\s*:\s*"{_JSON_STRING}"\s*,'
    rf'\s*"url"\s*:\s*"({_JSON_STRING})"'
)


def extract_vw_detail_urls(html: str) -> dict[str, str]:
    """Map each vehicle ID on a search-results page to its detail page URL."""
    return {sku: url.replace(r"\/", "/") for sku, url in _DETAIL_URL.findall(html)}


# The detail page's equipment tab. Its two <h4>s are the only ones inside it —
# the finance one lives past the specification tab, which bounds the slice.
_EQUIPMENT_REGION = '<div class="technical__equipment"'
_SPECIFICATION_REGION = '<div class="technical__specification"'
_STANDARD_HEADING = "Fitted as standard"
_OPTIONAL_HEADING = "Fitted optional extras"
_HEADING = re.compile(r"<h4>(.*?)</h4>", re.DOTALL)
# The glossary popup is a sibling of the label span, not a child, so a
# non-greedy match cannot swallow it.
_LABEL = re.compile(r'<span class="label">(.*?)</span>', re.DOTALL)


def extract_vw_features(html: str) -> ListingFeatures | None:
    """Read the equipment lists off a vehicle detail page.

    Returns None when the equipment region is missing, and also when it yields no
    standard equipment at all. Every real page carries a standard list, so an
    empty one means the markup moved: reporting that as an error beats storing
    nothing and marking the listing as fetched, which would never be retried.

    Labels are taken verbatim. Measured over 1588 real ones, none contains a tag
    or an entity, so there is nothing here to strip or unescape.
    """
    start = html.find(_EQUIPMENT_REGION)
    if start == -1:
        return None
    end = html.find(_SPECIFICATION_REGION, start)
    region = html[start:] if end == -1 else html[start:end]

    sections = _HEADING.split(region)
    labels = {
        heading.strip(): _labels(body)
        for heading, body in zip(sections[1::2], sections[2::2])
    }
    standard = labels.get(_STANDARD_HEADING, ())
    if not standard:
        return None
    # A listing with no optional extras is ordinary, not a failure.
    return ListingFeatures(
        standard=standard, optional=labels.get(_OPTIONAL_HEADING, ())
    )


def _labels(body: str) -> tuple[str, ...]:
    found = (label.strip() for label in _LABEL.findall(body))
    return tuple(label for label in found if label)


def extract_vw_vehicles(html: str) -> list[dict[str, Any]]:
    """Pull the embedded vehicle documents out of a search-results page.

    Decoding stops where the JSON array actually ends rather than at the first
    closing "')", which a value containing that sequence would otherwise truncate.
    The array is embedded more than once per page, so results are deduplicated
    on ID with first-seen order preserved.
    """
    decoder = json.JSONDecoder()
    vehicles: dict[str, dict[str, Any]] = {}
    search_from = 0
    while (anchor := html.find(_ANCHOR, search_from)) != -1:
        start = html.find("[", anchor)
        if start == -1:
            break
        try:
            array, end = decoder.raw_decode(html, start)
        except ValueError:
            search_from = anchor + len(_ANCHOR)
            continue
        for vehicle in array:
            vehicles.setdefault(str(vehicle.get("ID")), vehicle)
        search_from = end
    return list(vehicles.values())


def _text(vehicle: dict[str, Any], key: str) -> str | None:
    value = vehicle.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(vehicle: dict[str, Any], key: str) -> int | None:
    value = vehicle.get(key)
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _decimal(vehicle: dict[str, Any], key: str) -> float | None:
    value = vehicle.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _image_url(vehicle: dict[str, Any]) -> str | None:
    """Picserver gives a directory; the main image sits under "<n>_1024.jpg"."""
    base = _text(vehicle, "PICSERVER_URL_STR")
    main = _integer(vehicle, "MAIN_IMAGE_INT")
    if not base or main is None:
        return None
    return f"{base.rstrip('/')}/{main}_1024.jpg"


class VolkswagenSource:
    """Reads Volkswagen UK approved-used stock from the search-results pages."""

    name = "volkswagen"

    def __init__(
        self,
        client: httpx.Client | None = None,
        request_delay: float = REQUEST_DELAY_SECONDS,
    ):
        self._client = client or build_client()
        self._request_delay = request_delay
        self.expected_total: int | None = None
        self.failed_pages: list[int] = []
        self.failed_page_bodies: list[str] = []

    def fetch_raw(self) -> Iterator[RawListing]:
        """Walk /pageN until a page legitimately holds no vehicles.

        Past the last page the site answers 200 with zero vehicles and nonsense
        metadata, so the status code is never the terminator. A page whose embedded
        JSON cannot be found is a parse failure, not the end: it is logged and
        skipped so a single bad page does not silently truncate the run.
        """
        consecutive_failures = 0
        for page in range(1, MAX_PAGES + 1):
            if page > 1 and self._request_delay:
                time.sleep(self._request_delay)
            html = get_with_retry(
                self._client,
                f"{SEARCH_URL}/page{page}?view=list&FUEL_TYPE_LST=ELECTRIC",
            ).text
            if self.expected_total is None:
                self.expected_total = _result_count(html)

            if _ANCHOR not in html:
                self.failed_pages.append(page)
                if len(self.failed_page_bodies) < MAX_RETAINED_FAILED_PAGE_BODIES:
                    self.failed_page_bodies.append(html)
                consecutive_failures += 1
                logger.warning(
                    "volkswagen page %d held no vehicle payload (%d bytes)",
                    page,
                    len(html),
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILED_PAGES:
                    return
                continue

            consecutive_failures = 0
            vehicles = extract_vw_vehicles(html)
            if not vehicles:
                return
            for vehicle in vehicles:
                yield RawListing(
                    source=self.name, source_id=str(vehicle["ID"]), payload=vehicle
                )

    def to_car(self, raw: RawListing) -> Car | None:
        car = self.map_car(raw.payload)
        if car.fuel_type is not FuelType.ELECTRIC:
            return None
        return car

    def map_car(self, vehicle: dict[str, Any]) -> Car:
        """Map a document without filtering, so non-electric mapping stays testable."""
        registered = _text(vehicle, "INITIAL_REGISTRATION_DTE") or ""
        engine_cc = _integer(vehicle, "CAPACITY_CYLINDER_CCA_FLT")
        price = _decimal(vehicle, "PRICE_RETAIL_CUR_FLT")
        if price is None:
            raise ValueError(f"listing {vehicle.get('ID')!r} has no retail price")
        monthly = _decimal(vehicle, "PRICE_MONTHLY_RATE_CUR_FLT")
        fuel = (_text(vehicle, "FUEL_TYPE_LST") or "").lower()
        if fuel not in _FUEL_TYPES:
            raise ValueError(f"unrecognised fuel type {fuel!r}")
        model = _text(vehicle, "MODEL_TEXT_STR")
        if model is None:
            raise ValueError(f"listing {vehicle.get('ID')!r} has no model")
        mileage_miles = _integer(vehicle, "MILEAGE_MIL_INT")
        if mileage_miles is None:
            raise ValueError(f"listing {vehicle.get('ID')!r} has no mileage")
        dealer_name = _text(vehicle, "POOL_NAME1_STR")
        if dealer_name is None:
            raise ValueError(f"listing {vehicle.get('ID')!r} has no dealer name")

        return Car(
            source=self.name,
            source_id=str(vehicle["ID"]),
            brand=_text(vehicle, "MANUFACTURER_LST") or "Volkswagen",
            model=model,
            battery_kwh=_decimal(vehicle, "BATTERY_CAPACITY_FLT"),
            doors=_integer(vehicle, "NUMBER_OF_DOORS_INT"),
            mileage_miles=mileage_miles,
            year=int(registered[:4]),
            registration=_text(vehicle, "LICENSE_PLATE_STR"),
            price_pence=round(price * 100),
            dealer_name=dealer_name,
            fuel_type=_FUEL_TYPES[fuel],
            trim=_text(vehicle, "TRIM_STR"),
            description=_text(vehicle, "SUB_MODEL_TEXT_STR"),
            range_miles=_decimal(vehicle, "ELECTRIC_RANGE_MIL_INT"),
            # Delivered in bhp; the normalised figure is the kW equivalent, which is
            # what CUPRA reports natively and so the comparable column.
            power_kw=_integer(vehicle, "ENGINE_PWR_NORMALIZED_FLT"),
            engine_cc=None if engine_cc == _ENGINE_CC_SENTINEL else engine_cc,
            drivetrain=_text(vehicle, "DRIVE_TRAIN_LST"),
            transmission=_text(vehicle, "TRANSMISSION_LST"),
            colour=_text(vehicle, "BODY_COLOR_STR"),
            seats=_integer(vehicle, "NUMBER_OF_SEATS_INT"),
            first_registered=registered[:10] or None,
            monthly_price_pence=None if monthly is None else round(monthly * 100),
            dealer_city=_text(vehicle, "POOL_CITY_STR"),
            dealer_postcode=_text(vehicle, "POOL_ZIP_CODE_STR"),
            image_url=_image_url(vehicle),
            body_style=_text(vehicle, "BODY_STYLE_LST"),
            ac_charge_kw=_decimal(vehicle, "AC_KW_FLT"),
            dc_charge_kw=_decimal(vehicle, "DC_KW_FLT"),
            vin=_text(vehicle, "VIN_STR"),
            previous_owners=_integer(vehicle, "PREVIOUS_OWNER_STR"),
            model_year=_integer(vehicle, "YEAR_OF_MODEL_INT"),
        )


def _result_count(html: str) -> int | None:
    """The SRP states its own total in the inline analytics payload."""
    match = _RESULT_COUNT.search(html)
    return int(match.group(1)) if match else None
