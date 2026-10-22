# Design and limitations

Prospect Review separates three things that are easy to conflate: what a listing contains, what a person has reviewed and whether a record belongs in the current queue.

```text
Local file + source-rights manifest
              |
       normalize and import
              |
 SQLite observations + human reviews + exclusions
              |
   configured screening and explained scores
              |
 review queue / audit + provenance sidecar
```

## Stored evidence

Each import creates a batch with a source hash and rights snapshot. Reimports retain business identities when the source identity or conservative matching keys agree, while appending observations. Matching is not fuzzy: names alone, phones alone and addresses alone do not automatically merge businesses. Ambiguous matches remain separate.

Reviews append history. Machine screening, saved human review, evidence date and queue status remain separate fields. A current record can include older observations; repeated import is not a claim that old evidence was freshly checked. CLI search records metrics in the database. The browser app evaluates a temporary in-memory copy, so viewing a queue does not append search logs to the workspace database.

## Local browser app

The standard-library HTTP server binds only to `127.0.0.1`, serves bundled assets and uses a per-process token for API requests. Host and Origin checks reject foreign browser origins. No external scripts, fonts or analytics are loaded. Imported values are rendered as text. This is a single-user local tool; it has no remote authentication, collaboration or hosted-service mode.

Each workspace owns its SQLite database, configuration, imported source material and export bundles. Import preview does not write businesses. Confirming an import records original and normalized hashes, mapping details and source rights. Mutations are serialized; exports use new generated paths. Limits are 5,000 source rows per import and 20,000 business identities per workspace. Evaluation still loads the working dataset into memory; these are bounds, not throughput guarantees. See [the local app guide](LOCAL-APP.md).

Manual updates preserve omitted decision fields; an explicit `--replace` starts from defaults without erasing history. Each review has its actual recording time and a supplied observation date. Carried activity retains a separate evidence snapshot so a website-only correction cannot refresh it. Current-operation assessment uses that activity observation date and the configured `freshness_days` window. Legacy reviews without dated activity evidence remain unverified; their recording timestamp is not a substitute.

Investigation drafts are separate from saved reviews. Each checked question has its own evidence URL, notes and observation date. A guided save validates and stages every supplied question before changing the live workspace; skipped questions preserve their prior decisions. Review and draft revisions detect stale edits, including reviews made through the CLI or the earlier review endpoint. A request identifier prevents an identical retry from appending duplicate history.

Undo appends a compensating record for the latest untouched investigation session. It restores prior effective decisions and activity evidence without deleting history or exclusions; a previously confirmed website can therefore remain excluded under the existing historical-evidence rules. Identity questions record a human judgment without merging or splitting businesses. A conflicting-identity judgment places the record on hold for further investigation.

## Screening

The score sums website-gap, identity/contact, trade and activity components. It prioritizes inspection rather than estimating conversion probability. Listed dedicated websites are excluded from the website-gap queue. Social profiles, directories, shorteners and possible hosted websites have distinct treatment. An evidence-backed human finding is needed to confirm a provider-hosted site or mark a reviewed website search as `no_site_found`.

Suppression supports identifying phone, dedicated domain, specific shared-platform profile, source ID and full name/address/city/state keys. A contacted review adds persistent suppression keys. Bare shared hosts do not exclude every business on that platform. Default queue exports omit excluded records; `--include-excluded` deliberately produces an audit.

## Boundaries

The tool opens local files and SQLite databases. It does not crawl websites, collect new listings, verify URLs, send messages or connect to a CRM. The demo's dates and evidence are fictional and fixed for reproducibility.

The working dataset is loaded into memory. Tests cover synthetic cases, not representative market precision or national-scale performance. Finite host and category rules can miss providers or misclassify records. Historical website evidence is conservative, and a dated `no_site_found` review never proves current absence. `qualified` describes the configured evidence rules; it is not contact permission or proof of demand.

`profiles/` contains provisional pressure-washing and mobile-detailing examples. Their geography and scope must be adapted to the dataset. The advanced reconciliation workflow uses hashes to detect changed inputs and requires an explicit plan ID, but still depends on the quality of supplied human decisions; it does not discover truth independently.
