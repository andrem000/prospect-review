# Changelog

## 0.2.0 — Unreleased

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
