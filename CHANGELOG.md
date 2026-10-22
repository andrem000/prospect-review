# Changelog

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
