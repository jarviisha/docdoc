---

description: "Task list for Milestone 11 — The Operations Console"
---

# Tasks: The Operations Console

**Input**: Design documents from `/specs/011-operations-console/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Not optional. Four of this milestone's success criteria are claims about what does **not**
happen — SC-004 (zero bytes at rest in the browser), SC-006 (zero forbidden words, zero forbidden
nouns), SC-007 (zero of another tenant's runs), SC-008 (zero responses distinguishing forbidden from
absent) — and a claim of that shape has no form other than a test or a guard. The guards come
**before** the code they guard, in Phase 2, for the reason `check-readonly.mjs` records about itself:
a fence built after the thing it fences is a fence around whatever was already built.

**Organization**: grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US5 from spec.md. Setup, Foundational, and Polish carry no label

## Path Conventions

Two trees, both existing: `src/docdoc/` for the API and `ui/` for the browser client, per plan.md's
Structure Decision. Tests at `tests/` for Python and `ui/test/` for the console model.

## The one task that must not drift

**T016 mounts the console without the credential dependency.** Research R1 is the reason and
`app.py:709` is the evidence: the viewer's gated mount means that with authentication on it *"does
not load"*, and a browser navigation cannot carry a bearer token. If T016 is written by copying the
viewer's mount, the console will pass every other test in this list and be unusable on every
deployment it was built for. T017 is the test that catches it.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: a second entry point in an existing tree. Nothing here is a new project.

- [X] T001 Create `ui/vite.console.config.ts` with `base: "/console/"`, `outDir: "dist-console"`, `emptyOutDir: true`, `sourcemap: false`, the same `@model`/`@components` aliases plus a `@console` alias for `./src/console`, and the same `/v1` dev proxy. Comment why it is a second config rather than a multi-page input: `base` is per-build and research R1 put the console on a path the viewer's base cannot reach
- [X] T002 Create `ui/console.html` as the console entry, loading `src/console/main.tsx`. Title names the deployment surface, not the product, so a browser tab full of consoles is distinguishable
- [X] T003 Add `build:console` to `ui/package.json` scripts (`vite build --config vite.console.config.ts`). Leave `lint:boundaries` alone for now; T013 rewrites it once the guards exist
- [X] T004 [P] Create `ui/src/console/model/` and `ui/src/console/components/` with a `README.md` in each naming which guard scans it and which requirement that guard enforces — the directories are load-bearing for `check-readonly.mjs` staying unchanged (contracts/console-guards.md §1)
- [X] T005 [P] Add `ui/dist-console/` to the repository root `.gitignore`, beside the existing `ui/dist/` entry — there is no `ui/.gitignore`, and `specs/008` FR-038's no-committed-build-output rule is enforced from the root file
- [X] T006 [P] Extend `packaging/docdoc-ui/pyproject.toml` to ship `dist-console` beside `dist` in the wheel
- [X] T007 [P] Add a `CONSOLE_ASSETS` constant to `packaging/docdoc-ui/src/docdoc_ui/__init__.py` beside `ASSETS`, so `docdoc.api.ui` reads the path rather than recomputing it — the two-copies-of-one-fact problem that module's docstring already records
- [X] T008 [P] Extend `ui/tsconfig.json` include paths to cover `src/console/**` and the new test directory

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the fences, the mount, and the single network path. **No user story work begins until
this phase is complete** — every story writes code that one of these guards is supposed to be
watching.

### The fences, first

- [X] T009 Create `ui/scripts/check-console-boundary.mjs` with **two rule sets**, both scanning `ui/src/console/` and both taking an optional directory argument so T014 can point them at a fixture:
  1. ADR-0019 §2's five nouns as identifier, type name, property, or user-visible string — the match list is contracts/console-guards.md §3 (FR-002, SC-006);
  2. result content — `claimed_text`, `claimedText`, `grounding`, `grounding_score`, `field_path`, `fieldPath`, `findings` — as a field, property, or rendered binding, plus any import addressing the result endpoint (FR-037, SC-016). SC-016 promises *"one check with no exception list"* and this is it; without this rule set that sentence names nothing. **`value` and `outcomes` are deliberately not on the list**: `value` is the prop of every controlled input this console has (T029, T054) and `outcomes` is what T061 legitimately calls a delivery's attempts. A guard that fails correct code is a guard somebody switches off, and switching this one off takes SC-006 with it.
  Include the `# ponytail:` note stating the ceiling: a word list catches drift, not deliberate evasion under a different name
- [X] T010 [P] Create `ui/scripts/check-console-a11y.mjs`, taking the same optional directory argument as T009 so T014 can drive it: fail on an interactive handler attached to a non-interactive element without both a role and a tab stop, and on an input-taking control with no accessible name. Document what it cannot see — focus order, contrast, live-region correctness — because SC-014a claims *reachable and named* and FR-045c claims *no conformance level*, and the gap between those two sentences is deliberate
- [X] T011 [P] Extend `ui/scripts/check-model-boundary.mjs` to scan `src/console/model` and `src/console/components` with the existing rules unchanged. The console has more to lose than the viewer did: it sends a credential, and a second `fetch` site is a second place that can fail to
- [X] T012 [P] Add a comment to `ui/scripts/check-readonly.mjs` stating why the editing scan is `src/components/` and not `src/**/components` — so a future author reading a console full of forms does not "fix" the guard by widening it. **Change no rule and no path**: the scan is already correct for FR-040, and the persistence scan already covers the console's credential
- [X] T013 Rewrite `lint:boundaries` in `ui/package.json` to run all four scripts, and make each print its own clean line, as the existing two do
- [X] T014 Create `ui/test/guards-can-fail.test.mjs`: feed each new guard a fixture that must trip it, in the tradition of `test_this_check_can_actually_fail`. A guard nobody has seen fail is a guard nobody knows works

### The mount, and the credential it does not have

- [X] T015 Extend `src/docdoc/api/ui.py` with a console asset lookup beside the viewer's, reusing the same three-candidate search (explicit setting, installed `docdoc-ui`, checkout `ui/dist-console`) and the same two absence sentences — a missing build and a missing distribution are fixed by different commands
- [X] T016 Mount the console in `src/docdoc/api/app.py` at `/console` **without the router's credential dependency**, with a `501` naming what is missing when the assets are absent. Write the reason in the code, not only here: a browser navigation carries no bearer token, the page's purpose is to accept a key after it loads, and gating the shell makes the console impossible rather than strict (research R1, plan Complexity Tracking)
- [X] T017 Create `tests/contract/test_console_mount.py`: `/console/` answers `200` with authentication enabled; `/v1/runs` answers `401` in the same deployment; `/ui/` still answers `401`, so Milestone 8's posture is provably unchanged (FR-022b). This is the test that catches T016 being written by copying the viewer's mount

### The single network path

- [X] T018 Extend `ui/src/transport.ts` with an optional credential that becomes an `Authorization: Bearer` header. It stays the only `fetch` in the tree, and it stays decision-free: the credential is passed in, never read from anywhere
- [X] T019 Create `ui/src/console/model/client.ts` with **its own** allow-list and its own `requestFor`. Do not add console intents to `ui/src/model/client.ts`: that file's two-path `ALLOWED` and `writesToStore()` are load-bearing for Milestone 8's SC-013, and widening them to shorten this code is the exact trade the boundary check exists to prevent (research R5)
- [X] T020 [P] Create `ui/test/console/client.test.ts`: every intent maps to an allowed path and method; an intent outside the allow-list throws; no intent constructs a URL containing the credential (FR-008)
- [X] T021 [P] Create `ui/src/console/model/failure.ts` reusing the viewer's distinction between *the deployment reported a failure* and *we never heard a usable answer*, and adding the third case this milestone needs: *the credential was not accepted* (FR-010, FR-039)
- [X] T022 Create `ui/src/console/main.tsx` and `ui/src/console/components/Shell.tsx` using Astryx `AppShell`/`SideNav`, rendering nothing but the credential prompt until a session exists. **Zero requests before a key is entered** (User Story 1 scenario 1) — the shell must not probe anything on mount
- [X] T022a Add a `build:console` step to the `ui` job in `.github/workflows/ci.yml`, between "It builds" and "The build left nothing to commit". Without it the console build breaks green and T006 packages a tree CI has never produced — the same class of gap the perf job at the bottom of that file was added to correct

**Checkpoint**: four guards green on an empty console, `/console/` serving a shell that asks for a
key and calls nothing, `/ui/` and every `/v1` route behaving exactly as Milestone 10 left them.

---

## Phase 3: User Story 1 — Get in with the key you already have, and leave nothing behind (Priority: P1) 🎯 MVP

**Goal**: an operator can open the console, paste a key, work, and leave nothing behind in the
browser — and the console tells them which authentication mode the deployment is in.

**Independent Test**: paste a key, perform an authenticated read, reload, confirm the console asks
again; then inspect every browser storage mechanism and find nothing. Needs **no** route from this
milestone — it reads `/v1/schemas` and probes `/v1/admin/credentials`, both of which already exist.

- [X] T023 [P] [US1] Create `ui/src/console/model/session.ts` holding the credential, the mode, the administrative flag, and `last_activity_at` per data-model.md. No identifier, no server-side record, nothing stored
- [X] T024 [P] [US1] Implement `expired(session, now)` in `ui/src/console/model/session.ts` as a pure function with the **15-minute** bound as a named constant (FR-009a, research R4). The shell supplies `now`; the model decides
- [X] T025 [US1] Implement mode discovery in `ui/src/console/model/session.ts`: attempt a read with no credential; success means the deployment is unauthenticated and the console says so; refusal means ask for a key (FR-011, research R6). Add no route for this — a route reporting the authentication mode is a second new read and an unauthenticated oracle about the deployment's posture
- [X] T026 [US1] Implement administrative discovery in `ui/src/console/model/session.ts`: one probe of `GET /v1/admin/credentials` per session; a `404` sets `administrative: false` and hides every administrative area (FR-028, research R7). Never translate that `404` into a permission message
- [X] T027 [US1] Implement credential discard on refusal in `ui/src/console/model/session.ts`: any response reporting the credential was not accepted returns the session to its pre-key state, distinguished from a transport failure (FR-010)
- [X] T028 [P] [US1] Create `ui/test/console/session.test.ts`: expiry at the bound and not before; discard on refusal; mode discovery for both deployments; administrative flag from the probe's two outcomes; a trailing newline on a pasted key is trimmed before use (Edge Cases)
- [X] T029 [P] [US1] Create `ui/src/console/components/CredentialPrompt.tsx` using a native input inside an Astryx `Field`, with an accessible name and no autofill of a credential into browser-managed storage (FR-007, FR-045a)
- [X] T030 [US1] Wire `ui/src/console/components/Shell.tsx` to the session: idle tick supplies `now`, expiry returns to the prompt, and the mode is stated on screen in both deployments (FR-011)
- [X] T031 [P] [US1] Create `tests/contract/test_credential_is_never_logged.py`: drive the API with a credential seeded with a distinctive string and assert it appears in zero log lines, zero error bodies, and zero response bodies (SC-005, FR-038). **The browser half of SC-005 is T020's**, which asserts no intent constructs a URL carrying the credential — a Python test cannot observe what a browser sends, and splitting the claim is what makes both halves checkable

**Checkpoint**: User Story 1 is complete and demonstrable on its own. An operator can sign in, be
told which mode the deployment is in, be signed out by the idle bound, and leave nothing behind.

---

## Phase 4: User Story 2 — See what ran, without opening a shell on the server (Priority: P1)

**Goal**: the run list and the run detail, and the one new read that makes them possible.

**Independent Test**: submit runs that succeed, fail, and are cancelled; confirm each appears, that
filtering returns exactly the matching ones, and that a second tenant's runs appear in neither.

### The route

- [X] T032 [P] [US2] Create `src/docdoc/api/paging.py` with the cursor codec: base64url over `{"t", "c", "i"}`, decode refusing a cursor whose tenant is not the caller's, with the same answer a malformed one gets (FR-016, research R3). Unsigned, and the docstring says why: a forged cursor can only name a position inside the forger's own tenant
- [X] T033 [P] [US2] Create `tests/unit/test_run_page_cursor.py`: round-trip; malformed input; foreign tenant; a cursor whose body is valid base64 but not the expected shape. All four produce one refusal, not four messages
- [X] T034 [US2] Add `page_runs()` to `src/docdoc/runs/postgres.py`: tenant as a query predicate, optional status filter, keyset `WHERE (created_at, run_id) < (…)`, `ORDER BY created_at DESC, run_id DESC` — the primary key is **`run_id`**, not `id`, and the schema identity column is **`schema_identity`**, not `schema`, `LIMIT`. Reuse `runs_by_tenant`; add **no** migration and **no** index. Carry the `# ponytail:` note from research R2 about the tiebreak sort
- [X] T035 [P] [US2] Add `RunSummary` and `RunListResponse` to `src/docdoc/api/models.py` with exactly the fields data-model.md lists, and a comment naming what is deliberately absent: stage outcomes, values, claimed text, and any error message carrying document content (FR-019)
- [X] T036 [US2] Add `GET /v1/runs` to `src/docdoc/api/app.py` per contracts/console-http-api.md: `status`, `limit` (default 50, max 200, `422` above it rather than a silent clamp), `cursor`. Removed runs are absent (FR-020); no administrative scope is required (FR-021)
- [X] T037 [P] [US2] Create `tests/contract/test_console_http_api.py`: the response shape; `422` on an unknown status and an oversized limit; `400` on a malformed and on a foreign cursor with identical bodies; `next_cursor` null on the last page and never an empty page with a non-null cursor
- [X] T038 [P] [US2] Create `tests/integration/test_run_listing.py`: two tenants over byte-identical documents see disjoint lists (SC-007); paging an unchanging set returns every run exactly once (SC-009); a run submitted between two pages never causes a repeat; a removed run is absent from the listing and still reachable by identity to its owner (FR-020)
- [X] T039 [P] [US2] Extend `tests/contract/test_no_existence_oracle.py` coverage to the new route, or assert in T037 that another tenant's run is absent from the listing for the same reason an unknown one is — the two must not become two behaviours (FR-014, ADR-0014 §3)
- [X] T039a Create `tests/contract/test_console_adds_one_read.py`, the test for the sentence this milestone defines itself by: the route table differs from Milestone 10's by **exactly one** entry, that entry is `GET /v1/runs`, and it accepts `GET` only (SC-003). In the same file assert the absences FR-009 claims — no route whose path or operation id contains `login`, `session`, `token`, `logout`, or `auth` — because FR-009 is the one absence claim in this spec that nothing else is watching, and an unwatched absence claim is the failure mode every guard in this repository exists to answer

