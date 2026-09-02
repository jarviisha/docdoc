# Implementation Plan: Asynchronous Runs, Shared Storage, and Tenant Scoping

**Branch**: `009-asynchronous-runs` | **Date**: 2026-08-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-asynchronous-runs/spec.md`

## Summary

Docdoc becomes four processes where it was one: an API, one or more workers, a run-state database,
and a shared object store. No stage changes, no provider is added, and no value docdoc produces
moves — the milestone buys the ability to accept a document without holding a connection for the
several minutes a 400-page scan takes.

The technical approach follows from four facts established in research, and three of them make the
work smaller than it looks.

**The run row is a projection of `PipelineResult`, not a new model** (R2). That result already
carries `processing_id`, `failed_stage`, per-stage `failure_class`, `duration_ms`, and `request_id` —
every field the spec's failure-recording requirements ask for exists and is populated today. The
worker does not translate a result into run state; it copies six fields out of one.

**`pipeline.run()` needs no new inputs** (R1). Its signature already takes bytes, schema, registry,
adapter, store, limits, and a caller-supplied `request_id`. The worker is a loop around a call that
exists.

**The store's degradation rules already cover the distributed case** (R3). ADR-0010 §4 and §5 were
written for one process and happen to be exactly right for several: identical content written twice
is a no-op, so two workers racing on the same artifact is benign without a lock, and an unreachable
store runs without reuse rather than failing. The S3 implementations inherit those rules; they do not
restate them.

The one place the work is **larger** than the spec implies is cancellation (R4). FR-028 requires a
running run to observe cancellation at stage boundaries, and FR-040 originally required the worker to
call the pipeline "without modification to it". Those could not both hold: `run()` executes four
stages behind one call and exposes no point at which a caller can intervene, and the existing observer
hook is specified to ignore what it returns. The resolution is an additive optional parameter with a
default that changes nothing for existing callers, recorded in Complexity Tracking below; FR-040 was
amended to "without changing its behaviour for existing callers" on 2026-08-28 rather than left
contradicting the design.

## Technical Context

**Language/Version**: Python 3.11+ (existing; no change)

**Primary Dependencies**: existing `fastapi` + `uvicorn` behind `docdoc[api]`. New and both behind new
extras: `psycopg[binary,pool]>=3.1` for run state (`docdoc[postgres]`), `boto3>=1.34` for the object
store (`docdoc[s3]`). No ORM, no migration framework, no task framework, no broker client — see R6 and
R7 for why each was considered and declined

**Storage**: PostgreSQL 14+ for run state **only** — one table, mutable, queried by predicate. Artifacts
and blobs stay content-addressed in the artifact store, now optionally S3-backed. The two never mix:
no run row is an artifact, no artifact is mutated to carry run state (ADR-0013 §2)

**Testing**: `pytest`. Postgres-dependent tests run against a disposable container and are marked so
the offline suite skips them; the queue's claim semantics are additionally tested against a fake that
implements the same protocol, so the *policy* is covered without a database. S3 paths tested against a
local MinIO in the same manner. The four existing suites — unit, property, contract, integration —
keep their current entry points and must stay green with neither extra installed

**Target Platform**: Linux containers behind a load balancer; one image, two entry points

**Project Type**: web service plus a worker process, over an existing library

**Performance Goals**: run submission under 200 ms at p95 regardless of document size (SC-002);
claim-to-start latency under 1 s with an idle worker; a redelivered run repeats at most one stage
(SC-004)

**Constraints**: base install acquires zero new dependencies and the offline suite passes with neither
new extra present (SC-013); no clock or random access at or below `Pipeline` (FR-072, and the existing
guard enforces it); no change to kernel, ingest, extraction, grounding, validation, or the artifact
envelope's identity derivation (SC-012); golden-set metrics bit-identical (SC-010)

**Scale/Scope**: one new layer, one new table, one worker entry point, two store implementations, two
health routes, three run routes. One run per worker process — concurrency is replica count, not a
flag (R9a). No second database, no run priority, no fairness policy

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — the kernel is not touched; SC-012 asserts zero files changed under it |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — `RunProvenance` is carried through unchanged; the run row adds a *transport* identity beside it and discards nothing |
| 3 | **Grounding integrity (II)** | **N/A** — no grounding code path is reached by this milestone |
| 4 | **Determinism (III)** | **PASS** — `run_id`, `created_at`, `lease_until`, `expires_at` are allocated in `docdoc.runs` and passed downward as data; the AST scan and audit hook are neither relaxed nor exempted (FR-073). Determinism is now load-bearing for correctness, since redelivery is only safe if re-execution cannot disagree (ADR-0013 §4) |
| 5 | **Provider isolation (IV)** | **PASS, with reasoning recorded** — `boto3` is infrastructure the sanctioned stack names ("local filesystem or S3-compatible object storage"), not a document-processing provider SDK. It is imported lazily behind an extra, exactly as the parser and model adapters are. See R5 for why the `artifacts` layer can host it without becoming a provider layer |
| 6 | **Text-first (V)** | **N/A** — routing is untouched |
| 7 | **Schema-driven (VI)** | **PASS** — the worker treats `schema_identity` as an opaque string it stores and forwards; no branch anywhere reads its value |
| 8 | **Validation separation (VII)** | **N/A** |
| 9 | **No silent fallback (VIII)** | **PASS** — an unreachable database refuses submission with a typed, retryable error rather than accepting work it cannot record (FR-057); an unreachable store degrades to no-reuse and logs once, which is ADR-0010 §4's existing rule and not a new one |
| 10 | **Measurability (IX)** | **PASS** — SC-010 requires golden-set metrics to be bit-identical, which is the strongest form: this milestone touches no stage, so any movement is evidence of scope escape |
| 11 | **Layer direction (X)** | **PASS** — `docdoc.runs` joins the layers contract as a sibling of `docdoc.recording`, with an `independence` contract between them, mirroring the existing `api : cli` precedent exactly (R10) |
| 12 | **MVP discipline (XI)** | **PASS, and it is the gate that needed the most work** — see below |
| 13 | **Kernel test rigor (XII)** | **N/A** — no kernel, span, or geometry change |
| 14 | **Open decisions** | **PASS** — nothing is being resolved implicitly in code. ADR-0013 is written and Accepted; ADR-0014's decisions are made and recorded in the spec's Clarifications, awaiting only their ADR. See the deliverable note below |

### Gate 12: two constraints argued, one amended

Three constitutional sentences could be read to block this milestone, and each is answered in
`spec.md` under "Why this needs one constitutional amendment and not three". Restated here in one line
each so a reviewer can check the gate without leaving this file:

- **The deferred-technology list** names Kafka, Temporal, Kubernetes, multi-region, and distributed
  DAG engines. A table claimed with `SKIP LOCKED` is none of them, and the list's own rationale is
  about not building distributed infrastructure before having a correct document model — which eight
  milestones produced.
- **"PostgreSQL only where persistence is genuinely required"** is satisfied for the first time rather
  than circumvented: run state is mutable and queried by predicate, and ADR-0010 §5 makes the artifact
  store's refusal to overwrite a correctness guarantee that cannot be spent on this.
- **"Development Compose contains only api, postgres, and object storage"** — **amended**, not
  reinterpreted. Constitution v1.6.0 (2026-08-28) adds `worker` to that sentence. An earlier draft of
  this plan argued that "only" governs third-party infrastructure and that the worker, being the
  docdoc image at a different entry point, fell outside it. The argument was sound and the instrument
  was wrong: Governance says the constitution wins where a plan conflicts with it, so a plan that
  reinterprets a constitutional sentence to make itself compliant inverts the precedence.
  `/speckit-analyze` raised it as CRITICAL and the sentence was amended instead.

### Outstanding deliverable, which gates implementation and not planning

**ADR-0014 is not yet written** (FR-090). Its three decisions — per-tenant namespacing of the
content-addressed store, the existence oracle that closes, and authentication defaulting to off — were
made explicitly in the 2026-08-28 clarification session and are recorded in `spec.md`. Nothing about
them is implicit or deferred to code, which is why gate 14 passes. But FR-090 requires the ADR before
implementation begins, and Phase 1's contracts below assume its outcome.

### Re-check after Phase 1 design

Phase 1 changed three gates' evidence and raised one question the pre-design pass did not.

- **Gate 5 holds, now concretely.** `boto3` is imported inside `S3ArtifactStore.__init__` behind the
  `s3` extra, so a base install neither imports nor requires it (R5), and SC-013 measures that.
- **Gate 11 holds against the actual import graph.** `docdoc.runs` imports `docdoc.pipeline` and
  `docdoc.artifacts`, both below it in the layers list; it imports none of `api`, `cli`, `recording`,
  `evaluation` (contracts/runs-layer.md states this as the layer's rule, and the `independence`
  contract makes the `recording` half machine-checked).
- **Gate 12 holds with two new dependencies**, `psycopg` and `boto3`, each behind its own extra and
  each named by the sanctioned stack. No ORM, no migration framework, no task framework, no broker
  client — R6 and R7 record why each was considered and declined rather than never raised.

**The question Phase 1 raised: does `should_continue` make the pipeline non-deterministic?** It makes
`run()` sensitive to something other than its inputs, which is worth stopping on. It does **not**
breach Principle III, and the reason is specific rather than reassuring: a cancelled run produces no
terminal artifact, so there is no identity under which two different results could ever be observed.
Determinism's guarantee is that identical inputs yield identical outputs *under the same identity*,
and cancellation produces no identity at all — the same reason ADR-0012 §3 omits `job_id` from a
storeless response instead of nulling it. Stages that do run are unaffected, and the callback is
consulted only between them, never inside one.

## Project Structure

### Documentation (this feature)

```text
specs/009-asynchronous-runs/
├── plan.md              # This file
├── research.md          # Phase 0 output — R1..R15
├── data-model.md        # Phase 1 output — the Run entity and its transitions
├── quickstart.md        # Phase 1 output — the four-container validation walk
├── contracts/
│   ├── runs-http-api.md #   the three run routes and the two health routes
│   └── runs-layer.md    #   the Python-side contract: queue protocol, worker loop, store namespacing
├── checklists/
│   └── requirements.md  # spec quality checklist (complete)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── runs/                       # NEW LAYER — sibling of `recording`, above `pipeline`
│   ├── __init__.py             #   the public surface: submit, get, cancel, claim
│   ├── model.py                #   Run, RunStatus, RunFailure — pydantic, no I/O
│   ├── identity.py             #   run_id allocation and clock reads — the ONLY place either happens
│   ├── queue.py                #   the RunQueue protocol: submit / claim / heartbeat / finish / cancel
│   ├── postgres.py             #   the one implementation; raw SQL via psycopg, no ORM
│   ├── migrations/             #   NNNN-name.sql, applied by an explicit idempotent step
│   ├── worker.py               #   the claim → run → record loop
│   └── errors.py               #   RunError and its subclasses, provider-neutral
│
├── artifacts/
│   ├── s3.py                   # NEW — S3ArtifactStore + S3BlobStore, lazy boto3 import
│   └── paths.py                # CHANGED — tenant-namespaced key derivation
│
├── api/
│   ├── app.py                  # CHANGED — three run routes, two health routes, auth dependency
│   ├── auth.py                 # NEW — static key → principal → tenant_id
│   ├── health.py               # NEW — liveness and readiness
│   └── settings.py             # CHANGED — new env vars, same precedence rule
│
├── cli/commands/
│   └── worker.py               # NEW — `docdoc worker`, and `docdoc migrate`
│
└── pipeline/
    └── runner.py               # CHANGED — one additive optional parameter; see Complexity Tracking

