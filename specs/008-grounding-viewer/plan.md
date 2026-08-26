# Implementation Plan: Read-Only Grounding Viewer

**Branch**: `008-grounding-viewer` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-grounding-viewer/spec.md`

## Summary

A browser interface that shows a human which page and which rectangle each extracted value came
from, and says plainly when there is no answer to give — plus the two endpoints without which it
cannot work. The engine is unchanged: no new stage, no new provider, no change to any value docdoc
produces.

The technical approach follows from three facts established during research. **Normalized geometry is
already in displayed coordinates** (R1), so the viewer applies no rotation of its own and `Page.rotation`
must never reach the overlay — the one transformation a careful implementer would add is the one that
would break every rotated page. **`SchemaRegistry.identities()` already returns exactly what
`GET /v1/schemas` must return** (R6), so that endpoint is a projection and not a feature. And
**`pipeline.run()` already accepts bytes and a store** (R5), so the storeless path is a route that
passes `NullArtifactStore()` unconditionally rather than a new execution path.

The shape of the work is set by the first clarification: the rendered interface carries no automated
test, so every decision moves into a **view model** of pure functions that is tested alone, and the
rendering layer is reduced until it decides nothing. That boundary is not a style preference here — it
is the edge of what is verified, and R9 makes it machine-checked rather than aspirational, which is
what Principle XII requires of every other boundary in this repository.

## Technical Context

**Language/Version**: Python 3.11+ (existing) · TypeScript 5.x on Node 20+ (new, build- and test-time
only — nothing Python-side imports it)

**Primary Dependencies**: existing `fastapi` + `uvicorn` behind `docdoc[api]`; new browser-side
`react` and `react-dom` **≥19** (a peer requirement of Astryx, not a preference),
`@astryxdesign/core` + `@astryxdesign/theme-neutral` pinned exact (beta), `@astryxdesign/cli` as a dev
dependency, `pdfjs-dist` (Apache-2.0); build via Vite; tests via Node's built-in `node:test` — no
jsdom, no browser automation, no test framework dependency (R4)

**Storage**: N/A. The storeless path writes nothing by construction (FR-002, FR-008); the browser
holds the document and persists nothing (FR-032)

**Testing**: `pytest` for the two endpoints and the packaging guards; `node --test` for the view model.
The rendered interface is **not** tested — see the Recorded Risk below

**Target Platform**: an evergreen browser talking to the existing single-node, synchronous deployment

**Project Type**: web application — an existing Python service plus a new static browser client served
from the same origin

**Performance Goals**: up-front rendering work bounded by the number of pages the *result* names, not
by document length; a document at the deployment's default limit of 1000 pages
(`src/docdoc/ingest/source.py:66`) stays usable (FR-053, SC-016)

**Constraints**: base install acquires nothing — no dependency, no static asset, no build output
(FR-035); no build output committed (FR-038); no dependency incompatible with Apache-2.0 (FR-039); no
authentication, sessions, or credential storage (FR-060)

**Scale/Scope**: one screen, two new endpoints, one corrected contract. No accounts, no queue, no
database, no per-user state

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated before Phase 0 and re-evaluated after Phase 1. **No status changed between the two passes**;
where Phase 1 sharpened a justification, the sharpened text is what appears here.

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **N/A** — no kernel file is touched. The viewer consumes `Geometry` and `Span` as data over the wire and never imports the kernel. |
| 2 | **Provenance preservation (II, VIII)** | **PASS** — the viewer displays and discards nothing; a storeless run returns the same identities in its result and only declines to *persist* a terminal artifact (FR-003), which is a storage decision, not a provenance one. |
| 3 | **Grounding integrity (II)** | **PASS** — status travels with every value (FR-017), the three geometry states stay distinct (FR-018), ungrounded values get no rectangle (SC-002), and FR-020 forbids the blended confidence ADR-0004 rules out. |
| 4 | **Determinism (III)** | **PASS** — nothing is added to the kernel, grounding, or validation paths. The view model is pure by requirement (FR-041, FR-042) and takes elapsed time as an input rather than reading a clock. |
| 5 | **Provider isolation (IV)** | **PASS** — no provider SDK anywhere. The new `ui` extra carries no SDK, and R7 keeps its assets out of the base install entirely. |
| 6 | **Text-first (V)** | **N/A** — no parser or text-layer decision is involved. |
| 7 | **Schema-driven (VI)** | **PASS** — `GET /v1/schemas` returns `name@version` identities from the existing registry; nothing in the viewer branches on document type. |
| 8 | **Validation separation (VII)** | **N/A** — no rule is added, moved, or evaluated. Verdicts are displayed as produced. |
| 9 | **No silent fallback (VIII)** | **PASS** — FR-025 through FR-027 require typed errors to reach the screen with their cause; SC-010 counts the paths and forbids a blank result. |
| 10 | **Measurability (IX)** | **PASS** — this milestone makes no quality claim, so none needs golden-set backing; the `Correction` model stays representable and untouched (FR-030). |
| 11 | **Layer direction (X)** | **PASS** — the new routes live in `docdoc.api` and import downward only. The browser client is not a layer in the Python graph and imports nothing from it; R9 adds its own machine-checked boundary. |
| 12 | **MVP discipline (XI)** | **PASS**, and this is the gate that had to be argued rather than asserted. The Deferred list names "a full review UI"; this reviews nothing, corrects nothing, and holds no per-user state. The spec's section "Why this needs no constitutional amendment" is the argument, and FR-029 through FR-033 are the fence around it. |
| 13 | **Kernel test rigor (XII)** | **N/A** for the kernel — no span or geometry code changes, so no property test is owed. XII's other clauses are **PASS** and load-bearing here: the new public endpoints get documentation and a runnable example (workflow gate 7), and R9 makes the view-model boundary an automated test rather than a convention, as XII requires of every dependency boundary. |
| 14 | **Open decisions (§Open Constitutional Decisions)** | **PASS** — the list is empty and this milestone resolves nothing implicitly. It raises no new constitutional question: the amendment argument concludes that no amendment is needed, which is a finding, not a decision taken in code. |
| 15 | **Security constraints (§MVP Scope Constraints)** — size limits, MIME allowlist, request caps, secret isolation, temp-file cleanup enforced; document contents, PII, credentials, and prompt bodies never logged | **PASS** — limits reuse the submission path by calling the same code rather than restating the rule (FR-005, T016); redaction is covered by T018 and T019. |

**Row 15 is not in `plan-template.md`, and its absence is why it is here.** The template's fourteen
gates cover the twelve principles and the open-decisions list, and nothing checks the constitution's
Security paragraph — so FR-033 and SC-009 passed through this plan without a single task, on a
milestone that adds a new logging surface, and were found by `/speckit-analyze` rather than by any
gate. Adding the row to the template is a change to shared tooling and therefore out of this
milestone's scope; recording why it should be added is not.

### Recorded risk, which is not a gate failure

**The rendered interface carries no automated test** (spec clarification 1). No constitutional rule is
broken: Principle XII scopes its exhaustive-test mandate to the kernel, and workflow gate 5's
evaluation requirement applies to parsers, prompts, models, schemas, and grounding — none of which
this milestone touches. It is recorded here anyway because it is the largest deviation from this
repository's normal standard, and a reviewer who found it on their own would be right to ask why it
was not disclosed.

What the deviation costs is listed in the spec's Assumptions under "The rendered interface is not
under test". What contains it is FR-043: a renderer that decides nothing has little room to be wrong
alone, and R9 makes that enforceable rather than hoped for. What reverses it is additive — the view
model is already the seam a browser test would attach to.

## Project Structure

### Documentation (this feature)

```text
specs/008-grounding-viewer/
├── plan.md              # This file
├── research.md          # Phase 0 output — R1..R10
├── data-model.md        # Phase 1 output — the view model's entities and states
├── quickstart.md        # Phase 1 output — runnable validation
├── contracts/
│   ├── http-api-additions.md   # the two new endpoints
│   └── view-model.md           # the tested surface: inputs, outputs, invariants
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/api/
├── app.py            # + POST /v1/extract, + GET /v1/schemas, + mount built assets
├── models.py         # + SchemaListing, + StorelessRunResponse
├── settings.py       # + the asset-root setting name (importable without FastAPI)
├── errors.py         # unchanged — the new routes reuse the existing mapping
└── ui.py             # NEW — locate the built assets, or explain their absence (FR-037)

