# ADR-0013: The Asynchronous Run Model, and the Second Identity It Requires

- **Status**: Accepted
- **Date**: 2026-08-28
- **Amends**: [ADR-0010](0010-artifact-store-and-job-model.md) §6, which decided the job model was synchronous
- **Implements**: Milestone 9 (`specs/009-asynchronous-runs/spec.md`)
- **Principles engaged**: III (Determinism), VIII (Reproducibility and provenance), X (Layer direction), XI (Scale through boundaries)

## Context

ADR-0010 §6 decided that a job is a synchronous run identified by its terminal artifact, and it
argued the point rather than assuming it:

> The obvious reading of "job status" is asynchronous, and it is wrong twice. The deferred-technology
> list forbids the queue it would need; and a job id that *is* the terminal artifact id cannot be
> issued before the run, because that id is not knowable until the stages feeding it have run.
> Synchronous execution dissolves the problem rather than working around it.

Both halves were correct when written and one of them still is. What has changed is not the argument
but the deployment: the MVP is complete, and the first production deployments will put docdoc behind
a gateway.

**The synchronous model fails there in a specific and expensive way.** A deployment accepts
1000-page documents by default and routes scans to a billable cloud parser. `README.md` already warns
that a proxy "must allow a request duration at least as long as your slowest extraction, or it will
terminate runs you have already paid for". That is not a caveat an operator can configure away — no
gateway is willing to hold a connection for the tail of that distribution — and when the connection
drops, the run does not stop. It completes, writes its artifacts, and hands its result to nobody.
The system is at its least honest exactly where it is most expensive.

The queue objection is also weaker than it looked. The deferred-technology list names Kafka,
Temporal, Kubernetes, multi-region deployment, and distributed DAG engines. It forbids *distributed
infrastructure*, and its rationale says why: projects fail "by building distributed infrastructure
before they have a correct document model". Eight milestones produced the correct document model, and
what this needs is not a broker but a table in the database the sanctioned stack already permits.

The identity objection, however, has not weakened at all. It is the reason this ADR exists rather
than a patch to the jobs endpoint.

## Decision

### 1. Two identities, because one cannot be issued at two different times

`processing_id` is the terminal artifact id, derived from stage inputs per ADR-0003. At the instant a
request is accepted, no stage has run, so the value does not exist. `pending` therefore cannot be
added to `GET /v1/jobs/{job_id}`: there is no identifier under which to report it. ADR-0010 was right
about this and nothing here contradicts it.

So a second identity is introduced, and the first is left untouched:

| | What it is | Answers | Exists from |
|---|---|---|---|
| `processing_id` | terminal artifact id, content-addressed | *which result is this* | the run completing |
| `run_id` | opaque, allocated on acceptance | *which request of mine was that* | the request being accepted |

The pair is not redundancy. Submitting the same document against the same schema twice yields **two
`run_id`s and one `processing_id`**, and that is the correct answer rather than a collision: the
second attempt reuses every artifact the first wrote, calls no billable provider, and arrives at the
same result. A run is an *attempt*; a processing id is a *result*. A system with one identifier
cannot say "you asked twice and the answer was already known" — and that sentence is the entire
economic argument for the artifact chain.

This mirrors ADR-0002 one level up. That ADR separated `blob_id` from `document_id` because two
parses of one file must not share positions. This separates an attempt from a result for the same
class of reason: two things that differ in lifetime must not share a name.

**Async is therefore a new resource, not a new state on an old one.** `GET /v1/jobs/{job_id}` keeps
its route, its three statuses, and its closed set. No existing caller observes a change.

### 2. Run state is a mutable row, and it does not live in the artifact store

Run state is the first thing this project has needed that the artifact store cannot hold, and the
reason is a property that store was deliberately given rather than a limitation to be lifted. ADR-0010
§5 makes refusing to overwrite an existing id a correctness guarantee — it is how a processor whose
output moved while its version did not gets caught. Run state is *defined* by overwriting:
`queued → running → succeeded`.

It is also queried by predicate rather than by key — claim the next eligible run, expire the abandoned
ones — and ADR-0010 §1 rejected even SQLite for artifacts precisely because content-addressed
immutable creates need no transaction and no query. That argument is sound for artifacts and exactly
inverted here.

So: **PostgreSQL, for run state only.** This is the constitution's "only where persistence is
genuinely required" being satisfied for the first time, not circumvented. The separation runs both
ways and is load-bearing: no run row is an artifact, and no artifact is mutated to carry run state.
An artifact remains something a run *produced*, never something a run *is*.

### 3. The queue is that same table, claimed with `SELECT … FOR UPDATE SKIP LOCKED`

No broker, no coordinator, no scheduler process. A worker claims the oldest eligible run under a
time-bounded lease, extends the lease while it works, and releases it on completion. A lease that
expires makes the run claimable again, which is what turns a killed process into a delay rather than a
lost run.

This is chosen over Redis, which is not in the sanctioned stack and would need a constitutional
amendment to add, and over Kafka and Temporal, which are on the deferred list outright. It is also
chosen over an in-process thread pool, which loses every queued run when the API restarts and cannot
be scaled independently — the two properties the topology change exists to buy.

A run exceeding a configured attempt limit comes to rest in a terminal failed state naming
abandonment. Without that bound, a document that reliably terminates a worker is redelivered forever
and kills the pool one process at a time.

### 4. Delivery is at-least-once, and content-addressing is what makes that safe

A worker that dies mid-run has its lease expire and its work redelivered. Re-execution is safe
because it is not really re-execution: the stages that completed wrote artifacts, the redelivered
attempt reuses them, and only the stage in flight at the interruption repeats. ADR-0010 §5 makes
writing identical content twice a no-op, so the race is benign without a lock. A redelivered run
therefore produces the **same `processing_id`** an uninterrupted one would have.

