# Console Monitoring (Plan 2 of 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The console opens on an Overview of what is waiting, running, failing and done. Runs can
be filtered by kind. A run's detail shows its timeline, a content-free failure reason, and a Cancel
action.

**Architecture:** Runs gain `started_at` and a content-free `error_reason` drawn from the closed
`REASONS` sets the ingest errors already carry. Two new console reads (`/overview`, plus a `kind`
filter on `/runs`) and one console write (cancel) sit on the existing `/v1/console` router. The UI
gets a left sidebar with Overview as its landing area, and it refreshes every 5 s while the tab is
visible.

**Tech Stack:** Python 3 / FastAPI / psycopg; React 19 + TypeScript + Vite 8 + Astryx; pytest; `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`. This plan covers decisions
1 and 7, plus the Overview, Runs-list and Run-detail goals other than the result viewer. Plan 3
covers the page-image viewer. Plan 4 covers Submit OCR, Parsers, Tenants and Callbacks.

## Execution shape: two lanes in parallel

| Lane | Tasks | Worktree | Database |
|---|---|---|---|
| Backend | 1 → 2 | its own (`isolation: worktree`) | `DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc` |
| Frontend | 3 → 4 | its own | none |

The lanes touch disjoint files: backend touches `src/`, `tests/` and `docs/`; frontend touches `ui/`
and `docs/concepts/console.md`. The frontend lane builds against the **API contract below**, not
against the backend code. Merge both lanes, then run Task 5 (integration check) and the
whole-branch review.

**Never start, stop or create a container.** If the database at 55432 is unreachable, report
BLOCKED.

## API contract (both lanes build to this, exactly)

`ConsoleRunSummary` (every row in `/v1/console/runs`, `/overview` lists) =
today's fields `run_id, status, created_at, finished_at, blob_id, schema_identity, kind,
processing_id, tenant_id`, **plus**:

- `started_at: string | null`, the most recent claim, ISO 8601
- `ocr_parser: string | null`
- `error_class: string | null`
- `error_reason: string | null`

`GET /v1/console/runs/{id}` (detail) gains the same four fields. The detail body is
`Run.dump_public()` plus `tenant_id`, so they arrive by adding them to `Run` and to
`RunStateResponse`.

`GET /v1/console/runs` gains a `kind` query parameter.
- Accepted values: `ocr` or `extraction`.
- Any other value gives
  `422 {"error":{"class":"InvalidRunKind","message":"kind must be one of extraction, ocr"}}`.
- The cursor stays bound to the tenant filter only. A kind change resets paging client-side, in the
  same way a status change does.

`GET /v1/console/overview` returns this:

```json
{
  "window_minutes": 60,
  "counts": [{"kind": "ocr", "status": "queued", "count": 10}, {"kind": "ocr", "status": "failed", "count": 4}],
  "recent_failures": [ConsoleRunSummary, ...],
  "running": [ConsoleRunSummary, ...]
}
```

- `counts` holds only non-zero rows.
- The statuses `queued` and `running` are counted regardless of age.
- The statuses `succeeded`, `failed` and `cancelled` are counted only if `updated_at` is at or after
  `now − 60 min`.
- `recent_failures` holds at most 10 rows with `status == "failed"`, newest `updated_at` first.
- `running` holds at most 10 rows with `status == "running"`, newest `updated_at` first.
- When there is no run database, the answer is 503, the same as `/runs`.

`DELETE /v1/console/runs/{id}` cancels a run.
- It needs `X-Docdoc-Console: 1`, as every console write does.
- The answers are those of `DELETE /v1/runs/{id}`, under the run's owner:
  - `200` with the detail body plus `tenant_id`;
  - `409 {"error":{"class":"RunNotCancellableError","message":…,"detail":{"status":…}}}`;
  - `404` from `_unknown_run`.
- It logs a `console.cancel` event with the actor `console:<username>`, using the
  `_log_erasure`-style JSON line on the `docdoc.api` logger.

`error_reason` values are the members of the existing `REASONS` sets:
- `ProviderError.REASONS`: `timeout`, `deadline`, `rate_limit`, `auth`, `transport`, `service`,
  `incomplete`;
- `ParserError.REASONS`: `invalid_order`, `capability_mismatch`, `empty_result`, `wrong_document`,
  `internal`;
- `UnsupportedDocumentError.REASONS`: `mime_type`, `size_limit`, `page_limit`, `encrypted`,
  `corrupt`;
- `null` when the error carries none.

## Global Constraints

- **Clock and randomness.** Nothing under `src/docdoc/runs/` other than `identity.py` may read a
  clock or a random source (`tests/unit/test_runs_clock_confinement.py`). `now` is passed in.
- **Migrations** are plain, idempotent `NNNN_name.sql` files, never edited once applied. The next
  number is `0010`.
- **No new Python or npm dependency.**
- **No content.** `error_reason` is only ever a member of an error class's own `REASONS`
  frozenset, never a message (ADR-0023, spec decision 7).
- **Forbidden words.** Route paths must avoid the words `tests/contract/test_no_review_platform.py`
  forbids, `queue` included. Console source under `ui/src/console` must not match
  `check-console-boundary.mjs`: `queue` (`queued` is fine), `capacity`, `balance`, `assign`,
  `workload`, `approve`, `reject`, `escalate`, `disposition`.
- **Visible console text is English.**
- **Guard tests that every backend route or column change must keep green.** Update the lists they
  keep; never weaken an assertion.
  - `tests/contract/test_console_adds_one_read.py`: `CONSOLE_ADDED`
  - `tests/contract/test_http_ui_endpoints.py`: `ROUTES`
  - `tests/contract/test_jobs_routes_unchanged.py`: its list of paths containing `runs`
  - `tests/unit/test_data_model_matches_the_code.py`
  - `tests/unit/test_documented_api_references_resolve.py`
  - `tests/contract/test_protected_layers_untouched.py`: `kernel/`, `extraction/`, `grounding/` and
    `validation/` must stay untouched
  - `tests/integration/test_run_record_leaks_nothing.py`
  - `tests/contract/test_no_review_platform.py`
- **Frontend gates,** run from `ui/`:
  `npm run typecheck && npm test && npm run lint:boundaries && npm run build`.
- **Backend gates:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/docdoc`,
  `uv run lint-imports`. Run the full suite once, at the end of each task:
  `uv run pytest tests/unit tests/property tests/contract tests/integration -m "not perf" -q -o addopts=""`,
  with `DOCDOC_TEST_DATABASE_URL` set.

