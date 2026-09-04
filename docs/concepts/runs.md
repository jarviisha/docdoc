# Runs

A **run** is one attempt to execute the pipeline over one document against one schema, out of band.
It exists because a 400-page scan takes several minutes, and holding an HTTP connection open for
that is a bad way to spend a load balancer's patience.

Nothing about what docdoc *produces* changes here. A result obtained through a worker and the same
result obtained synchronously agree on every value, verdict, location, and identity — asserted by
`tests/contract/test_async_matches_sync.py`, which is the criterion this whole area exists to
satisfy. The worker calls `pipeline.run()` and copies six fields out of what comes back.

## The two identities

This is the thing to understand first, and everything else follows from it.

| | `run_id` | `processing_id` |
|---|---|---|
| What it identifies | an **attempt** | a **result** |
| Where it comes from | allocated when the request is accepted | derived from the stage outputs |
| Shape | an opaque UUID | `sha256:…`, content-addressed |
| Exists from | submission | completion |
| Same document twice | two of them | one of them |

Submitting one document twice gives you **two run ids and one processing id**. That is the answer,
not a collision. The two submissions really were two attempts, and they really did produce one
result — the second one reused every artifact the first wrote and cost nothing.

The obvious alternative was to add a `pending` status to `GET /v1/jobs/{job_id}`, and it is not
possible. `job_id` **is** `processing_id`, which is the terminal artifact's identity, and that id is
derived from stage outputs which do not exist yet. There is no identifier under which a queued run
could be reported there, and inventing one would hand a caller something that resolves to nothing.
So asynchrony is a new resource rather than a new status. See
[ADR-0013](../adr/0013-asynchronous-run-model.md).

```bash
BLOB=$(curl -sX POST localhost:8000/v1/documents --data-binary @invoice.pdf | jq -r .blob_id)
RUN=$(curl -sX POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" | jq -r .run_id)
# 202 for a run that was created. Send an `Idempotency-Key` and a repeat answers
# 200 with the original run, so a client retrying through a flaky network learns
# whether its first attempt landed.

curl -s "localhost:8000/v1/runs/$RUN" | jq '{status, processing_id}'
# once succeeded, the *unchanged* job route serves the result:
curl -s "localhost:8000/v1/jobs/$(…processing_id…)/result"
```

The result is not served on the run resource. A succeeded run names its `processing_id` and the job
route returns the result — one result representation, reachable one way.

## The lifecycle

Five states, and the set is closed.

```text
                 ┌──────────► cancelled
                 │
  queued ────────┼──────────► running ──────► succeeded
     │           │              │  ▲
     │           │              │  └── lease lapsed, redelivered
     └───────────┴──────────────┴──────────► failed
```

- **queued** — accepted and recorded. Nothing has run.
- **running** — a worker holds a *lease* on it. The lease is the liveness signal: there is no
  registry of workers to disagree with reality.
- **succeeded** — carries a `processing_id`, and nothing else does. A database constraint enforces
  that, because it is what keeps the two identities distinguishable.
- **failed** — carries a `failed_stage` and an `error_class`, plus the outcomes of the stages that
  *did* complete. Asynchronously nobody is holding the response that used to be the only place a
  failure existed, so the run row has to be it.
- **cancelled** — stopped between stages. No `processing_id`, and no `failed_stage` either: nothing
  refused anything.

There is deliberately **no `expired`**. An earlier draft had one and described a retention sweep to
set it; no such sweep was built, and a state no code path can reach lies to everyone who reads the
enum. `expires_at` is recorded meanwhile so that retention work inherits a deadline rather than
inventing one.

A terminal state is terminal. No transition leaves `succeeded`, `failed`, or `cancelled`, and no code
path deletes a row.

## Redelivery, and why it is mostly "resume"

A worker takes a run by claiming it, which sets a lease. It extends that lease on a timer while it
works. If the process dies, the lease lapses and the run becomes eligible again — there is no reaper
process to deploy, monitor, and have fail silently; expiry is a clause in the claim query.