This is the ADR-0003 artifact chain paying for something it was not designed for. Per-stage
checkpointing was built for prompt-change reuse; it turns out to be crash recovery as well, because
both are the same question — *what of this work is already known?*

**The exposure that remains is stated rather than hidden.** If the interrupted stage is the cloud
parser or the model adapter, that stage is billed twice. The bound is one stage per interruption, not
one run. An at-most-once design would need an in-flight marker written before the provider call, and
that marker has its own crash window between write and call — the problem moves one level down and
acquires a second failure mode on the way. At-least-once with a one-stage bound is accepted.

### 5. Cancellation is cooperative, and the documentation must not imply otherwise

A queued run is cancelled by never being claimed. A running run observes cancellation **at stage
boundaries**, so cancelling stops the *next* billable stage and nothing currently in flight. An HTTP
call to a cloud parser that has already been made will complete and will be paid for.

This is recorded as a decision because the honest version is the less impressive one, and because the
project has said it before: `README.md` tells viewer users that closing a browser "stops the waiting
and not the work". A cancel endpoint that implied more would be the same lie with a nicer interface.
A cancelled run keeps the artifacts its completed stages wrote and carries no `processing_id`,
because no terminal artifact was produced.

### 6. Failed runs are recorded, because the response is no longer the record

Today a failed run produces no terminal artifact and therefore no job, so the HTTP error response is
the only place the failure exists — `src/docdoc/api/app.py` says so in a comment at the point it
returns one. That is sound synchronously, because the caller is holding that response.

Asynchronously the caller is holding nothing. A failed run that is not persisted is a run that
silently never happened, so the run row records the failing stage, the error **class name**, and the
outcomes of the stages that completed. The class name and not the message: that is the rule
`PipelineResult` and `pipeline/observe.py` already follow, for the reason they already give — a
message can quote the document it choked on.

### 7. Identifiers and clock readings are allocated in the `Runs` layer

`run_id` is random and a lease deadline is a clock reading. The kernel performs no clock, file,
network, or random access, enforced by an AST scan and a runtime audit hook, and the guard is not
relaxed, narrowed, or granted an exemption for this milestone.

That constraint is treated as a design aid rather than an obstacle. `run_id`, `created_at`,
`lease_until`, and `expires_at` are produced in the new `Runs` layer and travel downward as data.
Everything at or below `Pipeline` stays a pure function of its inputs — which is not a formality but
the precondition for §4: redelivery is only safe because re-executing a stage cannot produce a
different answer.

`Runs` sits above `Pipeline` and is declared **independent of** `Recording`, enforced by the existing
import contract. Neither uses the other, and an ordered position would assert a relationship that does
not exist — the same reason `API` and `CLI` are already siblings rather than a stack.

## Consequences

**ADR-0010 §6 is amended, and §1 through §5 stand unchanged.** The store layout, the two hashes, the
format-version rule, the four read outcomes, and the no-overwrite rule are all relied on more heavily
by this decision than before, not less. Only the sentence declaring the job model synchronous is
superseded, and the synchronous routes it describes keep working exactly as specified.

**A deployment now has four process types where it had one**, and the operational floor rises with
it: a database that must be migrated, workers that must be scaled, and leases that must be tuned
against the slowest realistic document. This is a real cost, paid to stop discarding runs that have
already been billed.

**`POST /v1/extract` gains no asynchronous variant, and cannot.** ADR-0012 defines that route as
persisting nothing, unconditionally, as a property of the endpoint rather than of the deployment.
Asynchrony requires the result to outlive the request, which requires persistence. The two are
incompatible by construction, and the boundary is left clean rather than special-cased.

**The synchronous path is not deprecated.** It remains the right answer for small documents, for the
command line, and for the in-process library, none of which acquire a database dependency in any
configuration. This adds a path; it removes none — the same sentence ADR-0012 ended on, and true here
for the same reason.

**Determinism is now load-bearing for correctness, not only for reproducibility.** Before this ADR, a
non-deterministic stage would have produced confusing artifact identities. After it, one would produce
a redelivered run that disagrees with itself. The guard that was a quality property is now a
safety property.

## Alternatives considered

**Add `pending` to `GET /v1/jobs/{job_id}`.** The obvious design, and impossible: the identifier is
derived from stage outputs that do not exist at accept time. Any version of it requires either an id
that is not content-addressed or a status reported under an id nobody can hold.

**Make `run_id` a hash of the submission — document, schema, and a nonce.** Would have given one
identifier type instead of two. Rejected because a derived-looking identifier that is not the
artifact identity is worse than an obviously opaque one: it invites a caller to reason about it, and
every such inference is wrong.

**Keep the run in-process and stream progress over the connection.** Removes the queue and solves
nothing, because the failure being addressed is the connection ending. It also leaves restarts losing
every in-flight run.

**Write run state into the artifact store as a mutable envelope.** Would have avoided the database
entirely. Rejected because it requires disabling ADR-0010 §5's no-overwrite rule for one envelope
kind, and that rule is the only place the system can detect a processor whose output moved while its
version did not. Trading a correctness guarantee for an infrastructure saving, in the layer whose
whole purpose is provenance, is the wrong direction.

**Redis with a worker framework.** Mature, well-understood, and outside the sanctioned stack. It would
have needed a constitutional amendment to justify a component that a table in an already-permitted
database replaces. The simpler option was not provably inadequate, which is the test Principle XI
sets.

**At-most-once delivery via an in-flight marker.** Would bound the double-billing exposure to zero in
the common case. Rejected in §4: the marker's own crash window reintroduces the problem and adds a
state that can be wrong in a new way. Revisit if provider spend, measured rather than assumed, makes
one stage per interruption material.
