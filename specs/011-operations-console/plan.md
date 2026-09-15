# Implementation Plan: The Operations Console

**Branch**: `011-operations-console` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-operations-console/spec.md`

## Summary

A browser surface for the person who runs a deployment: list and inspect runs, issue and revoke
credentials, erase a tenant or a document, and read a run's delivery record. Every write goes through
a route Milestone 10 already ships. The whole server-side footprint is **one new read**,
`GET /v1/runs`, built on the index Milestone 9 already created — **zero migrations, zero new
dependencies, zero new processes**.

The credential is pasted into the page, held in memory, discarded on reload and after 15 minutes of
inactivity. The fence around the milestone is constitutional rather than technical: an operations
console is permitted by v1.9.0, a review platform is not, and the difference is enforced by two build
checks and one contract test that this milestone must not modify.

Planning turned up one finding that changed the design: **the console cannot be served from behind
the API's credential**, because a browser navigation carries no bearer token and the page's whole
purpose is to accept a key after it loads. It gets its own unauthenticated mount at `/console`
(research R1), and that divergence from the viewer's posture is recorded in Complexity Tracking.

## Technical Context

**Language/Version**: Python 3.11+ (API route, tests); TypeScript 5.9 / React 19 (console), Node 24

**Primary Dependencies**: none added. Server side: FastAPI and psycopg, both present. Browser side:
React 19, `@astryxdesign/core` 0.5.0 — which already ships `Table`, `Pagination`, `AlertDialog`,
`TextInput`, `Field`, `EmptyState`, `Banner`, `Badge`, `StatusDot`, `VisuallyHidden`, every control
this console needs

**Storage**: PostgreSQL, read-only from this milestone's perspective. **No new table, no new column,
no new index, no new migration** — `runs_by_tenant (tenant_id, created_at)` from `0001_runs.sql`
already covers the listing's access path

**Testing**: pytest (contract, unit, integration) for the route; `node --test` for the console's
model; four source guards for the properties no test can reach —
`check-readonly.mjs` (unchanged), `check-model-boundary.mjs` (extended to the console's dirs),
`check-console-boundary.mjs` and `check-console-a11y.mjs` (new)

**Target Platform**: the existing API process serves both; a modern browser runs the console

**Project Type**: web application — an existing Python service plus an existing browser client tree
that gains a second entry point

**Performance Goals**: a run listing page returns within the latency the existing indexed
tenant query already delivers; SC-015's operator-facing target is under 3 minutes from key to
diagnosed failure

**Constraints**: one new API route and it is a read (SC-003); zero bytes at rest in the browser
(SC-004); zero new runtime dependencies and four containers (SC-014); the diff touches no
deterministic layer (SC-002)

**Scale/Scope**: five user stories; one route; roughly eight console model modules and a similar
number of components; page size default 50, maximum 200

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — see "Post-design
re-check" below.*

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **N/A** — no kernel file is opened. `Document`, `BlobRef`, and every span type are untouched, and SC-002 asserts it by reading the diff |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — nothing here produces, transforms, or stores a result. The console displays run state and links to a result representation it does not render (FR-022a) |
| 3 | **Grounding integrity (II)** | **N/A** — no value, no span, no grounding status is computed or displayed anywhere in this milestone |
| 4 | **Determinism (III)** | **PASS** — the one clock this milestone introduces is the browser's, used for the idle bound, and it lives in a browser model that no Python path imports. Nothing in `kernel/`, `grounding/`, or `validation/` is reached |
| 5 | **Provider isolation (IV)** | **PASS** — no provider is named, imported, or configured. The base install acquires nothing |
| 6 | **Text-first (V)** | **N/A** — no parsing decision is made or displayed |
| 7 | **Schema-driven (VI)** | **PASS** — the console branches on no document type. A run row carries a state and two timestamps; a schema identity is not among the fields it shows |
| 8 | **Validation separation (VII)** | **N/A** — no rule is authored, evaluated, or shown |
| 9 | **No silent fallback (VIII)** | **PASS** — FR-039 forbids the console from inventing an error vocabulary, and requires a transport failure and a deployment-reported failure to stay distinguishable, which the existing `failure.ts` already separates |
| 10 | **Measurability (IX)** | **PASS, and this is the gate with the most to say** — Principle IX permits the correction model and forbids the platform. This milestone builds no correction surface at all (ADR-0019 §6) and holds none of the five nouns, checked by `check-console-boundary.mjs` and by `tests/contract/test_no_review_platform.py`, which FR-003 forbids modifying |
| 11 | **Layer direction (X)** | **PASS** — one route in `api`, reading through `runs`. No new package, no new layer, no new edge. The browser client sits outside the Python layer graph entirely, as it has since Milestone 8 |
| 12 | **MVP discipline (XI)** | **PASS**, and unlike Milestone 10's gate 12 it is not conditional: constitution **v1.9.0** was adopted on 2026-09-11, before planning, and it is what permits this work. See below |
| 13 | **Kernel test rigor (XII)** | **N/A** — no kernel, span, or geometry change |
| 14 | **Open decisions** | **PASS** — the constitution's open list is empty, and ADR-0019 decided this milestone's boundary before the spec was written rather than during implementation |

### Gate 12: the amendment came first, and the fence is the deliverable

Milestone 10 recorded its gate 12 as **CONDITIONAL** and wrote "until v1.8.0 merges, implementation
does not begin". This milestone inherits the pattern and the order: constitution v1.9.0 and ADR-0019
were written and adopted **before** the spec, so the gate opens clean.

What the amendment permits is narrow and the narrowness is the work. "A full review UI" is still on
the deferred list; what v1.9.0 distinguishes is the surface about the person who **runs the
deployment** from the surface about the person who **checks documents**. Three things keep that from
decaying into an opinion:

1. **`check-console-boundary.mjs`** fails the build on `assign`, `queue`, `workload`, `review state`,
   and `disposition` appearing in the console's sources (ADR-0019 §2).
2. **`tests/contract/test_no_review_platform.py`** already fails on fifteen words in any route path,
   and FR-003 forbids this milestone from touching it. The cost is real and was accepted in
   ADR-0019 §3: a backlog view, if one is ever built, is named `backlog` and not `queue`.
3. **No per-operator state exists to hold**, anywhere — no saved filter, no preference, no layout.
   The browser stores nothing at all (SC-004), which closes the usual first step.

### Gate 10: what "no correction surface" costs, stated once

The route exists and the console does not call it. An operator who wants to record a correction uses
the API. That is a worse experience than the alternative, and it is the one ADR-0019 §6 chose
deliberately: one reviewer annotating one value is Principle IX's product feature, and the same
screen with an inbox beside it is the platform Principle IX forbids. A milestone that builds it says
which side of the line it lands on; this one does not have to.

## Project Structure

### Documentation (this feature)

```text
specs/011-operations-console/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 — ten decisions, three of which changed the design
├── data-model.md        # Phase 1 — entities, none of them persisted
├── quickstart.md        # Phase 1 — how to run and validate it
├── contracts/
│   ├── console-http-api.md   # the one new route, and the ones it consumes unchanged
│   └── console-guards.md     # what the build checks assert, and what they cannot
├── checklists/
│   └── requirements.md  # spec quality checklist, 16/16
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── api/
│   ├── app.py               # + GET /v1/runs; + the /console mount (unauthenticated, research R1)
│   ├── models.py            # + RunListResponse, RunSummary
│   ├── paging.py            # new: the cursor codec, tenant-checked on decode
│   └── ui.py                # + console asset lookup, reusing the three-candidate search
└── runs/
    └── postgres.py          # + page_runs(): keyset, tenant-scoped, status-filtered