### The surface

- [X] T040 [P] [US2] Create `ui/src/console/model/runs.ts`: the listing state, the `cursor_stack` so "back" is a pop, and the three-way loading/loaded/failed state that keeps an empty list distinguishable from a failed request (data-model.md, Edge Cases)
- [X] T041 [P] [US2] Create `ui/test/console/runs.test.ts`: paging forward and back over a fixture; a stale cursor returns to the first page; an empty tenant produces the empty state and not a filter message
- [X] T042 [US2] Create `ui/src/console/components/RunList.tsx` with Astryx `Table`, `Badge`/`StatusDot` for state with a **text label** beside any colour, `Pagination`, and `EmptyState`. Keyboard-operable filters and paging (FR-045a)
- [X] T043 [US2] Create `ui/src/console/components/RunDetail.tsx`: state, timings, routing outcome **only when present** — absence and not an empty section, matching the API's own decision to omit rather than send null (FR-022) — and a link to the result representation. Render no part of a result, derive no figure from one (FR-022a, SC-016)
- [X] T044 [US2] Handle the `410` tombstone in `ui/src/console/components/RunDetail.tsx`: removal time and policy, and nothing else about what it held (FR-023). A `404` and a `410` read differently on screen because they mean different things
- [X] T045 [P] [US2] Handle the no-database deployment in `ui/src/console/components/RunList.tsx`: the console reports that runs are not available here rather than presenting an empty list that reads as "nothing has been submitted" (Edge Cases)