tests/
├── infra.py                            # the two variables that point the suite at real services
├── fixtures/
│   └── run_queue.py                    #   InMemoryRunQueue — the claim policy without a database
├── unit/
│   ├── test_run_state_machine.py       #   five states, both invariants, cancellation refusal
│   ├── test_claim_policy.py            #   oldest-first, lease expiry, attempt limit, idempotency
│   └── test_runs_clock_confinement.py  #   only identity.py reaches a stdlib clock (FR-072)
├── contract/
│   └── test_async_matches_sync.py      #   SC-001, the criterion the milestone exists for
└── integration/
    ├── test_run_queue_postgres.py      #   SKIP LOCKED under real concurrency; the check constraint
    ├── test_failed_run_is_recorded.py  #   three failure shapes, told apart (US2)
    ├── test_run_record_leaks_nothing.py #  SC-007 and FR-093, over the row and the log line
    ├── test_s3_store_rules.py          #   ADR-0010 §4 and §5, over an object store
    └── test_shared_store_reuse.py      #   SC-005, counted rather than timed

packaging/
└── docker/        # NEW — Dockerfile (one image, two entry points) and compose.yml
```

**Structure Decision**: A new top-level layer `docdoc.runs`, placed as a sibling of `docdoc.recording`
in the `layers` contract with an `independence` contract between them. Neither imports the other —
`recording` drives the pipeline to produce a prediction set, `runs` drives it to serve a request — and
an ordered position between them would assert a relationship that does not exist. This reuses the
`api : cli` precedent rather than inventing a pattern.

The S3 stores go inside `docdoc.artifacts` rather than into a new layer, because they implement
protocols that layer already defines and moving them would put the protocol and its implementation on
opposite sides of a boundary. R5 records the one consequence: the `pyproject.toml` comment claiming
`artifacts` "depends on `pydantic` and two kernel hashing helpers and on nothing else" stops being
true and is updated in the same change, because a comment that lies is worse than one that is absent.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **`pipeline.run()` gains an optional `should_continue` callback**, contradicting FR-040's "without modification to it" | FR-028 requires a running run to observe cancellation at stage boundaries. `run()` executes four stages behind one call and offers no interposition point, so without this the *only* cancellable moment is before the run starts — and the stage boundary worth catching is the one before the model call, which is where the money is | **The existing observer hook**: `pipeline/observe.py` specifies that docdoc "calls it and ignores what it returns", and that a raising observer cannot fail a run — so it cannot carry a decision, by design. **Per-stage calls from the worker**: no public per-stage entry point performs artifact reuse, so this would duplicate the pipeline's reuse logic in a second place. **Killing a subprocess per run**: works, and pays a process launch per run plus a much larger blast radius, to avoid one optional parameter whose default preserves every existing caller's behaviour byte for byte. **Consequence**: FR-040 **was amended** on 2026-08-28 to "without changing its behaviour for existing callers", rather than being silently reinterpreted |
| **`docdoc.artifacts` acquires an optional `boto3` dependency**, weakening its "nothing else" claim | The sanctioned stack names S3-compatible object storage, and multi-worker reuse is unsafe without a shared store (SC-005) | Putting the S3 implementations in a new layer separates a protocol from its only remote implementation and forces `artifacts` to import downward or the new layer to re-export. The import is lazy and behind an extra, so the base install is unchanged (SC-013); what is genuinely lost is a true sentence in a comment, and that comment is corrected in the same change |
