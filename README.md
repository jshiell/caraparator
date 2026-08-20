# carparator

Ingests used **electric** car listings from CUPRA UK and Volkswagen UK approved-used
into one SQLite database, mapped onto a single generic `Car` model.

This increment is the ingestion path only. There is no search UI — query the database
with `sqlite3` directly.

## Usage

```sh
uv sync
uv run carparator scrape --db carparator.db
uv run carparator scrape --source cupra --limit 20   # one source, capped
```

`--limit` caps listings per source and forces the run to be recorded as `partial`.

```sh
sqlite3 carparator.db \
  "SELECT brand, model, year, mileage_miles, battery_kwh, price_pence/100, dealer_name
   FROM cars ORDER BY price_pence LIMIT 10;"
```

## Schema

| table | holds |
|---|---|
| `cars` | the current listing, keyed `(source, source_id)`, with `first_seen` / `last_seen` |
| `price_history` | one row per observed price change (integer pence) |
| `raw_listings` | the untouched source payload, so the mapper can change without re-scraping |
| `scrape_runs` | one row per source per run: expected vs. seen counts, and status |

**There are no migrations.** A schema change means deleting the database and
re-scraping. `PRAGMA user_version` records the schema version.

**Stale listings are never deleted.** A listing that has sold simply stops being
refreshed, so infer "sold" from a cold `last_seen` — but **only across runs with
`status = 'complete'`**. A `partial` run means the run was cut short (short of
`expected_total`, or `--limit` was used) and its absences prove nothing.

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
`carparator/0.1 (personal use)` — around 65 requests per full run.

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

- `listing_url` is not captured — neither source exposes one in its JSON.
- `vin` and `previous_owners` are Volkswagen-only; `dealer_lat` / `dealer_lon` are
  CUPRA-only. Sources legitimately differ.
- `engine_size` is nullable and empty for EVs by nature. Volkswagen's sentinel
  value of `1` is discarded.
- Volkswagen reports power in bhp; the normalised kW figure is stored so
  `power_kw` is comparable across both sources.