**Checkpoint**: an operator can find a named failed run and read why. User Stories 1 and 2 together
are the milestone's usable core.

---

## Phase 5: User Story 3 — Revoke a leaked key from wherever you happen to be (Priority: P1)

**Goal**: issue, list, and revoke credentials from the browser, through Milestone 10's routes and no
others.

**Independent Test**: issue a credential through the console, use it, revoke it through the console,
and confirm it stops being accepted within the documented propagation bound.

- [X] T046 [P] [US3] Create `ui/src/console/model/credentials.ts`: issue, list, revoke, each mapping to a Milestone 10 route. Add no credential route, no second issuance path, no storage of an issued key beyond the single disclosure (FR-024, FR-027)
- [X] T047 [P] [US3] Create `ui/test/console/credentials.test.ts`: the disclosure is dropped on acknowledgement; no listing row ever carries a key or a fragment; a revocation repeated is a success and not an error (FR-012, FR-025, FR-026)
- [X] T048 [US3] Create `ui/src/console/components/CredentialList.tsx`: identity, tenant, scope, issuance time, revocation state. Never a key
- [X] T049 [US3] Create `ui/src/console/components/IssuedKey.tsx`: shows the key exactly once with the warning that it will not be shown again, and removes it from memory on acknowledgement (FR-024)
- [X] T050 [US3] Implement the self-revocation warning in `ui/src/console/model/credentials.ts`: once per session, before the first revocation, stating that a revocation may include the key in use. Do **not** identify which listed credential is the console's own — that needs a route returning the caller's credential identity, which is the second new read SC-003 forbids (FR-026a)
- [X] T051 [P] [US3] Extend `tests/contract/test_admin_surface_is_invisible.py` coverage, or assert in `tests/contract/test_console_http_api.py`, that a non-administrative principal receives byte-identical responses to a route that does not exist on every administrative route the console calls (SC-008, ADR-0016 §6)

