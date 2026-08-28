# Phase 0 Research: Asynchronous Runs, Shared Storage, and Tenant Scoping

**Feature**: `009-asynchronous-runs` | **Date**: 2026-08-28

Fifteen questions the plan could not answer without reading the code or making a decision. Findings
R1–R3 shrink the milestone; R4 is the one that grows it.

---

## R1 — What does the worker have to pass to `pipeline.run()`?

**Decision**: Nothing new. The worker calls the existing signature.

**Rationale**: `src/docdoc/pipeline/runner.py:171` already accepts `source` as bytes, plus `schema`,
`registry`, `adapter`, `store`, `limits`, the three per-stage option objects, and — usefully — a
caller-supplied `request_id`. Every input a worker has is already a parameter. ADR-0010 §6 predicted
this when it deferred the work: "nothing here would have to change but the transport: the pipeline is
already a function from inputs to a result, and the store is already the place a worker would write
to." That sentence is confirmed rather than assumed.

**Alternatives considered**: A `run_async()` variant, rejected because there is nothing asynchronous
about the computation — only about who is waiting for it. A worker-specific facade, rejected as an
abstraction with no present-tense reason (Principle XI).

---

## R2 — What does the run row have to store that `PipelineResult` does not already produce?

**Decision**: Six identity and lifecycle fields. Everything about *what happened* is copied from
`PipelineResult` as it exists today.

**Rationale**: `src/docdoc/pipeline/result.py` already carries `processing_id`, `failed_stage`,
`request_id`, `outcomes` — each with `stage`, `status`, `artifact_id`, `duration_ms`, and
`failure_class` — and `provenance`. The spec's failure-recording requirements (FR-035, FR-036) read
as new work and are a projection of a model that is already populated on every run, including failed
ones.

What the run row adds is only what exists *before* the pipeline is called or *outside* it:
`run_id`, `tenant_id`, `blob_id`, `status`, `attempts`, `lease_until`, `expires_at`,
`idempotency_key`. Note `failure_class` is already a class **name**, not a message — the rule FR-037
demands is enforced upstream and inherited, not re-implemented.

**Alternatives considered**: A separate `RunResult` model, rejected because it would be a second place
the outcome of a run is stated, and the two would eventually disagree — the same argument
`pipeline/observe.py` uses to refuse a run-level event.

---

## R3 — Do the artifact store's rules survive multiple writers?

**Decision**: Yes, unchanged. The S3 implementations inherit ADR-0010 §4 and §5 rather than restating
them.

**Rationale**: §5 says a write for an existing id never overwrites, identical content is a no-op, and
divergent content raises — and it already argues the concurrency case: "Concurrent writes of identical
content both succeed. No lock, no lease, no coordinator: atomic replacement of an immutable,
content-addressed entry is what makes the race benign." That was written about one process and holds
verbatim for several. §4's four read outcomes are equally portable, including the one that matters
most operationally: an unreachable store runs without reuse and never fails the run.

The only S3-specific work is achieving *atomicity*, which the filesystem store gets from
temp-file-and-replace. A single-part `PutObject` is atomic in S3-compatible stores — a reader sees the
old object or the new one, never a partial — so the property is available without a protocol.

**Alternatives considered**: A conditional write (`If-None-Match`) to make the no-overwrite rule
server-enforced. Attractive, and rejected for now because support varies across S3-compatible
implementations and the read-then-write check the filesystem store already performs is what the
contract specifies. Recorded as a plausible hardening.

---

## R4 — How does a running run observe cancellation?

**Decision**: `pipeline.run()` gains one optional keyword parameter, `should_continue: Callable[[],
bool] | None = None`, consulted at stage boundaries only. Default `None` preserves every existing
caller's behaviour exactly.

**Rationale**: This is the one place the spec asks for something the code cannot currently give.
FR-028 requires stage-boundary cancellation; FR-040 requires calling the pipeline unmodified. Both
cannot hold, and the tension is worth stating plainly rather than resolving quietly, because the
obvious workaround is wrong in an interesting way.