Delivery is therefore **at least once**, and that is safe for a specific reason rather than a
hopeful one: every completed stage wrote an artifact, and the redelivered attempt reuses all of
them. So it repeats *at most the stage that was in flight*, and it recomputes an identical
`processing_id`. Kill a worker between stages and the redelivered run costs nothing extra at all.

That is the artifact chain paying for something it was not designed for. Per-stage checkpointing was
built for prompt-change reuse, and it turns out to be crash recovery — because both ask the same
question: *what of this work is already known?*

**This only holds while the stages below are deterministic.** Re-executing a stage is safe precisely
because it cannot produce a different answer. That is why the determinism guard is load-bearing here
and not merely tidy, and why nothing in the run layer except `identity.py` may read a clock.

### The attempt limit

Three attempts, then the run comes to rest at `failed` with `error_class: "RunAbandonedError"`.

The failure this bounds is the poison document: one that terminates the process handling it, is
redelivered, terminates the next worker, and takes the pool one process at a time. Nothing logs an
error, because nothing survives to log one. Three, because **a document that terminates three
workers will terminate thirty** — retrying is for transient faults and this is not one.

`RunAbandonedError` means something specific and is reserved for it: the run was claimed, executed,
and lost its worker, that many times. An operator who reads it should go and look at the document. A
run whose *schema* stopped resolving fails once, terminally, with a schema error class and a null
`failed_stage` — precisely so that this word keeps naming only the poison case.

## Cancellation, and exactly what it does not do

```bash
curl -sX DELETE "localhost:8000/v1/runs/$RUN"
```

**A queued run never executes.** That is total: it moves to `cancelled` immediately and is never
claimed.

**A running run is a request, not a stop.** The response is `200` with `status: "running"`, and that
is not a bug in the response — it is the honest report:

- The worker consults the cancellation flag **at stage boundaries only**. A stage already executing
  runs to completion.
- So a provider call already in flight **completes and is billed**. Nothing is aborted.
- What is saved is the stage that had not started. Cancelling before the extract stage is the case
  with an economic point, because that is where the model call is.
- Poll `GET /v1/runs/{run_id}` to see it reach `cancelled`.

If you expected `DELETE` to return `cancelled` immediately, the contract disagrees on purpose. A
caller who saw `cancelled` would conclude the work had stopped, and would be wrong about their bill.

Cancelling a `succeeded` or `failed` run is refused with `409` naming the state. A succeeded run has
a stored result reachable by its identity, and calling it cancelled would make that result
unreachable through a lie about its history. Cancelling an already-cancelled run is `200`,
idempotently.

Under the hood this is `pipeline.run()`'s one additive parameter, `should_continue`, consulted
between stages and never inside one. Its default preserves every existing caller's behaviour byte
for byte. A cancelled run produces no terminal artifact, so there is no identity under which two
different results could be observed — which is why an input-sensitive `run()` is still
deterministic.

## Running the thing

Two processes, one image, one database, one shared store:

```bash
docker compose -f packaging/docker/compose.yml up -d      # api, worker, postgres, minio
docdoc migrate                                            # explicit, idempotent, never on boot
```

Migrations are never applied by a process starting. With several workers booting at once that would
be several processes altering one table, and a deployment's schema would depend on which container
won. `docdoc migrate --check` exits non-zero when anything is pending, which is what a rollout gates
on.

**One run per worker process.** There is no `--concurrency` flag, and its absence is a correctness
choice rather than a simplification: PyMuPDF and `rapidfuzz` hold the GIL in bursts, so a threaded
worker lets one long parse starve a sibling's heartbeat until that sibling loses a lease it is still
executing. Concurrency is replica count — `docker compose up --scale worker=3`.

**Scaling past one worker needs a shared store.** Give each worker a private `DOCDOC_STORE_ROOT` and
everything still works: every result is correct, nothing errors, no metric moves — and every worker
re-parses every document, because no lookup ever hits. The only evidence is the bill. Set
`DOCDOC_STORE_URL` to an object store, or share a filesystem.

On `SIGTERM` a worker stops claiming and **stops the run it holds at the next stage boundary**, then
releases it so it re-queues immediately. That matters because the alternative is not "the run
finishes": a run takes minutes and an orchestrator's grace period is seconds, so a worker that
insisted on finishing would be killed mid-run and the run would then wait out its whole lease. Every
completed stage kept its artifact, so whoever picks it up resumes rather than restarts.

