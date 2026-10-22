# Measuring extraction quality on a tenant's own documents

Date: 2026-10-22. Project B of three in the move to a server-side service. Project A (PDFium parser,
ADR-0025) is merged. Project C (retiring the installable-library surface, including the `docdoc
eval` CLI) comes after this one, and depends on it: this is what replaces `eval` for the people who
run docdoc.

## Why

Every deployment processes different documents: one tenant sends invoices, another contracts,
another scanned forms. The only quality measure docdoc has today is `docdoc eval` over a committed
golden set of four synthetic invoices. That tells a self-hosting operator nothing about *their*
documents, and nothing about whether a change they made (another model, a prompt edit, a parser
upgrade, a new schema version) helped or hurt.

The owner wants each tenant to be able to answer "does docdoc get the information we need right, on
our documents, and did my last change make it better or worse?", without preparing any dataset
files.

## What "right" means here

**Field-level extraction correctness**: is the value docdoc extracted for `invoice_number`, `total`,
… the right one, and was it read from the right place on the page? Reading the text correctly is
the means and the right field is the end. This is exactly what `docdoc.evaluation` already
measures (field_accuracy, coverage, missing_rate, incorrect_rate, grounding_rate, mislocation_rate),
so no new metric is invented. Character-level OCR accuracy is out of scope.

## The flow, as decided with the owner

1. **Checking, by eye.** A person opens a succeeded extraction run, sees each field's extracted value
   beside the page image with the place it was read from highlighted, and marks each field
   **correct**, or **wrong** with the right value (or "not present in the document"). They do not
   have to check every field. A run with at least one checked field becomes an entry in that tenant's
   **answer set** for the run's schema.
2. **Re-running.** One action re-processes every document in the answer set with the deployment's
   *current* configuration, scores the new results against the saved answers, and stores a
   **report** that records the configuration used (parser, model, schema version and hash, prompt
   hash). Answers do not change when re-running; only docdoc's output does.
3. **Comparing.** A Quality screen shows the latest report per schema (documents checked, field
   accuracy, located-on-page rate, missing rate, and a per-field table where each field links to the
   documents it got wrong) and the history of reports, so a configuration change shows up as a
   before/after.

Mockup reviewed with the owner (accepted as "hợp lý"):
`.superpowers/brainstorm/55681-1792684081/content/review-flow.html`. It shows the run-detail
checking table beside the page and the Quality screen with a stat strip and a per-field table.

## Goals

- Per **tenant**: each tenant has its own answer sets, reports, and scores. Nothing is shared across
  tenants. The console operator can see every tenant's, as for runs (ADR-0023).
- Both surfaces: the **HTTP API** (a tenant's Bearer key: record checks, start a re-run, read
  reports) and the **console** (the operator, cross-tenant, same capabilities).
- A console screen that shows an **extraction** run's result: fields, values, and where each was read
  on the page. Today only OCR results render (`showsResult` in `ui/src/console/model/runs.ts`), and
  an extraction run offers only a JSON download.
- Re-running warns how many documents it will process before it starts. It goes through the existing
  asynchronous run queue, so it never blocks the console, and it reuses unchanged stages through the
  content-addressed store. With an unchanged configuration it costs nothing.
- Checked documents survive retention: a run whose fields were checked is not swept while its answers
  live.
- Erasure stays complete: erasing a document or a tenant removes its answers and reports.

## Non-goals

- Character-level OCR accuracy metrics.
- Uploading a prepared answer file or a labelled dataset. Answers come from checking only.
- Sharing answer sets across tenants or across deployments.
- Changing what `docdoc.evaluation` measures, or its metric definitions.
- Retiring `docdoc eval` or the committed `datasets/mvp` golden set (project C).

## Anti-goals: what would make this a failure even if it works

- **Becoming a review platform.** Principle IX and ADR-0019 §2 forbid the console to hold, display,
  or let anyone set reviewer assignment, a review queue, workload, a review state, or a disposition.
  So there is no "documents waiting to be checked" list, no assigning, no per-person counts, no
  "checked / unchecked" lifecycle on runs, and no approve/reject vocabulary. The person chooses which
  runs to open, and a run's status set stays the closed five. The UI words are "correct" and
  "wrong", which pass `ui/scripts/check-console-boundary.mjs` (it forbids `approve`, `reject`,
  `queue`, `assign`, `workload`, `review_state`, and similar).
- **Numbers that silently mean different things.** A report must say which answers and which
  configuration it was computed from. Two reports over different answers must be visibly not
  comparable. `golden_set_id` already does this, and the UI must show it, not hide it.
