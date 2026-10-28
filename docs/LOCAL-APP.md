# Local app guide

Run `python -m prospect app` from a checkout, or `prospect-review app` after installation. Python 3.10 or newer is required. The browser opens automatically. Keep the terminal running; Ctrl+C stops the server. `--no-browser` prints the URL without opening it, and `--port 8765` chooses a specific loopback port. The default port is selected automatically.

## Start and resume

The default workspace is `prospect-review-workspace/` under your current directory. Use `--workspace "path/to/my-workspace"` to choose another location. Reuse that location to resume saved work. There is no account or cloud synchronization. Opening an evidence link is a deliberate visit to that external website in your browser.

On first launch, try the fictional demo or import your own permitted file. The app demo imports only synthetic listings and exclusions; it does not invent a completed human review. The separate CLI demo includes a simulated review and fixed-clock assertions for reproducibility.

## Import and map

1. Choose a UTF-8 CSV, JSON array or JSONL file, or paste its contents. Select canonical fields, saved Overture data or saved OSM/Overpass data as appropriate. The app does not acquire listings.
2. Preview the file. For canonical input, map columns to the displayed fields. Required identity/location fields must be supplied; IDs, geography and observation dates are not invented. CSV list fields use `|`. Inspect errors, skipped rows and normalized samples before proceeding. Any invalid canonical row blocks the import.
3. Record the source, license, terms URL, attribution and date the rights were reviewed. Explicitly confirm permission to retain, export and use the data for discovery. Supply applicable license/notice text for non-synthetic sources. These declarations record your decision; the app cannot determine the permissions for you.
4. Compare the import after supplying its source name. Inspect counts of new businesses, changed existing businesses, unchanged rows, additional source identities and identity warnings before confirming. A source or workspace change invalidates the comparison and requires a fresh comparison. Preview alone does not change the queue.
5. Confirm the import. Businesses, source hashes, mappings, rights and change events are saved together. Reimport appends evidence and batches even when strong identity matches keep the same business IDs. Choose **Review changes** or return to the normal queue.

There is an 8 MiB request limit, a maximum of 5,000 source rows per import and 20,000 business identities per workspace. Split larger permitted datasets before import. The [reference](REFERENCE.md) documents input formats, adapters and identity rules.

## Changes and evidence reminders

**Changes** groups attention by business, with Pending, Seen and All filters and a filter for existing review work. It compares the incoming observation with the preceding observation for the same source identity, showing the source links, batch references and observation dates. New source identities are additional evidence; disagreements are labeled separately. New businesses remain ordinary review-queue work.

Website/profile, identity/contact, location, activity and category/brand changes can prompt investigation. Harmless formatting and list ordering do not create new alerts. A value no longer supplied in an import does not retract historical evidence; a missing business does not mean it closed. The business display and screening continue to retain historical website/contact signals. Older incoming evidence is labeled and never becomes fresh merely because it was imported today.

**Mark seen** acknowledges one reason; **Restore** returns it to pending. These actions do not save a review, refresh evidence or clear exclusions. Identical imports do not repeat alerts; a later meaningful change, including a reversion, can create a new alert. Saving a question does not silently acknowledge all imported changes. **Investigate** opens the existing desk at the affected question, with a return path to Changes and its filters.

Saved questions also show **Old evidence** or **Evidence date/basis unknown**. Activity uses the existing activity freshness setting. Identity, website and service fit use a separate **Advisory review window**, default 180 days, which does not change qualification. Reminders are calculated when the inbox is opened/refreshed; there are no scheduled rechecks. Unknown or never-recorded dates are not treated as recent. Never-reviewed questions do not generate aging reminders. Marking a reminder seen does not reverify anything.

Older CLI reviews may lack a provable connection between a carried answer and a particular evidence date. These display an unknown basis rather than borrowing a newer answer's date. Research cards whose business context changed also appear as attention; formatting changes alone do not invalidate their context. No page is fetched automatically.

## Review the queue

Search by business name, city or address; combine state, trade, website, review-status and queue filters. Candidate, qualified and excluded views reflect the existing screening rules. A missing website field is a signal to investigate, not proof that a website is absent.

Open a business in the Investigation desk. The queue, source evidence and decision area stay together. The four question groups are identity, website, activity and service fit. Inspect original observations, machine reasons, source information and review history alongside the question you are answering. A conflicting identity judgment records a hold; it never automatically merges or splits businesses.

Save only the question you actually checked, with its evidence URL, notes and observation date. Dates are never automatically filled with today's date. Recording an uncertain result still requires the source you checked and when you checked it. If you cannot yet provide that basis, keep the unfinished draft or defer the business.

Saved question values follow the effective review, including later CLI or advanced corrections. Older guided answers remain in history. If a legacy correction's evidence cannot be associated with a particular answer, the editor leaves that evidence blank rather than reusing an older question's date or notes. Existing unfinished drafts are retained separately.

