"""CUPRA approved-used listings, via the private VTP search API."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