**Checkpoint**: a leaked key can be revoked from a phone.

---

## Phase 6: User Story 4 — Erase a customer's data, on purpose and not by accident (Priority: P1)

**Goal**: tenant and document erasure, with a confirmation that makes an accident hard.

**Independent Test**: erase a tenant through the console with a second tenant holding byte-identical
documents, and confirm the second tenant's runs still resolve and still reuse their artifacts.

- [X] T052 [P] [US4] Create `ui/src/console/model/erasure.ts` with `ErasureIntent` and the derived `armed` flag — `typed === target`, computed **in the model**. This comparison is one `===` away from being decorative and is the only thing between a mis-click and a tenant's data (FR-030)
- [X] T053 [P] [US4] Create `ui/test/console/erasure.test.ts`: a single differing character leaves the action unavailable; the intent is discarded when the credential is; a target pasted into the confirmation field that is not the target fails closed (Edge Cases)
- [X] T054 [US4] Create `ui/src/console/components/EraseDialog.tsx` using Astryx `AlertDialog` with a native input, keyboard-reachable, accessible-named, and focus-returning (FR-045a, FR-045b)
- [X] T055 [US4] Report the counts the deployment returned, as it returned them, in `ui/src/console/components/EraseDialog.tsx`. The console computes, sums, and estimates nothing (FR-031)
- [X] T056 [US4] Report in `ui/src/console/components/EraseDialog.tsx` an erasure of a nonexistent target as having succeeded and removed nothing, matching the route's idempotent behaviour rather than presenting it as a failure (FR-032)
- [X] T057 [P] [US4] Assert in `ui/test/console/erasure.test.ts` that the model exposes no multi-target or bulk erasure path at all (FR-033) — absence asserted, not merely unimplemented
- [X] T058 [P] [US4] Create `tests/integration/test_console_erasure_scope.py`: erase one tenant through the same routes the console calls, with two tenants holding byte-identical documents, and confirm the survivor's runs resolve and its artifacts remain reusable (SC-011's neighbour, already guaranteed by Milestone 10 and worth re-asserting through this path)

