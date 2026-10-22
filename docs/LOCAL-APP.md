# Local app guide

Run `python -m prospect app` from a checkout, or `prospect-review app` after installation. Python 3.10 or newer is required. The browser opens automatically. Keep the terminal running; Ctrl+C stops the server. `--no-browser` prints the URL without opening it, and `--port 8765` chooses a specific loopback port. The default port is selected automatically.

## Start and resume

The default workspace is `prospect-review-workspace/` under your current directory. Use `--workspace "path/to/my-workspace"` to choose another location. Reuse that location to resume saved work. There is no account or cloud synchronization. Opening an evidence link is a deliberate visit to that external website in your browser.

On first launch, try the fictional demo or import your own permitted file. The app demo imports only synthetic listings and exclusions; it does not invent a completed human review. The separate CLI demo includes a simulated review and fixed-clock assertions for reproducibility.

## Import and map

1. Choose a UTF-8 CSV, JSON array or JSONL file, or paste its contents. Select canonical fields, saved Overture data or saved OSM/Overpass data as appropriate. The app does not acquire listings.
2. Preview the file. For canonical input, map columns to the displayed fields. Required identity/location fields must be supplied; IDs, geography and observation dates are not invented. CSV list fields use `|`. Inspect errors, skipped rows and normalized samples before proceeding. Any invalid canonical row blocks the import.
3. Record the source, license, terms URL, attribution and date the rights were reviewed. Explicitly confirm permission to retain, export and use the data for discovery. Supply applicable license/notice text for non-synthetic sources. These declarations record your decision; the app cannot determine the permissions for you.
4. Confirm the import. Businesses, source hashes, mappings and rights are saved together. Preview alone does not change the queue. Reimport appends evidence and batches even when strong identity matches keep the same business IDs.

There is an 8 MiB request limit, a maximum of 5,000 source rows per import and 20,000 business identities per workspace. Split larger permitted datasets before import. The [reference](REFERENCE.md) documents input formats, adapters and identity rules.

## Review the queue

Search by business name, city or address; combine state, trade, website, review-status and queue filters. Candidate, qualified and excluded views reflect the existing screening rules. A missing website field is a signal to investigate, not proof that a website is absent.

Open a business in the Investigation desk. The queue, source evidence and decision area stay together. The four question groups are identity, website, activity and service fit. Inspect original observations, machine reasons, source information and review history alongside the question you are answering. A conflicting identity judgment records a hold; it never automatically merges or splits businesses.

Save only the question you actually checked, with its evidence URL, notes and observation date. Dates are never automatically filled with today's date. Recording an uncertain result still requires the source you checked and when you checked it. If you cannot yet provide that basis, keep the unfinished draft or defer the business.

## Guided review and drafts

Guided review presents one question group at a time, with the same underlying evidence and decisions as the desk. Move between questions, skip questions you did not check, and inspect the summary before saving. Edit any proposed answer from the summary. Only newly checked or edited questions are committed; untouched previous decisions remain unchanged. Every supplied question is validated before the whole session is saved.

Unfinished answers are saved as drafts inside the workspace, separately from review history. Switching business or view retains them, and saved drafts survive an app restart. Watch the draft status: a failed save must be retried before closing the app. Drafts do not affect qualification or appear in exports. Deferring a business keeps its draft without recording a review.

If another tab or the CLI changes a review, stale edits are rejected rather than silently replacing newer decisions. Your unfinished input remains available so you can compare it with the latest saved record before retrying. Use one running app process per workspace; revision checks do not turn this into a shared multi-user service.

Undo is available for the latest untouched investigation save. It appends a compensating history record and restores the submitted answers as a draft. It does not delete original observations, previous reviews or contact exclusions. Previously confirmed website evidence may continue to exclude a business under the existing conservative rules. A later review makes the older save ineligible for undo.

A later website correction preserves other decisions. Previously recorded activity keeps its original evidence date unless you change the activity decision or explicitly record a new activity check. Recording a review today does not make old evidence current. Contacted decisions add durable exclusions; changing a later review does not erase those controls.

The default app profile covers all supported US states, using the bundled trade rules. Settings let you adjust geography, trade groups and freshness. Qualification reflects those settings and recorded evidence; it is not contact permission. The app sends no messages.

## Exclusions and exports

Import an existing exclusion file through Settings before relying on a queue. CSV, JSON and JSONL use the suppression keys in the [reference](REFERENCE.md#suppression-and-reconciliation). Exclusions remain in the workspace and are visible in the excluded audit view.

Select individual records or explicitly choose all matching records across pages. Export CSV or JSON. Each download is a ZIP containing the data and its provenance sidecar, including source rights and notices. Keep both together. Exporting excluded records is an audit operation, clearly marked in export history; it does not clear their exclusions.

Exports remain available for repeat download in the Exports view. They are snapshots of the selected records at export time; subsequent reviews do not change earlier bundles.

## Storage, backup and troubleshooting

Stop the app before copying the **entire workspace directory** for backup or moving it to another machine. It contains `review.sqlite3`, `config.json`, imported source/notice material and generated exports. Treat this directory as private data; the software's MIT license does not grant permission to publish imported records. To start over safely, choose a new workspace. There is no destructive reset button.

The server is reachable only on `127.0.0.1`. It is designed for one local user and does not provide accounts or remote access. Use one running app per workspace. If the port is occupied, omit `--port` to let the app select an available one. After restarting, reload the page at the new terminal URL; an old page's session token is no longer valid. Uncommitted import previews expire on restart.

If a file fails, inspect the preview's row errors and mapping, then retry. Source data stays on this machine. For a bug report, include the operating system, Python version and a minimal fictional example; never upload a private workspace, original business file or exclusion list.
