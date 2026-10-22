# Input and command reference

All examples run from the repository root. After local installation, substitute `prospect-review` for `python -m prospect`. Global `--db` must appear **before** the command. Run `python -m prospect <command> --help` for the complete option list.

## Canonical input

Use UTF-8 CSV, a JSON array of objects, or JSONL/NDJSON with one object per line. Each row requires `source_id` and `name`. Rows need `country=US` and a supported two-letter `state` to be imported; missing or unsupported geography is skipped and counted. The 50 states and DC are supported.

| Field | Meaning |
| --- | --- |
| `source_id` | Stable identifier within the rights manifest's `source` namespace |
| `name`, `country`, `state` | Business identity and import geography |
| `city`, `address`, `postcode` | Optional location and identity evidence |
| `categories` or `trade` | Source categories matched against the selected config |
| `websites` or `website` | Listed URLs, not independently visited or verified |
| `phones` or `phone`, `socials` | Optional listed contact/identity signals |
| `brand`, `activity` | Optional source signals; blank activity remains unknown |
| `observed_at`, `source_url` | Original observation timestamp/date and evidence URL |

For list fields, CSV cells use `|` separators, such as `plumber|plumbing_service`. JSON input may use actual arrays. A CSV cell containing JSON-array **text** is not parsed as an array. Do not invent observation dates for imports with unknown dates.

Example fictional JSON row:

```json
{
  "source_id": "example-1",
  "name": "Fictional Example Plumbing",
  "country": "US",
  "state": "TX",
  "city": "Austin",
  "categories": ["plumber"],
  "websites": [],
  "activity": "unknown",
  "observed_at": "",
  "source_url": "https://example.org/fictional-record"
}
```

Saved Overture objects/GeoJSON and OSM/Overpass objects use `--adapter overture` or `--adapter osm`. A source file is required; these adapters do not download data. OSM inputs need explicit country/state tags; geography is not guessed from coordinates. An Overpass response reporting a remark/error is rejected. See [rights requirements](RIGHTS.md).

## Commands

`python -m prospect app` opens the local browser app. Use `--workspace <directory>` to resume a chosen workspace, `--port <number>` to select a loopback port, or `--no-browser` to print the URL without opening it. This command uses its workspace database; global `--db` belongs to the existing CLI commands below. See [the app guide](LOCAL-APP.md).

Create the database's parent directory first. Ordinary CLI commands can create the SQLite file when it does not exist, so check the `--db` path carefully.

| Command | Purpose |
| --- | --- |
| `demo --out <new-directory>` | Run the bundled fictional demonstration; refuse an existing output directory |
| `init` | Initialize the local database |
| `import <file> --rights <manifest> [--adapter canonical\|overture\|osm]` | Append a source batch and normalized observations |
| `suppress <file>` | Import durable exclusion keys from local CSV/JSON records |
| `search --config <file>` | Print queue records, scores, reasons and metrics |
| `show <id>` | Inspect a stored business and its observations/reviews |
| `review <id> ...` | Append an evidence-backed manual decision |
| `export --config <file> --out <new.csv\|new.json>` | Write records and a provenance sidecar |
| `stats` | Show stored run metrics; reported API cost covers this tool only |
| `reconcile-preview` / `reconcile-apply` | Plan dated review updates, then apply an approved plan to a fresh database copy |

`search` and `export` accept `--qualified-only` and `--include-excluded`. The latter is an audit scope and may include suppressed/contacted records. **Export refuses an existing payload or provenance sidecar**; both output paths must be new. Search appends run metrics, so it modifies the database.

### Manual review

`review` requires an HTTP(S) `--evidence-url` and nonempty `--notes` on every call. Decision flags omitted on an update retain their previous effective values. Initial reviews and `--replace` start with website status `unreviewed`, other decision fields `unknown`, disposition `pending`, and empty URLs/dates. The command appends history and returns the effective saved review so you can inspect the result.