- **Surprise cost.** A re-run that calls a paid provider for N documents without saying N first.
- **Cross-tenant leakage** of any answer, value, or score.

## Constraints and existing machinery to build on

- **Evaluation library** (`src/docdoc/evaluation`). `evaluate(golden, predictions, …) ->
  EvaluationReport` is pure: it never runs the pipeline or persists anything. `EvaluationReport`
  already carries per-document outcomes, per-field breakdowns (`metrics.per_field_path`),
  provenance (models, parsers, schema and prompt hashes), and a `report_id` derived from
  `golden_set_id` + `prediction_set_id` + options. `compare()` diffs two reports. Use it as is.
- **Labels.** `Label(field_path, expectation VALUE|ABSENT, value, location: ExpectedLocation(page,
  bbox?) | None, labeler, labeled_at)`. Location is optional.
- **GoldenDocument** needs `document_id, blob_sha256, tier, origin: DocumentOrigin(kind, basis, …),
  schema_identity, schema_hash`. A tenant's documents are `Tier.RESTRICTED`, with origin kind
  `RESTRICTED`, and the basis states they were submitted by the tenant for processing. The builder
  must check what RESTRICTED implies for report redaction (tiers.py says the tier decides "whether
  the report carries values or hashes"). Tenants must see their own values in their own reports.
- **Corrections today** (`evaluation/corrections.py`, `runs/corrections.py`, migration
  `0007_corrections.sql`, routes `POST/GET /v1/runs/{run_id}/corrections` in `api/runs.py`) record
  only "wrong, should be X". There is no "this value is correct". A check of "correct" is a first-class
  statement here and must not be faked as `corrected_value == predicted_value`. `promote()` needs
  an existing `GoldenSet` with full `GoldenDocument` provenance and cannot create one.
  `PostgresCorrectionStore` already pins runs against retention through `pinned_runs`.
- **Runs.** `POST /v1/documents/{blob_id}/runs?schema=…` already re-submits a stored blob as a new
  extraction run. The worker runs the pipeline; `pipeline/plan.py` and `runner.py`'s `_Reuse` reuse
  any stage whose inputs are unchanged. Re-run runs are ordinary runs and appear in the runs list.
- **Tenant scoping.** Bearer routes are under `/v1` (one key, one tenant, `principal_of` in
  `api/deps.py`). Console routes are `/v1/console/*` (session cookie, cross-tenant through `_owner`
  in `api/console.py`, ADR-0023). Both families exist for runs today. This feature follows the same
  split.
- **Guards that will be touched, deliberately.**
  - `tests/contract/test_no_review_platform.py` asserts the correction routes are exactly POST and
    GET on `/v1/runs/{run_id}/corrections`, forbids PATCH everywhere, forbids write routes on paths
    containing `jobs` or `result`, and bans route words such as `queue`, `assign`, `approve`, and
    `task`.
  - New routes must respect every word ban. Any change to the exact-routes assertion must widen it to
    an explicit, reviewed list, never loosen it to a pattern.
  - The no-edit and no-delete rule for corrections stands. A changed mind is a new check, and the
    latest one wins.
- **Erasure and retention** (`runs/retention.py`, `runs/postgres.py`'s `purge_tenant_rows` and
  `purge_rows_for`). Every new table must be added to both purge paths. Sweep must not remove a run
  that is part of a live answer set.
- **Console layering** (`ui/src/console/model/README.md`). Every decision lives in `model/`, which is
  tested; `components/` only renders. `npm run lint:boundaries` enforces it. Follow the
  `console-ui` skill (`.claude/skills/console-ui`) for look and patterns.
- **Pre-1.0** (ADR-0011): breaking changes are allowed. No deployment holds data that must be kept.
- **An ADR** records the decision. It amends ADR-0019's reading where needed: the checking table is a
  correction surface, not a review platform. If the constitution's console paragraph (Principle XI,
  "Deferred technology") does not already permit corrections and evaluation in the console, it gets
  an amendment record in the file's own format, like 1.12.1.

## Decided by the owner

- What is measured: field-level extraction correctness (the existing metrics).
- Answers come only from people checking results by eye. No file upload.
- Answers, reports, and scores are separate per tenant.
- Both HTTP API and console.
- A re-run action that re-processes the checked documents with the current configuration, and
  before/after comparison across reports.
- The two screens in the mockup: checking on run detail, and a Quality screen with a per-field
  breakdown that drills into the documents a field got wrong.

## Calls made by the assistant (owner may override)

- **API and console share one backend.** Tenants use Bearer routes and operators use console routes,
  as for runs.
