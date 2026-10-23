# Tenant Quality Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenants mark extracted fields correct or wrong (HTTP API and console). Those checks become a per-tenant answer set. A re-run re-processes the checked documents with the current configuration and stores a scored report, which the console's Quality screen shows with its history.

**Architecture:**
- **A check is a `Correction`.** It gains a `confirmed` form and is stored in the existing `corrections` table, which gains `blob_id` and `schema_name` columns. The existing routes, purge paths and retention pinning are reused.
- **`docdoc.runs.answers`** turns the latest check per document and field into a `GoldenSet`, restamped to the schema version being run. It also turns a finished run's stored artifacts into a `DocumentPrediction`.
- **`docdoc.runs.evaluations`** stores an evaluation: the stamped answer set, the run ids it submitted, and the report.
- **The report is computed lazily** on the first read after every run has finished. `docdoc.evaluation.evaluate` is used unchanged.

**Tech Stack:** Python 3.11+, FastAPI, psycopg 3 (Postgres), pydantic; React + TypeScript console (`node --test` for `ui/src/console/model`).

**Spec:** `docs/superpowers/specs/2026-10-22-tenant-quality-evaluation-design.md`

## Global Constraints

- **No review platform** (Principle IX, ADR-0019 §2).
  - No route path or console identifier may use `assign`, `queue`, `worklist`, `inbox`, `task`, `workload`, `capacity`, `balance`, `review_state`, `under_review`, `in_review`, `approve`, `reject`, `disposition`, or `escalate`.
  - UI copy says "Correct" and "Wrong".
  - No list of "documents waiting to be checked". No checked or unchecked status on runs.
  - The run status set stays the closed five.
- **`docdoc.evaluation` is not modified**, apart from Task 1's `Correction` change: `evaluate`, `compare`, redaction, tiers and metrics stay as they are.
- **Tenant scoping** is in the query, never checked after a fetch. Another tenant's run, answer or evaluation answers 404, exactly like one that does not exist.
- **Answers never expire** (spec, settled call 3). A check row's `expires_at` is `datetime.max` with UTC, and the correction-retention-days setting is retired.
- **One answer per (tenant, schema name, `blob_id`, field path):** the newest check on any run of that document wins.
- **Tenant documents are `Tier.RESTRICTED`**, with `OriginKind.RESTRICTED` and basis `"submitted by tenant {tenant_id} for processing"`.
- **Erasure.**
  - Erasing a tenant deletes its checks and evaluations.
  - Erasing a document deletes its checks (already done through `purge_rows_for`). It also removes that document from every stored answer set and report, keeps each report's aggregate metrics and `report_id`, and increments the report's `erased_documents`.
- **One evaluation per (tenant, schema name) at a time.** A second start while one is unfinished answers 409 `evaluation_in_progress`.
- **Starting an evaluation with no answers** answers 409 `no_answers`.
- Run tests as `AGENTS.md` says: `uv run pytest -n0 <files>` while iterating, `make test` before each task's commit. Postgres-marked tests need `make test` (it sets `DOCDOC_TEST_DATABASE_URL`).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Cross-tenant reads.** Tenant B asking for tenant A's evaluation id, or listing answers, must get 404 or an empty list and never A's data. Pinned in Task 5: `test_another_tenants_evaluation_is_not_found`.
2. **A document whose re-run fails, or whose blob was erased after the evaluation started.** It must count as a failed document in the report (`failed_stage` set) and never crash the settle. Pinned in Task 4: `test_a_failed_run_counts_as_a_failed_document`.
3. **The same field checked twice, on two different runs of one document.** The newer check is the answer. Pinned in Task 2: `test_the_newest_check_on_any_run_wins`.
4. **A schema version that dropped a checked field.** That field's answer is left out of the stamped set, and the answers for the rest still score. Pinned in Task 3: `test_a_field_the_new_version_dropped_is_not_scored`.
5. **An evaluation read before all of its runs are finished.** It reports `"running"` with counts and no report, and computes nothing. Pinned in Task 4: `test_an_unfinished_evaluation_has_no_report_yet`.