**Checkpoint**: every P1 story is complete. This is a shippable console.

---

## Phase 7: User Story 5 — Find out why a callback never arrived (Priority: P2)

**Goal**: the delivery record, reachable without a database client.

**Independent Test**: register a destination that fails every attempt, run a document through, and
confirm the console shows the attempts and the final at-rest state.

- [X] T059 [P] [US5] Create `ui/src/console/model/delivery.ts` reading `GET /v1/runs/{run_id}/delivery` and modelling the three states a run can be in: no destination registered, attempts in progress, delivery at rest
- [X] T060 [P] [US5] Create `ui/test/console/delivery.test.ts`: a run with no destination produces the "none registered" state and not an empty table; no field of the model can carry a signing secret (FR-034, FR-035)
- [X] T061 [US5] Create `ui/src/console/components/DeliveryRecord.tsx` showing attempts, outcomes, and the at-rest state, and offering no re-delivery, no cancellation, and no destination editing (FR-036)

**Checkpoint**: all five stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T062 [P] Add the Milestone 11 row to the README roadmap, and **flip `specs/011-operations-console/spec.md` Status to `Implemented`** — the transition the spec's own status vocabulary says rides on this task
- [X] T063 [P] Add a section to `README.md` for the console beside "The browser viewer": what it does, that it is **not** a review platform and holds no per-reviewer state (FR-046), that a reload and a 15-minute idle both require the key again and why (FR-047), and that single sign-on is an authenticating proxy the operator runs and docdoc ships no part of (FR-048)
- [X] T064 [P] State in `README.md` that no accessibility conformance level is claimed (FR-045c), in the same place the viewer's equivalent sentence lives
- [X] T065 [P] Document in `README.md` the unauthenticated `/console` mount and its bounded exposure — static assets, no tenant data, zero requests before a key — in the README's deployment guidance, so an operator meets it in documentation rather than in a scan report
- [X] T066 [P] Add the CHANGELOG entry for Milestone 11, naming what it does **not** add as prominently as what it does, in the shape Milestones 9 and 10 used
- [X] T067 Run `uv run pytest tests/contract/test_protected_layers_untouched.py` and confirm zero files under `kernel/`, `ingest/`, `extraction/`, `grounding/`, `validation/` (SC-002). **Do not modify the test** — it was written by Milestone 10 without knowing this milestone would be measured against it, which is what makes it evidence
- [X] T067a Run the public-tier golden set with `docdoc eval` and compare the report against the same command on `main`: field accuracy, coverage, missing rate, incorrect rate, and grounding rate must be **bit-identical** (SC-001). T067's diff read is evidence that nothing which could move a metric was touched; this is evidence that none moved. Milestone 10 ran both and this milestone claims the same thing
- [X] T068 Run `uv run pytest tests/contract/test_no_review_platform.py` and `git diff --stat main -- tests/contract/test_no_review_platform.py`. The first passes, the second prints nothing (FR-003, SC-006)
- [X] T069 [P] Run `npm --prefix ui run licenses` and confirm zero new obligations (FR-045, SC-014)
- [X] T070 [P] Confirm `docdoc migrate --check` reports nothing to apply: this milestone adds zero migrations, and a migration wanting to run means something exists that the plan says should not
- [X] T071 [P] Confirm the composition still has four containers and four process types, and that the offline suite passes with no database, no object store, and no browser (SC-014, FR-043, FR-044)
- [X] T072 Run every scenario in [quickstart.md](./quickstart.md) end to end against the four-container composition, **ticking each of the ten individually**, and correct what it gets wrong. Three success criteria have no other coverage and are named here so they cannot be lost inside a single checkbox: **scenario 0 is FR-005's only check** (the API starts and serves with the console's assets absent), **scenario 5 is SC-010's** (a revoked credential refused within the propagation bound), and **scenario 7 is SC-017's** (an unauthenticated deployment is fully reachable with no key entered). Milestone 10's equivalent pass found eleven errors in a document that read perfectly; this one has not been run at all and says so in its header
- [ ] T073 Perform the SC-015 operator test: hand someone a key and the URL with a named failed run in the list, and time them to "here is why it failed". Under three minutes, no documentation open. Record what it found — this is the only check with a human in it and the only one that can find a status column that is a colour with no label

