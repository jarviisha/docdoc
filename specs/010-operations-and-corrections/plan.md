# Implementation Plan: Retention, Credentials, Limits, Delivery, and the Human Loop

**Branch**: `010-operations-and-corrections` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-operations-and-corrections/spec.md`

## Summary

Milestone 9 ended with a list of seven things it deferred, each for the same reason — they could be
added without moving a process boundary. This milestone is that list, built, and it moves no
boundary: four process types, four containers, one queue, one store, `pipeline.run()` untouched.

The technical approach follows from four findings in research, and three of them made the work
different rather than smaller.

**The sweep reads survivorship; it does not derive it** (R1). A run row's `stage_outcomes` already
carries `artifact_id` per stage, so the set of artifacts a run holds is recorded. What the sweep
computes is a **set difference** — the artifacts of the runs being removed, minus the artifacts of
the runs that remain — and never a complement. A complement is the obvious implementation and it
**deletes every artifact `docdoc extract` ever wrote from the command line**, because CLI extractions
create no run row and are therefore unreferenced by construction. That is the single most dangerous
bug available in this milestone and it is designed out rather than tested for.

**Erasing the default tenant is erasing the store root** (R2). ADR-0014 §3 makes the default tenant's
namespace `<root>/` itself, and ADR-0014's Consequences section predicted this milestone would build
the naive version: *"a naive 'delete tenant' implementation in Milestone 10 would delete
everything."* So the prefix path refuses the default tenant, tenant erasure for it falls back to the
set difference, and emptying the root outright needs a destructive flag that exists on the command
line and at no URL.

**There is one observer slot, and `run.transition` has no hook at all** (R4). `pipeline/observe.py`
documents the single slot as a decision; an exporter that silently called `set_observer` would take
it from whatever the deployment had installed. So docdoc ships a bridge *factory*, installs it only
when the slot is empty, and says which happened. And `runs/observe.py` — which only calls `logging`
today — gains the same single slot, because FR-017 cannot bind to something that does not exist.

**The one thing that needs governance before code**: a per-tenant token budget counts tokens per
tenant, and constitution v1.6.0 defers "metering". Counting to refuse is not metering to bill, the
argument is correct, and Milestone 9 was told by `/speckit-analyze` — as CRITICAL — that arguing a
constitutional sentence into compliance inverts Governance's precedence. So v1.8.0 amends the
sentence instead (R14, FR-102), and carries the four new error names in the same change.

## Technical Context

**Language/Version**: Python 3.11+ (existing; no change)

**Primary Dependencies**: existing `psycopg[binary,pool]` (`docdoc[postgres]`), `boto3`
(`docdoc[s3]`), `fastapi`/`uvicorn` (`docdoc[api]`). **One new extra**: `docdoc[otel]` —
`opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`, imported inside the bridge factory
(R5). **Webhook delivery adds no dependency at all**: `http.client`, `socket`, `hmac`, and `hashlib`,
because R8's destination policy needs to connect to an address it has already validated while
presenting the original hostname, which the standard library gives directly and both high-level HTTP
clients hide behind a transport that would have to be subclassed (R6)

**Storage**: the existing run-state database, five new tables — `credentials`, `run_tombstones`,
`corrections`, `deliveries` (with `callbacks`), `limit_counters` — plus three columns and two indexes
on `runs`. No second database (FR-112). Artifacts and blobs stay content-addressed; the stores gain
deletion (R3) and nothing else

**Testing**: `pytest`. Postgres- and S3-dependent tests keep Milestone 9's marks so the offline suite
skips them. Three things get a fake so their *policy* is testable without infrastructure: the key
store, the limiter, and the deliverer. The webhook receiver is a stdlib HTTP server in
`tests/support/`, because a delivery test that mocks the transport tests nothing about R8

**Target Platform**: Linux containers behind a load balancer; the same one image and two entry points

**Project Type**: web service plus a worker process, over an existing library

**Performance Goals**: run submission still under 200 ms at p95 including the limit check (Milestone
9's SC-002 must not regress); credential resolution adds at most one database round trip per
`DOCDOC_CREDENTIAL_TTL` per process, not per request (R11); a maintenance tick delays a claim by at
most `DOCDOC_MAINTENANCE_BUDGET_MS` plus one delivery timeout (SC-024); the claim query's window
function sorts the *eligible* set, bounded by queue depth rather than table size, and is measured
rather than assumed (R10)

**Constraints**: base install acquires zero new dependencies and the offline suite passes with no
database, no object store, and neither the `otel` nor any delivery extra present (SC-021); every
capability off by default and a Milestone 9 deployment observing zero behavioural change (FR-101,
SC-002); `RunStatus` gains zero members (FR-004a, SC-023); golden-set metrics bit-identical (SC-001);
no fifth process and four containers (FR-116, SC-025)

**Scale/Scope**: five tables, three route groups, one CLI command group, one new extra, four new
error types, one new pure model, two protocol extensions on the artifact and blob stores, and **one
new layer** — `docdoc.telemetry`, sharing `evaluation`'s position. This line previously read "no new
layer, so Principle X's amend-in-the-same-change rule does not fire"; it fires, and constitution
v1.8.0 carries it (corrected 2026-09-04, during implementation)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — the kernel is not touched; SC-020 asserts zero files changed under it |
| 2 | **Provenance preservation (I, VIII)** | **PASS, and it is the gate this milestone had to argue** — see below |
| 3 | **Grounding integrity (II)** | **PASS** — routing *reads* grounding status and score and computes nothing; ungrounded values stay distinguishable, and no LLM signal enters the decision (FR-069). A test varies `model_confidence` alone and requires the decision not to move (SC-017) |
| 4 | **Determinism (III)** | **PASS, and it is the gate the task list nearly broke** — clocks, randomness, and the network live at or above `Runs` and travel downward as data (FR-096), and within `Runs` **`identity.py` stays the only module that reads either** (FR-096a). That is stricter than FR-096 and it is what `tests/unit/test_runs_clock_confinement.py` already enforces over every module in the package — so the seven modules added here would have turned it red, and the fix that presents itself is widening its `PERMITTED` set, which removes the guard. T026a extends `identity.py` instead and T026b asserts the guard was not widened. The AST scan and audit hook are neither relaxed nor exempted (FR-095). `routing.decide` is a pure function of a completed result and a policy |
| 5 | **Provider isolation (IV)** | **PASS** — `opentelemetry-*` is observability infrastructure the constitution names ("OpenTelemetry where practical"), not a document-processing provider SDK, and it is imported lazily inside `telemetry.bridge()` behind `docdoc[otel]`, exactly as `boto3` is inside `S3ArtifactStore.__init__`. Delivery adds no SDK at all (R6) |
| 6 | **Text-first (V)** | **N/A** — no parsing path is reached |
| 7 | **Schema-driven (VI)** | **PASS** — nothing here branches on a schema identity. The routing policy reads *requiredness* from the schema, which is generic structure, not document-type knowledge in a code path |
| 8 | **Validation separation (VII)** | **PASS** — routing consumes validation verdicts and produces none. It adds no rule, changes no severity, and cannot turn a failure into a pass |
| 9 | **No silent fallback (VIII)** | **PASS** — four typed, provider-neutral errors, each a subclass of the existing `RunError`. An unreachable store during a sweep deletes nothing *and removes no run row*, so the record of what to delete survives; an unreachable collector fails no run and reports once per outage |
| 10 | **Measurability (IX)** | **PASS, and this milestone is the one Principle IX was written for** — corrections become reachable through the product's own surface and remain reusable as dataset signal (FR-081), while assignment, queues, and review states stay absent (FR-083). SC-018 requires a correction to move zero metrics absent an explicit promotion |
| 11 | **Layer direction (X)** | **PASS, after a correction found by running the suite** — one layer *is* added, `docdoc.telemetry`, sharing `evaluation`'s position. This row previously said none was, on the argument that telemetry imports nothing of docdoc's; `tests/unit/test_layer_boundaries.py` requires every package on disk to be named in the contract, because an undeclared one is unconstrained. Constitution v1.8.0 carries the Principle X amendment in the same change, as that principle requires. Plus one new downward edge, `runs → evaluation`, forced by a contract rather than chosen (R13) |
| 12 | **MVP discipline (XI)** | **CONDITIONAL — PASS only once constitution v1.8.0 is adopted.** See below |
| 13 | **Kernel test rigor (XII)** | **N/A** — no kernel, span, or geometry change |
| 14 | **Open decisions** | **PASS, with four outstanding deliverables that gate implementation and not planning** — see below |

### Gate 2: deletion, and the sentence it has to survive

Principle VIII says "Provenance MUST NOT be silently overwritten" and "Artifacts MUST be immutable".
A retention sweep deletes both provenance and artifacts, so this gate cannot be waved through.

**Deleting is not overwriting, and *silently* is where the two are reconciled.** Nothing is rewritten
— FR-016 forbids it in as many words, and the store's `delete` either removes an object or does not.
What replaces a removed run is a **tombstone**: identity, tenant, deletion time, policy name, and
nothing else. The owning tenant asking about it is told it was here and is gone, and under which
policy; every other tenant is told what it would be told about an identifier that never existed,
which is Milestone 9's FR-066 unchanged.

The alternative — removing the row outright — makes "was this ever here?" unanswerable to the only
party entitled to ask, and that is the reading of Principle VIII that would actually breach it.

**Immutability is preserved in the sense the principle protects**: no artifact's bytes change, no
`artifact_id` is reused for different content, and ADR-0010 §5's refusal to overwrite is untouched.
An artifact that is deleted and later recomputed derives the same identity from the same inputs,
which is exactly what content-addressing promises.

### Gate 12: one amendment, and why arguing instead is the wrong instrument

Two constitutional sentences could be read to block this milestone.

- **"Metering, invoicing, and per-tenant pricing remain deferred"** (v1.6.0). R9 records a per-tenant
  token counter, and calling that "not metering" is a reinterpretation. **The amendment is v1.8.0**,
  distinguishing counting in order to refuse from metering in order to bill, and stating that the
  second remains deferred (FR-102, FR-048). This is the same instrument for the same reason
  Milestone 9 reached for it: Governance says the constitution wins where a spec conflicts with it,
  so a spec that reinterprets a sentence to comply with it inverts the precedence, and
  `/speckit-analyze` raised exactly that as CRITICAL on 2026-08-28.
- **"A full review UI"** is on the deferred list. This milestone adds no interface: routes that record
  and read corrections, and nowhere for a human to be assigned one (FR-083). The viewer stays
  read-only and gains no write path (FR-084). The operator interface the roadmap will eventually want
  is out of scope by name, and this milestone only makes it *possible* by giving credentials a
  lifecycle.

**Also folded into v1.8.0**: the error model's enumerated list gains `RetentionError`,
`CredentialError`, `LimitExceededError`, and `DeliveryError`. Adding names to an enumerated list in a
governing document is a MINOR amendment, and doing it in the same change avoids two amendments in one
milestone.

**Until v1.8.0 merges, gate 12 is FAIL and implementation does not begin.** Planning does; that is
what this document is.

### Outstanding deliverables, which gate implementation and not planning

**Four ADRs are not yet written** (FR-103 – FR-106). Their decisions are all made and recorded — in
the spec's Clarifications and in research R1, R2, R7, R8, R11, R12 — so nothing is being resolved
implicitly in code, which is why gate 14 passes. But each is required before implementation begins:

| ADR | Decision it records |
|---|---|
| 0015 | Deletion over a content-addressed store: survivorship by set difference, the tombstone, the tenant-prefix operation and the default tenant's exception |
| 0016 | Credential lifecycle: the table, the administrative scope, the cache-lifetime propagation bound, and the supersession of `specs/009` FR-061 |
| 0017 | The routing policy: which signals it may read, why `model_confidence` is not among them, how it is versioned, and why the outcome set has two members |
| 0018 | Webhook delivery: at-least-once, HMAC signing with the timestamp inside the signed material, one delivery per run, and the resolve-validate-pin destination policy |

### Re-check after Phase 1 design

Phase 1 changed the evidence for three gates and raised one question the pre-design pass did not.

- **Gate 2 holds concretely.** `data-model.md`'s `run_tombstones` is four columns and the sweep's step
  order — artifacts, then tombstone, then run row — means a crash never leaves a run whose deletion
  is unrecorded and unresumable.
- **Gate 5 holds against the actual import graph.** `opentelemetry` is imported inside
  `telemetry.bridge()`; nothing else in the codebase names it, and a new `forbidden` contract confines
  it and `http.client`/`socket` to `docdoc.runs.delivery` and `docdoc.telemetry` (FR-099).
- **Gate 11 holds with one new edge.** `docdoc.runs` imports `docdoc.evaluation.corrections`. It is
  downward and legal, and it was **forced**: the `"evaluation reaches no network and no provider"`
  contract bars `socket`, `urllib`, `http`, and `docdoc.artifacts` from that layer, so a
  database-backed correction store cannot live there and weakening the contract to host one would
  trade a machine-checked property for a convenience (R13).

**The question Phase 1 raised: does the claim query's window function break Milestone 9's `SKIP
LOCKED` guarantee?** It does not, and the reason is where the clause sits. The CTE ranks and orders
candidates; `FOR UPDATE SKIP LOCKED` still applies to the single row the outer statement selects, so
two workers still cannot claim one run. What does change is cost: the window function sorts the
eligible set rather than walking an index in order, so the partial index `runs_claimable` now
restricts the scan without satisfying the sort. The set is bounded by queue depth rather than table
size, and the plan's performance goals measure it rather than assuming it — which is the honest
position, because a deployment with a very deep queue is exactly the one that wanted fairness.

## Project Structure

### Documentation (this feature)

```text
specs/010-operations-and-corrections/
├── plan.md                      # This file
├── research.md                  # Phase 0 output — R1..R16
├── data-model.md                # Phase 1 output — five tables, three columns, two protocols
├── quickstart.md                # Phase 1 output — nine scenarios, scenario 0 first
├── contracts/
│   ├── operations-http-api.md   #   credentials, corrections, callbacks, erasure, and the 410
│   └── operations-layer.md      #   the Python surface: six protocols, one bridge, one tick
├── checklists/
│   └── requirements.md          # spec quality checklist (complete)
└── tasks.md                     # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── runs/                            # EXISTING LAYER — seven new modules, no new layer
│   ├── retention.py                 #   NEW — sweep() and erase(); the set difference (R1)
│   ├── keys.py                      #   NEW — KeyStore, CachedKeyStore, Credential
│   ├── limits.py                    #   NEW — Limiter, fixed-window counters, NullLimiter
│   ├── delivery.py                  #   NEW — Deliverer, signing, the destination policy (R8)
│   ├── routing.py                   #   NEW — decide(); pure, two outcomes, no model_confidence
│   ├── corrections.py               #   NEW — CorrectionStore; imports evaluation's model (R13)
│   ├── maintenance.py               #   NEW — tick(); bounded, called between claims (R15)
│   ├── identity.py                  #   CHANGED — new_credential_id / new_delivery_id /
│   │                                #     new_correction_id / new_erasure_id / new_key_secret /
│   │                                #     window_start. Still the ONLY module here that reads a
│   │                                #     clock or a random source (FR-096a)
│   ├── observe.py                   #   CHANGED — gains the single observer slot it lacked (R4)
│   ├── queue.py                     #   CHANGED — claim ranks per tenant, priority, starvation (R10)
│   ├── model.py                     #   CHANGED — priority, tokens_used, RoutingDecision; STATUS SET UNCHANGED
│   ├── postgres.py                  #   CHANGED — the new tables' statements
│   ├── worker.py                    #   CHANGED — calls maintenance.tick() between claims
│   ├── errors.py                    #   CHANGED — four new RunError subclasses
│   └── migrations/
│       ├── 0003_retention.sql       #   NEW — tombstones, the expires_at index
│       ├── 0004_credentials.sql     #   NEW
│       ├── 0005_limits.sql          #   NEW
│       ├── 0006_delivery.sql        #   NEW — callbacks and deliveries
│       └── 0007_corrections.sql     #   NEW — and runs.priority / tokens_used / callback_id
│
├── telemetry/                       # NEW LAYER — shares `evaluation`'s position (see below)
│   └── __init__.py                  #   bridge(); opentelemetry imported inside it (R5)
│
├── artifacts/
│   ├── store.py                     # CHANGED — ArtifactStore gains delete / delete_prefix (R3)
│   ├── blobs.py                     # CHANGED — the same pair
│   └── s3.py                        # CHANGED — the S3 implementations of both
│
├── api/
│   ├── app.py                       # CHANGED — credential, callback, correction, erasure routes; 410; 429
│   ├── auth.py                      # CHANGED — Principal gains `scopes`; KeyRing keeps working (FR-038)
│   └── settings.py                  # CHANGED — new env vars, same precedence rule
│
└── cli/commands/
    ├── sweep.py                     # NEW — `docdoc sweep [--once]`
    ├── erase.py                     # NEW — `docdoc erase --tenant | --document [--purge-store-root]`
    └── credential.py                # NEW — `docdoc credential issue|revoke|list [--admin]`

