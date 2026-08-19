"""Volkswagen UK approved-used listings, from the Solr documents in the SRP."""

from __future__ import annotations

import json
from typing import Any

from carparator.model import Car, FuelType, RawListing

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

        return Car(
            source=self.name,
            source_id=str(vehicle["ID"]),
            brand=_text(vehicle, "MANUFACTURER_LST") or "Volkswagen",
            model=_text(vehicle, "MODEL_TEXT_STR") or "",
            battery_kwh=_decimal(vehicle, "BATTERY_CAPACITY_FLT"),
            doors=_integer(vehicle, "NUMBER_OF_DOORS_INT"),
            mileage_miles=_integer(vehicle, "MILEAGE_MIL_INT") or 0,
            year=int(registered[:4]),
            registration=_text(vehicle, "LICENSE_PLATE_STR"),
            price_pence=round(price * 100),
            dealer_name=_text(vehicle, "POOL_NAME1_STR") or "",
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