Each question has an effective-answer summary showing its saved values and known or unknown evidence basis. Your draft is labeled separately and does not change that summary until you explicitly save a decision. Superseded guided answers remain available in history.

Source cards are grouped by source name and source ID. The default card is the **latest imported** observation for that identity; expand earlier observations to inspect its history. Import order is separate from the observation date, so importing older evidence later does not make it fresh. A missing value means it was not supplied, and an omitted business does not imply closure or deletion.

When you open a reason from Changes, the desk shows that reason's exact before/after context beside the affected question. Returning to Changes preserves its filters and page. If the selected reminder no longer applies after a save, the desk says so rather than treating it as the current reason. Mark seen remains a separate action. Historical-signal explanations identify retained websites/contact details and actual exclusion matches without clearing them.

## Guided review and drafts

Guided review presents one question group at a time, with the same underlying evidence and decisions as the desk. Move between questions, skip questions you did not check, and inspect the summary before saving. Edit any proposed answer from the summary. Only newly checked or edited questions are committed; untouched previous decisions remain unchanged. Every supplied question is validated before the whole session is saved.

Unfinished answers are saved as drafts inside the workspace, separately from review history. Switching business or view retains them, and saved drafts survive an app restart. Watch the draft status: a failed save must be retried before closing the app. Drafts do not affect qualification or appear in exports. Deferring a business keeps its draft without recording a review.

If another tab or the CLI changes a review, stale edits are rejected rather than silently replacing newer decisions. Saved drafts retain their review context across restart. A changed or unknown legacy context requires explicit comparison with the latest saved work; keeping your draft does not certify changed source evidence. Your unfinished input remains available so you can compare it with the latest saved record before retrying. Use one running app process per workspace; revision checks do not turn this into a shared multi-user service.

A material reimport also marks existing drafts as based on changed source evidence. Inspect the updated observations and use **Review updated evidence** before saving a decision. Your unfinished text remains intact. Undo retains the source context of restored answers, even when newer draft questions are present. Equivalent formatting-only imports do not require this step.

Undo is available for the latest untouched investigation save. It appends a compensating history record and restores the submitted answers as a draft. It does not delete original observations, previous reviews or contact exclusions. Previously confirmed website evidence may continue to exclude a business under the existing conservative rules. A later review makes the older save ineligible for undo. Use **Preview Undo** to compare current and previous saved answers and their evidence bases before confirming. Unrelated unfinished questions remain; when a question overlaps the saved session, explicitly choose to keep your newer draft or cancel. Preview and cancellation write nothing. If input has not finished saving, use the explicit **Save draft first** action before opening a preview. Any intervening review, draft or source change requires a fresh comparison. The result separates restored decisions, session answers restored as drafts and retained newer input; remaining changed or unknown context still needs review. Controls stay locked through Undo and its refresh. If a response is lost, **Check Undo outcome** reads the saved session before you can retry; keep the page open while input or the outcome is marked unsaved. The outcome check follows the exact confirmed save even if another save becomes latest. It loads current saved work separately from the historical receipt, and retains local typing for explicit comparison. Undoing the first check restores an unchecked effective question while keeping its submitted answer as a draft.

A later website correction preserves other decisions. Previously recorded activity keeps its original evidence date unless you change the activity decision or explicitly record a new activity check. Recording a review today does not make old evidence current. Contacted decisions add durable exclusions; changing a later review does not erase those controls.

The default app profile covers all supported US states, using the bundled trade rules. Settings let you adjust geography, trade groups and freshness. Qualification reflects those settings and recorded evidence; it is not contact permission. The app sends no messages.

## Saved decision history

Expand **Saved decision history** in the Investigation desk to inspect saved checks, corrections and Undo events. The list follows stored insertion order, newest first. Filter by question and page through longer histories. Generic records with unrecorded question attribution remain visible with an uncertainty label. Repeated checks with the same answer remain visible.

Expand an event to compare previous and resulting saved answers and their known or unknown evidence bases. Recording time is separate from evidence observation dates. Guided questions are grouped only when stored session metadata proves the association. Generic corrections are not labeled as CLI or browser work when their origin was not recorded. Record-level evidence is not automatically evidence for a carried question.

Undo events can open their original save when it is known. Older receipts describe the operation at its recording time; they are not the current answer or unfinished draft. History adds no rollback controls. Current effective answers, draft comparisons, source warnings and latest eligible Undo remain separate. Opening, filtering, paging, expanding and following an original-save link do not write reviews, drafts or Changes acknowledgements. Raw historical records remain available as supporting detail.

