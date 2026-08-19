"""The generic listing model every source maps into."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FuelType(StrEnum):
    ELECTRIC = "electric"
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"
    PLUG_IN_HYBRID = "plug_in_hybrid"


class Car(BaseModel):
    """One listing, normalised across sources. Money is always integer pence."""

    model_config = ConfigDict(frozen=True)

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
