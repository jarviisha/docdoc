# Service-Only Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docdoc stops presenting itself as an installable library. The local document-processing CLI commands, the library examples, and the base-install promise go away. The console can submit an extraction. The README opens with a four-step quickstart that works on the compose stack.

**Architecture:** This is mostly deletion: commands, examples, tests, the CI job and constitution text. Every test that touches removed things is either deleted or re-pointed at code the service still runs. One feature is added. A shared `submit_extraction` helper stores a blob and submits an extraction run, the console gets a route that calls it, and the Submit screen gets an extraction mode. Docs come last, and the quickstart is walked for real.

**Tech Stack:** Python/FastAPI, argparse CLI, React + TypeScript console, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-23-service-only-surface-design.md`

## Global Constraints

- **Removed:**
  - CLI commands `parse`, `extract`, `inspect`, `explain` and `eval`, plus code that only they use;
  - the examples `parse_pdf.py`, `extract_invoice.py`, `ground_invoice.py`, `run_pipeline.py`, `evaluate_golden_set.py`, `build_document.py`, `compare_reports.py` and `validate_invoice.py`;
  - the CI `base-install` job, and tests whose only purpose is the base-install promise;
  - Constitution Principle IV's base-install clause.
- **Kept, behaviour unchanged:**
  - the commands `migrate`, `worker`, `sweep`, `credential`, `console-user`, `erase` and `store`;
  - the `import-linter` layer contracts;
  - `datasets/mvp` and its recorder-parity and report-snapshot tests;
  - the `pyproject.toml` extras;
  - the HTTP client examples `submit_async_run.py` and `receive_webhook.py`, and the operating guides `erase_tenant.md`, `rotate_credential.md` and `serve_api.md`.
- **Test rule:** a test is deleted only when what it guards is gone (CLI-only behaviour or removed examples). A test that guards behaviour the service still has is re-pointed at the function or route the service uses. Every affected test file gets exactly one outcome, recorded in the task report.
- **`importorskip` gates stay** (ruling, against the spec's "for example"). They are cheap, and they still keep the suite honest for a contributor who has not installed every extra. Only tests whose sole subject is the base install are deleted.
- **No ADR-0019 words** (assign, queue, worklist, inbox, task, workload, approve, reject, disposition, review_state) in routes or console identifiers.
- **The console extraction submit** is `POST /v1/console/tenants/{tenant_id}/extractions?schema=<identity>`. The body is the file's raw bytes, exactly as the console OCR submit takes them. It honours `Idempotency-Key`, uses `Priority.URGENT`, goes through the tenant limiter (429 with `Retry-After`), and logs `console.extract_submit`.
- Doc-consistency tests stay green. Lists in them that name removed things are edited, not deleted.
- `make test`, `make lint` and `make ui` pass before each commit. The local Postgres is `docdoc-dev-pg`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An operator command that silently depended on a removed global CLI option or helper.** Examples: `worker` reading `--schema-path` or `--adapter`, and `erase`/`store` sharing render helpers. Every kept command must still parse and run. Pinned in Task 1 by `test_every_operating_command_still_parses`.
2. **A re-pointed test that no longer exercises anything.** For example, a credential-logging test that only ran through `docdoc extract`. Each re-pointed test must fail if the behaviour it guards breaks. The Task 1 reviewer checks each one against the report's mapping.
3. **The console extraction submit with an unknown schema, or an over-cap or empty file.** It answers the same refusals the Bearer path gives: 404 unknown schema, 413, 422. Pinned in Task 3.
4. **A quickstart step that fails on a fresh stack.** Covers no tenant, no key, and the echo adapter on the sample PDF. Pinned by Task 5's real walk.
5. **Links and references in docs to removed commands or examples.** Pinned by Task 5's grep step and the doc-consistency tests.

---

### Task 1: Remove the document-processing CLI commands

**Files:**
- Delete: `src/docdoc/cli/commands/{parse,extract,inspect,explain,eval}.py`
- Modify: `src/docdoc/cli/__init__.py` (argparse wiring, help, and global options used only by removed commands), `src/docdoc/cli/render.py` and `src/docdoc/cli/config.py` (remove only symbols with no remaining caller)
- Tests to resolve, each one deleted or re-pointed:
  - `tests/contract/test_cli_contract.py`
  - `tests/contract/test_http_contract.py`
  - `tests/integration/test_cli_offline.py`
  - `tests/integration/test_failure_surfaces_agree.py`
  - `tests/integration/test_reuse.py`
  - `tests/unit/test_cli_and_library_need_no_credential.py`
  - `tests/unit/test_cli_config_vocabulary.py`
  - `tests/unit/test_explain.py`
  - `tests/unit/test_stage_identity.py`
  - `tests/unit/test_worker_ocr.py`
  - any other file `make test` shows depends on them
- Create: `tests/unit/test_operating_commands.py`

**Interfaces:**
- Produces: `docdoc --help` lists exactly `migrate, sweep, credential, console-user, erase, worker, store`.

- [ ] **Step 1: Write the failing guard test**

```python
OPERATING = {"migrate", "sweep", "credential", "console-user", "erase", "worker", "store"}

