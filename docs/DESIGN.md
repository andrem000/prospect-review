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

The Investigation desk and Changes resolve effective answers and their evidence associations through one shared read model. Retained guided snapshots are history, not editor defaults after a newer CLI or advanced correction. Unknown evidence associations remain unknown; unfinished drafts remain separate. Partial core/CLI reviews read their merge basis under a writer transaction, preserving caller-owned transactions and preventing overlapping writes from restoring omitted stale values.

The desk's read-only evidence view is built from the same pinned snapshot as its displayed business and revision tokens. It groups observations by source identity using import order, keeps earlier observations accessible, exposes current Changes reasons, and explains retained historical website/contact signals. This presentation does not change identity matching, scoring, observation retention, acknowledgements or contact controls. Observation dates remain distinct from import order.

Reimport comparisons use preceding source-identity observations, independently of the accumulated business display. Change events append in the same transaction as observations; acknowledgements live separately from reviews. Formatting normalization suppresses equivalent differences, while missing fields retain their limited meaning of “not supplied.” Missing rows have no removal semantics. Source revisions include material transition identity so A → B → A cannot revive an older draft's authorization to save. Upgrading starts from existing observations without generating historical change events.

Exceptional identity warnings also persist for newly created identities, including conflicting duplicate source rows and ambiguous strong matches. Ordinary new businesses do not generate change noise. Warning details retain the source alias and implicated identities/observations without automatically merging or splitting businesses.

Evidence-age reminders are computed on read from question-specific bases that can be associated with effective answers. Activity keeps its existing qualification rule. Other questions use advisory_review_days (default 180), which changes attention only. Legacy evidence with an uncertain association is labeled unknown. Marking a reason seen cannot refresh dates or clear exclusions. Research context uses normalized identities; legacy cards are matched to their retained historical aggregate context without rewriting their capture evidence.

## Local browser app

The standard-library HTTP server binds only to `127.0.0.1`, serves bundled assets and uses a per-process token for API requests. Host and Origin checks reject foreign browser origins. No external scripts, fonts or analytics are loaded. Imported values are rendered as text. This is a single-user local tool; it has no remote authentication, collaboration or hosted-service mode.

Each workspace owns its SQLite database, configuration, imported source material and export bundles. Import preview does not write businesses. Confirming an import records original and normalized hashes, mapping details and source rights. Mutations are serialized; exports use new generated paths. Limits are 5,000 source rows per import and 20,000 business identities per workspace. Evaluation still loads the working dataset into memory; these are bounds, not throughput guarantees. See [the local app guide](LOCAL-APP.md).

The final app comparison is pinned to normalized input, its source namespace and relevant database evidence/review state. Commit rejects a changed baseline. App imports stage validation in memory, then insert only their new rows under a writer lock with a database-version check; they never replace a live database with an earlier snapshot. CLI imports acquire the writer lock before reading matching keys. The UI tracks input generations so an old upload or comparison response cannot replace newer input.

Investigation details and their save revisions come from one SQLite snapshot. An import cannot pair older displayed evidence with a newer authorization token. Workspace schema upgrades acquire the writer lock before inspecting or changing the schema, and roll back schema changes and evidence backfill together on failure.

Manual updates preserve omitted decision fields; an explicit `--replace` starts from defaults without erasing history. Each review has its actual recording time and a supplied observation date. Carried activity retains a separate evidence snapshot so a website-only correction cannot refresh it. Current-operation assessment uses that activity observation date and the configured `freshness_days` window. Legacy reviews without dated activity evidence remain unverified; their recording timestamp is not a substitute.

Investigation drafts are separate from saved reviews. Each checked question has its own evidence URL, notes and observation date. A guided save validates and stages every supplied question before changing the live workspace; skipped questions preserve their prior decisions. Review and draft revisions detect stale edits, including reviews made through the CLI or the earlier review endpoint. A request identifier prevents an identical retry from appending duplicate history.

Undo appends a compensating record for the latest untouched investigation session. It restores prior effective decisions and activity evidence without deleting history or exclusions; a previously confirmed website can therefore remain excluded under the existing historical-evidence rules. Identity questions record a human judgment without merging or splitting businesses. A conflicting-identity judgment places the record on hold for further investigation.

