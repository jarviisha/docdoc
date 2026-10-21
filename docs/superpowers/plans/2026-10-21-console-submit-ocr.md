# Console Submit OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator can submit a document for OCR from the console and land on the new run's
detail page to watch it. They choose the tenant, the parser and the language, and upload the file.

**Architecture:**
- The body of `POST /v1/ocr` becomes a module-level function that takes the tenant explicitly. The
  tenant route and a new session-gated console route both call it, so caps, validation, idempotency
  and storage stay one code path.
- Two small console reads feed the form: the tenant list, and the parser list (the same body as
  `/v1/parsers`).
- The UI gains a **Submit OCR** area in the sidebar.

**Tech Stack:** Python 3 / FastAPI; React 19 + TypeScript + Vite 8 + Astryx; pytest; `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`, goal "Submit OCR". This
plan runs before the page-image viewer, a reordering the owner made on 2026-10-21.

## Execution shape

| Lane | Tasks | Worktree |
|---|---|---|
| Backend | 1 | its own |
| Frontend | 2 → 3 | its own |

After both lanes, Task 4 integrates and checks the result end to end, including screenshots. The
lanes touch disjoint files: backend touches `src/` and `tests/`; frontend touches `ui/` and
`docs/concepts/console.md`. Database:
`DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc`. **Never start, stop
or create a container.**

## API contract (both lanes build to this)

**`GET /v1/console/tenants`** returns `200 {"tenants": ["acme", "mmerp"]}`. The list is sorted,
unique, and the union of four sources:
- `root_tenant()`;
- the tenants in the key-file ring;
- the distinct `tenant_id`s in the `credentials` table;
- the distinct `tenant_id`s in `runs`.

If a source isn't configured, it contributes nothing.

**`GET /v1/console/parsers`** returns exactly the body of `GET /v1/parsers`:
`{"parsers": [{id, available, unavailable_reason?, capabilities, media_types, languages?, healthy?}]}`.
Optional keys are absent, not null.

**`POST /v1/console/tenants/{tenant_id}/ocr?parser=&language=`**
- The body is the raw file bytes. It needs `X-Docdoc-Console: 1`.
- A `tenant_id` that doesn't match `TENANT_PATTERN` gives `422 {"error": "invalid_tenant_id"}`.
- Otherwise the answers are exactly those of `POST /v1/ocr` for that tenant:

  | Status | Body |
  |---|---|
  | 202 | the console detail body, which is `_detail(run, tenant)` in `api/console.py`: run fields plus `tenant_id` plus `finished_at` |
  | 200 | the same body, on an idempotent replay |
  | 413 | too large |
  | 422 | unknown parser, unsupported language, or unsupported document, in the existing error-handler shape |
  | 429 | `{"error":"limit_exceeded","limit":"ocr_queue","observed":n,"allowed":m,"retry_after_seconds":30}`, with `Retry-After` |
  | 503 | `{"error":"system_overloaded",…,"message":"the system is overloaded; please send again later"}`, or the no-run-database / no-store 503 |

**UI behaviour:**
- On 202 or 200 the UI opens Runs with that `run_id` selected.
- Error bodies with a string `detail` or `message` show that sentence.
- A 429 shows "This tenant already has {observed} OCR runs waiting (limit {allowed}). Try again in
  {retry_after_seconds} s."

## Global Constraints

- **No new Python or npm dependency.** Nothing under `src/docdoc/runs/` other than `identity.py`
  reads a clock or a random source.
- **One OCR path.** The tenant route and the console route call the same function. Caps,
  validation, blob storage and idempotency must never be copied.
- **Forbidden words** under `ui/src/console` (`check-console-boundary.mjs`): `queue` (`queued` is
  fine), `capacity`, `balance`, `assign`, `workload`, `approve`, `reject`, `escalate`,
  `disposition`. Route paths must avoid the words `test_no_review_platform.py` forbids.
- **Components decide nothing** (`check-model-boundary.mjs`). Use Astryx components: `TextInput`,
  `Selector`, `FileInput`, `Button`, `Banner`, `Card`. No raw `<input>`/`<select>`, because
  `reset.css` strips them.
- **Guard lists to keep green:**
  - `tests/contract/test_console_adds_one_read.py` (`CONSOLE_ADDED`)
  - `tests/contract/test_http_ui_endpoints.py` (`ROUTES`)
  - `tests/contract/test_jobs_routes_unchanged.py`
  - `tests/contract/test_no_review_platform.py`
  - `tests/contract/test_ocr_queue.py` and `tests/contract/test_ocr_routes.py`, which must pass
    unchanged and prove the refactor kept `/v1/ocr` identical
  - `tests/unit/test_documented_api_references_resolve.py`: add this plan to `CONFIG_DOCUMENTS`
    if it names `DOCDOC_*` settings