def test_the_cli_offers_only_operating_commands():
    from docdoc.cli import build_parser   # or the module's existing parser factory; read cli/__init__.py
    assert set(subcommand_names(build_parser())) == OPERATING

@pytest.mark.parametrize("argv", [["migrate", "--help"], ["sweep", "--help"], ["credential", "--help"],
                                  ["console-user", "--help"], ["erase", "--help"], ["worker", "--help"], ["store", "--help"]])
def test_every_operating_command_still_parses(argv):
    # SystemExit(0) from --help means the subparser and every option it needs still exist
    with pytest.raises(SystemExit) as done:
        main(argv)
    assert done.value.code == 0
```

Use the module's real parser factory and entry point names. Read `cli/__init__.py` first.

- [ ] **Step 2: Run them.** `uv run pytest -n0 tests/unit/test_operating_commands.py`. Expected: the first test fails (five extra commands), and the parametrized ones pass. They are the safety net.
- [ ] **Step 3: Delete the commands and their wiring.** Then remove helpers and options that have no remaining callers. Check each one with `grep -rn <symbol> src tests` before you remove it. `docdoc.artifacts.derivation`, `docdoc.evaluation` and `docdoc.recording` stay, because the service uses them.
- [ ] **Step 4: Resolve every listed test file.** Apply the Global Constraints test rule. In the task report, add a table: file → deleted / re-pointed (at what) / unchanged, with one line of why. Re-pointed tests must keep their assertion strength.
- [ ] **Step 5: `make test && make lint`.** Expected: green.
- [ ] **Step 6: Commit** `refactor(cli)!: remove parse, extract, inspect, explain and eval; operating commands only`

---

### Task 2: Remove the library examples and the base-install promise from code and CI

**Files:**
- Delete the eight library examples listed in Global Constraints.
- Modify: `tests/integration/test_examples_run.py`, which runs examples: drop the deleted ones and keep the HTTP client ones. Also modify any test that references a deleted example.
- Modify: `.github/workflows/ci.yml`. Delete the whole `base-install` job, and fix any other job's comment that refers to it.
- Delete or modify the tests whose sole subject is the base install:
  - `tests/unit/test_base_install_excludes_evaluation_data.py`
  - `tests/unit/test_base_install_excludes_ui.py`
  - base-install assertions in `tests/unit/test_packaging.py` (keep the wheel-content assertions the image build still relies on)
  - any other such test the grep `grep -rln "base install\|base-install\|base_install" tests` turns up. Read each one first and apply the test rule.
- Modify: `datasets/mvp/make_dataset.py` docstrings that point at removed examples.

- [ ] **Step 1: Delete and resolve.** Keep a deleted / re-pointed / unchanged table in the report.
- [ ] **Step 2: `make test && make lint`.** Expected: green. Also confirm `grep -rn "examples/\(parse_pdf\|extract_invoice\|ground_invoice\|run_pipeline\|evaluate_golden_set\|build_document\|compare_reports\|validate_invoice\)" src tests datasets .github` prints nothing.
- [ ] **Step 3: Commit** `chore!: drop library examples and the base-install job`

---

### Task 3: Submit an extraction from the console (API)

**Files:**
- Modify: `src/docdoc/api/runs.py`. Factor `submit_run`'s body into a shared helper, and add the blob-storing variant.
- Modify: `src/docdoc/api/console.py`. Add the route and `_log_extract_submit`.
- Modify: the route-list contract tests that enumerate routes (`test_http_ui_endpoints.py` ROUTES, `test_console_adds_one_read.py`). Add exactly the new route.
- Test: `tests/contract/test_console_extraction_submit.py` (postgres)

**Interfaces:**
- Produces: `async def submit_extraction(request: Request, *, tenant_id: str, data: bytes, schema: str, priority: Priority) -> JSONResponse` in `api/runs.py`.
  - It stores `data` as a blob, using the same storage and caps `POST /v1/documents` uses.
  - It then submits an extraction run, the same way `POST /v1/documents/{blob_id}/runs` does: schema resolution, limiter check and refusal (`_limit_refusal`), `record_submission`, `Idempotency-Key` replay, deadline.
  - It returns the same body and status that route returns.
  - `POST /v1/documents/{blob_id}/runs` must call the shared part, so its behaviour is unchanged. Its existing tests prove that.
- Route: `POST /v1/console/tenants/{tenant_id}/extractions?schema=<identity>`.
  - Session-only.
  - Same tenant validation as the OCR route (422 `invalid_tenant_id`).
  - 503 via `_no_runs()` when there is no database.
  - Reads the body with `_read_capped`.
  - Calls `submit_extraction(..., priority=Priority.URGENT)`.
  - On 200/202 it answers `_owned(body, tenant_id)` like the OCR route, and logs `_log_extract_submit(operator, tenant_id=, run_id=, schema=, status=)`.

- [ ] **Step 1: Write failing tests.**
  - `test_an_operator_submits_an_extraction`: 202, a `run_id`, and a run of kind extraction with `schema_identity == "invoice@1"` for that tenant.
  - `test_an_unknown_schema_is_404`
  - `test_an_empty_body_is_refused`: same status as `POST /v1/documents` with an empty body.
  - `test_a_rate_limited_tenant_gets_429`: `Retry-After` present.
  - `test_the_submission_is_logged`: a `console.extract_submit` record with the operator.
  - `test_bearer_cannot_reach_it`: the console gate's refusal.
- [ ] **Step 2: Run them.** `DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc uv run pytest -n0 tests/contract/test_console_extraction_submit.py`. Expected: 404 or 405 from the missing route.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: `make test && make lint`.** Expected: green.
- [ ] **Step 5: Commit** `feat(console-api): submit an extraction on a tenant's behalf`