---

### Task 1: A check can say "correct"

**Files:**
- Modify: `src/docdoc/evaluation/corrections.py` (`Correction`, its validator, `as_label`)
- Test: `tests/unit/test_confirmed_correction.py`

**Interfaces:**
- Produces: `Correction.confirmed: bool = False`.
  - When `confirmed` is true, `corrected_value` must be `None` and `corrected_absence` false.
  - When it is false, the existing rule holds: a `corrected_value`, or `corrected_absence=True`.
  - `reason` stays required when not confirmed. When confirmed it may be blank, and it defaults to `"confirmed correct"` if blank.
  - `as_label()` for a confirmed check returns `Label(expectation=VALUE, value=predicted_value, location=self.location, …)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_confirmed_check_becomes_a_label_of_the_predicted_value():
    c = Correction(report_id="sha256:" + "a" * 64, document_id="d", field_path="total",
                   predicted_value="228.00", confirmed=True, reason="", annotator="ops",
                   timestamp=NOW, location=ExpectedLocation(page=0))
    label = c.as_label()
    assert (label.expectation, label.value, label.location.page) == (Expectation.VALUE, "228.00", 0)

def test_confirmed_with_a_corrected_value_is_refused():
    with pytest.raises(ValidationError):
        Correction(..., confirmed=True, corrected_value="1")

def test_an_unconfirmed_check_still_needs_a_value_or_absence():
    with pytest.raises(ValidationError):
        Correction(..., confirmed=False, reason="x")
```

Fill `...` from the model's required fields (read the class). The last test pins existing behaviour and must still pass after the change.

- [ ] **Step 2: Run them and confirm the first two fail**

Run: `uv run pytest -n0 tests/unit/test_confirmed_correction.py`
Expected: FAIL. `confirmed` is an unexpected field (`extra=forbid`).

- [ ] **Step 3: Implement** the field, the validator branch and the `as_label` branch. Update the class docstring: a check is a correction that may also confirm.

- [ ] **Step 4: Run the tests, then the evaluation suite**

Run: `uv run pytest -n0 tests/unit/test_confirmed_correction.py tests/unit -k "correction or golden or evaluat"`
Expected: PASS. If a snapshot or identity pin moves only because the serialized model gained `"confirmed": false`, update the pin. Any other failure is a behaviour change: stop and report.

- [ ] **Step 5: `make test && make lint`, then commit** `feat(evaluation): a correction can confirm a value is correct`

---

### Task 2: Checks are stored as answers

**Files:**
- Create: `src/docdoc/runs/migrations/0011_answers.sql`
- Modify: `src/docdoc/runs/corrections.py` (`CorrectionStore` protocol, `NullCorrectionStore`, `PostgresCorrectionStore`)
- Modify: `src/docdoc/api/runs.py` (`record_correction`)
- Modify: `src/docdoc/runs/identity.py`, `src/docdoc/api/settings.py`, and docs that name the correction-retention-days setting (`README.md`, `docs/concepts/retention.md`, `docs/concepts/routing.md`). Retire the setting and update `tests/unit/test_configured_tunings.py` and `tests/unit/test_documented_api_references_resolve.py` accordingly. `tests/contract/test_milestone_9_deployment_unchanged.py` names it too: read why before changing it.
- Test: `tests/integration/test_answers_postgres.py` (mark `postgres`), plus additions to the existing corrections route tests in `tests/integration/test_review_routes_http.py`.

**Interfaces:**
- Migration `0011_answers.sql`:
  - `ALTER TABLE corrections ADD COLUMN IF NOT EXISTS blob_id text`
  - `ALTER TABLE corrections ADD COLUMN IF NOT EXISTS schema_name text`
  - `CREATE INDEX IF NOT EXISTS corrections_answers ON corrections (tenant_id, schema_name, blob_id, field_path, created_at)`
  - Existing rows keep NULLs and are not answers.