- **Backend gates:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/docdoc`,
  `uv run lint-imports`, and the full suite once at the end:
  `uv run pytest tests/unit tests/property tests/contract tests/integration -m "not perf" -q -o addopts=""`
  with the env var set.
- **Frontend gates,** from `ui/`:
  `npm run typecheck && npm test && npm run lint:boundaries && npm run build`.
- **Visual check is mandatory for UI tasks.** Use Playwright screenshots against a live server, then
  read the PNGs. The pattern is in `/tmp/console-shot2.py`. Run it with
  `cd /tmp && uvx --with playwright python <script>`.

## Review Focus

1. **The parser is "Automatic".** The language selector then offers only "Parser default" and sends
   no `language`. A language is only selectable after choosing a parser that declares it.
   Test: Task 2, `test_language_choices_follow_the_parser`.
2. **An unavailable parser** appears but cannot be chosen. A parser that doesn't respond
   (`healthy: false`) can be chosen but is labelled "(not responding)".
   Test: Task 2, `test_parser_choices_mark_unavailable_and_unhealthy`.
3. **A double-click on Submit** sends one request: the button is disabled while the request is in
   flight. Test: Task 2, `test_cannot_submit_while_sending`. Task 3 covers it visually.
4. **A tenant over its OCR cap** sees the readable 429 sentence, not a raw code.
   Test: Task 2, `test_limit_exceeded_reads_as_a_sentence`.
5. **The refactor changes nothing about `POST /v1/ocr` for API-key callers.** Its existing contract
   tests pass unchanged. In addition, `test_console_and_tenant_ocr_share_the_caps` shows a console
   submission counts against the same tenant cap.

---

### Task 1 (backend lane): one OCR submit path, plus console tenants, parsers and OCR routes

**Files:**
- Modify: `src/docdoc/api/ocr.py`.
  - Extract the body of `ocr()` after the run-database and store checks into a module-level
    function. Its signature is in Interfaces below.
  - `_stores_of(request, principal)` becomes `deployment.stores_for(tenant_id)`.
  - Extract the body of the `parsers` handler into `parser_listing(deployment) -> ParserListing`.
  - Both existing routes call these.
- Modify: `src/docdoc/api/console.py`. Add three routes.
  - The OCR route reads the body with `_read_capped` and calls `submit_ocr`.
  - On 200 or 202, the answer body is replaced with `_detail(run, tenant)`.
- Modify: `src/docdoc/runs/queue.py`, `postgres.py` and `tests/fixtures/run_queue.py`. Add
  `tenant_ids() -> tuple[str, ...]`, which runs `SELECT DISTINCT tenant_id FROM runs`.
- Modify: `src/docdoc/api/auth.py`. `KeyRing.tenant_ids() -> frozenset[str]`.
- Modify: `src/docdoc/runs/keys.py`. `tenant_ids()` on `PostgresKeyStore`, which runs
  `SELECT DISTINCT tenant_id FROM credentials`, and on `CachedKeyStore` and `ChainedKeyStore`,
  which delegate and union.
- Modify: the guard lists.
- Test: `tests/contract/test_console_submit_ocr.py` (new); the existing `test_ocr_queue.py` and
  `test_ocr_routes.py` (unchanged, must pass).

**Interfaces:**
- Produces, in `docdoc.api.ocr`:
  - `async def submit_ocr(request: Request, *, tenant_id: str, data: bytes, parser: str | None, language: str | None, callback_id: str | None) -> JSONResponse`.
    It runs the threadpool work exactly as today, and it reads `x-request-id` and
    `idempotency-key` from `request.headers` as today.
  - `parser_listing(deployment: Any) -> ParserListing`.
- Produces the console routes from the API contract. The tenants and parsers handlers are plain
  `def`s; the parsers probe is blocking.

- [ ] **Step 1: Write the failing tests**

```python
def test_console_submits_ocr_for_the_named_tenant(signed_in, ...):   # POST /v1/console/tenants/acme/ocr with PNG bytes + header → 202; body kind=="ocr", tenant_id=="acme", status=="queued", finished_at is None; queue has the run under acme
def test_console_ocr_passes_parser_and_language(...)                  # ?parser=<configured>&language=<declared> → run.ocr_parser/ocr_language set
def test_console_ocr_refuses_an_unknown_parser(...)                   # 422, no run, no blob (same as /v1/ocr)
def test_console_ocr_needs_the_console_header(...)                    # 403, no run
def test_console_ocr_refuses_a_bad_tenant(...)                        # /tenants/Not_Valid/ocr → 422 {"error":"invalid_tenant_id"}
def test_console_and_tenant_ocr_share_the_caps(...)                   # tenant cap 1: one via /v1/ocr (Bearer), then console submit → 429 limit_exceeded
def test_console_tenants_unions_every_source(...)                     # root tenant + a key-ring tenant + a credentials tenant + a runs-only tenant → sorted unique
def test_console_parsers_matches_v1_parsers(...)                      # same JSON as GET /v1/parsers with a Bearer key
def test_the_new_console_routes_refuse_a_bearer_key(...)
```

  Build the parser registry the way `tests/contract/test_ocr_queue.py` and `test_ocr_routes.py` do.
  Seed with `tests/support/console.py`.
- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/contract/test_console_submit_ocr.py -q -o addopts=""`.
  Expected: FAIL with 404.