## Review Focus

1. **A run cancelled while still queued has no `started_at`.** Its timeline must show wait "—" and
   run "—", never a negative or `NaN`.
   Test: Task 3, `test_a_run_never_started_has_no_wait_or_run_time`.
2. **A redelivered run, claimed twice,** shows the latest claim as `started_at`. Its wait therefore
   includes the earlier attempt.
   Test: Task 1, `test_a_second_claim_moves_started_at`, against both stores.
3. **A failure whose error has no `reason`, or one outside its class's `REASONS`,** stores
   `error_reason = null`. The UI then shows the error class alone.
   Tests: Task 1, `test_reason_of_ignores_unlisted_reasons`; Task 3,
   `test_an_unknown_reason_falls_back_to_the_class`.
4. **A deployment with no runs** gets an Overview with all-zero cards and two empty lists, not an
   error or a blank page.
   Tests: Task 2, `test_overview_of_an_empty_deployment`; Task 3, `test_cards_of_empty_counts_are_zero`.
5. **A hidden tab does not poll, and a tab that becomes visible refreshes at once** instead of
   waiting up to 5 s.
   Test: Task 3, `test_polling_pauses_while_hidden`.

---

### Task 1 (backend lane): `started_at`, `error_reason`, and where they are written

**Files:**
- Create: `src/docdoc/runs/migrations/0010_run_timeline.sql`. It adds
  `started_at timestamptz` and `error_reason text`, plus the index
  `runs_terminal_recent ON runs (updated_at) WHERE status IN ('succeeded','failed','cancelled')`.
  Its header comment cites this plan and spec decision 7.
- Modify: `src/docdoc/pipeline/result.py`. Add `failure_reason` to `StageOutcome` and the function
  `reason_of`.
- Modify: `src/docdoc/pipeline/runner.py` (~line 490). The failed `StageOutcome` sets
  `failure_reason=reason_of(error)`.
