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

Reviews append history. Machine screening, saved human review, evidence date and queue status remain separate fields. A current record can include older observations; repeated import is not a claim that old evidence was freshly checked. Search records metrics in the database, so it is not a read-only database operation.

## Screening

The score sums website-gap, identity/contact, trade and activity components. It prioritizes inspection rather than estimating conversion probability. Listed dedicated websites are excluded from the website-gap queue. Social profiles, directories, shorteners and possible hosted websites have distinct treatment. An evidence-backed human finding is needed to confirm a provider-hosted site or mark a reviewed website search as `no_site_found`.

Suppression supports identifying phone, dedicated domain, specific shared-platform profile, source ID and full name/address/city/state keys. A contacted review adds persistent suppression keys. Bare shared hosts do not exclude every business on that platform. Default queue exports omit excluded records; `--include-excluded` deliberately produces an audit.

## Boundaries

The tool opens local files and SQLite databases. It does not crawl websites, collect new listings, verify URLs, send messages or connect to a CRM. The demo's dates and evidence are fictional and fixed for reproducibility.

The working dataset is loaded into memory. Tests cover synthetic cases, not representative market precision or national-scale performance. Finite host and category rules can miss providers or misclassify records. Historical website evidence is conservative, and a dated `no_site_found` review never proves current absence. `qualified` describes the configured evidence rules; it is not contact permission or proof of demand.

`profiles/` contains provisional pressure-washing and mobile-detailing examples. Their geography and scope must be adapted to the dataset. The advanced reconciliation workflow uses hashes to detect changed inputs and requires an explicit plan ID, but still depends on the quality of supplied human decisions; it does not discover truth independently.
