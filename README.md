# Prospect Review

**An offline, explainable review queue for business-listing files.**

Prospect Review helps you inspect a local dataset you already have permission to use. It normalizes records, merges strong identity matches, distinguishes website signals, applies supplied exclusions and records human review decisions. Each score comes with reasons, and exports retain source provenance.

Built for modest US service-business datasets. Python **3.10+**, standard library only, **zero runtime dependencies**. No accounts, API keys, downloads or outreach are needed to run it.

## Run the fictional demo

```sh
git clone https://github.com/andrem000/prospect-review.git
cd prospect-review
python demo.py --out demo-output
```

Use `python3` if that is your Python 3 command. No installation is required. The output directory must be new; repeat with a different `--out` path.

Expected result: **12 checks pass**. Ten fictional rows become eight US business identities, two review candidates and one qualified candidate after a **simulated** human review. Nothing is fetched or sent. The demo uses a fixed clock; no real business is checked, qualified or contacted.

Inspect these files in `demo-output/`:

| File | What it shows |
| --- | --- |
| `report.json` | Twelve assertions, input hashes and import/search metrics |
| `candidates.csv` / `candidates.json` | The review queue with scores and explanations |
| `audit-before-review.json` | Excluded examples and their reasons |
| `*.provenance.json` | Source rights and attribution accompanying each export |
| `demo.sqlite3` | Observations, reviews and durable exclusions |

Keep exports with their provenance sidecars. Synthetic fixtures are included; acquired business datasets are not.

## Optional installation

From the checkout, create a virtual environment and install locally:

```sh
python -m venv .venv
```

Activate it with `source .venv/bin/activate` on macOS/Linux or `.venv\Scripts\Activate.ps1` in Windows PowerShell, then run:

```sh
python -m pip install .
prospect-review demo --out installed-demo-output
```

Installation may fetch build tooling. The installed demo bundles its fixtures and runs outside the checkout. The distribution is `prospect-review`; the Python module is `prospect`. `prospect` is also a command alias. This project is installed from source; the commands above do not assume a PyPI release.

## Review your own file

Create `local-data/`, put your input there and complete a copy of [`rights.template.json`](rights.template.json) with the applicable permissions and notices. Adapt [`config.example.json`](config.example.json); its regions are examples.

```sh
python -m prospect --db local-data/review.sqlite3 init
python -m prospect --db local-data/review.sqlite3 import local-data/input.csv --rights local-data/rights.json
python -m prospect --db local-data/review.sqlite3 search --config config.example.json
python -m prospect --db local-data/review.sqlite3 show 1
python -m prospect --db local-data/review.sqlite3 export --config config.example.json --out local-data/queue.json
```

Place global `--db` before the command. Load any existing exclusion file with `suppress` before using the queue. **Export refuses an existing output file or provenance sidecar**; choose new paths for each export. The [input and command reference](docs/REFERENCE.md) covers fields, suppression, manual review, profiles and reconciliation.

## Interpret the results

- A missing website field is a listing signal, never proof that no website exists. Scores rank review priority, not purchase likelihood.
- `qualified` reflects recorded evidence and configured rules. It does not grant contact permission or verify present-day facts.
- Reimport preserves business identities but appends batches and observations. It is not a byte-identical database operation.
- Rules cover finite host/category patterns; uncertain cases need human review. Provider-hosted sites require evidence-backed confirmation.
- The working dataset is loaded into memory. National-scale performance, classification precision and commercial outcomes are not established.

Saved Overture and OSM/Overpass adapters are included; acquisition and source-rights decisions remain yours. See [design and limitations](docs/DESIGN.md) and [software/data rights](docs/RIGHTS.md).

## Contribute and license

Run `python -m unittest discover -s tests -v`. [Contribution guidance](CONTRIBUTING.md) describes reproducible reports and synthetic tests. See the [changelog](CHANGELOG.md) for version history.

Original code, documentation and synthetic fixtures are available under the [MIT License](LICENSE), copyright 2026 André Matos. Imported data retains its own rights and obligations.