- `CorrectionStore.record(correction, *, tenant_id, run_id, blob_id: str, schema_name: str, at, expires_at) -> UUID`. This adds two keyword-only parameters.
- New dataclass in `runs/corrections.py`: `Answer(blob_id: str, run_id: UUID, correction: Correction)`.
- `CorrectionStore.answers(*, tenant_id: str, schema_name: str) -> tuple[Answer, ...]`. Returns the newest row per `(blob_id, field_path)` (`DISTINCT ON … ORDER BY … created_at DESC`), ordered by `blob_id, field_path`. `NullCorrectionStore.answers` returns `()`.
- `CorrectionStore.answer_summary(*, tenant_id: str) -> tuple[tuple[str, int, int], ...]`. Returns `(schema_name, documents, fields)` per schema name, from the same newest-row set.
- `ANSWERS_NEVER_EXPIRE: Final = datetime.max.replace(tzinfo=UTC)` in `runs/corrections.py`.
- Route `record_correction`:
  - It stores `blob_id=run.blob_id` and `schema_name=run.schema_identity.split("@")[0]`.
  - `expires_at=ANSWERS_NEVER_EXPIRE`.
  - For a `confirmed` body with no `location`, it fills `location` from the run's grounding: the outcome for `field_path`, using its first `Geometry` as `ExpectedLocation(page=geometry.page_index, bbox=geometry.bbox)`, or `None` when ungrounded. Load the grounding the way `api/documents.py`'s `_stored_result` does, from the tenant's store and the run's `processing_id`.
  - An OCR run (`schema_identity is None`) answers 409 `run_has_no_fields`.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_answers_postgres.py`, using the `queue` fixture pattern from `tests/integration/test_corrections_postgres.py`:
  - `test_the_newest_check_on_any_run_wins`: two runs of one blob, field `total` checked wrong (`"190.00"`) on run 1 and then confirmed on run 2 (predicted `"228.00"`). `answers()` returns one row, from run 2, confirmed.
  - `test_answers_are_per_tenant_and_per_schema_name`: an answer recorded for tenant A or for schema `receipt` is absent from `answers(tenant_id="b", schema_name="invoice")`.
  - `test_a_run_with_an_answer_is_pinned`: `pinned_runs([run_id], at=now + timedelta(days=36500))` contains it.
  - `test_the_summary_counts_documents_and_fields`: two blobs, three fields gives `("invoice", 2, 3)`.

  Route tests, added beside the existing correction route tests:
  - `test_a_confirmed_check_takes_the_location_it_was_read_from`: the stored payload has `location.page == 0`.
  - `test_an_ocr_run_cannot_be_checked`: 409.

- [ ] **Step 2: Run them and confirm they fail.** Command: `make test` (the postgres tests need the DSN). Expected: failures on the missing `answers` method and the missing columns.

- [ ] **Step 3: Implement** the migration, the store methods, the route changes, and the retirement of the correction-retention-days setting. Grep for every mention: `grep -rn CORRECTION_RETENTION --exclude-dir=.venv --exclude-dir=specs .`. CHANGELOG gets an entry in Task 9.

- [ ] **Step 4: `make test && make lint`.** Expected: green.

- [ ] **Step 5: Commit** `feat(runs): checks are stored as per-document answers and never expire`

---

### Task 3: An answer set and a prediction, from stored facts

**Files:**
- Create: `src/docdoc/runs/answers.py`
- Test: `tests/unit/test_answer_set.py`

**Interfaces:**
- Consumes: `Answer` (Task 2), `Correction.as_label` (Task 1), and `SchemaRegistry.describe(identity) -> SchemaDescription(identity, schema_hash, fields)` from `docdoc.extraction.registry`, where `fields` rows start with a path such as `line_items[].amount`.
- Produces:
  - `answer_set(answers: Sequence[Answer], *, tenant_id: str, schema: SchemaDescription) -> GoldenSet`
    - One `GoldenDocument` per `blob_id`, with `document_id=blob_id`, `blob_sha256=blob_id`, `tier=Tier.RESTRICTED`, `origin=DocumentOrigin(kind=OriginKind.RESTRICTED, basis=f"submitted by tenant {tenant_id} for processing")`, `schema_identity=schema.identity`, `schema_hash=schema.schema_hash` and `declared_label_count` = labels kept.
    - Labels come from `answer.correction.as_label()`. A label is kept only when its path, with every `[n]` replaced by `[]`, is one of the schema's field paths.
    - A document left with no labels is dropped.
    - `golden_set_id` is computed with `golden_set_id_for` (`docdoc.evaluation.identity`).
  - `prediction_for(store: Any, run: Run, document_id: str) -> DocumentPrediction`
    - A succeeded run's prediction is loaded from the tenant's artifact store by `run.processing_id`, with validation, then grounding and extraction through the validation provenance, exactly as `_stored_result` walks it.
    - `parser_id` and `parser_version` come from the parse artifact named by the extraction's provenance. Use `None` if that is not loadable.
    - Any non-succeeded run gives `DocumentPrediction(document_id=…, failed_stage=<the run's failed stage mapped to evaluation's Stage, or Stage.PARSE when unknown>, failure_reason=run.error_type or "run_" + run.status)`.

- [ ] **Step 1: Write failing tests**, with no database. Build `Answer`s by hand, use a `SchemaRegistry.from_paths([REPO / "schemas"])` description of `invoice@1`, and a minimal fake store for `prediction_for`:
  - `test_documents_are_restricted_and_stamped_with_the_schema_run`: `golden.documents[0].tier is Tier.RESTRICTED`, and its `schema_identity == "invoice@1"`.
  - `test_a_field_the_new_version_dropped_is_not_scored`: an answer for `"no_such_field"` is not among the labels, and a sibling `total` answer is.
  - `test_entry_indices_match_the_schema_template`: an answer for `line_items[1].amount` is kept.
  - `test_a_document_left_with_no_labels_is_dropped`.
  - `test_a_failed_run_becomes_a_failed_prediction`: a `Run` with status `failed` gives `failed_stage is not None` and `extraction is None`.
  - `test_evaluate_accepts_the_pair`: `evaluate(answer_set(...), PredictionSet(predictions={...}))` returns a report whose `metrics.micro.field_accuracy` is computable, with no `EvaluationError`. This is the integration point with the unmodified library.
- [ ] **Step 2: Run them.** `uv run pytest -n0 tests/unit/test_answer_set.py`. Expected: ImportError.
- [ ] **Step 3: Implement** `runs/answers.py`. It may import `docdoc.evaluation` and `docdoc.extraction.registry` types. Run `uv run lint-imports` to confirm no contract breaks. If one does, the description object can be duck-typed, as `schema_facts` does.
- [ ] **Step 4: Run tests and `make lint`.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(runs): answer sets and predictions from stored checks and runs`