Website status is `unreviewed`, `no_site_found`, `site_found` or `uncertain`. `site_found` requires an effective `--verified-website`; social, directory and shortener URLs cannot be confirmed as a business website. Use `--candidate-website` for an unresolved possibility. Other flags record activity, independent/chain ownership, residential/service fit, mobile/fixed delivery and disposition. Explicit `unknown`, `unreviewed` or `pending` values reset the respective field; an explicit empty URL clears that field. `--replace` resets omitted fields instead of preserving them.

Supply `--evidence-observed-at YYYY-MM-DD` for the date the evidence was actually observed. Newly supplied dates must be valid calendar dates and not later than the current UTC date. Recording time is stored separately. Omitted dates are preserved on updates, or remain unknown on a new/replacement review. Existing undated, malformed or future-dated activity evidence does not establish current operation. Do not substitute today's date merely because you are entering an old finding.

Activity retains its own evidence date, URL and notes. A website correction—even one with a newer observation date—does not refresh carried activity. Explicit `--activity active` records an activity assertion using the effective observation date and the supplied evidence URL/notes. For a newly observed activity check, supply its date explicitly. A missing date keeps the activity unverified. `freshness_days` is the maximum age in calendar days for this activity evidence, inclusive of the boundary; `0` permits only evidence observed on the scoring date. Old or future evidence relative to `as_of` cannot qualify a business as currently operating.

`--disposition contacted` adds persistent suppression keys. Later updates or `--replace` do not remove those keys, prior confirmed-site evidence or history. These are operator assertions; the program does not visit the evidence URL or verify a decision. The [README example](../README.md#record-and-correct-a-review) demonstrates a dated review and a partial correction using only fictional inputs. Consult `review --help` before recording a decision.

### Configuration

Start with [`config.example.json`](../config.example.json). `trades` is a required, nonempty mapping from trade names to category-alias lists. `regions` restricts state/city matches; an empty list permits supported US geography. Change the example regions to match the intended dataset.

Optional settings include `chain_names`, `freshness_days` (nonnegative integer, default 180, for manual activity evidence), `as_of`, `service_scope` (`residential` or `service`), `delivery_scope` (`any`, `mobile`, `fixed`), `broad_categories` and `excluded_categories`. `freshness_days` does not certify website absence, ownership or scope freshness; those remain operator assertions. Use `as_of` only for an intentional dated analysis; it does not refresh evidence. [`profiles/`](../profiles/) contains provisional alternative service examples.

### Suppression and reconciliation

Suppression rows can supply `phone`, `website`, `source` plus `source_id`, or full `name`/`address`/`city`/`state` identity. Include a `reason` to explain the exclusion. Inspect results rather than assuming a vague identifier matched. Specific shared-platform profiles are distinct; a bare platform host is not a business identity.

Reconciliation is an advanced local workflow for already-reviewed, hash-pinned snapshots. `reconcile-preview --manifest <file> --controls <file> --out <new-plan.json>` reads a database without changing it and records proposed actions/conflicts. `reconcile-apply --plan <file> --approve-plan <plan-id> --out-db <new.sqlite3>` requires a matching plan ID and writes only to a fresh database copy. Changed inputs, identity conflicts or an existing output path block application. The [synthetic reconciliation tests](../tests/test_queue_upgrade.py) illustrate the input contract; no ready-to-use real-data manifest is supplied.

## Errors and output

CLI results are JSON on standard output. Handled input/database errors print `Error:` on standard error and return exit code `2`; successful commands return `0`. Keep the original source and rights manifest alongside any derived output. A completed command is not proof that its input assertions are true.

An explicitly supplied activity assertion without `--evidence-observed-at` has an unknown observation date, even if an unrelated earlier check had a date. It cannot borrow that check’s freshness. Omitting activity entirely continues to preserve its existing evidence basis. An explicit `unknown` activity answer records an inconclusive check; an untouched default is not a check.