The obvious workaround is the observer hook. It does not work **by design**:
`src/docdoc/pipeline/observe.py` states that docdoc "calls it and ignores what it returns, so a
tracing bridge that raises cannot fail a run that had already succeeded". An observer that could
cancel would be an observer that can change a result, which that module exists to prevent.

The stage boundary worth catching is the one between parse and extract, because that is where the
model call has not happened yet. Cancellation that can only act before a run starts saves nothing on
the runs anyone actually wants to cancel.

**Alternatives considered**: **Per-stage calls from the worker** — no public per-stage entry point
performs artifact reuse, so the worker would hold a second copy of the reuse logic, which is exactly
what Milestone 7 consolidated. **A subprocess per run, killed on cancel** — works, and costs a process
launch per run and a much wider blast radius to avoid one optional parameter. **Dropping FR-028 to
queued-only cancellation** — honest and cheap, and forfeits the only cancellation with an economic
point. **Consequence, since applied**: FR-040 was amended on 2026-08-28 to "without changing its behaviour
for existing callers"; see Complexity Tracking in `plan.md`.

---

## R5 — Where do the S3 implementations live, given Principle IV?

**Decision**: `src/docdoc/artifacts/s3.py`, with `boto3` imported lazily inside the constructor and
declared under a new `docdoc[s3]` extra.

**Rationale**: Principle IV isolates **provider SDKs** — the parsers and models that produce document
content — and its rule is that a PR adding one outside an adapter directory is rejected on sight. An
object store client is not that: the sanctioned stack names "local filesystem or S3-compatible object
storage" as infrastructure, in the same sentence as PostgreSQL. Reading `boto3` as a provider SDK
would make the constitution forbid the storage its own stack line permits.

The implementations belong beside the protocols they satisfy. `ArtifactStore` is a `Protocol` in
`src/docdoc/artifacts/store.py:70`; putting its only remote implementation in another layer would
separate the two and force one of them to import across a boundary.

**Consequence, recorded because it makes an existing comment false**: `pyproject.toml`'s layers
comment says `artifacts` "depends on `pydantic` and two kernel hashing helpers and on nothing else".
After this milestone it depends optionally and lazily on `boto3`. The comment is corrected in the same
change. A comment nobody maintains is a comment that lies — the same argument the spec template makes
about a Status field.

**Alternatives considered**: A new `docdoc.storage` layer, rejected per above. `smart_open` or
`fsspec` as an abstraction over both backends, rejected because it would put a third-party
abstraction between docdoc and a protocol docdoc already defines, for one backend.

---

## R6 — Which Postgres driver, and is there an ORM?

**Decision**: `psycopg` version 3, raw SQL, no ORM. `psycopg[binary,pool]` under a `docdoc[postgres]`
extra.

**Rationale**: psycopg3 provides sync and async from one package, which the deployment needs in both
shapes: the worker is synchronous because `pipeline.run()` is, and the API is `async def` throughout.
One dependency covers both; `asyncpg` would cover only the API and require a second driver for the
worker.

No ORM, for the same reason ADR-0010 §1 refused even SQLite for artifacts: there is one table, the
queries are a handful of statements, and the claim query needs `FOR UPDATE SKIP LOCKED`, which an ORM
obscures rather than helps. SQLAlchemy would be the largest dependency in the project by a wide margin
and would arrive to manage one table.

**Alternatives considered**: `asyncpg` (fastest, async-only). SQLAlchemy Core without the ORM (still
the dependency, for query building nobody needs here).

---

## R7 — How are migrations applied?

**Decision**: Numbered plain-SQL files in `src/docdoc/runs/migrations/`, applied by an explicit
`docdoc migrate` subcommand that records applied versions in a table and is idempotent.

**Rationale**: FR-078 requires an explicit, repeatable, idempotent step and forbids applying schema
changes implicitly at process start — a rule that matters more with several worker processes racing to
boot. Alembic is the default answer and brings SQLAlchemy with it (R6), which is a large dependency
arriving to version one table.