New CLI and Advanced review corrections record where they were entered, whether they merged with current work or replaced omitted decisions with defaults, and which fields were explicitly supplied. History separates these supplied fields from changes to effective answers. A same-value submission can therefore be relevant to its question even when its answer did not change. Known notes/evidence-only corrections remain record-level updates; they do not imply that every carried question was checked.

Question filters use recorded field attribution and actual answer/evidence-basis changes where known. Older records, unavailable entry routes and unsupported metadata remain explicitly unknown and are included conservatively. This does not backfill history or make generic evidence the basis for a carried answer. Undo events retain their own classification even when older metadata is present.

## Research assistance

Open the research area beside the identity or website evidence. Supply a public website URL and explicitly confirm that you may retain its evidence. Checking the URL sends a request to that site from your machine; the business record, app session token and imported file are not sent. There is no paid API or account to configure.

The app reads one page, subject to public-address, robots, redirect, size and time limits. It does not run JavaScript, sign in, solve challenges, follow extracted links automatically or search for new businesses. A blocked, unavailable or unsupported page is an incomplete check, never evidence that a website does not exist. Use the saved-page option for text or HTML you already have permission to use, with its source URL and actual observation date. Saved-page analysis performs no network request and does not independently verify that content.

Research cards show the capture method, capture time, observation date, title, selected excerpt, source link and concrete matching, conflicting or missing details. The content hash identifies the supplied page bytes; it does not prove authorship or truth. Multiple conflicting business identities reduce confidence. Page availability does not refresh activity evidence or prove the business operates today.

Inspect the original source and proposed answer before using it. A proposal fills only the chosen identity or website draft question; it does not save a review. Existing unfinished answers require explicit replacement confirmation. Other question drafts remain intact. Edit the proposal as needed, then use the normal dated review flow to save your decision. A changed business identity makes older proposals stale; contact-excluded businesses cannot start new research or apply proposals.

Cards persist in the workspace across restart. Dismiss and restore cards to manage the evidence list without changing saved reviews. Research excerpts are not included in CSV/JSON export bundles; evidence URL and notes enter exported review data only when you explicitly save a review. The app's matching rules are assistance, not an independent verification service.

## Resume unfinished work

Open **Unfinished work** to find businesses with actual question edits saved in this workspace. Empty navigation-only draft state is not counted. Search and page through the list; resuming opens a drafted question and keeps a return path to the same worklist position. Contact-held drafts remain inspectable and read-only.

A saved-review correction can make a draft's review context older even when its answer text looks unchanged. Compare the **Review context for this draft**, **Current saved answer** and **Your draft** for each affected question. Unknown legacy context stays unknown. **Keep my draft answer** retains that question's editable draft against the displayed current review; **Use current saved answer** explicitly removes that question's unfinished edits so the current answer is shown. **Defer this question** leaves the question unresolved and its input intact. These choices prepare a draft; they do not submit a review, refresh evidence dates or mark Changes reasons seen.

Saving a resolved question preserves other unfinished questions and their original contexts, including unresolved ones. A concurrent correction can require comparison again. Source changes are a separate guard: reviewing updated source evidence does not resolve saved-review conflicts, and choosing a review answer does not accept changed source evidence.

Watch the draft status before closing: **saved**, **unsaved** and **failed save** are different states. Failed saves retain input in the open editor for retry; unsaved input is not guaranteed across closing or a process restart. Saved question drafts and unresolved context survive restart. Refresh and navigation protect edits made while requests are pending.

## Exclusions and exports

Import an existing exclusion file through Settings before relying on a queue. CSV, JSON and JSONL use the suppression keys in the [reference](REFERENCE.md#suppression-and-reconciliation). Exclusions remain in the workspace and are visible in the excluded audit view.

Select individual records or explicitly choose all matching records across pages. Export CSV or JSON. Each download is a ZIP containing the data and its provenance sidecar, including source rights and notices. Keep both together. Exporting excluded records is an audit operation, clearly marked in export history; it does not clear their exclusions.

Exports remain available for repeat download in the Exports view. They are snapshots of the selected records at export time; subsequent reviews do not change earlier bundles.

## Storage, backup and troubleshooting

Stop the app before copying the **entire workspace directory** for backup or moving it to another machine. It contains `review.sqlite3`, `config.json`, imported source/notice material and generated exports. Treat this directory as private data; the software's MIT license does not grant permission to publish imported records. To start over safely, choose a new workspace. There is no destructive reset button.

The server is reachable only on `127.0.0.1`. It is designed for one local user and does not provide accounts or remote access. Use one running app per workspace. If the port is occupied, omit `--port` to let the app select an available one. After restarting, reload the page at the new terminal URL; an old page's session token is no longer valid. Uncommitted import previews expire on restart.

If a file fails, inspect the preview's row errors and mapping, then retry. Source data stays on this machine. For a bug report, include the operating system, Python version and a minimal fictional example; never upload a private workspace, original business file or exclusion list.