---

### Task 4: Evaluations: stored, started, settled, erased

**Files:**
- Create: `src/docdoc/runs/migrations/0012_evaluations.sql`
- Create: `src/docdoc/runs/evaluations.py`
- Modify: `src/docdoc/runs/postgres.py`: add `"evaluations"` to `purge_tenant_rows`'s tuple.
- Modify: `src/docdoc/runs/retention.py`: the document-erase path calls `evaluations.forget_document`.
- Modify: `src/docdoc/api/app.py`: `Deployment.evaluations()`, built like `corrections()`.
- Test: `tests/integration/test_evaluations_postgres.py` (mark `postgres`)

**Interfaces:**
- Table `evaluations`:
  - `evaluation_id uuid PK`, `tenant_id text NOT NULL`, `schema_name text NOT NULL`, `schema_identity text NOT NULL`
  - `golden jsonb NOT NULL` (the stamped `GoldenSet`)
  - `run_ids uuid[] NOT NULL`, `document_ids text[] NOT NULL` (parallel to `run_ids`)
  - `report jsonb`, `erased_documents integer NOT NULL DEFAULT 0`
  - `created_at timestamptz NOT NULL`, `settled_at timestamptz`
  - Index `(tenant_id, schema_name, created_at DESC)`.
- `@dataclass(frozen=True) class Evaluation` with the same fields (`golden: GoldenSet`, `report: EvaluationReport | None`). Property `finished: bool = report is not None`.
- `class PostgresEvaluationStore(execute)`:
  - `create(evaluation: Evaluation) -> None`
  - `get(evaluation_id: UUID, *, tenant_id: str) -> Evaluation | None`
  - `list(*, tenant_id: str, schema_name: str | None, limit: int) -> tuple[Evaluation, ...]` (newest first)
  - `unfinished(*, tenant_id: str, schema_name: str) -> Evaluation | None`
  - `settle(evaluation_id: UUID, report: EvaluationReport, *, at: datetime) -> None`
  - `forget_document(*, tenant_id: str, blob_id: str) -> int` returns the number of evaluations touched. For each evaluation containing the blob, it removes that document from `golden` (documents and labels). If a report is present, it removes that document's entries from `report.outcomes` and `report.document_scores`. It increments `erased_documents`. Aggregates and `report_id` are left as they were.