- **Answers are keyed by tenant + schema name** (for example `invoice`, not `invoice@1`) and matched
  to fields by field path. When a schema moves to a new version, fields that no longer exist are not
  scored, and new fields count as unlabelled until someone checks them. Each report records the exact
  schema identity and hash it ran with.
- **Answers are pinned against retention** for as long as they live, by the same mechanism
  `pinned_runs` uses for corrections.
- **Re-runs reuse the content-addressed store.** Only changed stages call a provider, and the warning
  before a re-run gives the document count. Estimating cost in money is out of scope.
- A re-run runs **asynchronously**. Its report appears when every document's run has finished. A
  document whose run fails counts as failed in the report (the `failed_stage` path the evaluation
  library already handles). It is not dropped.

### Settled after the builder check (assistant's calls)

1. **Schema versions.** The library refuses to score across a schema identity or hash change
   (`predictions.py` `_check`, ADR-0008). `compare()` refuses to diff two reports across one. The
   library is left alone; the code around it adapts.
   - Before each re-run, the answer set is **restamped** to the schema version the re-run uses:
     every `GoldenDocument` gets that version's identity and hash, and labels whose field paths no
     longer exist in that version are dropped from the stamped copy (not from storage).
   - The restamped set has its own `golden_set_id`, so reports across a schema change are visibly
     not comparable by id.
   - The Quality screen still shows them side by side in the history, with a "schema changed:
     invoice@1 → invoice@2" marker, and does not call `compare()` across that boundary.
   - Within one schema version, `compare()` is used as is.
2. **Location.**
   - A **correct** check stores the location the checked run read the value from (page and box from
     its grounding) as the label's `ExpectedLocation`. So `mislocation_rate` measures whether later
     runs still read it from the same place.
   - A **wrong** check stores no location. Drawing one is out of scope.
   - "Located on page" on the Quality screen is `grounding_rate`. Mislocation is shown where it is
     measurable.
3. **Answers do not expire.** They live until someone checks the field again (the latest check
   wins), or until the document or tenant is erased. A run that carries a live answer is pinned for
   as long as that answer lives. The correction-retention setting does not apply to answers, so
   report history cannot break on its own.
4. **Erasing a document that past reports included.**
   - Reports are kept, with their aggregate metrics and `report_id`, because those describe a
     computation that happened.
   - Every per-document outcome, value, and location for the erased document is removed from them.
   - The report is marked as including N documents erased since, and the drill-down shows them as
     "erased".
   - Erasing a tenant removes all of its answers and reports.
   - Reports should be stored so per-document detail can be removed without rewriting aggregates,
     for example per-document rows beside a report row.
5. **Values in reports.**
   - Tenant documents are `Tier.RESTRICTED`, so the library redacts values to hashes in the report.
     That stays.
   - The values a tenant sees (in the drill-down: expected and extracted) are joined at read time
     from the stored answers and from each re-run's own artifacts, through the same tenant-scoped
     reads that already serve run results.
   - The library is not changed.
6. **One answer per document and field.** Answers are keyed by tenant + schema name + document
   (`blob_id`) + field path. The newest check of that field on **any** run of that document is the
   answer. Checking a re-run's output therefore updates the answers, which is intended: it is the
   same person looking again. Only the run that carries each live answer has to be pinned.
7. **Smaller points.**
   - A console operator's check on a tenant's run counts as that tenant's answer, recorded with the
     operator as the one who checked.
   - "Current configuration" for a re-run means the newest registered version of the schema name,
     and the deployment's current parser and model routing.

## Left to the builder

- **The data model for a check.** Either extend `Correction` with a "confirmed correct" form or add
  a sibling model that produces a `Label`. Either way, one statement per field per check, with the
  latest winning, never edited or deleted in place. Who checked and when must be recorded.
- **How the answer set is represented and stored:** tables, or `GoldenSet` JSON per tenant and
  schema. How its identity is computed so that adding or changing an answer yields a new
  `golden_set_id`.
- **How a re-run is tracked** across its N runs, and where reports are stored. The report's
  `report_id` and provenance come from `docdoc.evaluation` unchanged.
- **Route names and shapes** under `/v1` and `/v1/console`, within the word bans above.
- **The extraction result view.** Reuse `ResultViewer`'s page and box rendering where possible, and
  get grounding locations from the run's stored artifacts.
- **Whether the Quality screen is a new console area** or lives inside an existing one.
- **Concurrency.** A re-run started while a previous one for the same tenant and schema is still
  running. Pick refuse or supersede and say which.