- [ ] **Step 3: Implement.** Then run, unchanged, `tests/contract/test_ocr_queue.py`,
  `test_ocr_routes.py` and `test_extract_off_loop.py`.
- [ ] **Step 4: Run the gates and the full suite once.** Expected: all green.
- [ ] **Step 5: Commit** with the message `feat(api): console OCR submission, tenants and parsers; one submit path`.

---

### Task 2 (frontend lane): model for submitting OCR

**Files:**
- Create: `ui/src/console/model/submit.ts`, `ui/test/console/submit.test.ts`
- Modify: `ui/src/transport.ts`. Send `Content-Type: application/json` only when the body is a
  `string`. For a `Blob` or `File` body, set no `Content-Type`; the browser and the server's
  sniffing handle it.
- Modify: `ui/src/console/model/client.ts`. Add the intents `list-tenants`, `list-parsers` and
  `submit-ocr`, and update `ALLOWED` and `allIntents`.
- Modify: `ui/src/console/model/failure.ts`. The 429 `limit_exceeded` sentence from the contract.
  A 503 with a string `message` shows that message.
- Test: `ui/test/console/{client,failure}.test.ts`

**Interfaces:**
- Produces, in `client.ts`:
  - `{ type: "list-tenants" }` → `GET /v1/console/tenants`
  - `{ type: "list-parsers" }` → `GET /v1/console/parsers`
  - `{ type: "submit-ocr"; tenantId: string; parser?: string; language?: string }` →
    `POST /v1/console/tenants/{enc(tenantId)}/ocr`. Each query key is set only when its value is
    set. `hasBody` is true.
- Produces, in `submit.ts`:
  - `interface ParserOption { id: string; available: boolean; healthy: boolean | null; languages: string[]; mediaTypes: string[] }`
  - `toTenants(body: unknown): string[]`
  - `toParsers(body: unknown): ParserOption[]`
  - `AUTOMATIC = ""`
  - `parserChoices(parsers: ParserOption[]): { value: string; label: string; disabled: boolean }[]`.
    The first choice is `{value: AUTOMATIC, label: "Automatic", disabled: false}`. Then each
    parser, with label `id`, plus `" (unavailable)"` and `disabled: true` when unavailable, or plus
    `" (not responding)"` when `healthy === false`.
  - `languageChoices(parsers: ParserOption[], parser: string): { value: string; label: string }[]`.
    The first choice is `{value: "", label: "Parser default"}`, followed by that parser's
    languages, or nothing more when `parser === AUTOMATIC` or the parser is unknown.
  - `interface SubmitForm { tenant: string; parser: string; language: string; file: File | null; sending: boolean }`
  - `canSubmit(form: SubmitForm): boolean`. True only when there is a tenant, there is a file, and
    nothing is sending.
  - `submitIntent(form: SubmitForm): Intent`. It omits `parser` and `language` when they are empty.
  - `submittedRunId(body: unknown): string | null`

- [ ] **Step 1: Write the failing tests**