ui/                   # NEW — the browser client, source only; build output is gitignored
├── package.json
├── tsconfig.json
├── vite.config.ts
├── LICENSES.md       # every dependency and its licence (FR-039)
├── scripts/          # the boundary guards — how FR-029, FR-032 and FR-043 are enforced
│   ├── check-model-boundary.mjs   # model imports nothing that renders; components call no network
│   ├── check-readonly.mjs         # no editing control; nothing puts a document at rest
│   └── check-licenses.mjs         # every dependency recorded, none copyleft
├── src/
│   ├── model/        # the VIEW MODEL: pure, framework-free, the only tested part
│   │   ├── types.ts      # the wire shapes and the shapes a renderer is given
│   │   ├── run.ts        # result -> RunView (values, boxes, pages, labels)
│   │   ├── state.ts      # the run state machine (idle/running/complete/failed)
│   │   ├── pages.ts      # which pages render up front, and reaching the rest (FR-051..FR-053)
│   │   ├── failure.ts    # error body -> FailureView, keeping completed stages
│   │   ├── schemas.ts    # listing -> choices, and the empty-registry message
│   │   └── client.ts     # request construction and the URL — no store writes, no corrections
│   ├── transport.ts  # the ONLY place this application calls the network (SC-013)
│   ├── components/   # rendering only; decides nothing (FR-043)
│   └── main.tsx
└── test/             # node --test over src/model/** and the guards