tests/
├── support/
│   └── webhook_receiver.py          # a stdlib receiver; mocking the transport would test nothing
├── fixtures/
│   ├── key_store.py                 #   InMemoryKeyStore — the TTL policy without a database
│   ├── limiter.py                   #   InMemoryLimiter — the window policy without a database
│   └── routing/                     #   policies, and a result whose model_confidence varies alone
├── unit/
│   ├── test_sweep_is_a_difference.py     # R1 — the CLI-artifact hazard, as a test
│   ├── test_clock_guard_was_not_widened.py # FR-096a — PERMITTED still names only identity.py
│   ├── test_sweep_is_idempotent.py       # SC-003
│   ├── test_tombstone_holds_four_fields.py # FR-004
│   ├── test_run_status_set_is_closed.py  # SC-023 — asserts Milestone 9's five by name
│   ├── test_credential_cache_ttl.py      # R11 — hits cached, misses not
│   ├── test_principal_scopes_default_empty.py # FR-038 — upgrading grants no admin
│   ├── test_limit_windows.py             # R9 — including the documented boundary burst
│   ├── test_signature_covers_timestamp.py # R7 — replay is detectable
│   ├── test_destination_policy.py        # R8 — every resolved address, not the first
│   ├── test_delivery_payload_carries_no_content.py # FR-054 — seeded strings
│   ├── test_no_callback_no_socket.py     # FR-064 — the graph proves nothing; this patches socket
│   ├── test_runs_observer_slot.py        # R4 — the slot runs/observe.py did not have
│   ├── test_emission_points_unchanged.py # FR-024 — byte-identical with and without a bridge
│   ├── test_telemetry_leaks_nothing.py   # SC-015 — seeded strings, offline via `span_for`
│   ├── test_telemetry_collector_down.py  # SC-016 — once per outage, not once per event
│   ├── test_routing_ignores_model_confidence.py # SC-017 — the negative test
│   ├── test_routing_is_pure.py           # FR-073 — no clock, no store, no artifact
│   ├── test_priority_changes_no_result.py # FR-093 — priority reaches nothing below Runs
│   ├── test_claim_order.py               # R10 — priority, starvation, per-tenant rank, and the default
│   ├── test_no_fifth_process.py          # SC-025 — four process types, four services
│   └── test_maintenance_is_bounded.py    # SC-024
├── contract/
│   ├── test_milestone_9_deployment_unchanged.py # SC-002 — scenario 0, as a suite
│   ├── test_erased_run_responses.py      # SC-005 — 410 to the owner, 404 to everyone else
│   ├── test_limit_refusal_shape.py       # FR-043/FR-044 — names the limit; creates nothing
│   ├── test_admin_surface_is_invisible.py # a tenant key gets 404, never 403
│   ├── test_priority_ceiling.py          # SC-026 — accepted at the ceiling, never refused
│   ├── test_no_review_platform.py        # FR-083/FR-084 — the absence is the contract
│   └── test_protected_layers_untouched.py # SC-020 — zero files under kernel/, ingest/, …
└── integration/
    ├── test_erase_tenant.py              # SC-004 — two tenants, byte-identical documents
    ├── test_erase_default_tenant_refused.py # R2 — ADR-0014's predicted bug, as a test
    ├── test_revocation_without_restart.py # SC-006 — the Milestone 9 defect, closed
    ├── test_rotation_has_no_gap.py       # SC-008
    ├── test_fairness_bound.py            # SC-010 — 200 runs for one tenant, one for another
    ├── test_priority_does_not_starve.py  # FR-089 — against the claim *query*, not the policy
    ├── test_delivery_postgres.py         # SC-011, SC-012, FR-058, FR-063, FR-065
    ├── test_corrections_postgres.py      # SC-018, SC-019, FR-080
    ├── test_telemetry_installation.py    # R4 — an occupied observer slot is left alone
    └── test_golden_set_unmoved.py        # SC-001 — everything on, everything off, compared
