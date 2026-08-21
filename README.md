# carparator

Ingests used **electric** car listings from CUPRA UK and Volkswagen UK approved-used
into one SQLite database, mapped onto a single generic `Car` model.

## Usage

```sh
uv sync
uv run carparator scrape --db carparator.db
uv run carparator scrape --source cupra --limit 20   # one source, capped
uv run carparator scrape --refetch-features          # re-read every equipment list
```

`--limit` caps listings per source and forces the run to be recorded as `partial`.
`--refetch-features` is explained under [Equipment](#equipment).

```sh
sqlite3 carparator.db \
  "SELECT brand, model, year, mileage_miles, battery_kwh, price_pence/100, dealer_name
   FROM cars ORDER BY price_pence LIMIT 10;"
```

## Browsing the listings

```sh
uv sync --extra web
uv run carparator serve --db carparator.db --port 8000
```

A dense sortable table with filters, at `http://127.0.0.1:8000/`. It binds the
loopback address only and is **strictly read-only** — it opens the database with
`mode=ro`, so it can be left running while a scrape writes to the same file.
Scraping is never triggered from the browser.

Filter state lives in the query string, so a view can be bookmarked and the back
button works. Sources, brands, models and drivetrains are offered as facets;
colour, trim, dealer, dealer town and dealer postcode are substring searches.

**Every filter that could hide a car for want of data says so, with a count.**
Sources disagree about which fields they populate — `range_miles` and `seats` are
patchy, `body_style` and the charging rates are Volkswagen-only, `power_ps` and
`registration` are CUPRA-only — so any filter on a nullable column offers an
"include N unknown" box, ticked by default. The count is what that filter would
hide, and it does not move when the box is ticked or unticked. Unticking a box for
a field you have not filtered on does nothing: the box discloses what a filter
hides, it is not a filter itself.

Spelling differences between the sources are folded for display and filtering
only — the database is never rewritten. CUPRA's `Rear-wheel drive` and
Volkswagen's `Rear wheel drive` are one option, as are `ID.3` and `Id.3`.

The listing scope follows the sold-listing rule below, per source. A source with
no complete run cannot call anything sold, so **all** of its cars are shown, a
banner says why, and a "last seen" column shows how cold each one is. There is no
staleness cutoff — dropping old listings would assert "sold" on evidence that does
not license it.

## Schema

| table | holds |
|---|---|
| `cars` | the current listing, keyed `(source, source_id)`, with `first_seen` / `last_seen` |
| `price_history` | one row per observed price change (integer pence) |
| `raw_listings` | the untouched source payload, so the mapper can change without re-scraping |
| `car_features` | one row per equipment item per listing, `kind` being `standard` or `optional` |
| `scrape_runs` | one row per source per run: expected vs. seen counts, and status |

**There are no migrations.** A schema change means deleting the database and
re-scraping. `PRAGMA user_version` records the schema version.

**Stale listings are never deleted.** A listing that has sold simply stops being
refreshed, so infer "sold" from a cold `last_seen` — but **only across runs with
`status = 'complete'`**. A `partial` run means the run was cut short (short of
`expected_total`, or `--limit` was used) and its absences prove nothing.

## Equipment

Two cars of the same model, year and mileage are otherwise indistinguishable, so
each listing's **standard** and **optional** equipment is captured into
`car_features`. Both sources publish the two lists, and both require a **second
request per listing** to get them — neither search response carries any equipment
at all.

The items are stored as a flat ordered list per kind. Source order is preserved by
`position`, which is part of the primary key: `(source, source_id, kind, feature)`
is deliberately **not** unique, because Volkswagen splits one comma-separated
feature across several list items and so the same short string can legitimately
appear twice. Items are stored exactly as published; nothing is reassembled,
deduplicated or retitled, and CUPRA's category grouping is dropped because the two
sources' groupings do not correspond.

**Only listings that have no equipment yet are fetched.** The first run after a
schema change therefore pays for the whole catalogue — around 1250 extra requests,
roughly 13 minutes — and every run after it costs one request per genuinely new
listing. `cars.features_fetched_at` is the marker; it exists rather than counting
`car_features` rows because a car with no optional extras has no rows to count and
would otherwise be re-fetched for ever.

Detail responses are **not** retained, unlike search payloads. So an extraction bug
cannot be fixed by remapping — `--refetch-features` ignores the marker for one run
and reads every listing again. To redo only some, clear the marker directly:

```sh
sqlite3 carparator.db \
  "UPDATE cars SET features_fetched_at = NULL WHERE source = 'volkswagen';"
```

Equipment is fetched in a **second pass, after the listing work has committed**, so
that a kill mid-run costs at most one listing's equipment rather than every listing
of that run. A listing whose equipment cannot be read is counted in
`feature_errors` and left unmarked, so the next run retries it; it never changes the
run's status, because the listings themselves are already durable. A 404 on a detail
page means the car sold between the search and the fetch and is not counted. After
three consecutive failures the pass gives up for that source, on the assumption the
endpoint rather than the listing is what is wrong.

The web UI does not yet show or filter on equipment.

## Sources

Both sites render client-side but are backed by JSON reachable over plain HTTP, so
there is no headless browser and no HTML parser.

- **CUPRA** — a private REST API. Electric-only is a **matrix path parameter**
  (`;t_petr=E`); the `?t_petr=E` query form is silently ignored and returns
  unfiltered results. Paged via the `X-Page` header. Past the last page the API
  answers 200 and omits the `cars` key.
- **Volkswagen** — the Solr document for every vehicle is embedded as one JSON
  literal in an inline script on each search-results page. The page number is a
  **path segment** (`/page2`); `?page=2` is ignored. Past the last page the site
  answers 200 with zero vehicles, so the status code is never the terminator.

Both endpoints are **undocumented and private, and will break without notice.** The
raw payloads are retained, failures are isolated per source and per page, and
`pytest -m live` acts as a canary.

Requests are sequential, spaced, and honestly identified as
`carparator/0.1 (personal use)`. A full run is around 65 search requests, plus one
detail request per listing whose equipment is not already known — so roughly 1300
on a first run against an empty database, and back to about 65 plus new stock
thereafter.

## When a page fails to parse

Both sources are private APIs that can change shape without notice, so a page whose
payload cannot be found is treated as a parse failure rather than as the end of the
results — a single bad page must not silently truncate a run.

When that happens the scrape keeps going, and the raw HTML of the offending page is
written next to the database so it can be inspected:

```
carparator.db.failed-pages/volkswagen-page7.html
```

- The directory is created only when a page actually fails. A clean run leaves no trace.
- At most three page bodies are kept per run — enough to see the new shape, not enough
  to fill a disk. The **count** reported by the CLI is of every failed page, which may
  be higher than the number of files.
- `carparator scrape` appends `failed_pages N` to the source's line when N is non-zero.
- Three *consecutive* failures end that source's run early, on the assumption the site
  has changed rather than that one page is broken.
- Only Volkswagen retains bodies; the CUPRA API returns JSON directly, so there is no
  intermediate document worth keeping.

A run truncated this way is recorded as `partial`, so the sold-listing rule above
protects you: absences in a `partial` run prove nothing. This holds even if a site
change is severe enough that the expected total cannot be read either — a run with no
total to be measured against is never recorded as `complete`, because a truncated pass
and a full one would otherwise be indistinguishable.

Nothing here is load-bearing. If the directory cannot be created or written — a
read-only volume, a full disk — the failure is logged and the scrape continues. Losing
the diagnostics must never cost more than the diagnostics were worth.

## Tests

```sh
uv run pytest           # offline; unit tests only
uv run pytest -m live   # opt-in; hits the real endpoints
```

## Known gaps

- `listing_url` is not captured. Volkswagen does publish one, in the search page's
  JSON-LD, and it is read at scrape time to reach the detail page — it is simply
  not stored. CUPRA exposes only an API href, not a browsable page.
- `vin` and `previous_owners` are Volkswagen-only; `dealer_lat` / `dealer_lon` are
  CUPRA-only. Sources legitimately differ.
- `engine_size` is nullable and empty for EVs by nature. Volkswagen's sentinel
  value of `1` is discarded.
- Volkswagen reports power in bhp; the normalised kW figure is stored so
  `power_kw` is comparable across both sources.