## Health

Both process types serve the same two routes, on the same terms, so one orchestrator configuration
covers both:

- `GET /healthz` returns a constant and touches nothing. A liveness probe that checked a dependency
  would restart every replica for a fault none of them has.
- `GET /readyz` reaches the run-state database and the store, caches the outcome for two seconds, and
  names what it cannot reach: `{"status": "not_ready", "unmet": ["run-state-database"]}`.

Readiness is **strict**. A process that cannot reach the database reports not ready even though the
synchronous routes would still serve every request correctly. That withdraws working capacity on
purpose: the alternative is a per-capability readiness signal, and no orchestrator's probe can
express "route the synchronous half here", so a richer answer would be one nothing could consume.

Both routes are outside `/v1` and outside authentication, and neither discloses a configuration
value, a credential, a tenant identifier, or a count of anything stored.

## Tenants

Off by default. Point `DOCDOC_API_KEYS_FILE` at a key file and every route except the two health
routes requires `Authorization: Bearer <key>`; each key resolves to exactly one tenant.

Each tenant's content is namespaced in the store — `<root>/t/<tenant_id>/…` — **above** the
two-character fan-out, so per-tenant deletion is a prefix operation rather than a scan. Identity is
untouched: two tenants processing identical bytes derive identical `blob_id`s and `processing_id`s
independently. Only the location differs.

The namespacing is not about tidiness. Because identity is derived from content, a shared store
would let one tenant learn that another holds a document by submitting it and observing that the
result came back instantly and cost nothing. A status code cannot close that; separate namespaces
can. The price, paid knowingly, is that two tenants with the same invoice pay for two parses.

**The default tenant's namespace is the store root itself.** Content written before this existed
sits at `<root>/blobs/…` and stays exactly there — no copy, no move, no read-through fallback — which
is what makes upgrading a no-op. If that content belongs to a named tenant, say so with
`DOCDOC_DEFAULT_TENANT`; `docdoc migrate` records the answer and refuses to change it afterwards,
because moving it later would strand everything at a path nothing looks at. See
[ADR-0014](../adr/0014-tenant-scoping-and-store-namespacing.md).

## What is deliberately absent

- **No broker, coordinator, or scheduler process.** The queue is a table claimed with
  `FOR UPDATE SKIP LOCKED`, and an import contract enforces that rather than trusting review.
- **No ORM and no migration framework.** One table, a handful of statements, and the one that
  matters needs `SKIP LOCKED` — which an ORM obscures rather than helps.
- **No run priority and no fairness policy.** Claims are oldest-first and that is the whole ordering.
- **No retention sweep.** `expires_at` is recorded and nothing reads it yet.
- **No metrics exporter.** One `run.transition` event per state change goes to standard-library
  logging, carrying identifiers, states, and counts — never a duration, a cost, or anything from a
  document. Binding an exporter is later work; this gives it something to bind to.

## What the log says

One `run.transition` event per state change, emitted by the queue rather than by
its callers — so a transition cannot happen without an event, and a caller cannot
forget:

```text
submitted     -> queued        a run came into existence (from_state: null)
queued        -> running       claimed
running       -> running       redelivered: a lease lapsed and another worker took it
running       -> queued        released: a worker was signalled and let go
running       -> failed        RunAbandonedError: the attempt limit
queued|running-> succeeded|failed|cancelled   finished
running       -> running       cancel_requested: asked, not yet stopped
```

Each carries `run_id`, `tenant_id`, `from_state`, `to_state`, `attempts`,
`worker_id`, and a `reason` — and nothing else. No duration, no token count, no
cost, no stage result: the per-stage events already state each run's cost once,
and a second statement of it would drift. No document text, extracted value,
prompt body, credential, or provider message either.

Two non-events are deliberate. An idempotent replay emits nothing, because
nothing transitioned — a retrying client is not a queue filling up. And a
redelivered attempt that finishes a run the first one already concluded emits
nothing, for the same reason.
