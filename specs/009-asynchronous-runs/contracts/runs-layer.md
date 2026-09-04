# Contract: The `docdoc.runs` Layer

**Feature**: `009-asynchronous-runs` | **Date**: 2026-08-28

The Python-side surface. Two protocols, one worker loop, and one rule about where a clock may be read.

## Position

```toml
layers = [
    "docdoc.api : docdoc.cli",
    "docdoc.recording : docdoc.runs",   # siblings — neither imports the other
    "docdoc.evaluation",
    "docdoc.pipeline",
    ...
]
```

Plus an `independence` contract over `["docdoc.recording", "docdoc.runs"]`, for the reason
`pyproject.toml` already gives for `api : cli`: sharing a position does not say they may not import
each other, and an ordered position would have implied a permission neither needs (R10).

`docdoc.runs` imports `docdoc.pipeline` and `docdoc.artifacts`. It **must not** import
`docdoc.api`, `docdoc.cli`, `docdoc.recording`, or `docdoc.evaluation`.

## `RunQueue` — a Protocol, with one implementation

```python
class RunQueue(Protocol):
    def submit(self, spec: RunSpec, *, run_id: UUID, now: datetime,
               expires_at: datetime) -> Run: ...
    def claim(self, *, worker_id: str, now: datetime, lease: timedelta,
              max_attempts: int) -> Run | None: ...
    def heartbeat(self, run_id: UUID, *, now: datetime, lease: timedelta,
                  worker_id: str | None = None) -> bool: ...
    def release(self, run_id: UUID, *, now: datetime,
                worker_id: str | None = None) -> None: ...
    def finish(self, run_id: UUID, outcome: RunOutcome, *, now: datetime,
               worker_id: str | None = None,
               only_from: RunStatus | None = None) -> bool: ...
    def cancel(self, run_id: UUID, tenant_id: str, *, now: datetime) -> Run: ...
    def get(self, run_id: UUID, tenant_id: str) -> Run | None: ...
    def is_cancelled(self, run_id: UUID) -> bool: ...
    def ping(self) -> None: ...
```

### `worker_id`, on the three methods that act on a lease

**These are the fix for a defect, and this block used to publish the shape that had it.** A worker
proves it still holds the run before it may write about it. Without that proof each of the three was
wrong in its own way, and none of the three failed a test:

- `finish` matched on status alone, and redelivery puts the row back into `running` — so a stalled
  worker's late verdict landed on a run another worker was executing, and the live attempt's own
  `finish` was then the one suppressed, because by then the run was terminal. The result the caller
  received was computed by the worker that had already lost the run.
- `release` had the same hole with a worse consequence: a superseded worker that was then signalled
  requeued a run another worker was executing, for immediate reclaim, so the same document was
  processed twice and the provider paid twice — and it returned the attempt while doing it.
- `heartbeat`'s `lease_until >= now` cannot detect supersession, because redelivery *renews* the
  lease. The old worker was told `True`, never learned to stop, and went on extending the new owner's
  lease; had that owner then died, the lease would have been held open by a process not executing the
  run and redelivery would never have fired.

It is **optional** because not every caller is a worker. `cancel` acts on a queued run that nobody
holds and has no claim to prove, so it omits the argument and gets the old, status-only behaviour.

`only_from` narrows the accepted prior state, and exists for one caller. `cancel` reads a run's
status and then writes; a claim landing between those two steps meant a *running* run was marked
`cancelled` — worker and lease cleared, outcomes blanked — while the worker carried on executing it
and paying for provider calls, which is exactly what FR-029 forbids, arriving through the path for
queued ones. With `only_from=QUEUED` the claim either got there first or it did not, and the database
says which.

`finish` returns `bool` rather than `None`: **whether this call was the one that recorded the terminal
state.** `cancel` needs that answer to tell "I stopped it" from "somebody claimed it first", and a
caller left to infer it from a subsequent read would be re-opening the same race one line later.

`claim` takes `max_attempts` and `submit` takes `expires_at`. Both have since the code was written;
neither was recorded here.

`is_cancelled` and `ping` arrived with the code and are recorded here rather than left implicit.
`is_cancelled` is what the worker's `should_continue` callback reads at each stage boundary — no
`tenant_id`, because the worker holds a lease on the row and is not answering a caller's question
about it. `ping` is what readiness asks (FR-054); it is on the protocol rather than only on the
Postgres implementation because both process types probe through this surface, and because a fake
that cannot be made unreachable is a fake readiness cannot be tested against.

**`now` and `run_id` are parameters, never read inside.** This is the whole of R11 expressed as a
signature. Every method is a pure function of `(database state, arguments)`, which is what lets the
claim policy be tested against an in-memory fake at arbitrary times, and what keeps the layer honest
about FR-072.

`heartbeat` returns `False` when the lease was already lost — the worker learns it has been superseded
and abandons its run rather than writing a result for work another worker is redoing.

`cancel` and `get` take `tenant_id` and return `None`/raise identically for "not found" and "another
tenant's" (FR-066). The scoping is in the query, not in a check after the fetch: a scoped query cannot
be bypassed by a caller forgetting the check.