# Directories are named rather than enumerated where their contents change with
# the work. Three convergence passes found this tree stale in three different
# ways — first the model's files, then the tests', then `transport.ts` and the
# guards — because a list of filenames is a claim that has to be maintained and
# nothing maintained it. `components/` was never stale for exactly that reason.

packaging/docdoc-ui/  # NEW — the built assets as their own distribution (R7)
├── pyproject.toml    # no dependencies; a directory of files with a __file__ to find them by
├── build.sh          # the documented build: npm build, copy ui/dist, uv build --wheel
└── src/docdoc_ui/    # __init__.py exposes ASSETS; py.typed; assets/ copied in at build time

tests/
├── contract/
│   └── test_http_ui_endpoints.py       # the two endpoints against the corrected contract
├── integration/
│   ├── test_storeless_extract.py       # writes nothing, with and without a store (SC-007/008)
│   └── test_ui_endpoint_logging.py     # no document content in the logs (FR-033, SC-009)
└── unit/
    └── test_base_install_excludes_ui.py  # FR-035, FR-036 — extends the existing guard

specs/007-pipeline-api-cli/contracts/http-api.md   # corrected per FR-007
```

**Structure Decision**: the existing single Python package gains two routes and one small module; the
browser client is a sibling directory that is not a Python package and is never imported by one. The
built assets ship as a **separate distribution** that `docdoc[ui]` depends on (R7) — the only way found
to satisfy FR-035 ("the base install acquires no static asset") and FR-038 ("build output is not
committed") at the same time, since a Python extra can add a dependency but cannot add files to a
wheel that everyone already installs.

## Complexity Tracking

No Constitution Check gate failed, so nothing is owed this table. Two decisions are recorded anyway
because each adds a moving part a reviewer would otherwise have to reverse-engineer a justification
for.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| A second distribution for the built assets | FR-035 forbids the base install acquiring a static asset; FR-038 forbids committing build output. A wheel cannot include files conditionally on an extra, so the assets must live in something a user opts into installing. | Shipping assets in the main wheel gives them to every `pip install docdoc`, breaking FR-035. Committing `dist/` breaks FR-038. Building at install time requires Node on the user's machine. An operator-supplied asset directory works and was kept as the fallback in R7, but makes `pip install docdoc[ui]` deliver no interface, which is worse than the problem. |
| A view model separate from the components | Under clarification 1 it is the entire tested surface. Without the split, every requirement about what a user sees has zero coverage. | Testing components directly requires the simulated DOM or browser that clarification 1 excluded. Leaving the logic in components and testing nothing was the option that clarification rejected. |