The repository has a precedent for exactly this trade: the CLI is `argparse` because, as
`pyproject.toml` puts it, "it costs no dependency". A migration runner over numbered files is thirty
lines and the same bargain.

**Alternatives considered**: Alembic (see above). `CREATE TABLE IF NOT EXISTS` at startup, rejected by
FR-078 and by the multi-process race. Shipping SQL in the README for an operator to paste, rejected
because it is not repeatable and cannot be tested.

---

## R8 — What exactly is the claim query?

**Decision**:

```sql
UPDATE runs SET status = 'running',
                attempts = attempts + 1,
                lease_until = %(now)s + %(lease)s,
                worker_id = %(worker)s
WHERE run_id = (
    SELECT run_id FROM runs
    WHERE status = 'queued'
       OR (status = 'running' AND lease_until < %(now)s)
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

**Rationale**: One statement, so there is no window between selecting and claiming. `SKIP LOCKED`
makes concurrent workers step over each other's candidate rows instead of serialising on the oldest
one. `ORDER BY created_at` satisfies FR-024's no-starvation requirement. The `OR` clause is what makes
lease expiry self-healing: an abandoned run is simply eligible again, with no reaper process to
deploy, monitor, and have fail silently.

`now` is passed as a parameter rather than read as `now()` in SQL — the clock read happens in
`docdoc.runs.identity` (FR-072), and passing it makes the claim query testable at arbitrary times
without a database clock.

**Alternatives considered**: `SELECT … FOR UPDATE SKIP LOCKED` followed by a separate `UPDATE`,
correct inside a transaction and two round trips. A separate reaper that re-queues expired leases,
rejected as a process to operate that the `OR` clause makes unnecessary.

---

## R9 — Lease duration and heartbeat interval?

**Decision**: Lease 90 s, heartbeat every 30 s, both configurable. Attempt limit 3.

**Rationale**: The lease must exceed the heartbeat by enough to survive a slow tick, not exceed the
slowest document — that is the point of heartbeating. Three heartbeat periods gives two chances to
miss before a live worker loses its claim, and bounds the delay before a *dead* worker's run is
redelivered to 90 s. A lease sized to the slowest extraction (minutes) would make every crash cost
that long in latency.

Attempt limit 3 because the failure it bounds is the poison document, and a document that terminates
three workers will terminate thirty.

**Alternatives considered**: Lease equal to a document timeout, rejected above. Exponential lease
extension, rejected as tuning with no present-tense problem.

---

## R9a — How many runs does one worker execute at a time?

**Decision**: One. Concurrency is replica count; there is no `--concurrency` flag.

**Rationale**: `pipeline.run()` is synchronous and mixes provider I/O with CPU-bound work — PyMuPDF
parsing and `rapidfuzz` matching both hold the GIL in bursts. Under a threaded worker, one long parse
can starve the heartbeat of runs on sibling threads for long enough that they lose their leases while
still executing, and a run redelivered while its original is mid-flight is the one race this design
otherwise has none of. A resource-utilisation choice would have become a correctness bug.

One run per process also needs no answer to the questions threading raises: whether `boto3` clients
and `psycopg` connections are shared or per-run, whether the store's degradation logging is
thread-safe, whether a crash in one run's stage can take its siblings with it. `SKIP LOCKED` was
already chosen for cross-process claim contention (R8), so nothing new is required to make this work.

**The cost, stated**: a full Python interpreter per concurrent run — on the order of 100 MB resident.
Ten concurrent runs is ten containers, not one container with ten threads. For a workload whose
dominant cost is waiting on a provider, that is memory traded for the absence of an entire class of
question. A deployment that genuinely needs dozens of concurrent runs on one host should revisit this
with a requirement stating that the heartbeat runs where no stage's GIL can block it.

**Alternatives considered**: N threads (above). N managed subprocesses — the same isolation as
replicas, obtained by reimplementing the supervisor Docker and Kubernetes already provide. An async
worker pool — `pipeline.run()` is synchronous, so this is the threaded design with a layer on top.
Clarified 2026-08-28.

---

## R10 — Where does `docdoc.runs` sit in the import contract?

**Decision**: A sibling of `docdoc.recording` on one layers line, plus an `independence` contract.

```toml
layers = [
    "docdoc.api : docdoc.cli",
    "docdoc.recording : docdoc.runs",
    "docdoc.evaluation",
    ...
]
```

**Rationale**: `runs` must sit below `api` and `cli` (both call it) and above `pipeline` (it calls
that). Between those, its only neighbour is `recording`, and the two share nothing: recording drives
the pipeline to produce a prediction set, runs drives it to serve a request. An ordered position would
grant a permission neither needs, which is precisely the argument `pyproject.toml` already makes for
`api : cli` — "Sharing a layer position above says neither may import the other's *lower* neighbours
wrongly; it does not say they may not import each other, and a position in a list would have implied a
permission."

The total order then forces `runs > evaluation`, which `runs` does not import. That is the same
harmless artefact the existing comment acknowledges for `evaluation > pipeline`, and is preferable to
a dozen pairwise `forbidden` contracts nobody will maintain.

**Alternatives considered**: `runs` directly above `pipeline` and below `evaluation`, which is also
true and asserts an ordering against `evaluation` and `recording` that means nothing.

---

## R10a — Does the `Runs` layer emit its own events?

**Decision**: Yes. `src/docdoc/runs/observe.py` emits one `run.transition` event per state change,
via standard-library `logging`, exactly as the five existing `observe.py` modules do. No summary
event, and no export.

**Rationale**: `pipeline/observe.py` refuses a run-level event and gives its reason — "A fifth event
summarising the four would be a second place where the cost of a run is stated, and the two would
eventually disagree." That objection is against a *summary*, and a transition event is not one: it
carries identities, states, attempt count, and reason, and states no cost, duration, or stage result.

What changed is that asynchrony moved genuinely new events outside every stage. A claim, a lease
expiry, a redelivery, a cancellation, and an abandonment have no stage to attach to; and under FR-091
a run can fail without reaching a stage at all, emitting **no** `pipeline.stage` event. Leaving this
to Milestone 10 would ship a four-process topology in which the component hardest to debug — lease
handoff between workers — is the one that logs nothing.

**This is not OpenTelemetry arriving early.** No exporter, no tracing dependency, no new package. The
distinction that matters for Milestone 10 is that it will bind an exporter to a hook that already
emits, rather than adding the emission point and the exporter in one change and having no way to tell
which of the two is wrong.

**Alternatives considered**: No run-level observer at all — the most literal reading of
`pipeline/observe.py`, and it makes lease behaviour invisible. A transition event *plus* a terminal
lifecycle summary — convenient for dashboards, and exactly the second cost statement the existing
module forbids by name. Deferring the whole question to Milestone 10 — see above.
Clarified 2026-08-28.

---

## R11 — How is the determinism guard kept green?

**Decision**: All clock and random access is confined to `src/docdoc/runs/identity.py`, and a unit
test asserts that no other module in `docdoc.runs` imports `uuid`, `time`, `datetime`, `random`, or
`secrets`.

**Rationale**: FR-072 and FR-073 forbid weakening the existing guard, and the guard covers the kernel.
What is new is that layers *above* the kernel now genuinely need a clock, so the risk is drift: a
lease check written inline in `queue.py` would pass CI today and make the queue untestable at
arbitrary times tomorrow.

Confining it has a second payoff that is the real reason: `claim`, `expire`, and the state machine all
become pure functions of `(row, now)`, so the claim *policy* is tested without a database (see the
Testing note in `plan.md`).

**Alternatives considered**: Passing a `clock` callable through every function, rejected as
threading a parameter through call sites that have no other reason to differ.

---

## R12 — How is the store namespaced per tenant?

**Decision**: A tenant segment ahead of the existing layout, preserving the two-character fan-out —
**except for the default tenant, whose namespace is the root itself**:

```text
<root>/blobs/<aa>/<full-hash>                    default tenant — unchanged from Milestone 8
<root>/artifacts/<aa>/<full-hash>.json
<root>/t/<tenant_id>/blobs/<aa>/<full-hash>      every other tenant
<root>/t/<tenant_id>/artifacts/<aa>/<full-hash>.json
```

**The exception is the whole point, and it was nearly missed.** An unconditional prefix would put
every Milestone 8 deployment's existing content at a path the new code never looks at — correct
results, silently re-paying for every parse, and SC-018 red. Two alternatives bought path uniformity
and were rejected on cost: relocating a whole bucket on upgrade (an S3 "move" is copy-then-delete, at
the scale of every artifact a deployment has ever written), and a permanent read-through fallback,
which pays a second round trip on every **miss** — the common case for a new document, not the rare
one. Clarified 2026-08-28.

**Rationale**: ADR-0010 §1 chose the fan-out because "a flat directory of a hundred thousand entries
is slow on several filesystems and free to avoid", and that reason is unchanged by a prefix above it.
Putting the tenant *above* the fan-out rather than inside the hash keeps every identity derivation
untouched (FR-085) — the path changes, the name does not — and makes per-tenant deletion, which
Milestone 10 needs, a prefix operation rather than a scan.

`tenant_id` must be constrained to a character set that cannot escape a path segment; validated at the
auth boundary, not at the store.

**Alternatives considered**: Mixing the tenant into `artifact_id`, rejected outright: it would make
two tenants derive different identities for identical content, breaking FR-085 and making
`processing_id` no longer a function of inputs alone. A separate bucket per tenant, rejected as
operationally heavy and bounded by account bucket limits.

---

## R13 — What do the health endpoints actually check?

**Decision**: Liveness returns a constant. Readiness attempts one trivial database round trip
(`SELECT 1`) and one store metadata operation against a fixed key, with a short timeout, and caches
the outcome for two seconds.

**Rationale**: FR-053 requires liveness to touch nothing — a liveness probe that checks a dependency
turns a dependency outage into a restart loop, which is the classic way to convert a degradation into
an outage. FR-056 forbids readiness from invoking a provider, so no adapter is consulted; a model
provider's availability is not this deployment's readiness.

The two-second cache exists because readiness is polled by every load-balancer target at a fixed
interval, and an uncached check makes probe traffic scale with fleet size against the one component
already under stress.

**Alternatives considered**: Readiness checking schema-registry contents, rejected — an empty registry
is a valid deployment. Readiness attempting a real store write, rejected as a probe with a side
effect.

---

## R14 — How are static credentials compared and configured?

**Decision**: A mapping loaded at startup from a file named by `DOCDOC_API_KEYS_FILE`, compared with
`secrets.compare_digest` against a SHA-256 of the presented key. Absent variable means authentication
is disabled (FR-088).

**Rationale**: FR-068 forbids a credential appearing in a log line, a run record, an error body, or a
process argument list — which rules out passing keys on `argv` and matches the reasoning already in
the README for why `DOCDOC_GEMINI_MODEL` has a flag and the credential does not. A file rather than an
environment variable because a key set is a list, and because file permissions are a control the
environment does not offer.

`compare_digest` over a hash rather than the raw key: the comparison is constant-time either way, and
hashing means a file leak does not immediately yield usable credentials.

**Alternatives considered**: JWT, rejected as needing an issuer this milestone does not have. A keys
table in Postgres, rejected because FR-061 forbids runtime mutation and a table invites exactly that.

---

## R15 — How is idempotency enforced?

**Decision**: A partial unique index on `(tenant_id, idempotency_key) WHERE idempotency_key IS NOT
NULL`; a conflicting insert returns the existing `run_id`.

**Rationale**: FR-011 requires a repeated key under one tenant to return the original run, and SC-016
requires the same key under two tenants to produce two runs — which the composite key gives directly.
The partial index keeps the common case, where no key is supplied, out of the index entirely.

The database enforces this rather than a read-then-insert in application code, because two API
processes handling a client's retry concurrently would both read "not present" and both insert.

**Alternatives considered**: Deriving the key from the request body hash, rejected because it would
silently merge two deliberate resubmissions of the same document — which FR-005 requires to be two
runs.
