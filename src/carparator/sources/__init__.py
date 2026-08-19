"""Listing sources and the shared HTTP behaviour they rely on."""

from __future__ import annotations

import time
from typing import Iterator, Protocol

import httpx

from carparator.model import Car, RawListing

USER_AGENT = "carparator/0.1 (personal use)"
TIMEOUT_SECONDS = 30.0
REQUEST_DELAY_SECONDS = 0.5
MAX_ATTEMPTS = 4


class ListingSource(Protocol):
    """Fetching and mapping stay separate so mapping is testable without network."""

    name: str
    expected_total: int | None

    def fetch_raw(self) -> Iterator[RawListing]: ...

    def to_car(self, raw: RawListing) -> Car | None: ...


def build_client(**kwargs: object) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        **kwargs,
    )


def get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> httpx.Response:
    """GET with backoff on timeouts and 5xx. 4xx is not retried — it won't improve."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(url, headers=headers)
            if response.status_code < 500:
                response.raise_for_status()
                return response
            last_error: Exception = httpx.HTTPStatusError(
                f"server error {response.status_code}",
                request=response.request,
                response=response,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = error
        if attempt == max_attempts:
            raise last_error
        time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")
