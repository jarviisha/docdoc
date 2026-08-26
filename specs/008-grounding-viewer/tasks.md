# Tasks: Read-Only Grounding Viewer

**Input**: Design documents from `/specs/008-grounding-viewer/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Not optional here. The spec requires them by name — FR-033, FR-036, FR-042, SC-005 through
SC-009, SC-012 — and the constitution override below adds the layer-boundary case. What is *not*
tested is the rendered interface, by the deliberate decision of the spec's first clarification; every
task below that touches `ui/src/components/` therefore ships without coverage, and that is the
milestone's recorded deviation rather than an omission in this list.

**Constitution override (docdoc)**: of the six areas where tests are mandatory, one applies —
**layer boundaries**. T005 and T006 are that test, extended to a second language: `ui/src/model/**`
may import nothing from `ui/src/components/**`, React, or the renderer (Principle XII, R9). The kernel,
grounding, validation, provider adapters, and evaluation are all untouched by this milestone, so no
property test, no adapter integration test, and no golden-set metrics task is owed.

**Amended 2026-08-25**, after `/speckit-analyze` found one CRITICAL and two HIGH coverage gaps in the
first version of this list. Eight tasks were added and every task from the insertion points onward was
renumbered; nothing referenced the old identifiers, because no task had been started. The gaps were:
**log redaction** (FR-033, SC-009) had zero tasks despite being a constitutional MUST and despite this
milestone adding a new logging surface — now T018 and T019; **page reachability** (FR-052) had none
while SC-016 already claimed "0% of the document is unreachable" — now T032, T040, T046; and the three
negative guarantees (FR-029, FR-031, FR-060) had none — now T006, T020, T037.

**Organization**: grouped by user story so each can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 — maps to the user stories in spec.md
- Exact file paths in every description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: the browser client's toolchain, and the guards that keep it out of the base install from
the first commit rather than after the first mistake. Both fences (T005, T006) exist before the code
they fence, which is the only ordering under which a fence is never retrofitted around a violation.

- [X] T001 Create `ui/package.json` pinning `@astryxdesign/core` and `@astryxdesign/theme-neutral` at **exact** versions (the library is beta — spec Assumptions), with `@astryxdesign/cli` as a dev dependency, plus `react` and `react-dom` **≥19** (Astryx's peer requirement, not a preference), `pdfjs-dist` (Apache-2.0, R2), `vite`, and `typescript`. No test framework: the view model is tested with `node --test` (R4). Astryx ships pre-built CSS and JS, so no StyleX plugin or PostCSS configuration is needed anywhere
- [X] T002 [P] Create `ui/vite.config.ts` building to `ui/dist`, with the base path matching where `docdoc.api` mounts the assets
- [X] T003 [P] Create `ui/tsconfig.json` treating `ui/src/model` and `ui/src/components` as distinct roots, so the boundary of T005 is expressible
- [X] T004 [P] Add `ui/node_modules/` and `ui/dist/` to `.gitignore` — build output is never committed (FR-038)
- [X] T005 [P] Add the boundary check to `ui/` forbidding any import from `ui/src/model/**` to `ui/src/components/**`, `react`, `react-dom`, or `pdfjs-dist`, wired as `npm run lint:boundaries` (FR-043, R9, Principle XII)
- [X] T006 [P] Add a static check to `ui/` asserting that `ui/src/components/**` contains no `<input>`, `<textarea>`, `<form>`, `contentEditable`, or submit handler, wired into `npm run lint:boundaries`. This is the enforceable half of FR-029: the rendering layer carries no test, so the guarantee that it offers no editing control is kept by a check that reads the source rather than by a test that runs it (FR-029)
- [X] T007 [P] Add `ui/package.json` scripts: `build`, `test` (`node --test`), `lint:boundaries`, `licenses`
- [X] T008 Add a `ui` job to `.github/workflows/ci.yml` running `npm ci`, `npm test`, `npm run lint:boundaries`, and `npm run build` — and confirm the existing `base-install` job does **not** gain a Node step, since its whole purpose is proving the base install needs none

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the server side. Until the two endpoints exist the viewer has no data to show and no
schema to offer, so every user story is blocked on this phase.

**⚠️ CRITICAL**: no user story work begins until this phase is complete.

### Decisions that gate code

- [X] T009 Write `docs/adr/0012-storeless-extraction-over-http.md` recording why extraction gained a path that persists nothing: the `blob_id`-shaped route cannot avoid a store, the project already rejected Vision's asynchronous API for creating "a place for document content to come to rest outside the process", and the same objection applies with more force to our own interface. Record the consequence — no terminal artifact, therefore no job to fetch — as a property rather than a defect, and record the exposure change of FR-063. Add it to the constitution's decision table in the same change

### The storeless run

- [X] T010 Add `StorelessRunResponse` to `src/docdoc/api/models.py` — Milestone 7's run response with no `job_id`, documented as carrying none because a storeless run produces no terminal artifact
- [X] T011 Implement `POST /v1/extract` in `src/docdoc/api/app.py`: raw body, `schema` query parameter, reusing `_read_capped`, `detect_media_type`, `SourceFile.from_bytes`, and `check_limits` so FR-005 holds by calling the same code rather than restating the rule. Pass `NullArtifactStore()` **unconditionally** — the endpoint decides whether a run persists, never the deployment (FR-001, FR-008, R5)
- [X] T012 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: with no store configured, the route returns `200`, a full result, and no job identity (FR-001, FR-003)
- [X] T013 [P] Integration test in `tests/integration/test_storeless_extract.py`: a storeless run over a deployment with **no** store leaves zero bytes written — no blob, no artifact, no temporary file (FR-002, SC-007)
- [X] T014 [P] Integration test in `tests/integration/test_storeless_extract.py`: the same run over a deployment **with** a store configured also writes zero artifacts and zero blobs. This is the test that fails when a later change reuses the deployment's store because it happens to be there (FR-008, SC-008)
- [X] T015 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: a storeless result and a store-backed result over the same bytes agree on every value, verdict, location, and identity once `job_id` is removed (FR-004, SC-006)
- [X] T016 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: an oversized document and a disallowed media type are each refused before any parser runs and before any provider is contacted, leaving no temporary file, with the type detected from the bytes and not from `Content-Type` (FR-005)
- [X] T017 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: a run failing partway returns the typed error with its stage, the per-stage outcomes, and the results the completed stages produced (FR-006)

### What must never reach a log

- [X] T018 [P] Integration test in `tests/integration/test_ui_endpoint_logging.py`: a run over a document containing known distinctive strings emits **zero** of those strings, zero extracted values, zero credentials, and zero prompt bodies into the logs of `POST /v1/extract` and `GET /v1/schemas`, while 100% of the constitutionally required fields — request id, processing id, step id, latency, provider, model, token usage — are present. The same shape as Milestone 7's SC-008 test, applied to the surfaces this milestone adds (FR-033, SC-009)
- [X] T019 Confirm in `src/docdoc/api/app.py` that the new routes emit the **existing** per-stage structured events and add no logging path of their own. A second path is how the first one's redaction stops being the only one that matters (FR-033)

### What must never accumulate

- [X] T020 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: the application's route set is exactly the known routes, no response carries `Set-Cookie`, and no handler reads or writes session state — so "no per-user state on the server" and "no authentication added" are properties a test fails on rather than sentences in a spec (FR-031, FR-060)

### Listing schemas

- [X] T021 Add `SchemaListing` to `src/docdoc/api/models.py` and implement `GET /v1/schemas` in `src/docdoc/api/app.py` as a projection of `SchemaRegistry.identities()` — which already returns exactly the sorted `name@version` set `resolve()` accepts, so no translation exists to get wrong (FR-009, FR-010, R6)
- [X] T022 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: every listed identity is accepted verbatim by `POST /v1/extract`, and the response body contains no filesystem path (FR-010, FR-011)
- [X] T023 [P] Contract test in `tests/contract/test_http_ui_endpoints.py`: a deployment with no schemas configured returns `200` and an empty list, not an error (FR-012)

### The contract that was wrong

- [X] T024 Correct `specs/007-pipeline-api-cli/contracts/http-api.md` §1: replace "Running an extraction and reading what it produced do not [need a store]" with an accurate table and the reason — the `blob_id` route needs one because a `blob_id` exists only after a submission, and `POST /v1/extract` is the route that needs none. State that a storeless run has no job, and record the exposure change of FR-063 (FR-007, R10)
- [X] T025 [P] Test in `tests/contract/test_http_ui_endpoints.py` asserting the storeless path behaves as the corrected contract describes, so the contract and the code cannot drift apart again silently (SC-012)

### Serving the assets

- [X] T026 Add `src/docdoc/api/ui.py` locating the built assets, and add its setting name to `src/docdoc/api/settings.py` — which must stay importable with no web framework installed, as its own docstring requires
- [X] T027 Mount the assets same-origin in `src/docdoc/api/app.py` when present, so no cross-origin configuration is introduced anywhere (FR-034)
- [X] T028 [P] Test in `tests/contract/test_http_ui_endpoints.py`: with the extra installed and no assets found, the application names the step that fixes it and distinguishes the two cases — a checkout that has not been built, and an installation missing the `docdoc-ui` distribution. Never a blank page (FR-037)

**Checkpoint**: the interface has data. User stories can begin.

---

## Phase 3: User Story 1 — See where a value came from (Priority: P1) 🎯 MVP

**Goal**: a person picks a document, chooses a schema, and sees every located value's rectangle on the
page it came from, with selection working in both directions.

**Independent Test**: feed the committed fixture's result to the view model and confirm every located
value produces exactly the boxes the run returned, on the pages it named. Then open it once and look.

### Tests for User Story 1

> Write these first; they fail until the model exists.

- [X] T029 [P] [US1] `ui/test/run.test.ts`: for every located value the model emits **exactly one entry per box the run returned** — a value carrying three boxes yielding one entry is a failure, not a pass (SC-001, invariant 1)
- [X] T030 [P] [US1] `ui/test/run.test.ts`: coordinates pass through unchanged — no transform, no rounding, no clamping, no merging of adjacent boxes into a hull. Per R1 they are already in displayed space (FR-023, invariant 2)
- [X] T031 [P] [US1] `ui/test/pages.test.ts`: `pagesToRender` returns exactly the pages carrying located values, and its length does not grow when the same result is paired with a document ten times longer (FR-051, FR-053, SC-016)
- [X] T032 [P] [US1] `ui/test/pages.test.ts`: every page index in `0..pageCount-1` is requestable, and requesting a page the result does not name adds **exactly one** render request while leaving `pagesToRender` unchanged. Both halves: the first is FR-052's reachability, the second is FR-053's bound, and a change satisfying either alone breaks the other (FR-052, SC-016)
- [X] T033 [P] [US1] `ui/test/run.test.ts`: selecting a field distinguishes its boxes; selecting a box resolves its field; a field spanning multiple pages exposes all of them rather than one (FR-021, FR-022)
- [X] T034 [P] [US1] `ui/test/state.test.ts`: choosing a document clears the prior view before any new box entry exists, **and** a result arriving under a stale token is discarded. Two doors, one failure (FR-028, FR-049, SC-011)
- [X] T035 [P] [US1] `ui/test/state.test.ts`: while running, the model exposes elapsed time and **zero** proportions, percentages, stage counts, or estimates (FR-045, FR-046, SC-015)
- [X] T036 [P] [US1] `ui/test/client.test.ts`: across the full set of user intents, `requestFor` constructs zero requests that write to a store and zero corrections (FR-030, SC-013)
- [X] T037 [P] [US1] `ui/test/client.test.ts`: `requestFor` exposes no mutation intent of any kind — extending SC-013 from "no store writes and no corrections" to "no writes at all", which is the model-side half of the read-only guarantee T006 fences on the component side (FR-029, FR-030)

### Implementation for User Story 1

- [X] T038 [US1] `ui/src/model/run.ts` — `toRunView`: result to values, boxes, and pages, per [data-model.md](./data-model.md) `RunView` and `BoxEntry`. This function *is* FR-041's claim that every decision the viewer makes is computed outside the rendering layer (FR-041)
- [X] T039 [P] [US1] `ui/src/model/pages.ts` — `pagesToRender`, bounded by the result and not by document length
- [X] T040 [US1] `ui/src/model/pages.ts` — on-demand page requests, so a page carrying no located value stays reachable and renders when navigated to. `pagesToRender` remains the up-front set and does not grow (FR-052)
- [X] T041 [US1] `ui/src/model/state.ts` — the state machine of [data-model.md](./data-model.md) `RunState`, including the run token, the stale-result rule, and elapsed time as an **input** rather than a clock read
- [X] T042 [P] [US1] `ui/src/model/client.ts` — `requestFor`, covering submit, list schemas, and extract, and nothing that writes
- [X] T043 [P] [US1] `ui/src/components/Page.tsx` — render one page to a canvas through pdf.js's **default viewport**, which already applies the page's rotation. Apply no rotation here and never read `Page.rotation`: the parsers resolved it before normalizing, and applying it again double-rotates every rotated page (FR-014, FR-024, R1)
- [X] T044 [P] [US1] `ui/src/components/Overlay.tsx` — draw **every** `BoxEntry` a value carries, not the first, scaling to the rendered page size and applying no other transform (FR-014, FR-015, FR-023)
- [X] T045 [P] [US1] `ui/src/components/ValueList.tsx` — the list, with selection wired in both directions from the view model's output, and operable without a pointing device (FR-021, FR-056)
- [X] T046 [P] [US1] `ui/src/components/PageNav.tsx` — navigation to any page of the document, including the ones the result does not name (FR-052)
- [X] T047 [US1] `ui/src/components/Picker.tsx` — choose a local file and a schema from `GET /v1/schemas`; the document never leaves the browser except to the extraction endpoint, and is never written to browser-persistent storage (FR-013, FR-032)
- [X] T048 [US1] `ui/src/components/Running.tsx` — show that a run is in flight and how long it has been running. No progress bar, no percentage, no estimate, **no cancel control**, and no client-side timeout: stopping the wait does not stop the run, and the provider is paid either way (FR-045, FR-046, FR-047, FR-048)
- [X] T049 [US1] `ui/src/components/App.tsx` — make starting a second run unavailable while one is in flight (FR-049)

**Checkpoint**: US1 is independently demonstrable — quickstart scenarios 5 and 6.

---

## Phase 4: User Story 2 — Be told plainly what docdoc does not know (Priority: P2)

**Goal**: every value the model asserted is visible with its status, every distinction the engine
preserves survives to the screen, and nothing is quietly dropped for being undrawable.

**Independent Test**: apply a fixture with mixed outcomes to the view model and confirm the count it
lists equals the count in the result, each with its status, with absent fields distinguished from
ungrounded ones.

### Tests for User Story 2

- [X] T050 [P] [US2] `ui/test/run.test.ts`: the count of values listed equals the count the run produced, **and** every schema field the model reported absent is listed as absent rather than omitted (SC-003)
- [X] T051 [P] [US2] `ui/test/run.test.ts`: ungrounded values emit zero box entries and are never omitted from the list — both halves counted together (SC-002)
- [X] T052 [P] [US2] `ui/test/run.test.ts`: the three geometry states produce three distinct outputs with three distinct labels, and neither `unavailable` nor `empty` is labelled "not found" (FR-018, SC-004)
- [X] T053 [P] [US2] `ui/test/run.test.ts`: a field the model reported absent is distinguishable from a field asserted with a value that could not be grounded (FR-019)
- [X] T054 [P] [US2] `ui/test/run.test.ts`: a score is emitted only with its tier, and the model exposes no ordering, comparison, aggregation, or blended number across tiers (FR-020, invariant 6)
- [X] T055 [P] [US2] `ui/test/run.test.ts`: every fact the overlay conveys — field, value, verdict, status, pages — is present in the list, and every status, verdict, and geometry state carries a textual label (FR-055, FR-057, FR-058, SC-017)
- [X] T056 [P] [US2] `ui/test/failure.test.ts`: every failure path a user can reach — no schemas configured, document too large, unsupported type, provider failure, mid-run failure — produces a view naming the cause, and none produces an empty result or an untyped error (FR-025, SC-010)

### Implementation for User Story 2

- [X] T057 [US2] `ui/src/model/run.ts` — `presence`, `status`, and the three-member `GeometryState` union, keeping apart the facts `src/docdoc/grounding/result.py` keeps apart
- [X] T058 [US2] `ui/src/model/run.ts` — `labels` for status, verdict, and geometry state, so no distinction can depend on a colour the model never supplied (FR-057)
- [X] T059 [P] [US2] `ui/src/model/failure.ts` — `toFailureView`, carrying stage, error class, docdoc's own message, and the completed stages' results. Never a provider's error text (FR-025)
- [X] T060 [P] [US2] `ui/src/model/schemas.ts` — `toSchemaChoices`, and the empty-registry message naming the setting that populates the list (FR-026)
- [X] T061 [P] [US2] `ui/src/components/ValueList.tsx` — show ungrounded values as prominently as located ones (FR-016, FR-017)
- [X] T062 [P] [US2] `ui/src/components/Score.tsx` — render a score with its tier and never on a scale shared across tiers (FR-020)
- [X] T063 [US2] `ui/src/components/App.tsx` — state on screen that pages are being shown selectively, so a partial view is never taken for the whole document (FR-054)
- [X] T064 [US2] `ui/src/components/App.tsx` — show the deployment's typed reason when a document is refused for size or type (FR-027)

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: User Story 3 — Install it without acquiring it (Priority: P3)

**Goal**: `pip install docdoc` delivers exactly what it delivered before this milestone.

**Independent Test**: in a base-install environment, the package imports, the offline suite passes, and
the installed distribution holds zero viewer files.

- [X] T065 [US3] Add the `ui` extra to `pyproject.toml` as `ui = ["docdoc-ui==0.1.0"]` — pinned with `==` to the exact `docdoc` version, because the assets and the routes serving them are one artifact split for packaging reasons, not two things with a compatibility range — with the licensing note the neighbouring extras carry (R7)
- [X] T066 [US3] Create the `docdoc-ui` distribution's packaging — built from `ui/dist`, version-pinned against `docdoc`, and buildable from a clean checkout with a documented command (R7, FR-038)
- [X] T067 [P] [US3] `tests/unit/test_base_install_excludes_ui.py`: the wheel still ships `["src/docdoc"]` and nothing UI-shaped, read from `pyproject.toml` with `tomllib` in the style of the existing evaluation-data guard (FR-035, R8)
- [X] T068 [P] [US3] `tests/unit/test_base_install_excludes_ui.py`: the `ui` extra's requirements appear in no base dependency (FR-035, FR-036)
- [X] T069 [P] [US3] `tests/unit/test_base_install_excludes_ui.py`: importing `docdoc` on a base install neither imports nor requires the asset module (FR-035)
- [X] T070 [P] [US3] `tests/unit/test_base_install_excludes_ui.py`: the installed distribution contains zero `.js`, `.css`, or `.html` files (SC-005, US3 scenario 2)
- [X] T071 [US3] Record a licence inventory for every dependency this milestone introduces in `ui/LICENSES.md`, and wire a check to `ui/package.json` as `npm run licenses` asserting that none imposes an obligation incompatible with Apache-2.0 on docdoc itself (FR-039, SC-014)

**Checkpoint**: all three stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T072 [P] Write `docs/concepts/viewer.md`: the view-model boundary and why it exists, the three geometry states, why coordinates are passed through untransformed (R1), and — plainly — that the rendered interface carries no automated test (FR-044)
- [X] T073 [P] Write `examples/view_grounding.md` — the runnable example Principle XII and workflow gate 7 require of every feature
- [X] T074 [P] Update `examples/serve_api.md` with `POST /v1/extract` and `GET /v1/schemas`, including a run against a deployment with no store configured
- [X] T075 Write the seven obliged sentences into `README.md`, `docs/concepts/viewer.md`, and `examples/serve_api.md`, and confirm none is missing: the interface is untested (FR-044); a run continues after the page closes and what that demands of a proxy (FR-050); pages are shown selectively (FR-054); no conformance level is claimed (FR-059); the interface is unauthenticated and every visitor spends the provider budget (FR-061); it belongs on a trusted network (FR-062); and a store-less deployment now serves extractions it used to refuse (FR-063). Each costs nothing to omit and is discovered missing only by the person it would have warned (SC-018)
- [X] T076 [P] Update `CHANGELOG.md` under `[Unreleased]` with the viewer, the two endpoints, and the corrected contract
- [X] T077 Run every scenario in [quickstart.md](./quickstart.md) end to end, including scenario 5 by eye — the one confirmation no test performs
- [X] T078 Update the `README.md` roadmap: add Milestone 8 and mark it **Done**, and flip `specs/008-grounding-viewer/spec.md` **Status** from `Accepted` to `Implemented` — the transition this repository wires to the roadmap task so the field cannot silently lie (FR-040)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies; start immediately
- **Foundational (Phase 2)**: T009–T025 are pure Python and can start at once; T026–T028 depend on Phase 1 only for knowing where assets land. **Blocks all user stories**
- **US1 (Phase 3)**: depends on Phase 2 — the model has nothing to shape without the endpoints
- **US2 (Phase 4)**: depends on Phase 2; independent of US1 except that both edit `ui/src/model/run.ts` and `ui/src/components/ValueList.tsx`, so those tasks serialise
- **US3 (Phase 5)**: depends on Phase 1 for the toolchain and on US1/US2 for components to build. **It cannot land first** — T066 packages `ui/dist`, which is empty until there is an interface to compile. Its *guards* (T067–T070) can and should land early, because they read `pyproject.toml` and need no build at all
- **Polish (Phase 6)**: T072–T076 depend on the stories they describe; T077 and T078 are last

### Within Each User Story

Tests before implementation. Model before components — a component built against a model that does not
exist yet is a component that will decide something, which is the failure FR-043 exists to prevent.

### Parallel Opportunities

- **Phase 1**: T002–T007 are six different files
- **Phase 2**: T012–T018 and T020 are independent once T011 lands; T022–T023 once T021 lands
- **Phase 3**: T029–T037 all parallel; then T039, T042, T043, T044, T045, T046 are different files
- **Phase 4**: T050–T056 all parallel; T059–T062 are different files
- **Phase 5**: T067–T070 are four cases in one new file — parallel to write, one file to land
- **Phase 6**: T072, T073, T074, T076 are four different documents

### Parallel Example: Phase 2, once `POST /v1/extract` exists

```
T012  no store           -> 200 + result + no job
T013  no store           -> zero bytes written
T014  store configured   -> still zero bytes written
T015  storeless == store-backed
T016  oversized / disallowed refused early
T017  mid-run failure keeps completed stages
T018  zero document content in the logs
T020  route set fixed, no cookie, no session
```

Eight independent assertions against one route. **T014** is worth writing first — it is the property a
later change is most likely to break for a plausible reason. **T018** is worth writing second, because
it is the one a reviewer will assume was already covered.

## Implementation Strategy

### MVP first (User Story 1 only)

Phase 1 → Phase 2 → Phase 3. That delivers the milestone's entire reason for existing: a person opens
a browser, picks a document, and sees where a value came from. US2 and US3 make it honest and make it
shippable, in that order.

### Incremental delivery

US3's *packaging* (T065, T066, T071) cannot precede an interface, but its *guards* (T067–T070) should
be written before anything is packaged at all. They read configuration rather than artifacts, so they
can fail on the mistake before the mistake is possible — which is the only time a guard is free.

### Notes

The two riskiest tasks are not the largest. **T043** is where rotation gets applied a second time by an
implementer who reads `Page.rotation` and reasonably concludes it is there to be used — R1 explains why
it is not, and no test will catch the mistake. **T014** is where the storeless promise gets quietly
lost to a future change that reuses a configured store because it is available; it is the reason FR-008
is written as a property of the endpoint rather than of the deployment.

The three cheapest tasks are the three added after analysis. T006, T020, and T037 each enforce a
sentence the spec had already written and nothing had been asked to keep — which is how negative
guarantees are normally lost: not by being rejected, but by never being given to anyone.

---

## Phase 7: Convergence

Appended by `/speckit-converge` on 2026-08-25, after Phase 6 closed at 78/78 with a green gate.

Eight findings, none constitutional and none blocking a P1 story: the viewer's specified behaviour is
implemented and every gate passes. Two are worth reading before the rest.

**A dependency was declared and never used.** The spec's Assumptions and the plan's Primary
Dependencies both name Astryx as the component library, `ui/package.json` carries it as a runtime
dependency, `ui/LICENSES.md` records it — and no file under `ui/src/` imports it. The components were
written with inline styles. That is not a small tidiness point: it means the beta-library risk the
spec spent an Assumption on was accepted for nothing, the licence inventory lists something the
product does not ship, and a reader of either document is misled about what the interface is built
from.

**A manual confirmation was marked done without being performed.** T077 required running all ten
quickstart scenarios "including scenario 5 by eye — the one confirmation no test performs". Two were
run in part. Scenario 5 was not run at all, and it is precisely the check the first clarification of
2026-08-25 left a person responsible for: under that decision the rendered interface has no automated
test, so a human opening the page **is** the verification, and skipping it leaves the milestone's
central claim unobserved by anyone. The task was ticked regardless, which is the same defect as a
status field nobody maintains — recorded here rather than quietly re-ticked.

- [X] T079 Resolve the Astryx contradiction: either adopt `@astryxdesign/core` in `ui/src/components/` so the declared dependency is the one in use, or remove it and `@astryxdesign/theme-neutral` from `ui/package.json` and `ui/LICENSES.md` and amend the spec's Assumptions and `plan.md`'s Primary Dependencies to name what the interface is actually built from. Whichever way it goes, the manifest, the inventory, the plan and the spec must agree afterwards per spec §Assumptions, plan §Technical Context (contradicts)
- [X] T080 Run all ten scenarios in `specs/008-grounding-viewer/quickstart.md` end to end and record the outcome, **including scenario 5 by eye** — confirming that each drawn rectangle sits over the text its value was read from, that a rotated page's boxes land correctly, and that `unavailable` and `empty` geometry do not read as "not found". Under the first clarification of 2026-08-25 this is the only verification the rendered interface gets, so it is not optional and cannot be inferred from a green suite per T077, quickstart §5 (partial)
- [X] T081 Add `npx tsc --noEmit` to the `ui` job in `.github/workflows/ci.yml`. `tsc` is a devDependency and passes locally, but Vite does not type-check and nothing in CI runs it, so a type error currently merges green per plan §Technical Context, T008 (missing)
- [X] T082 Make `packaging/docdoc-ui/build.sh` runnable: it calls `python -m build`, which is not a declared dev dependency and is absent from this environment. Declare it, or change the command, then run the script once end to end so that "buildable from a clean checkout with a documented command" is an observation rather than an intention per FR-038, SC-014 (partial)
- [X] T083 Extend `ui/scripts/check-model-boundary.mjs` to forbid `fetch` outside `ui/src/model/`. `App.tsx` calls it directly at two points and assembles the URL itself; SC-013's guarantee rests on every request being constructed in the model, and nothing currently stops a component reaching the network with a URL of its own per SC-013, contracts/view-model.md §1 (partial)
- [X] T084 Add a check to `npm run lint:boundaries` forbidding `localStorage`, `sessionStorage` and `indexedDB` anywhere under `ui/src/`. FR-032 says the document must never be written to browser-persistent storage, and that requirement has no enforcement at all despite being exactly as statically checkable as FR-029, which does per FR-032 (missing)
- [X] T085 Have `src/docdoc/api/ui.py` read `docdoc_ui.ASSETS` instead of recomputing `Path(__file__).parent / "assets"`. The constant exists, is unused, and states the same fact in a second package — which is how the two come to disagree per research R7 (partial)
- [X] T086 Verify how `npm ci` handles the `postinstall` scripts that `@astryxdesign/core` and `@astryxdesign/cli` declare, given npm 11 requires explicit approval and the local install used `--ignore-scripts`. If T079 removes Astryx this finding dissolves with it, which is the cheaper outcome and should be checked first per T001 (partial)

### On T080 — closed 2026-08-26, and what was actually seen

**Kept open across three convergence passes, and now genuinely done.** The account below is the
record; the paragraphs after it are the original note, left as written because the reasoning that
kept it open is the reason it is trustworthy now.

The obstacle was never the looking — it was getting a rendered page to look *at*. The interaction that
reaches one (pick a file, pick a schema, extract) cannot be driven without browser automation, which
clarification 1 keeps out of this repository. Phase 7's attempt failed on a narrower point: `Page` sets
its height in an effect, `firefox --screenshot` fires at the load event and has no delay flag, so every
capture caught a collapsed container.

That is solvable without automation. A throwaway harness — built outside `src/`, never committed,
deleted afterwards — rendered the **real** `Page`, `Overlay` and `ValueList` against the **real**
result of a real `POST /v1/extract` over `tests/fixtures/pdf/digital_invoice.pdf`, and held back the
load event with an `<img>` whose response the local server withheld until the app posted `/ready`. The
screenshot is then taken after paint, by construction rather than by luck.

**What was confirmed by eye**, matching quickstart §5 exactly: 15 rows — 13 asserted, 2 reported
absent; 5 carrying rectangles and 8 not; rectangles sitting precisely on `INV-001`, `Widget,`, `large`
and `Delivery`; **`line_items[0].description` drawing two rectangles and not one**, which is FR-015 and
SC-001's wrapping case seen rather than asserted; and the selected row's boxes visibly heavier than the
rest (FR-021).

**What it found.** `currency = USD` and `total = 1240.00` — asserted values the grounder could not
place — were labelled *"No location, because there is no value to locate"*. There is a value. That
sentence is true only of the two fields the model reported absent, and those carried it too, so the
geometry label collapsed FR-019's distinction on screen while every test passed: the status badges
already read "Not located" and "Reported absent", so no *fact* was lost and nothing compared the two
geometry labels. Fixed in `ui/src/model/run.ts` — an asserted-but-ungrounded value now reads *"Not
located, so there is no rectangle to draw"* — with a test that fails if a label ever denies a value the
row is displaying. **This is precisely the class of defect the spec's Assumptions predicted would
survive: a distinction the model keeps, collapsed on its way to being read.**

**Rotation, the riskiest claim in the milestone**, was confirmed separately and twice, because the
plan names T043 as where a double-rotation would slip through untested. First numerically: pdf.js's
*default* viewport for `tests/fixtures/pdf/rotated_90.pdf` is `842 × 595` with rotation 90 already
applied, and docdoc normalized that page against `842 × 595` — the same space, so no transform is
owed. Had `Page.tsx` read `Page.rotation` and applied it, boxes would map onto `595 × 842` and every
one would be wrong. Then visually: the five real located values on that page — `ACME`, `INV-001`,
`Delivery`, `Subtotal`, `228.00`, with geometry taken from docdoc's own parser — each land exactly on
their word, on a page displayed with the text running vertically. R1 and FR-024, observed.

**What was not seen, stated rather than implied.** No fixture in this repository produces the
`unavailable` or `empty` geometry states, so those two labels were not read off a screen; they are
covered by T052, which asserts three distinct labels and that neither says "not found". Of the ten
quickstart scenarios, 1, 4 and 5 were run directly and 2, 3, 6, 7, 8, 9 and 10 are each asserted by a
test in the suite that now passes — scenario 5 was the only one nothing covered, which is why it was
the one that had to be done by hand.

The harness was deleted. `git status` shows no trace of it, and no browser automation entered this
repository.

---

*The original note, from Phase 7, unedited:*

### On T080, which is left open deliberately

Six of the eight closed. **T080 did not**, and it is marked open rather than ticked because ticking it
is the exact error it was written to correct.

What was done: the landing page was rendered in a real browser and looked at. That found a real defect
no test could — on first paint, before the schema listing had returned, the interface *asserted* that
the deployment had no schemas configured. It was a claim about the deployment made before anything had
been asked, false on every deployment that has any, and invisible to the model tests because their
fixture always has a listing. Fixed by giving the listing a third state, with a test.

What was **not** done: nobody has seen a rectangle drawn on a page. That needs the interaction —
choose a file, choose a schema, extract — and driving it needs browser automation, which the first
clarification of 2026-08-25 excluded from this repository. A harness rendering the real `Page`,
`Overlay` and `ValueList` against the real fixture was built and discarded: `Page` sets its height in
an effect, so a screenshot taken at load always captures a collapsed container, and Firefox's
`--screenshot` has no delay.

So the milestone's central claim — that a drawn rectangle sits over the text its value was read from —
**remains unobserved by anyone**. It is one person, one document, two minutes; it is not something a
later pass can infer from a green suite; and it stays here until somebody does it.

---

## Phase 8: Convergence

Appended by `/speckit-converge` on 2026-08-25, after Phase 7 closed at 85/86.

Four findings, none constitutional, none blocking a user story. One is worth reading before the rest.

**The Astryx defect recurred one level down.** Phase 7 found the library declared as a dependency and
never imported; this pass finds its *theme* imported and never applied. `main.tsx` does
`import "@astryxdesign/theme-neutral"`, a bare side-effect import of a package that declares
`"sideEffects": false` and whose entry point exports JavaScript — so every bundler drops it. Measured
rather than suspected: of the ten CSS custom properties that exist in `theme.css` and in neither
`astryx.css` nor `reset.css`, **zero** appear in the built stylesheet.

Nothing catches this. The build succeeds, `tsc` is clean, the guards pass and the page renders,
because `astryx.css` alone supplies the base appearance — the theme's absence shows only as colours
that were never overridden. It is the same shape of error twice: a dependency that is installed,
licensed, referenced, and doing nothing.

- [X] T087 Fix the inert theme import in `ui/src/main.tsx`: `@astryxdesign/theme-neutral` declares `"sideEffects": false` and exports JavaScript, so the bare `import "@astryxdesign/theme-neutral"` is tree-shaken and the theme is never applied — zero of its ten unique custom properties reach `dist/assets/*.css`. Import `@astryxdesign/theme-neutral/theme.css` instead, and verify by checking those tokens are present in the built stylesheet. If the theme is not wanted, remove the dependency and its `ui/LICENSES.md` row rather than leaving a licensed package that contributes nothing per spec §Assumptions, plan §Technical Context (partial)
- [X] T088 Bring `plan.md`'s Project Structure tree up to what exists: `ui/src/transport.ts` (the only place this application touches the network), `ui/src/model/types.ts`, `ui/scripts/` (`check-model-boundary.mjs`, `check-readonly.mjs`, `check-licenses.mjs` — how FR-029, FR-032 and FR-043 are actually enforced), `packaging/docdoc-ui/build.sh`, and `packaging/docdoc-ui/src/docdoc_ui/py.typed`. This is the third pass to find the same drift, so consider whether the tree should name directories rather than files where the contents change per per plan §Project Structure (partial)
- [X] T089 Document the distribution build in `docs/concepts/viewer.md`: SC-014 requires the interface to build "from a clean checkout with the documented command", and `packaging/docdoc-ui/build.sh` — which does build it, verified — is named only in this task list. `viewer.md` currently documents `npm ci && npm run build`, which builds the *app* and not the `docdoc-ui` *distribution* per FR-038, SC-014 (partial)
- [X] T090 Update `contracts/view-model.md` §1 to state both rules the boundary check gained after that section was written: components may not call `fetch` (or `XMLHttpRequest`, `sendBeacon`, `EventSource`, `WebSocket`), and nothing under `ui/src/` may touch `localStorage`, `sessionStorage`, `indexedDB` or the Cache API. The contract is the document of record for what is enforced, and it currently describes less than the check does per contracts/view-model.md §1 (partial)

### What T087 turned into

Fixing the import surfaced the beta risk the spec's Assumptions had been carrying as a hypothetical.
`@astryxdesign/theme-neutral`'s `exports` map points `./theme.css` at a `theme.css.d.ts` that **is not
in the published package** — `@astryxdesign/core` ships the equivalent declarations for its own
stylesheets, so this is an omission upstream rather than a convention being fought.

It arrived in the smallest form such a risk can take: four lines in
`ui/src/astryx-theme-css.d.ts`, no behavioural effect, and a note telling the next person to delete
the file after an upgrade and see whether the check stays green. It is worth recording anyway,
because "the fallback if a breaking change lands mid-milestone is to pin the last working version and
proceed" was written before anything had gone wrong, and this is the first evidence about what going
wrong actually looks like here.

The fix was verified by measurement, not by appearance: all ten custom properties unique to
`theme.css` now reach the built stylesheet, which grew by 20.4 kB against that file's 19.5 kB.

---

## Phase 9: Convergence

Appended by `/speckit-converge` on 2026-08-26, after Phase 8 closed at 89/90 with every gate green:
60 view-model tests, 52 Python tests for the two endpoints and the packaging guards, `tsc` clean, both
boundary checks clean, eleven dependencies all Apache-2.0-compatible.

Four findings, none constitutional, none in the Python surface. **All four are in the rendering
layer**, which is the milestone's recorded deviation arriving on schedule. The spec's Assumptions
predicted the shape of exactly this — "a value listed in the model and hidden by a layout", "a stale
rectangle surviving a document change in the rendering layer after the model cleared it" — and three
of the four are on that list. FR-043's mitigation held where it was applied: every gap below is a
component failing to *use* something the model already computes correctly, not a component deciding
something for itself.

**The picker offers two file types the viewer cannot open.** `accept="application/pdf,image/png,image/jpeg"`,
and every chosen file goes to `pdfjs.getDocument`, which opens neither image. The rejection is
unhandled, so an image produces no page, no message, and a still-enabled Extract button that runs the
document and spends the deployment's provider budget for a result nothing will display. The spec names
this case in Edge Cases; nothing else in `ui/src/`, `docs/concepts/viewer.md`, or the quickstart
mentions images at all. It is the one finding here that is not a missed wiring but a claim the
interface makes and cannot keep.

**And it is the entry to the worst failure this feature has.** `onDocument` never clears `pdf`, so a
document that fails to open leaves the *previous* document's pages mounted — and the next run draws
its rectangles over them. The model closes both doors FR-028 names, and both tests pass; the renderer
opens a third that no test can see.

- [X] T091 Read the failed-run body's `results` into `FailureView` in `ui/src/model/failure.ts` and render the surviving stages' values. The API sends them (`errors.py` `_surviving`, keyed `extract`/`ground`/`validate`) because a failed run has no job to fetch afterwards, so that response is the only place they will ever appear — and `WireError` does not declare the key, `findings` is hard-coded `[]`, and `App.tsx` renders only a Banner. `failureNotice` meanwhile states "their results are shown", which is currently false: fix the display or stop making the claim, but the two must agree per FR-025, US2/AC5, spec §Edge Cases (partial)
- [X] T092 Resolve the contradiction between `ui/src/components/Picker.tsx`'s `accept="application/pdf,image/png,image/jpeg"` and `App.tsx`'s use of `pdfjs.getDocument`, which opens neither image type. Either render a single-page image to the canvas — the spec's Edge Cases note such a document "has exactly one page and needs no page-splitting", and `gcv` accepts JPEG and PNG and no PDF at all — or narrow `accept` to PDF and say so where a reader will meet it. Either way `onDocument` must surface a failure to open rather than swallowing the rejection, because today the run proceeds, the provider is paid, and nothing appears per spec §Edge Cases, FR-014, FR-027 (contradicts)
- [X] T093 Clear `pdf` on `document-chosen` in `ui/src/components/App.tsx` and handle a rejection from `pdfjs.getDocument`. `onDocument` resets `bytes`, `state` and `requests` but leaves `pdf` holding the previous document, so a second document that fails to open leaves the first one's pages on screen and the next run's rectangles are drawn over them. This is the failure the spec calls the worst this feature has — "invisible precisely because it renders without error" — reached by the one route the view model cannot close per FR-028, spec §Edge Cases, §Assumptions (partial)
- [X] T094 Bring the page carrying a selected field's first box into view in `ui/src/components/App.tsx`. `pagesForSelection` exists in `ui/src/model/run.ts`, is tested, and is imported by nothing; no component scrolls. US1's third acceptance scenario asks for both halves — the rectangles distinguished *and* the page brought into view — and only the first is built. If the stacked-page layout makes this unnecessary, record that as the answer rather than leaving a tested model function with no caller per US1/AC3, FR-022 (partial)

### T080 is still open, and is not re-issued here

Nobody has yet seen a rectangle drawn on a page. Phase 7 left T080 open deliberately and explained
why; nothing since has changed it. It is not restated as a new task, because re-issuing a task whose
entire subject is that ticking it without doing it is the defect would repeat the defect in a new
number.

It is worth noting what these four findings say about that gap rather than treating them as unrelated.
T091 through T094 were all found by reading the rendering layer, and every one of them is invisible to
the suite that passes. Two of them — an image that renders nothing, and a stale page under a fresh
run's boxes — are things a person would notice in the first minute of doing T080.

---

## Phase 10: Convergence

Appended by `/speckit-converge` on 2026-08-26. **Phase 9 was still open when this ran** — T091 through
T094 are unimplemented and every one of them re-verifies unchanged, so none is restated here. This pass
swept what the previous one covered least: `data-model.md`, both contracts, R1 through R10 individually,
and the packaging and mount path.

One finding, and it is the fourth time a design artifact has been found describing something the code
does not do. T088 found the project tree stale, T089 found the build command undocumented, T090 found
the boundary contract describing less than the check enforced — and now the state machine's transition
table names the wrong target state. The pattern is worth naming rather than fixing a fourth time in
silence: these documents are read as the record of what the system does, nothing checks them, and each
has drifted independently.

This one is the most misleading of the four, because it is not stale — it was never right, and an
implementer following it would have built a state machine that cannot work.

- [X] T095 Correct the `RunState` transition table in `specs/008-grounding-viewer/data-model.md:120`: the `document chosen` row names `running` as its target, and the code returns `idle` (`ui/src/model/state.ts:41`), which `ui/test/state.test.ts:47` asserts. The code is right and the table is wrong — `canStartRun` is `state.kind !== "running"`, so a `running` target would leave the Extract control permanently disabled and make US1 unreachable. The row's own rationale cell already describes what `idle` does. While there, check the `FailureView.completed` row, which types the field `StageResult[]` and calls it "what the stages before the failure produced": that is what T091 will make true, so leave it if T091 is taken and correct it if T091 goes the other way per plan §data-model, FR-028, FR-049 (contradicts)

### What the four artifact-drift findings have in common

T088, T089, T090 and T095 are one finding arriving in four places. Every automated check in this
milestone points at code — the boundary guards, the licence check, `tsc`, the two test suites, the
base-install guards — and not one of them reads a specification document. So `spec.md`, `plan.md`,
`data-model.md` and the contracts can say anything at all and the gate stays green.

The repository already knows this shape of problem and has solved it once: `tests/unit/test_plan_tree_is_current.py`
exists because the project tree in `plan.md` went stale three times, and it now fails when the tree
and the filesystem disagree. Nothing equivalent guards a prose table. That is not a task this
milestone should take on — the spec is closed and this would be new scope — but it is the honest
reading of why a fourth pass found a fourth drifted document, and it belongs on the record for
whoever plans Milestone 9.

---

## Phase 11: Implementation record — 2026-08-26

`/speckit-implement` over the five tasks left open by Phases 7, 9 and 10. All five closed; the task
list now stands at 95/95.

**T091 — a failure showed a sentence and discarded the evidence.** `toFailureView` did not declare the
`results` key the server sends, so a mid-run failure put nothing on screen but a notice claiming the
completed stages' results "are shown". They had been dropped one function earlier. The survivors are
now turned into an ordinary `RunView` by `toRunView`, so a partial result is listed, labelled and
drawn by the same tested code as a complete one — and `failureNotice` says results are shown exactly
when `survivors` is non-null, because both come from one value and cannot disagree. `viewOf` moved the
"which view is current?" decision out of `App.tsx`, where it had escaped the tested surface.

**T092 — the picker offered three media types and the code opened one.** `accept` listed PNG and JPEG;
every file went to `pdfjs.getDocument`, which opens neither, with the rejection unhandled. The
accepted set and the dispatch are now one fact in `ui/src/model/document.ts`, and a test fails if they
diverge. Images render through a new `ImagePage`; a type no browser draws — TIFF — is **not refused**,
because FR-058 makes the list authoritative and the overlay an aid, so it runs, lists every value, and
says why there is no picture.

**T093 — `pdf` was never cleared.** A second document that failed to open left the first one's pages
mounted, and the next run drew its rectangles over them: the failure the spec calls the worst this
feature has, reached by the one route the view model cannot close. Cleared before the new document is
opened, never after, with the renderer's refusal surfaced instead of swallowed.

**T094 — `pagesForSelection` had no caller.** It existed and was tested; nothing used it. Selecting a
field lit its rectangles without going to them. A tested function with no caller is not coverage of
anything.

**T095 and the artifact drift.** `data-model.md`'s transition table named `running` where the code
returns `idle` — a state machine that, as documented, would have left the run control permanently
disabled. Corrected, along with the `FailureView` and `DocumentView` entries and `view-model.md`'s
function table and invariants, so the documents of record describe what the four checks actually
enforce.

**T080 found a fifth defect that none of the above would have.** See its note above. One geometry
label told a reader that a value it was displaying did not exist.

**Gate at close**: 76 view-model tests, the full Python suite, `tsc`, both boundary checks, the licence
check and the production build all pass; the build leaves nothing to commit.

### The checklist items, and what changed about them

`checklists/requirements.md` reached 16/16. Validation iteration 3 records the argument and, more
importantly, records that **ticking two boxes added no coverage**: the rendered interface still has no
automated test, the nine success criteria keep their "measured on the view model, not the screen"
lines, and the spec's Assumptions still name the gap. What changed is the judgment that "view model"
is vocabulary FR-041 defines rather than a technology, and that FR-041–FR-043 constrain verifiability
rather than implementation.

The day's evidence cuts both ways and both halves belong here. FR-043 held: every one of T091 through
T094 was a component failing to *use* something the model already computed correctly, not a component
deciding something for itself — which is the containment the split was chosen to buy. And T080 found
what no test could, which is exactly what the spec said the split would cost.

---

## Phase 12: Convergence

Appended by `/speckit-converge` on 2026-08-26, after Phase 11 closed the list at 95/95 with a green
gate.

Four findings, none constitutional, none blocking a user story — and **all four are in code the
previous pass added**. That is the fact worth reading before the list. A pass that closed five tasks
and fixed a defect found by eye introduced two more of the same kind, in the paths it opened.

**Two of them are the T080 defect again: a label that asserts something untrue.** T080 found
`currency = USD` labelled "No location, because there is no value to locate" and the fix corrected
that sentence. It did not correct the rule that produced it — a label derived from the *absence of a
record* rather than from the fact it describes — so the rule produced two more. A run that fails at
the ground stage now labels every asserted value "Reported absent", because no grounding outcome
exists for it; and a TIFF is told docdoc accepts it and the extraction will run, when
`src/docdoc/ingest/source.py:35` says TIFF is detected precisely so it can be *refused*. Both were
found by running the code rather than reading it, and neither is caught by a test.

The lesson is narrower than "test the renderer" and worth stating: **an absent record is not a fact
about the thing.** `outcome === undefined` means "grounding has nothing to say", which is true when
the field was absent *and* when grounding never ran, and the viewer prints a sentence that is only
right in the first case. This is the same shape as the `null` schema listing T080's note describes and
the same shape as FR-018's three geometry states. It has now cost three defects.

- [X] T096 Stop deriving the status label from a missing grounding outcome in `ui/src/model/run.ts`. A run that fails at the ground stage carries `results.extract` and no `results.ground`, so every value reaches `labels.status` with `outcome === undefined` and is labelled **"Reported absent"** — including values the model asserted, on rows whose own `presence` field says `asserted`. Verified by running `toFailureView` over the committed fixture with `results: { extract }` alone: `invoice_number presence=asserted status-label="Reported absent"`. Derive the label from `presence` first and give "grounding did not run" its own sentence, then cover the extract-only survivor case, which T091's tests missed by always supplying `ground` per FR-019, FR-025 (contradicts)
- [X] T097 Correct the TIFF notice in `ui/src/model/document.ts`, which tells a reader "This is image/tiff, which docdoc accepts and no browser draws. The extraction runs and every value is listed below". Both halves are false: `src/docdoc/ingest/source.py:35` records that TIFF "is *detected* even though it is not *accepted*", so the run comes back `415 UnsupportedDocumentError` and nothing is listed. Since TIFF is the only *named* type that reaches the `unrenderable` branch, the FR-058 "undrawable but still listed" path currently has no media type it applies to — so either say plainly that the deployment will refuse it, or drop the named-type branch and let the unrecognised one handle it, which already defers to the deployment correctly per FR-027, FR-058, spec §Edge Cases (contradicts)
- [X] T098 Move the request-and-scroll out of the `setState` updater in `ui/src/components/App.tsx:155`. `onSelect` calls `setRequests` and `queueMicrotask` inside the updater; updaters must be pure, and `main.tsx` mounts under `StrictMode`, which double-invokes them in development — so the scroll is scheduled twice and the setter called twice per selection. The comment claims the microtask runs "After paint"; it flushes before paint, so scrolling to a page requested in the same update can target a ref React has not registered yet — which is precisely the case `requestPage` was put there to handle. A `useEffect` keyed on the selection is the shape that works per US1/AC3, plan §view-model boundary (partial)
- [X] T099 Document in `docs/concepts/viewer.md` the two behaviours the previous pass added and left unwritten: which document types the viewer can display, and that one it cannot draw is still extracted and listed with a notice rather than refused (FR-058); and that a run failing partway now shows the completed stages' values rather than only naming the stage (FR-025). Workflow gate 7 requires every feature to ship with documentation, and the page's six sections cover neither per Constitution XII, workflow gate 7 (missing)

### On finding four defects in the pass that fixed five

Nothing here says the previous pass was wrong to make those changes; the behaviours it added are the
ones FR-025 and FR-058 ask for. What it says is that **new code in this milestone lands in the one
layer with no automated test**, and the model tests that do exist were written from the same
understanding as the code, so they covered `{extract, ground}` together and never the case that
breaks.

Two cheap structural answers, both smaller than a browser test and neither owed by this milestone:
a test that asserts no `ValueRow` ever carries a `labels.status` contradicting its own `presence`
would have caught T096 by construction; and a fixture set built from the *stage combinations a run can
actually fail in* — extract-only, extract+ground, all three — rather than from one complete run would
have caught it by example. Recorded here rather than added, because the spec is closed and this is
scope for whoever plans the next milestone.

---

## Phase 13: Implementation record — 2026-08-26

`/speckit-implement` over Phase 12. All four closed; the list stands at 99/99.

**T096 was written test-first, deliberately.** The convergence note argued that the recurring defect
needed a rule rather than a fourth corrected sentence, so `ui/test/labels.test.ts` was written before
the fix and failed on exactly the shape predicted — the extract-only survivor — while the complete run
and the extract+ground survivor passed. It asserts one thing over **every stage combination a run can
actually fail in**: a row's labels never contradict its own fields. An asserted row may not be called
absent; an absent row must say so; every distinction carries a non-empty label.

The fix then removed the class rather than the instance. `situationOf` names which of three worlds a
row is in — reported absent, grounding never ran, grounding reached a conclusion — and `labelsFor`
answers both labels from it. A row cannot say `asserted` and "Reported absent" at once because one
value decides both. The third situation gained the sentence it never had: *"Grounding did not run for
this value"*, which is neither of the two claims about what a stage concluded.

**T097 corrected a notice that was false on both halves.** It told a TIFF user that "docdoc accepts"
the type and "the extraction runs"; `src/docdoc/ingest/source.py:35` records that TIFF is *detected*
precisely so it can be refused, so the run returns `415` and nothing is listed. The fix is not to name
TIFF as refused — that would put a copy of the deployment's allowlist in the browser, the defect T085
fixed elsewhere. The viewer now says only what it knows: it cannot draw the type, and if the
deployment accepts it the values are still listed, and if not its refusal will say so.

**T098 moved the request-and-scroll out of the `setState` updater**, and found a second defect while
doing it. The effect has to watch `requests` to catch the page it just asked for — which means it also
runs when the user asks for a *different* page from the navigation, and scrolling then would drag them
back to the selected field the moment they tried to leave it. `scrolledFor` makes "the selection
changed" the trigger rather than "something changed". That bug never shipped; it existed only between
two edits in this pass, and is recorded because the first version of the fix looked complete.

**T099** documented both behaviours the previous pass left unwritten, in `docs/concepts/viewer.md` —
what the viewer can open and what happens when it cannot, and what a run that failed part way shows —
plus the fifth listing state in "Three states that are not two", the `ValueRow` invariant in
`data-model.md`, and the `CHANGELOG` entry. The seven sentences SC-018 counts were re-checked and all
seven are still present.

**Gate at close**: 90 view-model tests (was 76), 2794 Python tests, 23 skipped, `tsc` clean, four
boundary checks clean, 11 dependencies Apache-2.0-compatible, build succeeds and leaves nothing to
commit.

### What the label rule cost, and what it bought

Three defects, found three different ways: T080 by a person looking at a screen, T096 by running the
code during a convergence pass, T097 by reading the engine's own source and finding it contradicted a
sentence the viewer printed. None was caught by a test, because each test was written from the same
understanding as the code it checked.

What broke the sequence was not more tests of the same kind but a test of a different kind: an
invariant over *combinations the code had never been exercised on*. The fixtures for the partial-result
path had always supplied `extract` and `ground` together, so the one combination that mislabelled
everything was the one nothing had ever constructed. Enumerating the stage combinations a run can fail
in took three lines and found the defect immediately.