---

## Dependencies & Execution Order

### Phase dependencies

```text
Phase 1 (Setup)
  └─> Phase 2 (Foundational)  ← T009–T014 guards and T015–T017 mount gate ALL story work
        ├─> Phase 3 (US1) ────────────────┐
        ├─> Phase 4 (US2) ────────────────┤
        ├─> Phase 5 (US3) ────────────────┼─> Phase 8 (Polish)
        ├─> Phase 6 (US4) ────────────────┤
        └─> Phase 7 (US5) ────────────────┘
```

### User story dependencies

**All five stories are independent of each other.** US1 deliberately reads `/v1/schemas` and probes
`/v1/admin/credentials` — both pre-existing — so it does not wait for US2's new route. US3, US4, and
US5 each call only Milestone 10 routes. What they share is Phase 2, and only Phase 2.

In practice an operator needs US1 to reach any of the others, so the demonstrable order is US1 first
and then whichever matters most. That is a delivery order, not a code dependency.

### Within each story

Model before components, in every case. The rendering layer carries no automated test by decision, so
a decision that drifts into a component is a requirement that silently loses its coverage.

### Parallel opportunities

- **Phase 1**: T004–T008 are five different files
- **Phase 2**: T010, T011, T012 are three different guard scripts; T020 and T021 are different modules
- **Phase 4**: the route (T032–T039) and the surface (T040–T045) are two trees and can be built by two people against contracts/console-http-api.md
- **Across stories**: after Phase 2, US3, US4, and US5 touch no shared file

### Parallel example: after Phase 2

```bash
# One developer on the route, one on the console, one on credentials:
Task: "T034 page_runs() in src/docdoc/runs/postgres.py"
Task: "T040 listing state in ui/src/console/model/runs.ts"
Task: "T046 credential operations in ui/src/console/model/credentials.ts"
```

---

## Implementation Strategy

### MVP

Phase 1 + Phase 2 + Phase 3 (US1). That is a console that signs an operator in, tells them which
authentication mode the deployment is in, signs them out on idle, and leaves nothing in the browser
— with all four guards green. It shows almost nothing, and it proves the two things the milestone is
actually risky about: that the shell loads without a credential and that the credential never comes
to rest.

Add US2 next and the console becomes worth opening.

### Incremental delivery

1. Setup + Foundational → the fences exist before the code they fence
2. US1 → sign in, idle out, leave nothing → **MVP**
3. US2 → the run list and the one new read → the console is useful
4. US3, US4 → credentials and erasure → the console replaces the SSH session
5. US5 → delivery diagnosis
6. Polish → documentation, the human test, and the three assertions about what did not change

### Notes

