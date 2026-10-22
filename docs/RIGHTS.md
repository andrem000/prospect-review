# Software, fixtures and source-data rights

## Included material

The original Prospect Review code, documentation and fictional fixtures are provided under the [MIT License](../LICENSE), copyright 2026 André Matos. The license text follows the [Choose a License MIT template](https://choosealicense.com/licenses/mit/). Keep its copyright and permission notice with copies or substantial portions of the software.

The project uses Python's standard library and does not vendor a third-party runtime library. Provider names and URLs identify adapter formats or synthetic test cases; they do not imply affiliation, endorsement or redistribution of provider datasets. Test URLs are strings, not instructions to visit those services.

## Imported material

The software license does **not** grant rights to data you import. This repository includes no acquired business dataset. Obtain and assess any outside source separately, including its retention, discovery, export, attribution and notice requirements.

Copy [`rights.template.json`](../rights.template.json) for each source. The importer requires:

- Nonempty `source`, `license`, `terms_url`, `attribution` and ISO-date `reviewed_at` fields.
- Explicit `true` values for `retention_permitted`, `export_permitted` and `lead_discovery_permitted`.
- For non-synthetic data, readable `notice_files` containing the applicable license/notice text. Paths resolve relative to the rights manifest.

Use only trusted, operator-reviewed manifests. Notice paths can be absolute or traverse parent directories, and referenced file contents are embedded verbatim in database provenance and exports. Reference only the intended license/notice files; never private files, credentials or unrelated documents. The importer is not a sandbox for untrusted manifests.

These fields are operator assertions, not legal verification. Do not change them to `true` merely to bypass validation. The persistent importer rejects Google content. Overture and OSM adapters require matching `source` identifiers, but adapter availability does not establish current provider permission or compatibility.

Import batches preserve the supplied manifest, notice contents and source-file hash. Exports write a `.provenance.json` sidecar with the relevant sources and notices. Keep that sidecar with the export and review its contents before sharing. An audit export can include excluded records; provenance and source notices may also contain information you should not publish.

Listing availability, a published contact address, a score or a saved review does not authorize outreach. Maintain your own current source-rights, recipient, objection and contact-history checks outside the software.