- Modify: `src/docdoc/runs/model.py`:
  - `Run` gains `started_at: datetime | None = None` and `error_reason: str | None = None`.
  - `RunOutcome` gains `error_reason: str | None = None`.
  - `RunOutcome.of` copies the failing outcome's `failure_reason`.
  - `StageOutcomeRecord` is unchanged.
- Modify: `src/docdoc/runs/worker.py`, `_execute_ocr` (~line 291). The `IngestError` branch sets
  `error_reason=reason_of(exc)`.
- Modify: `src/docdoc/runs/postgres.py`:
  - `_COLUMNS` and `_row_to_run` include both columns.
  - Every claim statement that sets `status = 'running'` also sets
    `started_at = %(now)s`. There are two statements, near lines 565 and 698.
  - `finish` writes `error_reason`.
- Modify: `tests/fixtures/run_queue.py`, with the same behaviour in `claim` and `finish`.
- Modify: `src/docdoc/api/models.py`. `RunStateResponse` gains
  `started_at: str | None = None` and `error_reason: str | None = None`.
- Modify: `docs/concepts/runs.md`. One short section: what `started_at` means on redelivery, and
  that `error_reason` is a closed, content-free vocabulary.
- Test: `tests/unit/test_failure_reason.py` (new), `tests/integration/test_run_timeline_postgres.py` (new), and the existing pipeline and worker tests.

**Interfaces:**
- Produces: `reason_of(error: BaseException) -> str | None` in `docdoc.pipeline.result`. It returns
  `error.reason` only when `type(error)` has a `REASONS` frozenset containing it, and `None`
  otherwise.
- Produces: `Run.started_at`, `Run.error_reason`, and the same two keys in `dump_public()` and in
  `RunStateResponse`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reason_of_reads_a_listed_reason():
    assert reason_of(ProviderError("x", reason="timeout", parser_id="p")) == "timeout"
    assert reason_of(UnsupportedDocumentError("x", reason="encrypted", ...)) == "encrypted"  # fill required kwargs

def test_reason_of_ignores_unlisted_reasons():
    error = ProviderError("x", reason="timeout", parser_id="p"); error.reason = "the invoice said 42"
    assert reason_of(error) is None
    assert reason_of(ValueError("boom")) is None

def test_a_failed_parse_records_its_reason(...):          # pipeline: StageOutcome.failure_reason == "timeout"
def test_an_ocr_run_failure_stores_the_reason(...):        # worker _execute_ocr with a parser raising ProviderError(reason="service") → run.error_reason == "service", error_class == "ProviderError"
def test_a_second_claim_moves_started_at(queue):           # both InMemoryRunQueue and PostgresRunQueue: claim at T1, release/lease-expire, claim at T2 → started_at == T2
def test_finish_writes_error_reason(queue):                # both stores
def test_the_public_run_body_carries_the_two_fields(...):  # GET /v1/runs/{id} with a Bearer key → "started_at" and "error_reason" keys present
```

  The Postgres variants go in `tests/integration/test_run_timeline_postgres.py`, following the
  `queue` fixture in `tests/integration/test_delivery_postgres.py`. Its `TRUNCATE` list must cover
  every table the test writes.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/unit/test_failure_reason.py tests/integration/test_run_timeline_postgres.py -q -o addopts=""`
  Expected: FAIL with an ImportError or a missing attribute.