```ts
test("language choices follow the parser", () => {
  const ps = [{ id: "paddle-vl", available: true, healthy: true, languages: ["vi", "en"], mediaTypes: [] }];
  assert.deepEqual(languageChoices(ps, AUTOMATIC), [{ value: "", label: "Parser default" }]);
  assert.deepEqual(languageChoices(ps, "paddle-vl").map(c => c.value), ["", "vi", "en"]);
});
test("parser choices mark unavailable and unhealthy", () => { /* unavailable → disabled + " (unavailable)"; healthy:false → " (not responding)", enabled; first is Automatic */ });
test("cannot submit while sending", () => {
  const f = { tenant: "acme", parser: "", language: "", file: new File(["x"], "a.png"), sending: true };
  assert.equal(canSubmit(f), false); assert.equal(canSubmit({ ...f, sending: false }), true);
  assert.equal(canSubmit({ ...f, sending: false, file: null }), false);
});
test("submit intent omits empty parser and language", () => {
  assert.deepEqual(requestFor(submitIntent({ tenant: "acme", parser: "", language: "", file: null, sending: false })).query, {});
  assert.deepEqual(requestFor({ type: "submit-ocr", tenantId: "acme", parser: "paddle-vl", language: "vi" }),
    { method: "POST", path: "/v1/console/tenants/acme/ocr", query: { parser: "paddle-vl", language: "vi" }, hasBody: true });
});
test("limit exceeded reads as a sentence", () => {
  const f = failureOf({ ok: false, status: 429, body: { error: "limit_exceeded", limit: "ocr_queue", observed: 100, allowed: 100, retry_after_seconds: 30 } });
  assert.equal(describe(f), "This tenant already has 100 OCR runs waiting (limit 100). Try again in 30 s.");
});
test("overloaded shows the server's sentence", () => { /* 503 system_overloaded → "the system is overloaded; please send again later" */ });
```

- [ ] **Step 2: Run them and confirm they fail.** Run: `cd ui && npm test`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend gates.** Expected: green.
- [ ] **Step 5: Commit** with the message `feat(ui): model for submitting OCR`.

---

### Task 3 (frontend lane): the Submit OCR area

**Files:**
- Create: `ui/src/console/components/SubmitArea.tsx`
- Modify: `ui/src/console/components/Shell.tsx`. Add a nav item **Submit OCR** after Runs, in the
  Monitoring group. On success, Shell receives `onOpenRun(runId)` and opens Runs with that run.
- Modify: `docs/concepts/console.md`. Add a short "Submit OCR" section.

**Interfaces:**
- Consumes: Task 2's exports only.
- **The form,** inside an Astryx `Card` about 520 px wide:
  - **Tenant:** a `Selector` filled from `list-tenants`.
  - **Parser:** a `Selector` filled from `parserChoices`.
  - **Language:** a `Selector` filled from `languageChoices`. It resets to "Parser default" when
    the parser changes.
  - **Document:** a `FileInput`.
  - **Submit:** a primary `Button`, disabled unless `canSubmit`, and showing loading while
    sending.
- **Failures** show `describe(failure)` in a `Banner` inside the card.
- **Success** calls `onOpenRun(submittedRunId(body))`.
- **On mount** it loads tenants and parsers. A load failure shows a banner and keeps the form
  usable with whatever loaded.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the frontend gates.** Expected: green.
- [ ] **Step 3: Visual check.** Build with `npm run build`. The test server on :8765 serves
  `ui/dist`. Screenshot:
  - the empty form;
  - the form with a tenant, a parser and a language chosen;
  - the parser dropdown open;
  - an error banner, which you can produce by submitting a `.txt` file, giving 422.

  Read each PNG and iterate until the screen looks deliberate. If the :8765 server is not running,
  report it; do not start containers.
- [ ] **Step 4: Commit** with the message `feat(ui): Submit OCR area`.

---

### Task 4 (after merging both lanes): integration and end-to-end check

- [ ] **Step 1: Merge the lanes into the plan branch.** Run the backend gates, the full suite and
  the frontend gates.
- [ ] **Step 2: Restart the :8765 test server on the merged code.** The controller owns the server
  process; it restarts it, or tells you how.
- [ ] **Step 3: Drive the real UI with Playwright.**
  - Sign in as `admin` / `admin1234567`.
  - Open Submit OCR, choose tenant `mmerp` and parser Automatic, and upload a small PNG (generate
    one with Python).
  - Submit.
  - Assert the page lands on Runs with the new run's detail, showing `queued` and kind OCR.
  - Screenshot each step and read the PNGs.
  - Then confirm over HTTP that `/v1/console/overview` counts one more queued OCR run.
- [ ] **Step 4: Commit any fixes,** with the message `fix(console): <what>`.