## Screening

The score sums website-gap, identity/contact, trade and activity components. It prioritizes inspection rather than estimating conversion probability. Listed dedicated websites are excluded from the website-gap queue. Social profiles, directories, shorteners and possible hosted websites have distinct treatment. An evidence-backed human finding is needed to confirm a provider-hosted site or mark a reviewed website search as `no_site_found`.

Suppression supports identifying phone, dedicated domain, specific shared-platform profile, source ID and full name/address/city/state keys. A contacted review adds persistent suppression keys. Bare shared hosts do not exclude every business on that platform. Default queue exports omit excluded records; `--include-excluded` deliberately produces an audit.

## Boundaries

The tool opens local files and SQLite databases. Optional research assistance checks a single supplied public page after an explicit request, or analyzes saved text/HTML without fetching. It does not discover listings, run background crawls, execute page scripts, send messages or connect to a CRM. The demo's dates and evidence are fictional and fixed for reproducibility.

Research capture is separate from human review. Compact evidence cards retain the source URL, capture mode/time, observation date, content hash, selected excerpt and explainable clues. The full page is not stored. Matching names, phones and locations support proposals; missing evidence is not a contradiction, and a working page does not establish current business activity. Proposals do not change decisions until a user brings one into a draft and saves it through the existing review workflow. A changed business identity invalidates old proposals.

Explicit network checks validate public destinations and redirects, pin connections to validated addresses, verify TLS, respect robots restrictions and bound response sizes and work. They send no listing record, cookies, saved credentials or session token to the target. Network work occurs outside the app's mutation lock; identity/contact controls are checked again before saving evidence. Saved-page analysis performs no network requests and retains its operator-supplied observation date. External text and structured claims remain untrusted, including JSON-LD. No confidence score is a substitute for human source review.

The working dataset is loaded into memory. Tests cover synthetic cases, not representative market precision or national-scale performance. Finite host and category rules can miss providers or misclassify records. Historical website evidence is conservative, and a dated `no_site_found` review never proves current absence. `qualified` describes the configured evidence rules; it is not contact permission or proof of demand.

`profiles/` contains provisional pressure-washing and mobile-detailing examples. Their geography and scope must be adapted to the dataset. The advanced reconciliation workflow uses hashes to detect changed inputs and requires an explicit plan ID, but still depends on the quality of supplied human decisions; it does not discover truth independently.

Unfinished question drafts retain per-question saved-review revision baselines. A changed or unknown baseline requires explicit resolution for that question, independently of source acceptance. A partial review save keeps unsubmitted draft questions and their baselines in the same transaction. The unfinished-work read view excludes empty navigation state and does not acknowledge changes or alter reviews. Revision-based context checks are conservative: unchanged answer text does not itself certify a newer review context.

Undo preview reads a single SQLite snapshot without writing. Browser confirmation binds the displayed session, review, exact draft and baselines, source revision and matching contact exclusions with a canonical SHA-256 token, then rechecks them under the writer lock. Overlapping unfinished questions require an exact explicit keep list; unrelated drafts retain their context. The compensating review, merged draft and replay receipt commit atomically. An identical confirmed retry returns the original receipt without reapplying Undo; current investigation state remains authoritative after later edits. The legacy two-field local Undo API keeps its prior behavior for compatibility; the browser always uses preview confirmation.

Saved decision history reads one business snapshot without changing stored state. Events follow review insertion IDs; only proven contiguous guided rows are grouped. Compensating Undo records are classified before inherited question metadata, and original-save lookup remains scoped to that business. Historical before/after answers use the same evidence association rules as the effective-answer model. Generic recording evidence and unknown origin remain explicit; observed and recorded dates are independent. Pagination limits output; this local read model scans the business history and does not claim national-scale optimization.

New correction provenance is recorded at supported CLI/advanced writer boundaries before defaults or merges. The versioned route/mode/supplied-fields envelope is insertion metadata, never an external request claim or carried-forward origin. Unknown direct callers and legacy records retain unknown attribution. History validates the envelope conservatively, classifies guided/Undo first, and separates supplied fields from effective value changes and existing evidence associations. This metadata does not change replay, qualification, contact controls, draft baselines or Undo eligibility.