- [ ] **Step 3: Implement**, as listed under Files.
- [ ] **Step 4: Run the focused tests, then the guards**
  (`test_data_model_matches_the_code`, `test_run_record_leaks_nothing`,
  `test_protected_layers_untouched`, `test_runs_clock_confinement`), then the full suite once.
  Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(runs): started_at and a content-free error_reason`.

---

### Task 2 (backend lane): console overview, kind filter, cancel

**Files:**
- Modify: `src/docdoc/runs/queue.py` (Protocol), `src/docdoc/runs/postgres.py` and
  `tests/fixtures/run_queue.py`. Add `page_all_runs(..., kind: str | None = None)`, plus the two new
  methods below.
- Modify: `src/docdoc/api/models.py`. `ConsoleRunSummary` gains the four fields from the contract.
- Modify: `src/docdoc/api/console.py`. Add the `kind` parameter on `GET /runs`, plus the
  `GET /overview` and `DELETE /runs/{run_id}` routes. Fill the summary's new fields wherever
  summaries are built.
- Modify: the guard lists named in Global Constraints (`CONSOLE_ADDED`, `ROUTES`, the
  jobs-routes list).
- Test: `tests/contract/test_console_overview.py` (new), `tests/contract/test_console_runs.py` (extend), `tests/integration/test_run_timeline_postgres.py` (extend)

**Interfaces:**
- Consumes: Task 1's `Run.started_at`, `Run.error_reason` and migration 0010.
- Produces:
  - `status_counts(*, since: datetime) -> tuple[tuple[str, str, int], ...]` returns
    `(kind, status, count)`, using the rule in the API contract. In Postgres it is one
    `GROUP BY kind, status` query.
  - `latest(*, status: str, limit: int) -> tuple[Run, ...]` returns runs ordered by
    `updated_at DESC, run_id DESC`.
  - The routes exactly as in the API contract.
  - The overview handler is a plain `def`, like the other console run handlers. It reads `now` from
    `docdoc.runs.identity.now()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_overview_of_an_empty_deployment(signed_in):
    body = get("/v1/console/overview").json()
    assert body == {"window_minutes": 60, "counts": [], "recent_failures": [], "running": []}

def test_overview_counts_active_always_and_terminal_within_the_hour(signed_in, queue):
    # seed: 2 queued ocr (one 3h old), 1 running extraction, 1 failed ocr 10 min ago, 1 failed ocr 2h ago, 1 succeeded ocr 5 min ago
    counts = {(c["kind"], c["status"]): c["count"] for c in body["counts"]}
    assert counts == {("ocr","queued"): 2, ("extraction","running"): 1, ("ocr","failed"): 1, ("ocr","succeeded"): 1}

def test_overview_lists_are_newest_first_and_capped_at_ten(...)
def test_overview_rows_carry_the_new_fields(...)   # tenant_id, started_at, error_class, error_reason, ocr_parser present
def test_kind_filter(signed_in, queue)             # kind=ocr only ocr rows; kind=bogus → 422 InvalidRunKind
def test_cancel_a_queued_run(signed_in, queue)     # 200, status cancelled, tenant_id present; caplog has console.cancel with actor console:alice
def test_cancel_a_finished_run_is_409(...)
def test_cancel_needs_the_console_header(...)      # 403 without X-Docdoc-Console
def test_overview_and_cancel_refuse_a_bearer_key(...)
```

  For the Postgres queue, add `test_status_counts_and_latest_postgres` to
  `test_run_timeline_postgres.py`.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/contract/test_console_overview.py tests/contract/test_console_runs.py -q -o addopts=""`
  Expected: FAIL with 404 or 422 on `kind`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the focused tests, the guard lists, and the full suite once.** Expected: PASS.
- [ ] **Step 5: Commit** with the message `feat(api): console overview, kind filter, cancel`.

---

### Task 3 (frontend lane): model for overview, timeline, reasons, polling, cancel

**Files:**
- Create: `ui/src/console/model/overview.ts`, `ui/test/console/overview.test.ts`
- Create: `ui/src/console/model/polling.ts`, `ui/test/console/polling.test.ts`
- Modify: `ui/src/console/model/runs.ts`:
  - `RunSummary` gains the four contract fields;
  - `ListingState` gains `kind: RunKind | null`, with `byKind(state, kind)` resetting the cursor
    stack as `filtered` does;
  - add `timeline` and `reasonLabel`.
- Modify: `ui/src/console/model/client.ts`. Add the intents `overview` and `cancel-run`, plus
  `kind` on `list-runs`; update `ALLOWED` and `allIntents`.
- Modify: `ui/src/console/model/format.ts`. Add `duration`.
- Test: `ui/test/console/{runs,client,format}.test.ts`

**Interfaces:**
- Produces, in `overview.ts`:
  - `type RunKind = "ocr" | "extraction"`, exported from `runs.ts` and re-used here;
  - `interface OverviewCards { queued: KindSplit; running: KindSplit; failed: KindSplit; done: KindSplit }`,
    where `KindSplit = { total: number; ocr: number; extraction: number }`. `done` counts
    `succeeded` only; `cancelled` is not shown as a card.
  - `interface Overview { windowMinutes: number; cards: OverviewCards; recentFailures: RunSummary[]; running: RunSummary[] }`;
  - `toOverview(body: unknown): Overview`. It throws on a body without a `counts` array, the same
    way `toPage` does.