---

### Task 4: The Submit screen can submit an extraction (console)

**Files:**
- Modify: `ui/src/console/model/client.ts`. Add the intent `{type: "submit-extraction"; tenantId: string; schema: string; idempotencyKey?: string}`, which maps to POST `/v1/console/tenants/{t}/extractions` with query `schema`, and add its `ALLOWED` pattern.
- Modify: `ui/src/console/model/submit.ts`. Add `SubmitKind = "ocr" | "extraction"`, `toSchemas(body: unknown): string[]` (from the tenant answers, or a schema listing; see below), and `canSubmit(form) -> boolean` covering both kinds.
- Modify: `ui/src/console/components/SubmitArea.tsx`. Add a kind toggle. In extraction mode it shows a schema picker instead of parser and language.
- Test: `ui/test/console/submit.test.ts`, `client.test.ts`

**Schema list source:** if the console has no route that lists the deployment's registered schemas, add `GET /v1/console/schemas`. It is session-only and returns `{"schemas": ["invoice@1", …]}`, the same data `GET /v1/schemas` gives a tenant (read `api/documents.py` or `api/runs.py` for that route). Add it to Task 3's route-list tests too, since it ships with this task. If an equivalent route already exists, use it.

- [ ] **Step 1: Write failing `node --test` cases.**
  - `submit-extraction` plans a POST to the allowed path with `schema` in the query.
  - `canSubmit` is false for extraction without a schema or without a file, and true with both.
  - `toSchemas` parses the listing and ignores garbage.
