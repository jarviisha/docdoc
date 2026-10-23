# docdoc is a service: retire the installable-library surface

Date: 2026-10-23. Project C, the last of three in the move to a server-side service. Project A
(PDFium parser, ADR-0025) and project B (tenant quality evaluation, ADR-0026) are merged.

## Why

docdoc is an open-source document-processing service that people self-host from its Docker image.
Its users reach it only through the HTTP API and the operations console. Operators run a handful
of CLI commands inside the container. The codebase and its docs still present docdoc as a Python
library and a local command-line tool:
- `pip install docdoc` and `from docdoc.ingest import parse` open the README;
- `docdoc parse/extract/inspect/explain/eval` process documents on a laptop with no server;
- Constitution Principle IV and the CI `base-install` job guarantee a "base install" that needs only
  pydantic.

That surface costs maintenance and tests, and it tells newcomers the wrong story about what docdoc
is. Project B gave the service its own quality measurement, so `docdoc eval` is no longer the only
way to measure quality.

## Goals

- **A newcomer's first ten minutes**, decided with the owner:
  1. `git clone` and `docker compose -f packaging/docker/compose.yml up -d`.
  2. `docker compose exec api docdoc console-user add admin`, then open
     `http://localhost:8000/console` and sign in.
  3. **With no API key:** submit a sample PDF from the console's Submit screen and see the text and
     its boxes on each page. The native PDFium path works offline.
  4. **With an LLM key** (for example Gemini) in `.env`: submit an extraction against `invoice@1`
     from the console, open the run to see each field and where it was read, mark a few fields
     Correct or Wrong, and run Quality.

  `schemas/` is already mounted at `/schemas` in compose. Point the steps at a sample PDF that is
  already in the repository.