- Produces, in `polling.ts`:
  - `POLL_MS = 5000`;
  - `nextPoll(hidden: boolean): number | null`. It returns `null` when hidden and `POLL_MS`
    otherwise.
  - `onVisible(wasHidden: boolean, hidden: boolean): boolean`. It is true exactly when the tab has
    just become visible, which means refresh now.
- Produces, in `runs.ts`:
  - `timeline(run: { created_at: string; started_at: string | null; finished_at: string | null }, now: number): { waitMs: number | null; runMs: number | null }`.
    - `waitMs = started − created`. It is `null` when `started_at` is null.
    - `runMs = (finished ?? now) − started`. It is `null` when `started_at` is null.
    - Any negative value is clamped to 0.
  - `reasonLabel(reason: string | null, errorClass: string | null): string | null`. Its labels are
    fixed copy:

    | Reason | Label |
    |---|---|
    | `timeout` | "The OCR host timed out" |
    | `deadline` | "The run ran out of time" |
    | `rate_limit` | "The host rate-limited the request" |
    | `auth` | "The host refused docdoc's credentials" |
    | `transport` | "docdoc could not reach the host" |
    | `service` | "The host reported an error" |
    | `incomplete` | "The host returned fewer pages than it was sent" |
    | `mime_type` | "This file type is not supported" |
    | `size_limit` | "The file is too large" |
    | `page_limit` | "The file has too many pages" |
    | `encrypted` | "The file is encrypted" |
    | `corrupt` | "The file is corrupt" |
    | `empty_result` | "The parser found no text" |
    | `capability_mismatch` | "The parser cannot handle this input" |
    | `invalid_order`, `wrong_document`, `internal` | "The parser produced an invalid document" |

    - An unknown non-null reason returns the raw reason string.
    - A `null` reason with a non-null `errorClass` returns `errorClass`.
    - Both null returns `null`.
- Produces, in `format.ts`: `duration(ms: number | null): string`, which gives `"—"` for `null`,
  `"850 ms"` under 1 s, `"14 s"` under 60 s, `"3 min 5 s"` under 1 h, and `"2 h 4 min"` above.
- Produces, in `client.ts`:
  - `{ type: "overview" }` → `GET /v1/console/overview`;
  - `{ type: "cancel-run"; runId: string }` → `DELETE /v1/console/runs/{id}`;
  - `list-runs` gains `kind?: RunKind`, which becomes the `kind` query parameter only when set.

- [ ] **Step 1: Write the failing tests.** Key assertions:

```ts
test("a run never started has no wait or run time", () => {
  assert.deepEqual(timeline({ created_at: T0, started_at: null, finished_at: T1 }, NOW), { waitMs: null, runMs: null });
});
test("a running run's run time grows with now", () => {
  assert.equal(timeline({ created_at: T0, started_at: T0plus6s, finished_at: null }, T0plus20s).runMs, 14000);
});
test("an unknown reason falls back to the class", () => {
  assert.equal(reasonLabel(null, "ProviderError"), "ProviderError");
  assert.equal(reasonLabel("timeout", "ProviderError"), "The OCR host timed out");
  assert.equal(reasonLabel("brand_new", "X"), "brand_new");
});
test("cards of empty counts are zero", () => {
  const o = toOverview({ window_minutes: 60, counts: [], recent_failures: [], running: [] });
  assert.deepEqual(o.cards.queued, { total: 0, ocr: 0, extraction: 0 });
});
test("cards split by kind", () => { /* counts [{ocr,queued,10},{extraction,queued,2},{ocr,succeeded,218}] → queued {12,10,2}, done.total 218 */ });
test("polling pauses while hidden", () => {
  assert.equal(nextPoll(true), null);
  assert.equal(nextPoll(false), 5000);
  assert.equal(onVisible(true, false), true);
  assert.equal(onVisible(false, false), false);
});
test("cancel-run is a DELETE under the console API", () => {
  assert.deepEqual(requestFor({ type: "cancel-run", runId: "r 1" }), { method: "DELETE", path: "/v1/console/runs/r%201", query: {}, hasBody: false });
});
test("duration formats", () => {
  assert.equal(duration(null), "—"); assert.equal(duration(850), "850 ms");
  assert.equal(duration(14000), "14 s"); assert.equal(duration(185000), "3 min 5 s"); assert.equal(duration(7440000), "2 h 4 min");
});
```