- Commit after each task or logical group
- Every `[P]` task touches a different file from its siblings
- Stop at any checkpoint and validate the story independently
- **T016 and T017 are the pair to review hardest.** Everything else in this list fails loudly; a
  gated console shell fails by being unusable on exactly the deployments nobody tests locally

---

## Phase 9: Convergence

Appended by `/speckit-converge` on 2026-09-11, after the implement pass. Each item is a gap
between what `spec.md` requires and what the code does — assessed against the present state of
the tree, not against a diff.

**Two of the three are requirements a task claimed to have satisfied.** T061 is ticked and the
file it names does not exist; the comment above the tombstone banner cites FR-023 and shows
neither of the two fields FR-023 names. That is the failure mode this repository writes guards
against, arriving in the one place no guard was watching: the rendering layer, which carries no
automated test by decision.

- [X] T074 Render a run's delivery attempts in a new `ui/src/console/components/DeliveryRecord.tsx` — each attempt's time, the receiver's status, and its outcome — and use it from `RunDetail` in place of the single summary line, per FR-034 and US5/AC1 (partial). `model/delivery.ts` already parses `Attempt` and nothing renders it. Keep `summarise()` as the heading above the list: the sentence answers "was anyone told?" and the list answers "what did they say?". Offer no re-delivery, no cancellation, and no destination editing (FR-036) — and keep `NOT_OFFERED` as the record of why
- [X] T075 Show the removal time and the policy on a removed run's detail in `ui/src/console/components/RunArea.tsx`, read from the `410` body's `deleted_at` and `policy`, per FR-023 and US2/AC6 (partial). The banner currently says only that the run is gone, while the comment above it claims the fields are shown. Extend `model/failure.ts` to carry the two fields through — the component must not parse the body itself, or the decision lands where no test reaches it
- [X] T076 Announce state changes to assistive technology in the console, per FR-045b and SC-014a (missing). Zero live regions exist under `ui/src/console/`; the viewer's `Running.tsx` is the pattern (`role="status"`, `aria-live="polite"`). The three FR-045b names are the ones to cover: a list loading, an erasure completing, and a credential being refused. Extend `ui/scripts/check-console-a11y.mjs` to fail when a component renders a status message with no live region, so this stays true — a requirement about what a screen reader hears is otherwise checked by nobody
- [X] T077 Record or remove the work that traces to no task: `ui/src/console/model/format.ts` with its tests, and the two client mounts plus `DOCDOC_UI_ROOT`/`DOCDOC_CONSOLE_ROOT` in `packaging/docker/compose.yml` (unrequested). Both were written during T072 and after the first screenshot, both are justified — the formatters by FR-045a/SC-015, the mounts because the image carries no assets and the quickstart could not otherwise run — and neither appears in any task. Either cite them in this phase and keep them, or remove them and let the tasks stay the record of what was built

---

## Phase 10: Convergence

Appended by `/speckit-converge` on 2026-09-11, after the Phase 9 implement pass. Phase 9's four
gaps are closed and verified.

**Not one of these four is in the code.** All four are a document asserting something about code
that is no longer true — three of them written by this milestone, about itself, and made false by
this milestone's own later work. That is a different failure from Phase 9's and worth naming: the
first pass built the wrong thing, this pass described the right thing wrongly. A document that
disagrees with the code is worse than one that is silent, because it is trusted.

- [X] T078 Assert in `ui/test/client.test.ts` that zero viewer intents construct a non-`GET` plan other than `POST /v1/extract`, per FR-022b (contradicts). FR-022b says the viewer "MUST NOT be modified" and three of its files were: `model/client.ts` widened `RequestPlan.method` to admit `DELETE` for the console, `transport.ts` gained the credential, `components/App.tsx` followed the call-site change. The substance holds — the two-path allow-list is intact and `writesToStore` is already asserted false across `allIntents` — but nothing yet pins the new hole: the *type* now permits a `DELETE` the viewer must never build. Assert the absence rather than reverting the widening; one shared transport is what FR-041 asks for
- [X] T079 Record the a11y guard's third rule in `specs/011-operations-console/contracts/console-guards.md` §4, per `plan: post-design re-check` (partial). That section lists two rules and T076 added a third — a live region must exist in any file that renders a `Banner` or a loading message. Its ceiling belongs beside it and is not what the current "Cannot catch" line says: the rule proves a region exists **in the file**, not that it wraps the message that matters or fires at the right moment. `plan.md:199` claims this file states what each guard asserts and what it cannot, and until that paragraph is written the plan's own claim is false
- [X] T080 State that `GET /v1/admin/credentials` requires a `tenant_id` in `contracts/console-http-api.md`, and record the probe's mechanics in `research.md` R7, per FR-028 (partial). The contract lists the route as consumed unchanged and names no parameter; R7 describes the administrative probe without mentioning that it takes one. The route answers `422` without it, which is what made the console's first probe read "not an administrator" for an administrator and hide the credentials and erasure areas from the only person who can use them — found by running the composition, and still absent from the two documents a reader would consult first. Include why a placeholder tenant answers the question: the scope check precedes the lookup
- [X] T081 Add `CredentialRecord`, `Delivery`, and `Failure` to the browser-side entities in `data-model.md`, per `plan: data model reference` (partial). It describes four and the console has seven. `CredentialRecord` matters most: its real shape — `scopes` as a list, plus `label` and `last_used_at` — was learned from a running deployment and appears in no design document, so the next reader starts from the same wrong assumption this milestone did. `Failure` carries the `removed` kind T075 added, which is where FR-023's two fields now live