- `start(deployment: Any, *, tenant_id: str, schema_name: str, at: datetime) -> Evaluation`
  - Raises `EvaluationUnavailable(code: Literal["no_answers", "evaluation_in_progress", "unknown_schema"])`.
  - Steps: resolve the newest registered identity for `schema_name`; `answer_set(...)`; refuse when it is empty or when `unfinished` exists; submit one extraction run per `GoldenDocument` with the same queue call `POST /v1/documents/{blob_id}/runs` uses (priority ordinary, no callback); `create`; return.
- `settled(deployment: Any, evaluation: Evaluation, *, at: datetime) -> Evaluation`
  - If `finished`, or if any of its runs is not terminal, return it unchanged.
  - Otherwise build a `PredictionSet` from `prediction_for` over every run (a run that is gone counts as failed), call `evaluate(evaluation.golden, predictions, facts=schema_facts([description]))`, `settle`, and return the updated evaluation.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_evaluations_postgres.py`. Use the pattern of the existing postgres integration tests, with an in-process deployment and the echo adapter as the run-execution tests do (see `tests/integration/test_async_matches_sync.py` or `test_restart_survival.py` for how a worker step is driven in-process):
  - `test_starting_submits_one_run_per_checked_document`: two checked blobs give two new extraction runs and an `Evaluation` with two `run_ids`.
  - `test_an_unfinished_evaluation_has_no_report_yet`: `settled(...)` with runs still queued returns `report is None` and writes nothing.
  - `test_a_finished_evaluation_gets_a_report_once`: drive the worker until both runs finish, then `settled` gives a report. A second `settled` returns the same `report_id`, with no second write (`settled_at` unchanged).
  - `test_a_failed_run_counts_as_a_failed_document`: one run fails (for example, its blob is deleted before the worker claims it). The report exists and that document's score shows the failure.
  - `test_a_second_start_while_unfinished_is_refused`: `EvaluationUnavailable("evaluation_in_progress")`.
  - `test_no_answers_is_refused`: `EvaluationUnavailable("no_answers")`.
  - `test_erasing_a_document_strips_it_and_keeps_the_aggregates`: after `forget_document`, the document is absent from `golden` and from `report.outcomes`. `report.metrics` and `report_id` are unchanged. `erased_documents == 1`.
  - `test_erasing_a_tenant_removes_its_evaluations`.
- [ ] **Step 2: Run** `make test`. Expected: failures on the missing module and table.
- [ ] **Step 3: Implement** the migration, the store, `start`, `settled`, the purge and erase wiring, and `Deployment.evaluations()`.
- [ ] **Step 4: `make test && make lint`.** Expected: green.
- [ ] **Step 5: Commit** `feat(runs): evaluations re-run checked documents and score them once`

---

### Task 5: HTTP API for tenants

**Files:**
- Create: `src/docdoc/api/evaluations.py`, with `routes() -> APIRouter`, included in the `/v1` Bearer router in `app.py` next to `run_routes`.
- Modify: `tests/contract/test_no_review_platform.py`: the exact correction-routes assertion becomes an explicit list. Add the console pair from Task 6 here too, so the change lands once. Every word ban is untouched.
- Test: `tests/contract/test_evaluation_routes.py` (postgres)

**Interfaces** (all tenant-scoped through `principal_of`):
- `GET /v1/answers` → `{"schemas": [{"schema_name", "documents", "fields"}]}` from `answer_summary`. This is what the "this will re-run N documents" warning reads.
- `POST /v1/evaluations?schema=<name>`:
  - On success, 202 with `{"evaluation_id", "schema_identity", "documents": n, "status": "running"}`.
  - On `EvaluationUnavailable`, 409 with `{"error": code, "detail": …}`.
  - With no database, 404, like the runs routes.
- `GET /v1/evaluations?schema=<name>&limit=20` → `{"evaluations": [summary…]}`. Each summary is `{"evaluation_id", "schema_identity", "created_at", "status": "running"|"finished", "documents", "erased_documents", "metrics": report.metrics.micro or null, "golden_set_id"}`.
- `GET /v1/evaluations/{evaluation_id}` → the summary plus `"per_field"` (from `report.metrics.per_field_path`) and `"documents"`.
  - `"documents"` has one entry per document: `{"document_id", "run_id", "outcomes": [{"field_path", "kind", "expected", "extracted"}]}`.
  - `expected` and `extracted` are joined at read time, from the stored answers and from that run's extraction artifact. They are never taken from the redacted report.
  - It calls `settled(...)` first.

- [ ] **Step 1: Write failing contract tests:**
  - `test_starting_and_reading_an_evaluation`: 202, then GET shows running; drive the worker; GET shows finished with `metrics.field_accuracy`.
  - `test_another_tenants_evaluation_is_not_found`: tenant B's GET of A's id gives 404, and B's list is empty.
  - `test_starting_with_no_answers_is_409`: `error == "no_answers"`.
  - `test_the_drill_down_shows_values_not_hashes`: a wrong field's `expected` is the corrected value as typed.
  - `test_the_answer_summary_counts`.
- [ ] **Step 2: Run** `make test`. Expected: 404s from the missing routes.
- [ ] **Step 3: Implement** the routes and widen the exact-routes assertion in `test_no_review_platform.py` to the explicit list: the existing pair, the console pair, and nothing else.
- [ ] **Step 4: `make test && make lint`.** Expected: green, including `test_no_review_platform.py`.
- [ ] **Step 5: Commit** `feat(api): tenants record checks, run evaluations, and read reports`

---

### Task 6: Console routes (operator, cross-tenant)

**Files:**
- Modify: `src/docdoc/api/console.py`
- Test: `tests/contract/test_console_evaluations.py` (postgres)

**Interfaces:** session-only, under `/v1/console`, following ADR-0023 §3. The owner comes from `_owner` for run paths, and from the path for tenant paths.
- `POST /v1/console/runs/{run_id}/corrections` and `GET /v1/console/runs/{run_id}/corrections`: the same behaviour as the Bearer pair, sharing one helper. The annotator is the signed-in operator's username, whatever the body says.
- `GET /v1/console/tenants/{tenant_id}/answers`
- `POST /v1/console/tenants/{tenant_id}/evaluations?schema=<name>`
- `GET /v1/console/tenants/{tenant_id}/evaluations?schema=<name>`
- `GET /v1/console/tenants/{tenant_id}/evaluations/{evaluation_id}`
- The responses are the same shapes as Task 5. Put the shared response builders in `api/evaluations.py` and import them here.

- [ ] **Step 1: Failing tests.**
  - `test_an_operator_check_is_recorded_as_the_operator`: the payload's `annotator` is the operator's username.
  - `test_console_evaluation_round_trip`.
  - `test_console_evaluation_routes_need_a_session`: Bearer only gives the console gate's refusal.
- [ ] **Step 2: Run, and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: `make test && make lint`.**
- [ ] **Step 5: Commit** `feat(console-api): operator checks and evaluations, cross-tenant`

---

### Task 7: Console model: extraction result, checking, quality

**Files:**
- Modify: `ui/src/console/model/client.ts`: add intents and `ALLOWED` patterns.
- Modify: `ui/src/console/model/runs.ts`: `showsResult` also covers a succeeded extraction run, and add `showsFields(run) -> boolean`.
- Create: `ui/src/console/model/fields.ts`
- Create: `ui/src/console/model/quality.ts`
- Modify: `ui/src/console/model/navigation.ts`: `Area` gains `"quality"`.
- Test: `ui/test/console/fields.test.ts`, `ui/test/console/quality.test.ts`, and additions to `client.test.ts` and `navigation.test.ts`.

**Interfaces:**
- New `Intent`s:
  - `{type:"run-checks"; runId}` → GET `/v1/console/runs/{id}/corrections`
  - `{type:"record-check"; runId}` → POST, same path, `hasBody: true`
  - `{type:"tenant-answers"; tenantId}` → GET `/v1/console/tenants/{t}/answers`
  - `{type:"start-evaluation"; tenantId; schema}` → POST `/v1/console/tenants/{t}/evaluations`, query `schema`
  - `{type:"list-evaluations"; tenantId; schema?}` → GET `/v1/console/tenants/{t}/evaluations`
  - `{type:"evaluation"; tenantId; evaluationId}` → GET `/v1/console/tenants/{t}/evaluations/{id}`
  - Each path gets its `ALLOWED` regex.
- `fields.ts`:
  - `toFieldRows(result: unknown, checks: unknown) -> FieldRow[]`, where `FieldRow = { path: string; value: string; found: boolean; location: {page: number; bbox: [number,number,number,number]} | null; check: "correct" | "wrong" | null; correctedValue: string | null }`. Rows are in the extraction's field order. The latest check per path wins.
  - `checkBody(row: FieldRow, verdict: "correct" | "wrong", input: {value?: string; absent?: boolean; reason?: string}, reportId: string) -> object` returns the `Correction` JSON body. `annotator` is left to the server.
  - `rowsToCheck(rows) -> {checked: number; total: number}` is used for the "Save check (3 of 5 fields)" label.
- `quality.ts`:
  - `toStatStrip(summary) -> {label, value, detail}[]` gives Checked documents, Fields correct, Located on page and Missing, formatted as `91.4%` with `(304/333)`.
  - `toFieldTable(detail) -> {path, correct, wrong, missing, documents: string[]}[]`.
  - `historyRows(list) -> {evaluationId, when, accuracy, schemaChanged: boolean, comparable: boolean}[]`. `schemaChanged` is true when an entry's `schema_identity` differs from the next older one's. `comparable` is true when `golden_set_id` is equal to it.
  - `rerunWarning(documents: number) -> string` returns ``"This re-runs ${n} document(s) with the current configuration. Unchanged steps are reused; changed ones may call paid providers."``.

- [ ] **Step 1: Write failing `node --test` cases**, one per function above, plus `client.test.ts` cases asserting each new intent's plan is `isAllowed`. Example:

```ts
test("the newest check per field wins", () => {
  const rows = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL, CONFIRMED_NEW_TOTAL] });
  assert.equal(rows.find((r) => r.path === "total")!.check, "correct");
});
test("a schema change is marked in the history", () => {
  const rows = historyRows([{ schema_identity: "invoice@2", golden_set_id: "b" }, { schema_identity: "invoice@1", golden_set_id: "a" }]);
  assert.deepEqual(rows.map((r) => r.schemaChanged), [true, false]);
});
```

- [ ] **Step 2: Run** `cd ui && npm test`. Expected: import failures.
- [ ] **Step 3: Implement.** No React imports in `model/`.
- [ ] **Step 4: Run** `cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries`. Expected: green.
- [ ] **Step 5: Commit** `feat(console): model for extraction fields, checks, and quality`

---

### Task 8: Console screens

**Files:**
- Create: `ui/src/console/components/FieldCheckTable.tsx`, `ui/src/console/components/QualityArea.tsx`
- Modify: `ui/src/console/components/RunArea.tsx` (a succeeded extraction run renders the page view and `FieldCheckTable` instead of only "Download result". Keep the download.)
- Modify: `ui/src/console/components/ResultViewer.tsx` only to let it take highlight boxes from outside (the hovered field's location), without moving decisions out of the model.
- Modify: `ui/src/console/components/Shell.tsx` (a "Quality" nav entry).

**Interfaces:** consumes Task 7's functions only. Follow the `console-ui` skill and the mockup at `.superpowers/brainstorm/55681-1792684081/content/review-flow.html`:
- **Run detail.** The page is on the left and the field table on the right. Hovering a row highlights its box. Each row has "Correct" and "Wrong" buttons. "Wrong" opens inline inputs for the value, "Not in the document" and a reason. A "Save check (N of M fields)" button posts one `record-check` per changed row.
- **Quality.** It has a tenant picker and a schema picker, populated from `tenant-answers`, and a stat strip from `toStatStrip`. "Re-run on checked documents" opens a confirm dialog with `rerunWarning(n)`. The field table links each field to the documents it got wrong, which open in run detail. The history list marks "schema changed" and "not comparable".

- [ ] **Step 1: Build the screens.** Components carry no tests by decision (`ui/src/console/model/README.md`). `lint:boundaries` is the gate.
- [ ] **Step 2: Run** `cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run build`. Expected: green. `check-console-boundary.mjs` must pass with no exemptions added.
- [ ] **Step 3: See it working.** Run `make dev` and submit an extraction with the echo adapter. Check two fields (one correct, one wrong). Open Quality, re-run, and see the report and history. Take a screenshot for the PR.
- [ ] **Step 4: Commit** `feat(console): check extracted fields and see quality per tenant`

---

### Task 9: Docs, ADR, and the PR

**Files:**
- Create: `docs/adr/0026-tenant-quality-evaluation.md`. It records the decision, and why checking and quality are a correction surface rather than a review platform (ADR-0019 §2 still holds word for word). It also records that answers never expire, and the erasure semantics for reports.
- Modify: `docs/adr/README.md` (index row).
- Modify: `docs/constitution.md`, if and only if its console paragraph in MVP Scope Constraints, "Deferred technology", does not already permit corrections and evaluation in the console. If an amendment is needed, add a record (1.12.1 → 1.13.0, MINOR: permitted list widened) in the file's own format.
- Create: `docs/concepts/quality.md`. It covers checking, answers, re-runs, reports, schema-version behaviour, cost and erasure. Add it to the docs index wherever `docs/concepts/*.md` are listed (`grep -rn "concepts/runs.md" README.md docs`).
- Modify: `README.md` (the API section gains the evaluation routes), `CHANGELOG.md` (Unreleased: the feature, plus the retired correction-retention-days setting).

- [ ] **Step 1: Write the docs.** Run `uv run pytest -n0 tests/unit/test_documented_api_references_resolve.py tests/unit/test_plan_tree_is_current.py tests/unit/test_convergence_gaps.py`. Expected: PASS. A new doc naming a `DOCDOC_*` setting goes in that test's recorded list.
- [ ] **Step 2: `make test && make lint && make ui`.** Expected: green.
- [ ] **Step 3: Commit** `docs: ADR-0026 and the quality concept doc`.
- [ ] **Step 4: Push and open the PR.** Squash-merge convention: the body describes all the work. Use the `mattpocock-skills:pr` template, with Task 8's screenshot as evidence and the before/after report from the round trip.