```

**The tree above is the *current* one, corrected during implementation.** Six
files named here at planning time do not exist under those names, and each was
consolidated rather than dropped: the four delivery integration files became
`test_delivery_postgres.py` and the two correction ones became
`test_corrections_postgres.py`, because every one of them needed the same
fixture — a migrated database, a registered destination or correction, and a
finished run — and separate files would have been separate copies of it drifting
apart. Phases 3 to 6 made the same consolidation for the same reason, which is
why `test_sweep_bounded_and_degraded.py` and `test_erasure.py` are what shipped.
`tests/unit/test_plan_tree_is_current.py` is what requires this list to say so.

**`telemetry/` is a layer, and this paragraph used to argue it was not.** The argument was: it
imports nothing of docdoc's except the event payload shape it is handed, so it has no position to
occupy and adding one would assert a dependency that does not exist. Coherent, and wrong.
`tests/unit/test_layer_boundaries.py` failed on the first run of the skeleton and gave the better
reason in its own docstring — a package on disk that no layer names is **unconstrained**: free to
import anything, in any direction, with CI green. What a position buys is not an accurate statement
of what telemetry depends on; it is a constraint on **who may import telemetry**, and that is worth
more.

It sits with `evaluation`: below `runs`, because `api` and `runs.worker` install the bridge, and
above `pipeline`, because nothing at or below the pipeline may reach an exporter. Constitution
**v1.8.0** carries the Principle X amendment in the same change, which that principle requires in as
many words.

The fix that was available and not taken: `telemetry.py` as a module rather than a package passes the
test, because the test walks directories. That is the shape of change FR-096a warns about — one
written to make a guard pass which also removes what the guard was watching.

It is additionally confined by the new `forbidden` contract that confines `http` and `socket`, which
is the mechanism appropriate to "this module and no other may reach the network".

**`retention.py` lives in `runs` rather than in `artifacts`**, even though most of what it deletes is
artifacts. The decision needs the run rows to compute the difference (R1), and `artifacts` sits far
below `runs` and must not learn what a run is. The store's job is to delete an id it is given; the
sweep's job is to know which ids.

**Structure Decision**: no new layer. Seven modules join `docdoc.runs`, one module joins the codebase
outside the layer graph, and two existing protocols in `docdoc.artifacts` grow a method pair. The
layers contract in `pyproject.toml` is unchanged, which means Principle X's "this text MUST be amended
in the same change that adds a layer" does not fire — the first milestone since 1.4.0 for which that
is true, and it is a deliberate consequence of the milestone adding capability rather than topology.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **The `ArtifactStore` and `BlobStore` protocols gain `delete` and `delete_prefix`**, a public break under ADR-0011 and a weakening of the "immutable store" framing | Every deletion requirement — FR-001, FR-005, FR-006 — is unimplementable without it. `store.py` defines `get` and `put` and nothing else, so today a deployment can accumulate content forever and has no in-product way to comply with a deletion request | **A separate `DeletableStore` protocol** implemented only by the real stores: every call site then narrows before deleting, growing a branch for a case only `NullArtifactStore` exercises. **Deleting through the filesystem and `boto3` from the sweep directly**: puts key derivation in two places, which is precisely how a deletion routine deletes the wrong prefix. **Consequence**: a changelog entry names the protocol change; the on-disk format and the identity derivations — ADR-0011's two deprecation-path surfaces — are untouched, and no artifact's bytes are ever rewritten (FR-016) |
| **A per-tenant token counter**, against v1.6.0's "metering … remain deferred" | FR-042 requires a token budget, and a budget without a count is a wish. The counting is for refusal: nothing prices, invoices, or emits a billing record (FR-048) | **Arguing that enforcement counters are not metering**: the argument is correct and the instrument is wrong — Governance gives the constitution precedence, and Milestone 9 was raised as CRITICAL for exactly this move. **Shipping without the token budget**: drops one of the seven items the milestone was asked to carry in order to avoid writing a paragraph. **Consequence**: constitution **v1.8.0** amends the sentence and gate 12 stays FAIL until it merges (FR-102) |
| **The claim query gains a window function**, so the partial index no longer satisfies its sort | FR-090's fairness bound has to be a real bound. Ranking each tenant's queue independently makes a tenant's delay proportional to the *number of tenants with work*, not to anyone's backlog depth (R10) | **Worker-local weighted round-robin**: N workers make N independent decisions, so the bound stops holding at exactly the scale it is needed. **A queue table per priority class**: three tables polled in order, which reintroduces starvation one level up. **A per-tenant deficit column**: mutable state to reconcile after every crash, buying what the window function gives for free. **Consequence**: sort cost over the eligible set, bounded by queue depth and measured rather than assumed; `SKIP LOCKED` is unaffected because the clause still applies to the row the outer statement selects |
| **`docdoc.runs` imports `docdoc.evaluation`** — a new edge in the layer graph | The `Correction` model is Milestone 6's and must not be redefined (FR-077), and its storage needs a database | **Putting the store in `docdoc.evaluation`**: forbidden by that layer's own `forbidden` contract, which bars `socket`, `urllib`, `http`, and `docdoc.artifacts`; weakening it would trade a machine-checked property for convenience. **A third sibling layer for corrections**: one table and one import, which Principle XI's "concrete, present-tense reason" rejects. **Consequence**: one line of `pyproject.toml` stops being true. The layers contract's comment reads *"The total order then forces `runs > evaluation`, **which `runs` does not import**"*, and after this milestone it does. The comment is corrected in the same change, for the reason Milestone 9 gave when the same thing happened to the `artifacts` comment: a comment that lies is worse than one that is absent |