- [ ] **Step 2: Run them and confirm they fail.** Run: `cd ui && npm test`.
  Expected: FAIL on the missing exports.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend gates.** Typecheck may fail only in components; Task 4 fixes that.
  List any such errors in the report.
- [ ] **Step 5: Commit** with the message `feat(ui): model for overview, timeline, reasons, polling`.

---

### Task 4 (frontend lane): sidebar, Overview, Runs filters and columns, Run detail

**Files:**
- Create: `ui/src/console/components/OverviewArea.tsx`
- Modify: `ui/src/console/components/Shell.tsx`, `RunArea.tsx`
- Modify: `docs/concepts/console.md`. Describe the Overview and the 5 s refresh.

**Interfaces:**
- Consumes: Task 3's exports only. Components decide nothing (`check-model-boundary.mjs`).
- **Shell**
  - Layout: a left sidebar of about 200 px.
  - Nav order: **Overview**, **Runs**, then a muted "Admin" label, then **Credentials** and
    **Erasure**. The username and **Sign out** sit at the bottom.
  - The default area is `"overview"`. The content area is on the right.
  - The nav is a list of buttons; the current one gets `aria-current="page"`.
  - Shell holds `openRunId: string | null`. `OverviewArea`'s `onOpenRun(id)` switches to Runs with
    that run open.
- **OverviewArea**
  - Four cards: Queued, Running, Failed (last hour), Done (last hour). Each shows a total and an
    "OCR n · Extraction n" line. Failed uses the danger tone when greater than 0.
  - Then two tables, "Recent failures" and "Running now". Recent failures shows kind, tenant,
    `reasonLabel`, and the finished time. Running now shows kind, tenant, parser, and run time from
    `timeline`.
  - Clicking a run id calls `onOpenRun`.
  - It polls with `nextPoll`/`onVisible` through a `visibilitychange` listener, and shows the time
    of the last refresh.
  - On a request failure, it keeps the last good data and shows the failure banner.
- **RunArea**
  - Filters: a Kind `<select>` (All, OCR, Extraction) next to the Status `<select>` and the Tenant
    text filter.
  - Columns: State, Kind, Tenant, Submitted, Duration, Run.
    - Duration is `duration(timeline(...).runMs)`.
    - Kind shows the parser in parentheses for OCR runs.
  - Auto-refresh uses the same polling helpers as Overview, but only while on the first page.
    Paging stops it, so rows don't move under the reader; returning to page 1 resumes it.
- **RunDetail**
  - A timeline line: Submitted → Started (wait `duration`) → Finished (run `duration`).
  - Metadata: tenant, parser and language for OCR, worker, attempts, error class, and
    `reasonLabel` as the failure reason.
  - A **Cancel run** button for `queued`/`running` runs. It asks inline first ("Cancel this run?",
    then Confirm or Keep running).
  - The response replaces the shown run. A 409 shows its message.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the frontend gates.** Expected: all pass.
- [ ] **Step 3: Commit** with the message `feat(ui): sidebar, overview, run filters, timeline, cancel`.

---

### Task 5 (after merging both lanes): integration check

**Files:** none, unless it finds a mismatch. If it does, fix it in whichever lane's file disagrees
with the API contract, and add a test that pins the contract.

- [ ] **Step 1: Merge the lanes into the plan branch.** Then run the backend gates, the full suite,
  and the frontend gates.
- [ ] **Step 2: Check end to end over HTTP against a running server** on port 8765.
  Setup:
  - `DOCDOC_RUN_DATABASE_URL` points at the 55432 database.
  - Run `uv run docdoc migrate`, then `uv run docdoc console-user add`.
  - Seed runs directly through `PostgresRunQueue`: two tenants, one queued OCR run, one failed OCR
    run with `error_reason='timeout'`, and one succeeded extraction run.
  Check:
  - `/v1/console/overview` counts and lists match the seed;
  - `/runs?kind=ocr` returns only OCR runs;
  - cancelling the queued run gives 200, and a second cancel gives 200 (already cancelled);
  - the built `/console/` serves.
  Stop the server afterwards.
- [ ] **Step 3: Commit any fixes** with the message `fix(console): <what>`.
