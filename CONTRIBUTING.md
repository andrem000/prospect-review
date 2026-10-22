# Contributing

Use Python 3.10 or newer. The runtime and tests use the standard library; no service credentials or business dataset are needed.

```sh
git clone https://github.com/andrem000/prospect-review.git
cd prospect-review
python -m unittest discover -s tests -v
python demo.py --out contribution-demo
```

Use a fresh demo output directory. For installed-command changes, also create a virtual environment, run `python -m pip install .`, change to a directory outside the checkout and run `prospect-review demo --out <new-directory>`. Do not substitute an editable installation for that packaging check.

For app changes, run `python -m prospect app --workspace <new-directory>` and exercise import, review, filtering and export in a browser with fictional data. Check keyboard navigation and a narrow window. Build a wheel and run `python scripts/verify_install.py --wheel-dir <wheel-directory>` to verify the packaged app and CLI from an isolated installation. Do not commit test workspaces or generated exports.

## Report a problem

Open an [issue](https://github.com/andrem000/prospect-review/issues) with the Python version, operating system, exact command, expected result and actual result. Include the smallest synthetic input that reproduces the problem. Replace real names, addresses, contact details, source credentials and private paths before posting. Do not attach a real database, exclusion list or customer export.

## Propose a change

Keep changes focused and explain the behavior they alter. Add a regression test when fixing a functional bug; use fictional records and temporary directories. Tests must run offline and must not read files outside the checkout or their temporary directory. Preserve uncertainty, provenance and existing contact exclusions when changing scoring, identity matching or review behavior.

Update the reference and changelog for user-visible changes. State the commands actually run, their results and any checks not performed. Avoid claims that a score predicts demand or a missing field establishes website absence.

Only contribute material you have the right to share under this project's MIT License. Preserve applicable copyright and attribution notices. Imported source-data licenses are separate from the software license.
