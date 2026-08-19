"""Volkswagen UK approved-used listings, from the Solr documents in the SRP."""

from __future__ import annotations

import json
from typing import Any

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