---

## Phase 11: Convergence

Appended by `/speckit-converge` on 2026-09-11, after the Phase 10 implement pass. Phase 10's four
gaps are closed and verified.

**Both of these live where no guard reaches.** `console.html` is in none of the four directories the
build checks scan, and a quickstart is prose. Three convergence passes have now found three different
shapes: Phase 9 was code that was missing, Phase 10 was documents describing code wrongly, and this
is a stated behaviour with no home in any checked surface — plus a document invalidated by a later
task in its own milestone. The guards keep getting better at the failures they were written for,
which is exactly why the ones outside them are what is left.

- [X] T082 Give `ui/console.html` something to say when the bundle never runs, per `Edge Case: a browser with JavaScript disabled or an asset that fails to load` (missing). Today `#root` is empty and there is no `<noscript>`, so JS disabled or a 404 on the asset produces a blank page — which is the *"silently render a shell with no data"* the edge case forbids, and the same failure `specs/008` FR-037 spent a requirement on for the server side. Put the sentence **inside** `#root` so React replaces it on a successful mount, and a `<noscript>` beside it: one covers a failed load, the other covers a browser that will not run it. Leave `ui/index.html` alone — the viewer has the same hole, but FR-022b says this milestone does not touch it, and the edge case being closed is the console's
- [X] T083 Correct quickstart Scenario 0 so its recipe produces the state it claims, per `quickstart: Scenario 0` (partial). `rm -rf ui/dist-console` no longer yields a `501`: T072 added a read-only bind mount of that directory into the api container, so deleting it on the host leaves the mount pointing at a dead inode and the API serving whatever it already resolved. Either add the `docker compose restart api` the recipe now needs, or build the absence a different way — unsetting `DOCDOC_CONSOLE_ROOT` is one. The document was right when it was written and was made wrong two tasks later by this same milestone, which is the reason it is worth stating rather than quietly fixing

---

## Phase 12: Convergence

Appended by `/speckit-converge` on 2026-09-11, after the Phase 11 implement pass. Phase 11's two
gaps are closed and verified against a running composition.

**One finding, and it is the last shape this milestone had left to produce.** The CHANGELOG entry
was written in the first implement pass and is the only artifact nothing since has revisited — so
it describes a milestone that stopped three phases ago. Convergence has now found, in order: code
missing (Phase 9), documents describing code wrongly (Phase 10), a behaviour with no checked home
plus a doc its own milestone invalidated (Phase 11), and now a record that was accurate the day it
was written and was never asked again.

One hypothesis was tested and **rejected** rather than filed: that compose always setting
`DOCDOC_UI_ROOT` would shadow an installed `docdoc-ui` and change the viewer's behaviour, against
FR-022b and SC-013. It does not — `_built()` requires the entry point, so an empty configured root
falls through to the next candidate, and the absence message stays correct in the composition
because building into `ui/dist` on the host *is* `/clients/viewer`. Recorded here because a
convergence pass that reports only what it confirmed hides how much of it was guesswork.

- [X] T084 Bring the Milestone 11 CHANGELOG entry up to what shipped, per FR-046 / T066 (partial). It was written before Phases 9–11 and four things it does not mention are now the milestone's: a run's **delivery attempts** are rendered, not summarised in one line (T074, FR-034); a removed run shows **when and under which policy** (T075, FR-023); the console **announces state changes** to assistive technology and `check-console-a11y.mjs` has a third rule enforcing it (T076, FR-045b) — the entry still describes that guard as two properties; and `console.html` **says it could not start** when the bundle never runs (T082). Keep the entry's shape: what it does not add belongs beside what it does, and the four additions do not change that half