**A protocol with one implementation needs a present-tense justification** (Principle XI). It has two:
the in-memory fake that makes the claim policy testable without a database, and the fact that the API
and the worker both depend on this surface while only the worker needs the loop around it.

## The worker loop

```text
loop:
    run = queue.claim(worker_id, now(), lease)
    if run is None:            sleep briefly; continue
    start heartbeat timer          # a background thread; the ONLY thread a worker has
    resolve schema  → on failure: finish(SchemaError); terminal, no retry (FR-091)
    result = pipeline.run(blob_bytes, schema=…, store=tenant_scoped_store,
                          request_id=run.request_id,
                          should_continue=lambda: not queue.is_cancelled(run.run_id))
    queue.finish(run.run_id, RunOutcome.of(result), now())
```

Three properties this shape guarantees:

- **The pipeline is called once, unmodified except for the callback of R4.** No stage is driven from
  here, so the worker holds no copy of the stage order or the reuse logic.
- **`RunOutcome.of(result)` is a projection, not a translation** — six fields copied from a
  `PipelineResult` that already carries them (R2). It contains no conditional on schema or document
  type.
- **A crash between `pipeline.run()` returning and `finish()` committing is safe.** The lease expires,
  the run is redelivered, every completed stage's artifact is reused, and the recomputed
  `processing_id` is identical (ADR-0013 §4). The worst case is that the last stage repeats.

On `SIGTERM`: stop claiming, and stop the run in flight at its **next stage boundary** — the shutdown
flag joins the cancellation flag in `should_continue` — then `release()` it so it re-queues
immediately instead of waiting out its lease (FR-042, FR-043).

A run stopped this way is **not** `cancelled`. It looks identical to a cancelled one from
`PipelineResult` — no `failed_stage`, no `processing_id` — so the two are told apart by whether
cancellation was actually requested. Recording a shutdown as a cancellation would be terminal, and
the work would be lost rather than delayed.

## Observability

```python
EVENT_NAME = "run.transition"
```

One event per state change, through standard-library `logging`, following the five existing
`observe.py` modules. The payload carries `run_id`, `tenant_id`, `from_state`, `to_state`,
`attempts`, `worker_id`, and `reason` — and **nothing else** (FR-092).

**What never appears**: a duration, a token count, a cost, a stage result, or any summary of the run.
`pipeline/observe.py` already states each run's cost once, and a second statement of it would drift.
Nor does any document text, extracted value, prompt body, credential, or provider message (FR-093) —
the same no-content rule every other layer's observer follows.

**Emitted by the queue, not by its callers.** `PostgresRunQueue` and the in-memory fake both call
`log_transition` from the method that performs the transition, so a transition cannot happen without
an event. The first implementation emitted from the worker's terminal path only, and a claim, a lease
expiry, an abandonment and a cancellation each changed a run's state and said nothing — which is what
"one event per state change" has to be structural to prevent.

The transitions, and the `reason` each carries:

| From | To | `reason` |
|---|---|---|
| *(none)* | `queued` | `submitted` — `from_state` is `null`, since there was no previous state |
| `queued` | `running` | `claimed` |
| `running` | `running` | `redelivered` — a lease lapsed and another worker took it |
| `running` | `queued` | `released` — a worker was signalled and let go (FR-043) |
| `running` | `failed` | `RunAbandonedError` — the attempt limit, one event per run in the sweep |
| `queued` | `failed` | `RunAbandonedError` — a run stranded at the limit; see below |
| `queued`/`running` | `succeeded`/`failed` | the error class, or `completed` |
| `queued`/`running` | `cancelled` | `cancelled` — it actually stopped |
| `running` | `running` | `cancel_requested` — asked, not yet stopped (FR-029) |

**`cancelled` and `cancel_requested` are two rows because they are two events**, and merging them is
what the table used to do. A cancelled run carries no `error_class` — nothing refused anything — so
the terminal row's "the error class, or `completed`" resolved to `completed` for every deliberate
stop. An operator counting completions counted cancellations among them, in the one place built to
make a run's history legible. `observe.reason_for` decides this now, in one function both queue
implementations call, so a fake cannot label a transition differently from the real thing.

The `queued → failed` row is the sweep reaching a run that is stranded rather than stalled. The claim
requires `attempts < max_attempts` and the sweep once looked only at `running` rows, so a run sitting
in `queued` at the limit matched neither and was invisible to both — for ever, with the caller polling
and no `RunAbandonedError` ever recorded, which is the one state the attempt limit exists to produce.
`release` no longer creates that shape and the sweep no longer ignores it; between them, the invariant
is that **every non-terminal run is either claimable or abandonable.**

Two silences are deliberate. An **idempotent replay** emits nothing, because nothing transitioned; a
retrying client is not a queue filling up. A **redelivered attempt finishing a run the first one
already concluded** emits nothing, for the same reason — `finish` is a no-op on a terminal run, and
since it now also refuses a worker that no longer holds the run, a *superseded* attempt's conclusion
is silent for the same reason rather than by luck.