- [ ] **Step 2: Run them.** `cd ui && npm test`. Expected: failures.
- [ ] **Step 3: Implement** the model, the component and the schema route if one is needed. Follow the `console-ui` skill. Decisions stay in `model/`.
- [ ] **Step 4: Run the checks.** `cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run build`, then `make test && make lint`. Expected: green.
- [ ] **Step 5: Commit** `feat(console): submit an extraction from the Submit screen`

---

### Task 5: Docs, the quickstart walked for real, ADR-0027 and the constitution

**Files:**
- Rewrite: `README.md`, in this order: the quickstart (the spec's four steps, using `tests/fixtures/pdf/digital_invoice.pdf`, with step 4 on the echo adapter), then configuration, API routes, operating (the kept commands), and architecture.
  - Add a "Use a real model" note: set `DOCDOC_MODEL_ADAPTERS=gemini` and `GEMINI_API_KEY` in `.env`, then run `docker compose up -d`.
  - Remove `pip install`, `import docdoc` and extras tables.
- Create: `packaging/docker/.env.example` with both variables, commented.
- Replace `FIRST_DOC.md`: either point it at the README quickstart, or rewrite it as the same walk-through. Pick one and say which.
- Modify the docs that name removed commands or examples:
  - `docs/concepts/*.md`: evaluation, quality, retention, ingest, runs
  - ADRs that describe *current* usage: 0015 and 0021 mention `docdoc parse`/`docdoc extract`/`docdoc eval`. Add a one-line "superseded usage: the command was removed in ADR-0027" note rather than rewriting history.
  - `examples/serve_api.md`, `examples/erase_tenant.md`
  - `CONTRIBUTING.md`
- Create: `docs/adr/0027-service-only-surface.md` (format as 0025/0026). It covers the decision, the removed surfaces, why Principle IV's base-install clause goes, and that the layer contracts stay. Add it to the `docs/adr/README.md` index.
- Modify: `docs/constitution.md`.
  - Remove the base-install clause from Principle IV, keeping the adapter-isolation rule.
  - Fix any other sentence that promises a library or base install (grep "base install", "library", "pip install").
  - Add an amendment record 1.13.0 → 2.0.0 in the file's own format. This is MAJOR: a principle's guarantee is removed, so mark it MAJOR.
  - Bump the footer to 2.0.0 and Last Amended to 2026-10-23.
- Modify: `CHANGELOG.md`. Add an Unreleased "Removed" entry (commands, examples, base install) and an "Added" entry (console extraction submit).

- [ ] **Step 1: Write the docs.** Then run `grep -rnE "docdoc (parse|extract|inspect|explain|eval)\b|pip install docdoc|from docdoc\.(ingest|extraction|grounding|validation) import" --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=specs . | grep -v "docs/superpowers/\|CHANGELOG.md"`. Expected: nothing, except the history notes added to ADRs.
- [ ] **Step 2: Walk the quickstart for real.**
  - Run `docker compose -f packaging/docker/compose.yml up -d --build`, then `docker compose -f packaging/docker/compose.yml exec api docdoc console-user add admin` (follow the prompts).
  - Sign in at `http://localhost:8000/console`, submit `digital_invoice.pdf` as OCR and see its page with boxes, then submit it as an extraction against `invoice@1`.
  - Open the run, mark two fields, and run Quality.
  - Use headless Chromium for screenshots if that is available.
  - Afterwards run `docker compose -f packaging/docker/compose.yml down -v`.
  - Record every step's outcome in the report. If any step fails, fix the cause (docs, compose or code), not the wording.
- [ ] **Step 3: Run `make test && make lint && make ui`.** Expected: green, including `test_documented_api_references_resolve`, `test_plan_tree_is_current`, `test_convergence_gaps` and `test_data_model_matches_the_code`.
- [ ] **Step 4: Commit** `docs: service-only README and quickstart, ADR-0027, constitution 2.0.0`