- **The console's Submit screen can submit an extraction**: choose tenant, file and schema. Today it
  submits OCR only. This is what makes step 4 console-only (the assistant's call; see below).
- **README rewritten in this order:** quickstart (the four steps), configuration, API routes,
  operating (the remaining CLI commands), architecture. No `pip install docdoc`, no `import docdoc`
  examples. `FIRST_DOC.md` is replaced by the quickstart. Fold it into the README, or rewrite it as
  the same walk-through and link to it.
- **Removed:**
  - the CLI commands `parse`, `extract`, `inspect`, `explain` and `eval`, with their modules under
    `src/docdoc/cli/commands/`, their argparse wiring, their global options that only they use,
    their tests, and their docs;
  - the library-usage examples in `examples/`: `parse_pdf.py`, `extract_invoice.py`,
    `ground_invoice.py`, `run_pipeline.py`, `evaluate_golden_set.py`, `build_document.py`,
    `compare_reports.py`, `validate_invoice.py`, and any test that runs them;
  - the CI `base-install` job, and every test whose only purpose is the base-install promise (for
    example, tests asserting the base install imports or runs without extras, SC-013/FR-053-style
    guards, the `importorskip` gates that exist only for it);
  - Constitution Principle IV's base-install clause, recorded as an amendment in the file's own
    format with a version bump, plus an ADR (ADR-0027) recording the decision and the principle
    change.
- **Kept:**
  - the operating CLI commands `migrate`, `worker`, `sweep`, `credential`, `console-user`, `erase`
    and `store`, unchanged in behaviour;
  - HTTP client examples (`submit_async_run.py`, `receive_webhook.py`) and the operating guides
    (`erase_tenant.md`, `rotate_credential.md`, `serve_api.md`), updated to stop referring to removed
    commands;
  - the layer boundaries enforced by `import-linter` (provider SDKs only inside adapters, the
    kernel's purity). They are architecture, not a library promise;
  - the committed golden set `datasets/mvp` and the internal tests that score it (recorder parity,
    report snapshots). They remain the regression net for parser and prompt changes, driven from
    tests and `datasets/mvp/make_dataset.py`, not from a CLI command;
  - the `pyproject.toml` extras, as the way the Docker image composes what it installs. The README
    stops mentioning them.

## Non-goals

- Changing the HTTP API, the console's existing screens, or the run model.
- Renaming or restructuring the internal Python packages. `docdoc.ingest`, `docdoc.extraction` and
  so on stay as they are. They simply stop being a public contract.
- Publishing an image to a registry, or changing the Dockerfile beyond what removing the commands
  requires.
- Rewriting historical specs and plans under `specs/` and `docs/superpowers/`. They are records.

## Anti-goals

- Breaking an operator's workflow. Every operating command keeps working exactly as before, and the
  worker and migrate behave identically.
- Losing a test that guards behaviour the service still has. Remove a test only when the thing it
  guards is gone. When a removed CLI test was the only coverage of a library function the service
  still uses, move that coverage to a test that calls the function directly or goes through the API.
- A quickstart that does not work. Each step must be walked for real against the compose stack
  before merge.

## Constraints

- Pre-1.0 (ADR-0011): breaking changes are allowed.
- Squash-merge convention: the PR body describes all the work.
- Tests: `make test`, `make lint` and `make ui` stay green. Doc-consistency tests
  (`test_documented_api_references_resolve`, `test_plan_tree_is_current`, `test_convergence_gaps`,
  `test_data_model_matches_the_code`) must keep passing. Lists in them that name removed things are
  updated, not deleted.
- Console additions follow the model/components split (`ui/src/console/model/README.md`) and the
  `console-ui` skill. They introduce no ADR-0019 words.
- The extraction submit from the console goes through a session-only console route
  (`/v1/console/tenants/{tenant_id}/…`) that shares the Bearer submission's helper, the way the OCR
  submit does. It is rate-limited and logged like the OCR submit.

## Calls made by the assistant (owner may override)

- **Step 4 is console-only.** The Submit screen gains extraction, so the quickstart needs no `curl`.
- **The extras stay** in `pyproject.toml` as image composition. They are not removed or collapsed.
- **`FIRST_DOC.md`** is replaced by the quickstart (the builder decides whether it is folded in or
  kept as a separate walk-through).

### Settled after the builder check (assistant's calls)

1. **The tenant.** A fresh stack already has one. The console's tenant list always includes the
   root tenant (`list_tenants` in `api/console.py`: `root_tenant()`, plus key-file and credential
   tenants, plus tenants seen in runs). The quickstart therefore stays four steps, and the
   Submit screen's tenant picker shows that tenant.
2. **The model adapter in step 4.** Compose defaults to `DOCDOC_MODEL_ADAPTERS=echo`, with
   `tests/fixtures/echo` mounted. There is an `invoice@1` fixture.
   - Step 4 works with **no key** on the echo adapter. The README says so plainly: echo returns
     canned answers, and it exists so the whole flow can be walked offline. Its values ground on
     `tests/fixtures/pdf/digital_invoice.pdf`, which is the sample the quickstart uses.
   - A short "use a real model" note follows. It tells the reader to set
     `DOCDOC_MODEL_ADAPTERS=gemini` **and** `GEMINI_API_KEY` in `.env`, then run
     `docker compose up -d`.
   - Add a `.env.example` at the compose directory with both, commented. Compose's defaults do
     not change.
   - Step 3 (OCR) needs no model at all.
3. **The test migration rule.** Go through every test that imports `docdoc.cli.commands.{parse,
   extract,inspect,explain,eval}` or runs those commands. Each one ends up in exactly one of two
   states:
   - **Deleted**, if what it guards is CLI-only: argument parsing, CLI rendering, exit codes, CLI
     config vocabulary for removed options, examples that are removed.
   - **Re-pointed** at the function or route the service still uses, if it guards behaviour the
     service still has. Examples: no credential is logged, failure surfaces agree, and
     `run_operations` for the kept commands.

   The plan lists every file with its outcome. Code that only the removed commands use goes too:
   for example, parts of `cli/render.py` and `cli/config.py` options. Check each symbol's callers
   first. Code the service uses stays, such as `docdoc.artifacts.derivation`, which artifact
   identity uses.
4. **Golden-set scoring.** The scoring logic stays in `docdoc.evaluation` and `docdoc.recording`,
   because the service uses them. `datasets/mvp/make_dataset.py` and the recorder-parity and
   report-snapshot tests drive it directly. Nothing calls `cli.commands.eval`. Tests that reference
   the removed example scripts are re-pointed or deleted under rule 3.
5. **The console extraction submit** is `POST /v1/console/tenants/{tenant_id}/extractions?schema=<identity>`,
   a multipart file upload, modelled on the console OCR submit (`/tenants/{t}/ocr`):
   - same body handling, same `Idempotency-Key` support, same `Priority.URGENT`;
   - the tenant limiter, through the same check and refusal helper the Bearer run submission uses
     (429 with `Retry-After`);
   - a `console.extract_submit` log line beside `console.ocr_submit`.

   It stores the blob and submits an extraction run through the shared submission path that
   `POST /v1/documents/{blob_id}/runs` uses.
6. **The sample PDF** is `tests/fixtures/pdf/digital_invoice.pdf` for steps 3 and 4. The reader
   uploads it from their clone, so the container does not need to see it.

## Left to the builder

- Exactly which tests and docs reference the removed commands, and how each is resolved: deleted,
  moved to direct-function coverage, or updated.
- The console route shape for an extraction submit (the OCR submit route is the template) and the
  Submit screen's layout.
- Which sample PDF the quickstart uses, and whether the walk-through needs a `.env.example` entry for
  the LLM key.
- Whether removing the CLI's global options (`--schema-path`, `--adapter`, `--store`, …) is safe,
  given what `worker` and `migrate` still read. Check before removing each one.
