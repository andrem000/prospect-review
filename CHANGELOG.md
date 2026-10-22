# Changelog

## 0.6.0 — 2026-10-22

- Show each question's effective saved answer and evidence basis separately from unfinished drafts and superseded guided answers.
- Group observations by source identity, showing the latest import first with earlier observations available in history.
- Carry the selected Changes reason into the Investigation desk, alongside retained historical signals that explain screening.

## 0.5.1 — 2026-10-22

- Keep the Investigation desk's saved answers aligned with newer guided, advanced and CLI corrections, without reviving superseded evidence.
- Retain actionable identity warnings for conflicting initial imports and newly ambiguous source identities.
- Serialize partial review updates so overlapping writers preserve newer decisions and caller-owned transactions.

## 0.5.0 — 2026-10-22

- Compare reimports before confirmation and show meaningful source changes in a persistent Changes inbox.
- Acknowledge or restore individual alerts without changing review decisions, drafts or exclusions.
- Show old or unknown evidence separately for saved review questions; retain activity qualification rules and add a separate advisory reminder window.
- Preserve unfinished work when source evidence changes, including across undo and upgrade from v0.4.
- Keep formatting-only imports quiet, preserve saved identity decisions on unrelated edits, and protect imports against concurrent local writes.
- Bind displayed investigation evidence to its save revisions, serialize workspace upgrades, and surface newly supplied evidence from additional sources.

## 0.4.0 — 2026-10-22

- Add explicit research assistance for a supplied public website link, with a saved-page text/HTML option that works offline.
- Show captured source evidence, dates, matching and conflicting identity clues, and cautious website/identity proposals for human review.
- Save compact research cards locally with source hashes; allow reversible dismissal and resume them after restart.
- Bring a selected proposal into an editable question draft without committing a review or changing other answers.
- Bound website requests and reject private network destinations; retain existing contact exclusions and review controls.

## 0.3.0 — 2026-10-22

- Add an Investigation desk with a business queue, readable source evidence and separate identity, website, activity and service-fit questions.
- Add guided review with question navigation, deferral, an editable summary and an atomic save of the questions actually checked.
- Keep unfinished drafts in the local workspace across navigation and restarts, separately from saved decisions and exports.
- Attach evidence to each checked question; website corrections preserve the original activity evidence date.
- Detect stale edits, make session retries idempotent and allow undoing the latest untouched investigation save by appending a compensating review.
- Preserve original observations, prior reviews and durable contact exclusions. Research assistance remains outside this release.

## 0.2.0 — 2026-10-22

- Add a bundled local browser app launched with `prospect-review app`, with no third-party runtime dependencies or external assets.
- Preview and map listing files before an explicit import, preserving source permissions, notices and transformation provenance.
- Search and filter a review queue, inspect evidence and history, and save dated decisions using the existing review engine.
- Export selected or filtered records as a ZIP containing CSV/JSON and its provenance sidecar; revisit previous exports.
- Persist settings, exclusions and progress inside a local workspace. Include a one-click fictional demo for an empty workspace.

## 0.1.1 — 2026-10-22

- Honor `freshness_days` when deciding whether recorded activity evidence is current, including a zero-day window.
- Accept explicit evidence observation dates in manual reviews. Freshness uses the activity evidence date rather than the time a review was entered; undated legacy evidence remains unverified.
- Preserve omitted fields when updating a review. Explicit values can clear decisions, and `--replace` starts a new decision from defaults while preserving history and durable exclusions.
- Keep carried activity evidence separate so an unrelated website correction cannot make an old activity check appear current.
- Return the saved effective review and document a complete fictional review/correction workflow.

## 0.1.0 — 2026-10-22

Initial public-project version.

- Offline CSV, JSON and JSONL review workflow with canonical, saved Overture and saved OSM adapters.
- Conservative identity merging, explained website/trade/activity scoring and configurable US service-business scopes.
- SQLite observations and human review history, durable exclusions and provenance-preserving CSV/JSON exports.
- Export collision checks that refuse existing data files or provenance sidecars.
- Hash-pinned reconciliation previews and application to a new database copy.
- Twelve-check fictional demo runnable from source or an installed package, without API keys or runtime dependencies.
- Portable packaging, synthetic regression tests and MIT-licensed public documentation.

Release tags identify published versions. See [distribution](PUBLICATION-STATUS.md) for installation naming and scope.