There is **no exporter here.** Milestone 10 binds one; this milestone gives it something to bind to.

## Store namespacing

```python
def tenant_root(tenant_id: str) -> str:
    """'' for the default tenant, 't/<tenant_id>' for every other."""
```

Applied ahead of the existing layout, preserving the two-character fan-out (R12):

```text
<root>/blobs/<aa>/<full-hash>                     default tenant — the Milestone 8 layout, unmoved
<root>/t/<tenant_id>/blobs/<aa>/<full-hash>       every other tenant
```

**The empty string for the default tenant is a compatibility rule, not an oversight** (FR-084a).
An existing deployment's content is already at the unprefixed path; giving the default tenant a
prefix would strand it, and SC-018 exists to catch exactly that. This is the one branch permitted in
path derivation, and the docstring above must say why so nobody "tidies" it away.

`tenant_id` is validated as `[a-z0-9_-]{1,64}` at the **auth boundary**, so a value that could escape a
path segment never reaches a store. The stores do not re-validate; one validation point that always
runs beats two that might disagree.

**No identity derivation changes.** `artifact_id`, `content_id`, `blob_id`, and `processing_id` are
computed exactly as before, so two tenants processing identical bytes arrive at identical identities
independently (FR-085). Only the location differs.

## `S3ArtifactStore` / `S3BlobStore`

Satisfy the existing `ArtifactStore` protocol (`src/docdoc/artifacts/store.py:70`) and the blob store's
surface, with `boto3` imported lazily in the constructor so that a base install neither imports nor
requires it.

They inherit rather than restate ADR-0010's rules (R3):

| Condition | Behaviour |
|---|---|
| nothing stored under the id | miss; execute the stage |
| incompatible `artifact_format_version` | miss; execute; log the mismatch |
| `content_id` mismatch | **raise**; do not execute, do not recompute |
| bucket unreachable, credentials rejected, quota exceeded | run without reuse; log once; never fail the run |
| write of identical content | no-op |
| write of divergent content under an existing id | raise, naming both |

The last row is the one that makes multi-worker safe without a lock, and it is ADR-0010 §5's existing
sentence — "atomic replacement of an immutable, content-addressed entry is what makes the race
benign" — holding for several processes exactly as it held for one.

## `pipeline.run()`'s new parameter

```python
def run(..., should_continue: Callable[[], bool] | None = None) -> PipelineResult: ...
```

Consulted **only at stage boundaries**. `None` — the default, and what every existing caller passes by
omission — preserves current behaviour byte for byte. Returning `False` stops the run before the next
stage begins; stages already completed keep their artifacts, and the result carries no
`processing_id`, because no terminal artifact was produced.

This is the milestone's one deviation from FR-040 and is recorded in `plan.md`'s Complexity Tracking
with the three alternatives that were rejected. FR-040 was amended to "without changing its behaviour
for existing callers" on 2026-08-28.

## CLI additions

```bash
docdoc worker  [--run-database-url URL] [--lease-seconds 90] [--max-attempts 3]
               [--health-port PORT]
docdoc migrate [--run-database-url URL] [--check] [--default-tenant TENANT]
```

| Flag | On | What it does |
|---|---|---|
| `--run-database-url` | both | Where run state lives. **No default** — like `DOCDOC_STORE_ROOT`, where state accumulates is an operator's decision, and a command that invented a database to write to would be making it for them |
| `--lease-seconds` | `worker` | Claim duration; the heartbeat fires at a third of it, so a live worker misses two ticks before losing a run it still holds |
| `--max-attempts` | `worker` | Claims before a run is abandoned (FR-021). A non-positive value is refused with exit `64` rather than accepted: the claim requires `attempts < max_attempts` while the sweep abandons at `>=`, so `-1` would abandon the entire queued backlog on the first cycle |
| `--health-port` | `worker` | Serve `/healthz` and `/readyz`, which is how FR-053's "both process types" is reached on a process that has no FastAPI. **Off unless given** — a worker behind no load balancer has nobody to answer, and binding a port nobody asked for is how a host network collides |
| `--check` | `migrate` | Report pending migrations and apply nothing; non-zero when any are outstanding, which is what a deployment pipeline gates on |
| `--default-tenant` | `migrate` | The explicit step FR-089 requires: records which tenant owns the unprefixed store root, and **refuses to change it afterwards**, because moving it once content exists strands that content and the symptom is not an error but correct answers plus a silent re-payment for every parse |

The shared flags — `--json`, `--store`, `--store-url`, `--schema-path`, `--adapter`, and the two
document limits — are on every subcommand and are deliberately absent from the table above, which is
about what these two commands add rather than what they inherit.

**No `--concurrency`.** A worker executes one run at a time and concurrency is replica count
(FR-025, R9a). The flag is absent rather than defaulted to 1, because a flag that only accepts one
value is an invitation to make it accept more.

Subcommands of the existing single console script, because `[project.scripts]` cannot be conditioned
on an extra — the reasoning `pyproject.toml` already records for why there is no `docdoc-api` command.
`--check` exits non-zero when migrations are pending, which is what a deployment pipeline gates on.