ui/
├── index.html               # viewer entry, unchanged
├── console.html             # new entry
├── vite.config.ts           # viewer build, unchanged
├── vite.console.config.ts   # new: base "/console/", outDir "dist-console"
├── src/
│   ├── transport.ts         # + optional credential; still the only fetch in the tree
│   ├── model/               # viewer model, unchanged
│   ├── components/          # viewer components, unchanged — check-readonly.mjs keeps scanning here
│   └── console/
│       ├── main.tsx
│       ├── model/           # session, client, runs, credentials, erasure, delivery,
│       │                    #   failure, and format — how an id and a timestamp read
│       └── components/      # shell, run list, run detail, credentials, erasure,
│                            #   credential prompt, delivery record
└── scripts/
    ├── check-readonly.mjs         # unchanged; gains a comment saying why it scans the viewer only
    ├── check-model-boundary.mjs   # + the console's model and components directories
    ├── check-console-boundary.mjs # new: the five nouns, and result content
    └── check-console-a11y.mjs     # new: accessible name, keyboard reach, live regions

packaging/docdoc-ui/          # ships dist/ and dist-console/
packaging/docker/compose.yml  # + the two client builds, mounted read-only:
                              #   the image carries no assets and never will

tests/
├── contract/test_console_http_api.py     # shape of GET /v1/runs
├── contract/test_console_mount.py        # the shell loads unauthenticated; /v1 and /ui do not
├── contract/test_console_adds_one_read.py # exactly one new route, and the absences FR-009 claims
├── contract/test_credential_is_never_logged.py
├── integration/test_run_listing.py       # two tenants, paging, tombstones, cursors
├── integration/test_console_erasure_scope.py
└── unit/test_run_page_cursor.py          # encode/decode, tenant mismatch, malformed
```

**Structure Decision**: the existing single Python package plus the existing browser client tree. The
console is a **second entry point in the same tree**, not a second project: it shares
`transport.ts`, the toolchain, the licence audit, and the test runner, and it keeps its own `model/`
and `components/` directories so that each guard scans exactly the sources whose property it
asserts. No new distribution, no new container, no new process.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **The `/console` mount is served without the credential**, diverging from `specs/008` FR-059's "everything behind the same credential" | A browser's top-level navigation cannot carry a bearer token. `app.py:709` already documents the consequence for the viewer — with authentication on, *"it does not load"*. The console exists to be used on authenticated deployments and accepts its key after the page is up (FR-006), so gating the shell makes the feature impossible rather than strict | **Gating the shell**: the console never loads where it is needed. **A cookie session**: ADR-0019 §4 rejected it, and it reintroduces four security surfaces to protect a credential that already exists. **A credential in the URL**: forbidden by FR-008, and it lands in history, proxy logs, and referrers. What the divergence costs is bounded and stated: the assets carry no tenant data, no run, no identifier, and the console makes zero requests before a key is entered |
| **A second Vite config** where the repository has had one | `base` is per-build, and R1 put the console on a path the viewer's base cannot reach | **Multi-page input under `/ui/`**: mechanically fine, and it puts the console back behind the credential gate. **Relative bases**: changes the viewer's build, which FR-022b forbids |
| **A fourth and fifth build guard** in `ui/scripts/` | Two new properties are asserted that no test can reach: the five forbidden nouns (ADR-0019 §2) and keyboard reachability, in a tree whose rendering layer carries no automated test by decision | **Review**: the reason every existing guard in this directory was written. **A browser driver**: a dependency, a CI browser, and a flake class, bought for a milestone that claims no conformance level |

## Post-design re-check

Re-evaluated after Phase 1. No gate moved. Three design outputs are worth recording against the
gates they touch:

- **Gate 11 stays PASS with no new edge**: `page_runs()` lands in `docdoc.runs.postgres` beside the
  queries it resembles, and `api` reads it exactly as it reads `get()` and `tombstone()`. Nothing new
  imports anything new.
- **Gate 4 stays PASS with the clock named**: the idle bound is decided by a pure function taking
  `now` as an argument (research R4), so the only thing holding a clock is the shell. No Python path
  gains one.
- **Gate 12's fence gained a measurement**: `contracts/console-guards.md` states what each guard
  asserts **and what it cannot**, because a guard whose ceiling is undocumented is one a reader
  trusts past its limit.
