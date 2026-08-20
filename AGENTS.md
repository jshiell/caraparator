# AGENTS.md

Working notes for agents. The README covers what the tool does and how to run it; this
file covers what is easy to get wrong. Read both.

## Verification gate

- `uv run pytest` — always. Offline by default: `addopts = -m "not live"` in
  `pyproject.toml` deselects the live tests, so a bare run never touches the network.
- `uv run pytest -m live` — always, when a source's fetching or mapping changed. Hits
  the real CUPRA and Volkswagen endpoints. It is a canary for silent API drift, and it
  is the only thing that catches an endpoint changing shape.
- Manual end-to-end — when the ingest driver, the store, or the CLI changed:
  ```sh
  uv run carparator scrape --db /tmp/cp.db
  sqlite3 /tmp/cp.db "SELECT source, status, expected_total, listings_seen FROM scrape_runs;"
  sqlite3 /tmp/cp.db "SELECT COUNT(*) FROM cars WHERE fuel_type != 'electric';"  -- expect 0
  ```
  Expect both sources `complete` with `listings_seen == expected_total`.

### Known failures (baseline)

None. Every test passes. A failure is a real failure — do not assume it is pre-existing.

## Invariants that must not be broken

These were each established deliberately, and several were regressions that had to be
fixed after the fact. Breaking one is silent — no test elsewhere will notice.

- **Never hardcode a listing count.** Inventory drifts daily (CUPRA moved 249→248 and
  Volkswagen 1021→1017 during development alone). Counts come from the source's own
  reported total at runtime. Live tests assert sanity *ranges*, never totals.
- **A failure in one source must not affect another.** `ingest()` isolates each source,
  and that includes `finish_run` — if finalising the run row is left outside the guard,
  a store failure strands the row at `status='running'` and aborts every later source.
- **Diagnostics must never cost more than the diagnostics.** `_retain_failed_pages`
  writes the HTML of pages that failed to parse. Its I/O is guarded, because an
  unwritable directory previously turned "lost 20 cars" into "whole scrape aborted".
- **Pass `encoding="utf-8"` explicitly on every text file write.** Scrapes run from cron
  and systemd, where the locale default is ASCII; Volkswagen pages all contain `£`.
- **Money is integer pence everywhere.** The price-change comparison in `price_history`
  must never be a float equality.
- **Fuel-type filtering belongs to the source, not the model.** `Car` accepts every
  `FuelType` deliberately, so a mapper bug can never be silently absorbed as a
  fuel-type exclusion. `to_car` returning `None` is a skip; raising is an error; the
  two are counted separately.
- **`complete` must be earned, never defaulted to.** It is the one status that licenses
  inferring a listing has sold, so an unknown `expected_total` yields `partial`. Left as
  `complete`, a site change that broke both the vehicle payload and the total marker
  would end the run having seen nothing and report full coverage — making every listing
  look sold overnight.
- **`SqliteStore.transaction()` is not re-entrant** and raises `TransactionError` rather
  than silently nesting into a single flat commit.
- **Required fields must raise, not default.** Mappers previously wrote `or 0` / `or ""`
  for missing mileage, model and dealer name, which silently manufactured bad rows.
  Guard on `is None` so a genuine `0` mileage still maps.

## Endpoint traps

Both endpoints are undocumented and private. Each of these was verified against live
data, and several contradict what the documentation-free obvious guess would be.

**CUPRA** (`vtpapi.seat.com`)

- Electric-only is the matrix path parameter `;t_petr=E`. The `?t_petr=E` query form is
  accepted and **silently ignored**, returning petrol and hybrid cars.
- Past the last page the API returns 200 and **omits the `cars` key** rather than
  emptying it. Terminate on `.get("cars") or []`. This was found by an end-to-end run,
  not by a unit test — the fixture had encoded the wrong assumption.
- The battery regex must anchor on `kWh`. 95/100 titles also carry a `kW` power figure,
  so an unanchored pattern returns `250` from `250kW`.
- `source_id` is `carid`, not `key` (which is base64 with a trailing `=`).
- Read `raw_value` for mileage and price — the display values are `"2,222"` and
  `"49,985.00"`.
- Assert `mileage.unit == "MI"` rather than assuming it.

**Volkswagen** (`usedcars.volkswagen.co.uk`)

- The page number is a **path segment** (`/page2`). `?page=2` returns page 1's exact ID
  set.
- `FUEL_TYPE_LST=ELECTRIC` must be uppercase.
- The expected total comes from `'numberOfResults': 'N'` in an inline script. The plan
  called for a `data-results-found` attribute; **it does not exist** in the served HTML.
- Extract the vehicle array with `json.JSONDecoder().raw_decode` from the `[`. Do not
  search for the closing `')` — a value containing that sequence would truncate the blob.
- Past the last page the site returns **200** with zero vehicles and nonsense metadata
  (`1961-1018 of 1018 results`). Never trust the status code as a terminator.
- Use `PRICE_RETAIL_CUR_FLT`. `PRICE_CUR_FLT` is null on every record.
- `ENGINE_PWR` is the string `"136,bhp"`. Use `ENGINE_PWR_NORMALIZED_FLT` (kW) so power
  is comparable across sources; `power_ps` is left null rather than storing bhp under a
  PS label.
- `year` is the registration year from `INITIAL_REGISTRATION_DTE`, **not**
  `YEAR_OF_MODEL_INT`, which is the model year and routinely differs by one.

## Fixtures

`tests/fixtures/vw_srp_page1.html` is a full captured page (~650KB) and is kept
**intact on purpose**. Hand-trimming it risks removing the `JSON.parse('` wrapper or a
second embedding — exactly what the parser exists to handle. `cupra_search.json` is
trimmed to five real records chosen to cover each edge case: a plain `5dr` title with a
`250kW` figure, first-class `doors`/`seat` items, the spaced `77 kWh` AFV title format,
a dealer with no phone, and a petrol car carrying `motor.capacity`.

## Conventions

- Strict TDD: one failing test, one minimal implementation, one commit per increment.
- `pydantic` needs `ConfigDict(protected_namespaces=())` on `Car` — `model_year`
  otherwise collides with pydantic's protected `model_` namespace.
- Tests use `httpx.MockTransport`, not `respx`. Runtime dependencies are `httpx` and
  `pydantic` only; dev is `pytest` only. Keep it that way — no HTML parser, no headless
  browser, no ORM, no scraping framework.
