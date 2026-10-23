# ADR-0027: docdoc Is a Service, Not an Installable Library

- **Status**: Accepted
- **Date**: 2026-10-23
- **Requires**: constitution v2.0.0 (Principle IV's base-install clause removed)
- **Amends**: [ADR-0020](0020-retire-the-grounding-viewer.md) in part (the Docker image carries the
  console's build)
- **Relates to**: [ADR-0011](0011-pre-1.0-versioning.md) (pre-1.0 breaking changes),
  [ADR-0025](0025-native-pdf-parsing-on-pdfium.md) and
  [ADR-0026](0026-tenant-quality-evaluation.md) (the two projects this one completes)
- **Design**: `docs/superpowers/specs/2026-10-23-service-only-surface-design.md`
- **Principles engaged**: IV (provider-agnostic adapters), X (layered dependency direction),
  XI (MVP discipline), XII (open-source quality)

## Context

docdoc is self-hosted from its Docker image. Its users reach it through the HTTP API and the
operations console, and operators run a handful of commands inside the container. The repository
still presented it as a Python library and a local command-line tool: the README opened with
`pip install docdoc` and `from docdoc.ingest import parse`; `docdoc parse`, `extract`, `inspect`,
`explain` and `eval` processed documents on a laptop with no server; eight examples called the
packages directly; and Principle IV, with a CI `base-install` job behind it, guaranteed an install
that needed only `pydantic` and `rapidfuzz` (the Packaging paragraph named those two; Principle IV
promised no provider SDK).

That surface cost tests and maintenance, and it told newcomers the wrong story. ADR-0026 gave the
service its own quality measure, so `docdoc eval` was no longer the only way to measure quality,
and the image carries the console, which can now submit an extraction, so nothing in a first
walk-through needs a local command.

## Decision

**docdoc is a service.** Its public contract is the HTTP API, the console, the Docker image and its
configuration, and the operating commands. The Python packages are its implementation. They keep
their names and layout, and they are no longer a public contract.

### 1. What is removed

- The CLI commands `parse`, `extract`, `inspect`, `explain` and `eval`, with their modules, their
  argparse wiring, the global `--verify-cache` option only they read, and their tests. A test that
  guarded behaviour the service still has was re-pointed at the function or route the service uses,
  not deleted.
- The library-usage examples: `parse_pdf.py`, `extract_invoice.py`, `ground_invoice.py`,
  `run_pipeline.py`, `evaluate_golden_set.py`, `build_document.py`, `compare_reports.py` and
  `validate_invoice.py`.
- The CI `base-install` job, and the tests whose only subject was the base install.
- Principle IV's clause "The base install MUST NOT force installation of any provider SDK or OCR
  engine", and the sentences in Principle X and the MVP Scope Constraints that promised a usable
  core library (constitution 2.0.0).

### 2. What stays

- The operating commands `migrate`, `worker`, `sweep`, `credential`, `console-user`, `erase` and
  `store`, with unchanged behaviour, and the global options the worker still reads.
- The HTTP client examples (`submit_async_run.py`, `receive_webhook.py`) and the operating guides.
- `datasets/mvp` and the tests that score it (recorder parity, report snapshots). They are the
  regression net for parser and prompt changes, driven from tests and `make_dataset.py`.
- The `pyproject.toml` extras, as the way the image composes what it installs.
- **The layer contracts.** `import-linter` still enforces the dependency direction, provider SDKs
  only inside adapter directories, and the kernel's purity. These were never a library promise:
  they are what keeps the domain model testable and the providers replaceable, which a service
  needs as much as a library did.

### 3. Why the base-install clause goes and the adapter rule does not

The clause protected one reader: someone who ran `pip install docdoc` and wanted no provider SDK.
That reader no longer exists. The image installs the extras it needs, and its size is set by
`DOCDOC_EXTRAS`, not by a promise about an install nobody makes. Keeping the clause would keep a CI
job and a family of tests guarding a configuration nobody runs.

The adapter-isolation rule is a different guarantee. It says where a provider SDK may be imported,
not what an install contains, and it is what lets a deployment swap Gemini for another model, or
PDFium for a cloud parser, without touching the domain layers. It stays, word for word, and the
`importorskip` gates that let the suite run without a given extra stay with it.

Removing a principle's guarantee makes previously non-compliant work compliant, so the amendment is
MAJOR: 1.13.0 → 2.0.0.

### 4. The image carries the console, and compose migrates (amends ADR-0020 in part)

ADR-0020 left the image without built assets: `specs/008` FR-035 kept static assets out of the
base install, so the image shipped none either and compose mounted a host `ui/dist`. That made Node a
prerequisite of the quickstart. With the base install retired, the reason no longer reaches the
image. The Dockerfile gains a `console` stage that builds `ui/` with Node 24 and copies the result
to `/opt/docdoc/console`, and the image sets `DOCDOC_CONSOLE_ROOT` to it. Node stays in that stage;
the runtime image carries the files only. The **wheel** is unchanged: it still carries no static
asset, and the `ui` extra still delivers `docdoc-ui` to a deployment outside the image.

Compose gains a one-shot `migrate` service, like `createbucket`, that runs `docdoc migrate` once
and exits. The API and both workers start only after it succeeds. The schema is still applied by
one explicit process, never by every process on boot. A newcomer's first ten minutes are then
`git clone`, `docker compose up -d`, `docdoc console-user add`, and the console.

## Consequences

- The README opens with a quickstart that brings up the compose stack, signs in to the console,
  and walks a sample PDF through OCR, extraction, checks and a quality report, with no API key
  (the echo adapter). `FIRST_DOC.md` points at it.
- A script that called the removed commands must use the HTTP API. Pre-1.0 (ADR-0011): no
  deprecation period.
- Passing `--verify-cache` is now an argparse error. `pipeline.run(verify=True)` remains for
  internal use.
- ADR-0003 accepted opaque cache keys on condition that something would explain them, and that was
  `docdoc explain`. `derivation_chain` and `derivation_of` now have no production caller, and drift
  detection (`--verify-cache`) is gone too. Explanation and drift checking are internal-only; the
  derivation records are still written. An HTTP or console surface for them is follow-up work.
- Documents that described the removed commands as current usage carry a one-line note pointing
  here (ADR-0015, ADR-0021, ADR-0026) rather than being rewritten. Specifications and plans under
  `specs/` and `docs/superpowers/` are records and are not edited, except the `plan.md` of specs
  006, 008 and 010, which `test_plan_tree_is_current` requires to name files that still exist.

## Alternatives considered

- **Keep the library surface and document the service beside it.** Rejected: two first stories for
  one product, and the library half had no user.
- **Keep the CLI document commands as debugging tools.** Rejected: the console's run view shows the
  same page, fields and boxes against the real deployment, and a second path to the same answer is
  a second thing to keep in agreement.
- **Collapse the extras into one dependency list.** Rejected for now: the extras are how the image
  is composed, and `DOCDOC_EXTRAS` lets an operator build a smaller one.
