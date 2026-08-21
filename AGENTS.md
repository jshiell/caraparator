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
  sqlite3 /tmp/cp.db "SELECT COUNT(*) FROM cars WHERE features_fetched_at IS NULL;" -- expect 0
  ```
  Expect both sources `complete` with `listings_seen == expected_total`, and
  `feature_errors` at or near zero. A first run against an empty database fetches
  a detail page per listing and takes roughly 13 minutes; re-running the same
  command must report `features_fetched 0` and finish in seconds, which is the
  check that the new-listings-only rule still holds.
- Manual web checks — when the web module changed. The test client is single-threaded
  and cannot catch a connection shared across threads, so open the real thing:
  ```sh
  uv sync --extra web && uv run carparator serve --db /tmp/cp.db
  ```
  Then: a full scrape followed by `--limit` on one source, confirming the limited run's
  cars still appear; a filter on `range` showing an unknown count that does not move
  when the box is unticked; and a scrape running against the same file while the page
  is served.

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
  The feature pass is not exempt: its `has_features` and `store_features` calls sit
  inside the per-listing guard alongside the fetch, so an unwritable database costs
  that source its equipment and nothing more.
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
- **Current stock is `>=` the latest complete run, per source, never `=`.**
  `carparator scrape --source cupra --limit 20` is `partial` by construction, so its
  cars carry a *later* run id than the last complete run. Matching `=` would hide
  precisely the cars most recently confirmed to exist. Absence during a complete run
  implies sold; presence in any later run, complete or not, is positive evidence and
  wins. The predicate is written as an exclusion so the default is to include.
- **`last_seen_run_id IS NULL` is never dropped.** The column is nullable and
  `NULL >= 5` is NULL, so a bare comparison discards those rows silently — the
  project's signature defect, reintroduced by the fix for it. The web reader counts
  them separately and always lists them.
- **The unknown-count definition is fixed, and its invariance is a test.** For field F:
  rows where F IS NULL, within current-stock scope, matching every other active filter,
  **ignoring F's own predicate and F's own toggle**. Anything else is self-referential
  and reads "include 0 unknown" the moment the box is unticked.
- **The unknown toggle is inert unless that field's filter is active.** It discloses
  what a filter would hide; with no filter set it must not delete rows.
- **`normalise.drivetrain_key` mirrors `DRIVETRAIN_KEY_SQL` exactly**, algorithm for
  algorithm, not "equivalently". Filtering runs in SQL and the option list is built in
  Python; a value they folded differently would be unselectable — the user ticks an
  option and gets nothing back. There is a test that runs both over the same values.
- **Sort column and direction are whitelisted, never bound.** SQLite cannot
  parameterise an ORDER BY, so the whitelist is the only defence. Absent values sort
  last in *both* directions, or sorting by range ascending presents every car whose
  range nobody stated as though it had the worst.
- **Empty equipment is never success.** `extract_vw_features` and
  `extract_cupra_features` return `None` rather than an empty `ListingFeatures`
  when the standard list is empty. Returning empty instead would store zero
  features, set `features_fetched_at`, retire those listings from every future
  run, leave `feature_errors` at 0 and report the run `complete` — this
  project's signature class of silent failure. Measured over a full run:
  252/252 CUPRA listings parse, and 994/1003 Volkswagen ones; the nine that do
  not are two known page shapes, both listed under the endpoint traps.
- **The circuit breaker counts transport failures only, never parse failures.**
  A page that arrived intact but would not parse is evidence about one listing;
  a 5xx or a timeout is evidence about the endpoint, and only the latter carries
  `get_with_retry`'s 1+2+4s backoff. The distinction is load-bearing: from the
  second run on, the listings still lacking features are *precisely* the ones
  that failed to parse before, so counting them would trip the breaker on every
  run and starve the genuinely new listings queued behind them — silently, with
  the run still reporting `complete`. This was observed on a real second run
  before it was fixed, not reasoned about in the abstract.
- **`features_fetched_at` is a marker, never "has rows in `car_features`".** A
  listing with genuinely no optional extras has no rows to count and would
  otherwise be re-fetched on every run for ever.
- **The feature pass runs after the listing transaction commits.** Folding
  ~1250 detail fetches into it would stretch one commit from seconds to minutes,
  so a kill mid-run would cost every listing rather than none, and would widen
  the concurrent-scrape lock window by the same factor. The pass must also never
  change the run's `status`, and never touch `listings_seen`, `listings_stored`
  or `expected_total` — `_is_complete` and the sold-listing inference depend on
  them.
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
- Equipment needs a **second, per-listing request**: the search response carries no
  equipment keys at all. The `href` for it hangs off the search **entry**, not
  `entry["car"]`, and its last path segment is the entry's base64 `key` — so the
  map from `source_id` to href has to be built as the search is walked.
- The detail endpoint answers **401** without `X-Pattern: cuprawebfe`.
- `serie_equip` is standard equipment and `equip` is optional. **`special_equip` is
  not equipment** — it holds insurance type classes, and its entries are shaped
  differently (a `text` envelope, with `value` always `""`).

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
- Equipment needs a **second, per-listing request**: the 189 keys in the search
  payload include none of it. The detail URL is already on the SRP, in a JSON-LD
  blob whose `sku` equals the vehicle `ID` exactly, so reading it there costs no
  extra request. The detail page in fact resolves on the trailing ID alone — the
  slug and the make/model segments are ignored, and the ID is case-insensitive
  (`.../cupra/nonsense/zzz-r7ec4dx` serves the same Golf) — but use the JSON-LD
  URL anyway: it is canonical and costs nothing.
- The equipment slice must be bounded by `<div class="technical__specification"`.
  Read to the end of the document instead and the finance tab's
  `<h4>Select a finance product</h4>` falls inside it.
- **`<h4>Fitted optional extras</h4>` is legitimately absent** on some listings —
  0 to 10 optional items per car, against 65 to 132 standard.
- **Volkswagen fragments one comma-separated feature across several `<li>` by
  design.** "ACC - Adaptive cruise control with front assist" / "forward collision
  warning" / "distance monitoring" arrive as three items. Items are captured
  verbatim; there is no heuristic reassembly, and so
  `(source, source_id, kind, feature)` is **not** unique.
- The glossary popup is a **sibling** of `<span class="label">`, not a child, and
  repeats the label's text. A non-greedy match on the span cannot swallow it.
- Across 1588 real labels, **zero contain `<` and zero contain `&`**. There is no
  tag-stripping and no `html.unescape`, because neither is reachable and so
  neither could be driven by a failing test.
- **There is a second standard-equipment markup, and it is truncated.** About
  0.5% of listings (5 of 1003 on a full run: `FKEEF69`, `K5EDNRA`, `P1EDQL7`,
  `P1EDRGM`, `K5EDHZC`) render the standard list not as `<li><span class="label">`
  items but as one bare `<span>` holding a comma-separated blob — and the blob is
  cut off by a length cap, all five landing between 906 and 1017 characters with
  roughly 30 items against the usual 65–132. **Do not parse it.** Splitting it on
  commas would store a truncated list indistinguishable from a complete one,
  which is the very failure the empty-is-never-success guard exists to prevent.
  These listings are correctly counted in `feature_errors` and retried each run.
- **Some detail pages carry no equipment region at all** and still answer 200
  (4 of 1003: `NXEED8W`, `NXEED8Z`, `S3EED8X`, `Q4D7B9L`). There is no data to be
  had; `extract_vw_features` returns `None` and the listing is counted.
- Together those two shapes put a **floor of around 9 on Volkswagen's
  `feature_errors`**. Treat that as the baseline; a jump well above it is drift.

## Fixtures

`tests/fixtures/vw_srp_page1.html` is a full captured page (~650KB) and is kept
**intact on purpose**. Hand-trimming it risks removing the `JSON.parse('` wrapper or a
second embedding — exactly what the parser exists to handle. `cupra_search.json` is
trimmed to five real records chosen to cover each edge case: a plain `5dr` title with a
`250kW` figure, first-class `doors`/`seat` items, the spaced `77 kWh` AFV title format,
a dealer with no phone, and a petrol car carrying `motor.capacity`.

`vw_vdp.html` and `cupra_detail.json` are the detail responses for the **first record
of each source's search fixture** — vehicle `R7EC4DX` (132 standard / 5 optional) and
carid `GBR551693296921` (56 standard / 29 optional) — so detail tests line up with the
search tests already written against those records. `vw_vdp.html` is kept intact for
the same reason `vw_srp_page1.html` is, and loaded with `scope="module"`.

## Recovering from a bad feature extraction

Detail responses are **not** retained — unlike search payloads, which go to
`raw_listings`. So a bug in `extract_vw_features` or `extract_cupra_features` cannot be
fixed by remapping; the features have to be fetched again. Two ways, both of which cost
a detail request per listing:

```sh
uv run carparator scrape --refetch-features          # ignore the marker for this run
sqlite3 carparator.db "UPDATE cars SET features_fetched_at = NULL;"   # or clear it
```

Clearing the marker is the one to reach for when only some listings are affected — add
a `WHERE` clause — since the flag is all-or-nothing.

## Conventions

- Strict TDD: one failing test, one minimal implementation, one commit per increment.
- `pydantic` needs `ConfigDict(protected_namespaces=())` on `Car` — `model_year`
  otherwise collides with pydantic's protected `model_` namespace.
- Tests use `httpx.MockTransport`, not `respx`. **The scraper's** runtime dependencies
  are `httpx` and `pydantic` only. Keep it that way — no HTML parser, no headless
  browser, no ORM, no scraping framework.
- The web UI is an **optional extra**: `flask` lives in `[project.optional-dependencies]
  web`, never in `[project] dependencies`. It is also in the `dev` group, because
  `uv run pytest` installs dependency-groups but **not** extras — without it
  `tests/test_web_app.py` fails at collection and the zero-known-failure baseline is
  gone. So dev is `pytest` **and** `flask`, deliberately.
