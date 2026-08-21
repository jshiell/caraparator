"""The generic listing model every source maps into."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class FuelType(StrEnum):
    ELECTRIC = "electric"
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"
    PLUG_IN_HYBRID = "plug_in_hybrid"


class Car(BaseModel):
    """One listing, normalised across sources. Money is always integer pence."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    source: str
    source_id: str
    brand: str
    model: str
    battery_kwh: float | None = None
    doors: int | None = None
    mileage_miles: int
    year: int
    registration: str | None = None
    price_pence: int
    dealer_name: str
    fuel_type: FuelType

    trim: str | None = None
    description: str | None = None
    range_miles: float | None = None
    power_kw: int | None = None
    power_ps: int | None = None
    engine_cc: int | None = None
    drivetrain: str | None = None
    transmission: str | None = None
    colour: str | None = None
    seats: int | None = None
    first_registered: str | None = None
    monthly_price_pence: int | None = None
    dealer_city: str | None = None
    dealer_postcode: str | None = None
    dealer_phone: str | None = None
    dealer_lat: float | None = None
    dealer_lon: float | None = None
    image_url: str | None = None
    body_style: str | None = None
    ac_charge_kw: float | None = None
    dc_charge_kw: float | None = None
    vin: str | None = None
    previous_owners: int | None = None
    model_year: int | None = None


class RawListing(BaseModel):
    """One listing exactly as the source delivered it, before mapping."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str
    payload: Any


class ListingFeatures(BaseModel):
    """The two equipment lists a source publishes for one listing.

    Both sources group their equipment; the grouping is dropped and each kind is
    flattened to one ordered list, because the groupings do not correspond
    between sources.
    """

    model_config = ConfigDict(frozen=True)

    standard: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
