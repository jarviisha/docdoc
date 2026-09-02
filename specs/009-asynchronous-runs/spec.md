# Feature Specification: Asynchronous Runs, Shared Storage, and Tenant Scoping

**Feature Branch**: `009-asynchronous-runs`

**Created**: 2026-08-28

**Status**: Draft

<!--
  Status vocabulary, and who moves it. Every spec in this repository read "Draft"
  through Milestone 5 — including five that had shipped and merged — because
  nothing ever moved it. A field nobody maintains is a field that lies, so the
  transitions are named here and each one is owned by a step that already runs.

    Draft        still being written; /speckit-clarify may still change answers
    Accepted     clarified, planned, and tasked; implementation not started
    Implemented  the behaviour this spec describes exists and is merged
    Superseded by NNN-name   replaced by a later spec

  Draft -> Accepted is manual, done when tasks.md is first produced. Nothing
  enforces it. Accepted -> Implemented is wired: every milestone's task list
  carries a README-roadmap task, and that task now flips this field too, so the
  transition that matters most rides on a step the milestone already has to do.
-->

**Input**: User description: "dự án này đã qua MVP. giờ chuẩn bị cho giai đoạn tiền production" —
followed, once the scope was cut, by "phần bất đồng bộ sẽ xử lý như nào?" and a decision to bound this
milestone to **one change of deployment topology** and nothing else.

## Why Milestone 9 exists, when Milestone 8 said the MVP was complete

Nothing here retracts that. Milestones 1–8 produced a correct document model and made its guarantees
visible; this milestone adds **no stage, no provider, no schema, and no change to any value docdoc
produces**. Milestone 8 said its own version of this sentence and meant it about visibility. This one
means it about *where docdoc runs*.

Today docdoc is one process that does everything inside one HTTP request. That is the right shape for
an MVP and the wrong shape for a first production deployment, for a reason the codebase already
states out loud. `README.md` warns that a proxy in front of the viewer "must allow a request duration
at least as long as your slowest extraction". A deployment that accepts 1000-page documents by
default and routes scans through a billable cloud parser will exceed any gateway timeout an operator
is willing to configure, and when it does, the run is not cancelled — it is **paid for and
discarded**, because closing the connection stops the waiting and not the work.

Principle XI anticipated exactly this and named the shape of the answer: local synchronous execution
"MUST be able to evolve into API → queue → workers ... without rewriting the domain model". This
milestone performs that evolution and holds it to the second half of the sentence.

**One milestone, one change of topology.** After this, docdoc is an API process, one or more worker
processes, a run-state database, and a shared object store. Everything genuinely required to make
that topology work is in scope. Everything that could be bolted on afterwards without moving a
process boundary is not — observability export, data-retention cascade, quotas, webhooks, corrections
and confidence routing are all deferred to Milestone 10, and Out of Scope says so by name. Changing
the deployment shape twice costs more than doing it once.

## The identity that cannot be issued early, which is the whole design

ADR-0010 §6 chose a synchronous job model, and it did not choose it casually. It wrote down the
objection this milestone has to answer:

> The obvious reading of "job status" is asynchronous, and it is wrong twice. The deferred-technology
> list forbids the queue it would need; and a job id that *is* the terminal artifact id **cannot be
> issued before the run**, because that id is not knowable until the stages feeding it have run.

The second half is not an argument about taste. `job_id` is `processing_id` is the terminal artifact
id, derived from the stage inputs per ADR-0003. At the moment a request is accepted, no stage has
run, so the value does not exist. It follows that **`pending` cannot be added to
`GET /v1/jobs/{job_id}`**, because there is no id under which to report it. Any design that tries
produces an identifier that either is not content-addressed or is not available at accept time, and
each of those breaks a promise the system currently keeps.

So this milestone introduces a **second identity**, and keeps the first one exactly as it is:

| | What it is | Answers | Exists from |
|---|---|---|---|
| `processing_id` | terminal artifact id, content-addressed (ADR-0003) | *which result is this* | the run completing |
| `run_id` | an opaque identifier allocated on acceptance | *which request of mine was that* | the request being accepted |

The two answer different questions, which is why merging them fails. Submitting the same document
against the same schema twice yields **two `run_id`s and one `processing_id`** — and that is the
correct outcome rather than a collision to suppress. The second run reuses every artifact the first
one wrote, completes without calling a billable provider, and points at the same result. A run is an
*attempt*; a processing id is a *result*; a system that conflates them cannot express "you asked
twice and the answer was already known".

The API consequence is that async is a **new resource** rather than a new state on the old one.
`GET /v1/jobs/{processing_id}` keeps its three statuses and its closed set, unchanged, and no existing
caller sees a difference.

## Why this needs one constitutional amendment and not three

Three constraints could plausibly be read to block this milestone. Two of them do not, on argument
recorded below. The third did, and was amended rather than argued around — see the end of this
section. All three are addressed here so that no implementer has to reconstruct the reasoning, and so
that a reviewer can disagree in one place.

**The deferred-technology list.** It names Kafka, Temporal, Kubernetes, multi-region deployment, and
distributed DAG engines. A work queue implemented as a table in the database the sanctioned stack
already permits is none of those. The list forbids *distributed infrastructure*, and its rationale
says so: "most IDP projects fail either by shipping a demo with no boundaries or by building
distributed infrastructure before they have a correct document model." Eight milestones produced the
correct document model; this milestone adds one table and one process type, and no broker,
no coordinator, and no scheduler.

**"PostgreSQL only where persistence is genuinely required."** Run state is the first thing in this
project that genuinely requires it. It is **mutable** — `queued → running → succeeded` — and it is
**queried by predicate** rather than by key: claim the next eligible run, expire the abandoned ones.
The artifact store can serve neither, and it is not a limitation to be lifted: ADR-0010 §5 makes
refusing to overwrite an existing id a deliberate correctness property, and ADR-0010 §1 rejects even
SQLite for the artifact store because content-addressed immutable creates need no transaction. That
argument is sound *for artifacts* and is precisely inverted here. Run state is therefore not stored
in the artifact store, in either direction — no run row is an artifact, and no artifact is mutated to
carry run state.

**Principle XI's domain-model clause.** It is satisfied literally rather than in spirit.
`pipeline.run()` is already a function from inputs to a result, and the store is already the place a
worker writes to; ADR-0010 §6 said as much when it deferred this work. The worker claims a row, calls
that function unchanged, and records what came back. No stage, no kernel type, no artifact envelope,
and no identity derivation is touched by this milestone — a claim SC-012 measures rather than asserts.

**"Development Compose contains only api, postgres, and object storage."** This milestone's
composition has a fourth container, and the word is "only". An earlier draft of this section argued
that the sentence governs third-party infrastructure — the list it terminates is Kafka, Temporal,
Kubernetes, vector databases, workflow engines — and that a worker is not infrastructure the project
acquires but the docdoc image already present at a different entry point.

**That argument was sound and the instrument was wrong.** Governance states that where a spec
conflicts with the constitution, the constitution wins; a spec that reinterprets a constitutional
sentence in order to comply with it inverts that precedence, however openly the reinterpretation is
argued. `/speckit-analyze` raised it as CRITICAL on exactly that ground.

So the sentence was amended instead. **Constitution v1.6.0 (2026-08-28)** adds `worker` to it, and
clarifies in the same change that the deferred "multi-tenant billing" forbids billing rather than
tenant isolation — the two are one phrase apart and this milestone does the second and none of the
first. The amendment's own rationale records why a literal three-container reading could not stand:
Principle XI mandates that local synchronous execution be able to become "API → queue → workers", so
the document required an evolution its own scope constraint forbade demonstrating.

What the milestone does need is **ADR-0013**, which amends ADR-0010 §6. That section is a Decision
with a Status of Accepted, and superseding part of it silently is exactly the failure the ADR
directory exists to prevent. It also needs **ADR-0014** for tenant scoping, which is a separate
question with its own security rationale — it would be required even if docdoc stayed synchronous.

## The two guards that will reject a naive implementation

Neither is a nuisance; both are the repository enforcing a boundary this milestone must respect
anyway.

**Determinism.** The kernel performs no clock, file, network, or random access, enforced by an AST
scan and a runtime audit hook rather than by convention. A run identifier is random and a lease
deadline is a clock reading, so a straightforward implementation that generates either inside the
pipeline turns CI red. The resolution is not to weaken the guard but to observe that it is pointing
at the correct design: `run_id`, `created_at`, `lease_until`, and `expires_at` are allocated in the
**Runs layer** and travel downward as data. The pipeline continues to be a pure function of its
inputs, which is what makes redelivery safe at all.

**Layer direction.** `Runs` is a new layer that calls `Pipeline`. It neither uses nor is used by
`Recording`, so an ordered position would assert a relationship that does not exist; the two are
declared siblings under an `independence` contract, exactly as `API` and `CLI` already are.

## Clarifications

### Session 2026-08-28

- Q: Does Milestone 9 carry the whole pre-production list — observability export, retention, quotas,
  webhooks — or only what a topology change requires? → A: **Only the topology change.** The cut is
  by a single test: does it stop the four-process deployment from working? Shared object storage
  passes (workers with private store roots break artifact reuse silently), health endpoints pass (a
  four-container compose cannot be scheduled without them), tenant scoping passes (the run table
  needs the column at creation or it is a backfill later). OpenTelemetry, retention cascade, quotas,
  and webhooks all fail it and move to Milestone 10.
- Q: Postgres, or a broker? → A: **Postgres**, claiming work with `FOR UPDATE SKIP LOCKED`. Redis is
  not in the sanctioned stack and would need an amendment to add; Kafka and Temporal are on the
  deferred list outright. Postgres is already sanctioned, and the queue is one table.
- Q: Does `POST /v1/extract` gain an asynchronous variant? → A: **No, and it cannot have one.**
  ADR-0012 defines that route as writing nothing, unconditionally, as a property of the endpoint
  rather than of the deployment. Asynchrony requires the result to be retrievable after the request
  ends, which requires persistence. The route stays synchronous, and the incompatibility is a clean
  boundary rather than a gap.
- Q: Is the auth in this milestone a complete authentication story? → A: **No, deliberately.** It is
  the minimum that lets a tenant identifier exist: a static key-to-principal mapping loaded at
  startup. Key issuance, rotation, revocation-at-runtime, and per-tenant quotas are Milestone 10.
  What cannot wait is the `tenant_id` column, because adding it to a populated table later is a
  migration with a backfill whose correct value is unknowable.
- Q: Is the content-addressed store shared across tenants, or namespaced per tenant? → A:
  **Namespaced per tenant.** Sharing it maximises reuse and opens a cross-tenant existence oracle
  that no response body can close: `processing_id` is derived from content, so a tenant who submits a
  document another tenant has already processed observes an instant, unbilled result and has thereby
  learned that the other tenant holds it. FR-066 requires cross-tenant reads to be indistinguishable
  from non-existence, and latency and a provider invoice are not things a status code can make
  identical. The cost is that two tenants submitting the same bytes pay for two parses; that is
  accepted, because cross-tenant duplicates are rare outside narrow industries while the oracle is
  present in every deployment. Reuse **within** a tenant — the case ADR-0003 was written for — is
  unaffected.
- Q: When authentication is switched on over a deployment that already holds blobs and artifacts,
  what tenant does that content belong to? → A: **Authentication is off by default, and a deployment
  without it has exactly one implicit tenant that owns everything.** Any other answer either breaks
  an existing deployment on upgrade or invents an owner nobody can verify. Turning authentication on
  is an operator's explicit act, and at that moment the pre-existing content is assigned to a named
  default tenant, stated in configuration rather than assumed. ADR-0011 promises a deprecation path
  rather than a silent break for anything that invalidates data on somebody else's disk, and this is
  that path.
- Q: When the run-state database is unreachable, does readiness fail outright, or stay ready because
  the synchronous routes need no database? → A: **It fails outright.** Readiness is a binary signal
  read by infrastructure that cannot express "ready for some routes", and a signal that answers a
  question nobody asked is worse than a strict one. The consequence — losing synchronous capacity
  that would still have worked — is real and is documented rather than hidden. A deployment that
  genuinely wants to degrade instead of withdraw is making a deliberate availability choice, and that
  belongs in ADR-0013, not inside a health check.
- Q: With authentication off, where does previously stored content live — under a namespace prefix or
  where it already is? → A: **The default tenant's namespace is the store root itself.** Content for
  the default tenant stays at `<root>/blobs/…` and `<root>/artifacts/…`; every other tenant lives
  under `<root>/t/<tenant_id>/`. This was raised because FR-084, R12, and T055 said namespacing was
  unconditional while SC-018 required pre-existing content to remain readable with authentication at
  its default — and those two cannot both be true, since existing content carries no prefix. The
  alternatives each bought uniformity with a real cost: relocating a whole bucket on upgrade, or a
  permanent read-through fallback that pays a second round trip on every **miss**, which is the
  common case for a new document. The price of this answer is one compatibility branch in path
  derivation, stated as a rule rather than discovered as behaviour.
- Q: How many runs does one worker process execute concurrently, and by what mechanism? → A: **One.**
  A worker claims a run, executes it, and claims the next; concurrency comes from running more worker
  processes. `--concurrency` is therefore not a flag. Threads were the alternative and were rejected
  on a specific hazard rather than on taste: PyMuPDF and `rapidfuzz` hold the GIL in bursts, so a
  1000-page parse in one thread can delay the heartbeat of runs in sibling threads far enough that
  they lose leases **while still executing** — turning a resource decision into a correctness bug.
  One run per process also matches what `SKIP LOCKED` was chosen for: claim contention was already
  designed across processes. The cost is a full Python process per concurrent run, which is memory
  spent to buy the absence of a shared-state question.
- Q: A run is queued; the deployment is reconfigured and its schema identity no longer resolves. What
  happens when a worker claims it? → A: **It fails terminally on the first occurrence, classified as a
  schema error, and does not consume the retry budget.** A withdrawn schema is a configuration fault,
  not a transient one, so retrying it only spends attempts and then mislabels the outcome as
  `RunAbandonedError` — a word that names the wrong cause. This is the rule `extraction/retry.py` already
  applies one layer down ("every refusal fail on the first attempt"). *Implementation note*: the claim
  statement increments `attempts` atomically (R8) and that is not undone; "does not consume the retry
  budget" is satisfied by the run being terminal at once, never by a compensating decrement.
  Re-queueing to wait for the schema to return was rejected — it produces a run waiting indefinitely
  with nothing to distinguish "waiting for a schema" from "waiting for a worker", which the five-state
  set cannot express. Pinning the schema's content into the run row was rejected because it makes the
  run table a second home for schema content, which ADR-0013 §2 refuses in both directions, and
  because `schema_hash` (ADR-0008) already answers "did anything result-affecting change".
- Q: Does the `Runs` layer emit structured events of its own, given that `pipeline/observe.py`
  deliberately refuses a run-level event? → A: **Yes — one `run.transition` event per state change,
  and no summary.** The refusal in `pipeline/observe.py` is against a *fifth event summarising the
  four*, on the ground that it would be "a second place where the cost of a run is stated". A
  transition event states no cost: it carries the identities, the states, the attempt count, and the
  reason, and nothing about duration, tokens, or stage results — so the objection does not reach it.
  What makes this necessary rather than tidy is that asynchrony moved real events outside every
  stage: a claim, a lease expiry, a redelivery, a cancellation, an abandonment, and — as of FR-091 —
  a failure that touches no stage and therefore emits no `pipeline.stage` event at all. Without this
  the hardest thing in the new topology to debug is the only thing that is silent. **This is not
  early OpenTelemetry work**: it uses the standard-library logging the five existing `observe.py`
  modules already use, and Milestone 10 still owns the exporter — it simply now has something to bind
  to instead of adding an emission point and an exporter in one change.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submit a slow document without holding a connection open (Priority: P1)

Someone submits a 400-page scanned PDF that routes to the cloud parser. Instead of holding an HTTP
connection for the several minutes that takes — through a gateway that will not allow it — they get
an identifier back immediately and poll it. When the run finishes, the identifier tells them so and
hands them the same result the synchronous route would have produced.

**Why this priority**: This is the milestone. Every other story here exists to make this one
deployable rather than to add capability of its own.

**Independent Test**: Submit a document to the run endpoint, observe a run identifier returned before
the pipeline has executed, poll until the status is terminal, and fetch a result identical to the one
the synchronous route produces for the same document and schema.

**Acceptance Scenarios**:

1. **Given** a stored document and a configured schema, **When** a run is submitted, **Then** the
   response is immediate, carries a run identifier, and carries **no** processing identifier, because
   none exists yet.
2. **Given** a run that has not been claimed, **When** its status is requested, **Then** it reports
   queued, and the response says plainly that no result exists yet rather than reporting an error.
3. **Given** a run that has completed, **When** its status is requested, **Then** it reports succeeded
   and carries the processing identifier, from which the existing job routes serve the result
   unchanged.
4. **Given** the same document and schema submitted twice, **When** both runs complete, **Then** they
   carry two different run identifiers and one identical processing identifier, and the second run
   invokes no billable provider.

---

### User Story 2 - Find out why a run failed, without having been there (Priority: P1)

A run fails partway — the provider refused, the document was malformed, the schema was withdrawn. The
submitter was not holding a connection when it happened. They ask the run what became of it and are
told which stage failed, what class of error it was, and what the stages that did complete produced.

**Why this priority**: P1 alongside Story 1 rather than below it, because without it Story 1 ships a
system that loses failures. Today a failed run produces no terminal artifact and therefore no job, so
the HTTP error response is the only place the failure ever exists — `src/docdoc/api/app.py:317`
records that in a comment. Synchronously that is sound, because the caller is holding that response.
Asynchronously the caller is holding nothing, so a failed run that is not persisted is a run that
silently never happened.

**Independent Test**: Configure an adapter that fails, submit a run, and confirm that after the worker
gives up, the run reports a terminal failed status naming the failing stage and error class, and
carries the outcomes of the stages that completed.

**Acceptance Scenarios**:

1. **Given** a run whose extraction stage fails, **When** its status is requested, **Then** it reports
   failed, names the stage, names the error class, and carries no processing identifier.
2. **Given** that same run, **When** the failure detail is read, **Then** it contains no document text,
   no extracted value, no prompt body, and no provider message — identifiers, classes, and counts
   only, exactly as the existing observability rule requires.
3. **Given** a run that repeatedly kills the worker process, **When** the attempt limit is reached,
   **Then** the run comes to rest in a terminal failed state naming abandonment, and no further worker
   claims it.

---

### User Story 3 - Run more than one worker without paying twice (Priority: P2)

An operator scales from one worker to three. Documents already parsed stay parsed: a run that lands on
a worker which has never seen the document still reuses the parse another worker performed, and the
billable parser is not called a second time.

**Why this priority**: P2 because a single worker is a working deployment and three is an
optimisation — but it is listed above health and auth because getting it wrong is invisible. Workers
with private store roots produce *correct results* while silently re-paying for every parse. A bug
that costs money and passes every test is worse than one that fails.

**Independent Test**: Run two workers against one shared object store, submit the same document twice,
and assert on a counter that the parser executed exactly once.

**Acceptance Scenarios**:

1. **Given** two workers sharing an object store, **When** a document already parsed by one is
   submitted again **by the same tenant** and claimed by the other, **Then** the parse artifact is
   reused and the parser is not invoked.
2. **Given** the same two workers, **When** both complete runs over the same inputs concurrently,
   **Then** identical artifact content written twice is a no-op and neither run fails, per the
   existing store's concurrency rule.
3. **Given** an object store that becomes unreachable mid-run, **When** the stage completes, **Then**
   the run proceeds without reuse and is logged once, matching the existing "root unwritable" rule
   rather than inventing a second behaviour for the same condition.

---

### User Story 4 - Deploy it behind a load balancer (Priority: P2)

An operator brings up the four containers and points a load balancer at the API. Each process answers
whether it is alive and whether it is ready to accept work, so a process that cannot reach its
database or its store is taken out of rotation instead of accepting runs it cannot service.

**Why this priority**: P2 because it adds no capability, and mandatory because a four-process topology
cannot be scheduled by any orchestrator without it.

**Independent Test**: Start the API with its database unreachable and confirm liveness passes while
readiness fails, naming the unmet dependency.

**Acceptance Scenarios**:

1. **Given** a running API process, **When** liveness is probed, **Then** it answers without touching
   the database, the object store, or any provider.
2. **Given** an API process whose run-state database is unreachable, **When** readiness is probed,
   **Then** it reports not ready and names the unmet dependency, and the run-submission route refuses
   with a retryable status.
3. **Given** a worker process, **When** it is probed, **Then** it answers on both the same terms as the
   API, so one orchestrator configuration covers both process types.

---

### User Story 5 - Keep one tenant out of another tenant's results (Priority: P2)

Two customers share a deployment. Each presents a key. Neither can read the other's runs or results,
including by guessing an identifier.

**Why this priority**: P2 in sequencing and non-negotiable in content. A shared deployment without it
is not deployable for anyone but its author, and the column it requires cannot be added afterwards
without a backfill whose correct value nobody knows.

**Independent Test**: Submit runs under two keys and confirm that each identifier — run and processing
alike — is unreadable under the other key.

**Acceptance Scenarios**:

1. **Given** a run created under tenant A, **When** it is requested under tenant B's key, **Then** the
   response is identical to the response for an identifier that never existed.
2. **Given** a request carrying no key or an unknown key, **When** any route other than liveness is
   called, **Then** it is refused before any document is read, any provider is called, or any store is
   touched.
3. **Given** a blob submitted by tenant A, **When** tenant B submits a run against that blob
   identifier, **Then** the run is refused as though the blob did not exist.

---

### User Story 6 - Stop a run that should not have been submitted (Priority: P3)

Someone submits the wrong document, points at the wrong schema, or fires a batch by mistake. They ask
the run to stop. If it has not started, it never runs. If it has, it stops at the next stage boundary
— before the model call, if the model call has not happened yet — and they are told plainly that
anything already in flight was not recalled.

**Why this priority**: P3 because nothing is *stuck* without it. Every run is bounded by the attempt
limit and the pipeline's own completion, so a deployment with no cancellation still reaches a terminal
state for every run it accepts. What cancellation buys is **money**, not liveness: the boundary worth
catching is the one between parse and extract, where the model call has not been paid for. That makes
it valuable and not urgent, which is exactly P3.

**Why this story exists at all, stated because it was nearly missed**: cancellation carried eight
functional requirements and a success criterion through spec, plan, and task list **without a single
user journey describing who needed it**. `/speckit-analyze` surfaced that on 2026-08-28. A requirement
with no story is a requirement nobody can prioritise, test against a real need, or delete on purpose —
and eight of them is not an oversight, it is a section that skipped a step.

**Independent Test**: Cancel a queued run and confirm it never executes; cancel a running one and
confirm it stops before the next billable stage while the stage already in flight completes.

**Acceptance Scenarios**:

1. **Given** a queued run, **When** it is cancelled, **Then** it is never claimed and never executes.
2. **Given** a running run between stages, **When** it is cancelled, **Then** the next stage does not
   begin, the artifacts already written are retained, and the run carries no processing identifier.
3. **Given** a running run inside a provider call, **When** it is cancelled, **Then** the call
   completes and is billed, and the response says the cancellation was *requested* rather than
   claiming the run stopped.
4. **Given** a run that has already succeeded or failed, **When** cancellation is requested, **Then**
   it is refused, naming the state it is already in.
5. **Given** an already-cancelled run, **When** cancellation is requested again, **Then** the response
   is the same as the first time.

---

### Edge Cases

- **A worker dies holding a lease.** The lease expires, the run returns to the queue, another worker
  claims it, and the completed stages' artifacts are reused, so only the stage that was in flight
  repeats. The re-run produces the same `processing_id` as an uninterrupted run would have.
- **A worker dies mid-provider-call, having already been billed.** The stage repeats and is billed
  again. This is accepted and bounded to one stage; a design that avoided it would need an in-flight
  marker whose own crash window merely moves the problem.
- **A run is cancelled while a provider call is in flight.** The call is not aborted. Cancellation is
  observed at the next stage boundary, so the run stops before the *next* billable stage and not
  during the current one. The status must not claim otherwise.
- **A run is cancelled after it has already succeeded.** The cancellation is refused; a completed run
  has a processing identifier and a stored result, and pretending otherwise would make a retrievable
  result unreachable.
- **The same document is submitted twice concurrently.** Two runs execute, both write identical
  artifact content, both writes succeed as no-ops, and both report the same processing identifier.
- **A run's row is updated but the process dies before the response is sent.** The client re-polls and
  observes the terminal state; nothing is lost because the row, not the response, is the record.
- **The run-state database is reachable but the object store is not.** Runs are accepted and executed
  without reuse; nothing fails, and the degradation is logged once rather than per stage.
- **A run outlives its retention window.** Nothing happens to it in this milestone. `expires_at` is
  recorded at creation so the deadline is knowable, and acting on it — sweeping, reporting an aged-out
  run as distinct from one that never existed, deleting anything — is Milestone 10's retention work.
  A state that no code can ever set would be a state that lies.
- **Two tenants submit byte-identical documents against the same schema.** Both runs execute in full
  and both parses are paid for. The two results carry the **same `processing_id`** — content-addressed
  identity does not change — stored under two tenant namespaces, and neither tenant can observe that
  the other exists, by result, by latency, or by invoice.
- **Tenant scoping is switched on over a store that already holds artifacts and blobs.** Pre-existing
  content is assigned to a configured default tenant by an explicit migration step. Until
  authentication is switched on, a deployment has one implicit tenant and nothing about its behaviour
  changes.
- **The run-state database is down but the synchronous routes would still work.** Readiness fails and
  the node leaves rotation, taking working synchronous capacity with it. This is intended, not a
  side effect, and is stated in the operator documentation.

## Requirements *(mandatory)*

### Functional Requirements

#### Run identity and lifecycle

- **FR-001**: The system MUST allocate a `run_id` at the moment a run is accepted, before any stage
  executes.
- **FR-002**: `run_id` MUST be opaque and MUST NOT be derived from document content, schema identity,
  or any stage output, so that two submissions of identical inputs are distinguishable.
- **FR-003**: `processing_id` MUST retain its existing derivation, defined by ADR-0003 and unchanged
  by this milestone.
- **FR-004**: A run MUST carry its `processing_id` once and only once it has succeeded, and MUST carry
  none before.
- **FR-005**: Two runs over identical inputs MUST produce two distinct `run_id` values and one
  identical `processing_id`.
- **FR-006**: Run status MUST be drawn from a closed set: `queued`, `running`, `succeeded`, `failed`,
  `cancelled`. Five states, and `expired` is deliberately not among them — see FR-015 and Out of Scope.
- **FR-007**: A run MUST NOT be deleted or transitioned by any code path in this milestone once
  terminal, so that a retention policy added later inherits a complete history rather than a
  reconstructed one.
- **FR-008**: `GET /v1/jobs/{job_id}` MUST be unchanged in route, status vocabulary, and semantics.
- **FR-009**: The two existing synchronous extraction routes MUST be unchanged in request shape,
  response shape, and status codes.
- **FR-010**: `POST /v1/extract` MUST remain synchronous and MUST NOT gain an asynchronous variant,
  because ADR-0012 defines it as persisting nothing.
- **FR-011**: A run submission MUST accept an idempotency key, and a repeat submission carrying a key
  already seen for the same tenant MUST return the original `run_id` rather than creating a second run.
- **FR-012**: The system MUST expose run submission, run status, and run cancellation as routes
  distinct from the existing job routes.
- **FR-013**: A succeeded run MUST make its result retrievable through the existing job-result route
  addressed by `processing_id`, rather than through a second result representation.
- **FR-014**: A run MUST record the schema identity it was submitted against, so that a run's history
  remains readable after a registry changes.
- **FR-015**: A run MUST carry a retention deadline set at creation. Nothing in this milestone reads
  it: the column exists because adding it to a populated table later is the same migration problem
  `tenant_id` has, and acting on it is Milestone 10's.

#### Queue, leasing, and redelivery

- **FR-016**: Work MUST be claimed such that two workers never execute the same run concurrently.
- **FR-017**: A claim MUST place a time-bounded lease on the run.
- **FR-018**: A worker MUST extend its lease while it is still executing.
- **FR-019**: A run whose lease expires MUST become claimable again without operator action.
- **FR-020**: Each claim MUST increment the run's attempt count, atomically within the claim
  statement itself.
- **FR-021**: A run exceeding a configured attempt limit MUST come to rest in a terminal failed state
  naming abandonment, and MUST NOT be claimed again.
- **FR-022**: Redelivery MUST reuse artifacts written by the interrupted attempt, so that only the
  stage in flight at interruption repeats.
- **FR-023**: A redelivered run that completes MUST produce the same `processing_id` an uninterrupted
  run would have produced.
- **FR-024**: Claiming MUST NOT starve: eligible runs MUST be claimed in ascending order of creation
  time. Stated as an ordering rather than as "oldest first, all else equal" — with no priority classes
  and no fairness policy in this milestone, nothing else is ever unequal, so the qualifier described a
  discretion the design does not have.
- **FR-025**: A worker process MUST execute **exactly one run at a time**. The number of runs a
  deployment executes concurrently is therefore the number of worker processes it runs, and is
  configured by replica count rather than by a flag. A worker MUST NOT execute runs in threads,
  subprocesses, or an event loop, because a stage holding the GIL must not be able to delay another
  run's heartbeat into losing a lease it still holds.
- **FR-026**: The system MUST NOT require a message broker, a coordinator, or a scheduler process,
  and MUST enforce this with a `forbidden` import contract rather than by review. This is the only
  functional requirement in this specification that names technology, and it does so as a prohibition
  carried over from the constitution's deferred-technology list — a requirement of that kind with no
  automated guard is a comment.

#### Cancellation

- **FR-027**: A queued run MUST be cancellable, and MUST NOT subsequently be claimed.
- **FR-028**: A running run MUST observe cancellation at stage boundaries.
- **FR-029**: Cancellation MUST NOT claim to abort an in-flight provider call, and the documented
  behaviour MUST state that it does not.
- **FR-030**: A cancelled run MUST retain the artifacts its completed stages wrote.
- **FR-031**: Cancelling a run in a terminal state MUST be refused, naming the state it is already in.
- **FR-032**: *Withdrawn 2026-08-28, folded into FR-063.* It read "cancellation MUST be scoped to the
  tenant that owns the run", which FR-063 already says in the word "cancellable". The number is
  retained rather than reused, because a requirement identifier that changes meaning is worse than a
  gap in a sequence.
- **FR-033**: A cancelled run MUST carry no `processing_id`, because no terminal artifact was produced.
- **FR-034**: Cancellation MUST be idempotent.

#### Failure recording

- **FR-035**: A failed run MUST record the stage that failed and the **class name** of the error, never
  its message, matching the rule `PipelineResult` and `pipeline/observe.py` already follow.
- **FR-036**: A failed run MUST record the outcomes of the stages that completed.
- **FR-037**: Run state MUST NOT contain document text, extracted values, claimed text, prompt bodies,
  credentials, or provider error messages.
- **FR-038**: A run that fails MUST be distinguishable from one that was cancelled, from one that was
  abandoned after the attempt limit, and from one that failed on configuration rather than on the
  document.
- **FR-092**: The `Runs` layer MUST emit one structured event per run state transition, carrying at
  minimum the run identity, the tenant, the previous and new state, the attempt count, and the reason
  for the transition. It MUST NOT carry a duration, a token count, a cost, a stage result, or any
  summary of the run's execution — those are stated by the existing per-stage events, and a second
  statement of them would eventually disagree with the first.
- **FR-093**: Run events MUST obey the same no-content rule as every existing observer: no document
  text, no extracted values, no claimed text, no prompt bodies, no credentials, and no provider
  messages. Identifiers, hashes, states, counts, and class names only.
- **FR-091**: A run whose `schema_identity` no longer resolves when a worker claims it MUST fail
  terminally on that first occurrence, carrying a schema error class and **no** `failed_stage`,
  because no stage was reached. It MUST NOT be re-queued, MUST NOT be retried, and MUST NOT reach the
  attempt limit and be reported as abandoned. A configuration fault and a poison document are
  different failures, and the run record MUST NOT name one as the other.

#### Worker process

- **FR-039**: The system MUST provide a worker entry point invocable as a subcommand of the existing
  command-line interface.
- **FR-040**: The worker MUST execute runs by calling the existing pipeline entry point **without
  changing its behaviour for existing callers**. An additive optional parameter whose default
  reproduces current behaviour byte for byte satisfies this; altering what any current call site
  already does, does not. Amended 2026-08-28 from "without modification to it", which FR-028 made
  unsatisfiable — `pipeline.run()` executes four stages behind one call and exposes no point at which
  a caller can intervene, and the observer hook is specified to ignore what it returns. See research
  R4 and plan.md's Complexity Tracking.
- **FR-041**: The worker MUST resolve schemas, adapters, and limits from the same configuration
  vocabulary the API uses, with no second set of variable names.
- **FR-042**: The worker MUST shut down cleanly on signal: stop claiming, finish or relinquish the
  current run, and release its lease.
- **FR-043**: A relinquished run MUST return to the queue immediately rather than waiting for lease
  expiry.
- **FR-044**: The worker MUST NOT import the API layer, and the API MUST NOT import the worker's
  execution loop. The first half follows from the layers contract; the second does **not** — `api`
  sits above `runs` and may therefore import anything in it — so the second half MUST be enforced by
  an explicit `forbidden` contract naming `docdoc.runs.worker`. Without it this requirement is
  unfalsifiable, which `/speckit-analyze` raised on 2026-08-28.

#### Shared storage

- **FR-045**: The system MUST provide an object-store-backed artifact store satisfying the existing
  artifact store contract.
- **FR-046**: The system MUST provide an object-store-backed blob store satisfying the existing blob
  store contract.
- **FR-047**: Both MUST preserve every behaviour ADR-0010 §4 specifies: a miss executes the stage, a
  format-version mismatch executes and logs, a content mismatch raises without recomputing, and an
  unavailable store runs without reuse and never fails the run.
- **FR-048**: Both MUST preserve ADR-0010 §5: identical content written twice is a no-op, divergent
  content under an existing identity raises.
- **FR-049**: A write MUST NOT be observable in a partial state, matching the atomicity the filesystem
  store obtains by temporary-file-and-replace.
- **FR-050**: The filesystem store MUST remain available and MUST remain the default; the object store
  MUST be selected by configuration.
- **FR-051**: Selecting a store MUST NOT change any value, verdict, location, or identity a run
  produces.
- **FR-052**: The object store integration MUST ship as an optional extra and MUST NOT be a dependency
  of the base install.
- **FR-084**: Both stores MUST be namespaced by tenant, such that a lookup performed under one tenant
  cannot observe, read, or overwrite content written under another. *(Numbered after FR-083 because it
  arrived with the 2026-08-28 clarifications; it belongs beside FR-045.)*
- **FR-084a**: The **default tenant's namespace MUST be the store root itself** — its content resides
  at `<root>/blobs/…` and `<root>/artifacts/…`, exactly where a Milestone 8 deployment already wrote
  it. Every other tenant MUST reside under `<root>/t/<tenant_id>/`. No migration, relocation, copy, or
  read-through fallback is permitted for this compatibility: an existing deployment's content MUST
  remain in place and readable, which is what makes SC-018 true by construction rather than by a
  remedial step. The asymmetry is a stated compatibility rule and MUST be documented as one where the
  derivation lives.
- **FR-085**: Namespacing MUST NOT alter `processing_id`, `artifact_id`, `content_id`, or any other
  identity derivation. It changes where content is stored, never what it is called — two tenants
  processing identical bytes MUST arrive at the same identities independently.
- **FR-086**: Artifact reuse MUST operate strictly within a tenant, and a reuse hit MUST NOT be
  observable across tenants by result, by latency, or by provider invocation count.

#### Health and readiness

- **FR-053**: Both process types MUST expose a liveness signal that touches no database, no object
  store, and no provider.
- **FR-054**: Both process types MUST expose a readiness signal that reports the reachability of the
  run-state database and the configured store.
- **FR-055**: A readiness failure MUST name the unmet dependency.
- **FR-056**: Readiness MUST NOT invoke a model provider or a billable parser.
- **FR-057**: When the run-state database is unreachable, run submission MUST be refused with a
  retryable status rather than accepted and dropped.
- **FR-058**: Health routes MUST be reachable without authentication, and MUST disclose no
  configuration values, credentials, tenant identifiers, or counts of stored content.
- **FR-087**: Readiness MUST be a single binary signal covering every dependency. A process that
  cannot reach the run-state database MUST report not ready even though routes requiring no database
  would still function, and the operator documentation MUST state that this withdraws working
  synchronous capacity on purpose.

#### Authentication and tenant scoping

- **FR-059**: When authentication is enabled, every route except liveness and readiness MUST require a
  credential.
- **FR-060**: A credential MUST resolve to a principal carrying exactly one tenant identifier.
- **FR-061**: The credential-to-principal mapping MUST be loaded from deployment configuration at
  startup, and MUST NOT be creatable or mutable through any route in this milestone.
- **FR-062**: Every run MUST record its owning tenant at creation.
- **FR-063**: A run MUST be readable, cancellable, and countable only under its owning tenant.
- **FR-064**: A blob MUST be readable and referenceable only under the tenant that submitted it.
- **FR-065**: A result addressed by `processing_id` MUST be readable only under a tenant that has a
  run which produced it.
- **FR-066**: A cross-tenant access attempt MUST be indistinguishable, in status and body, from an
  identifier that does not exist.
- **FR-067**: An absent or unrecognised credential MUST be refused before any document is read, any
  provider is called, or any store is touched.
- **FR-068**: A credential MUST NOT appear in a log line, a run record, an error body, or a process
  argument list.
- **FR-069**: The command-line interface and in-process library MUST be unaffected: neither gains a
  credential requirement.
- **FR-088**: Authentication MUST be disabled by default. A deployment with it disabled MUST behave
  exactly as Milestone 8 did — no credential required on any route — and MUST have exactly one
  implicit tenant owning all content, so that "one tenant" is a description of the deployment rather
  than a convention the code assumes.
- **FR-089**: Enabling authentication over a deployment that already holds blobs and artifacts MUST
  assign that content to a tenant named in configuration, by an explicit, idempotent migration step.
  The system MUST NOT infer an owner, and MUST NOT leave pre-existing content unreachable.

#### Layering and guards

- **FR-070**: A new `Runs` layer MUST sit above `Pipeline` and MUST be declared independent of
  `Recording`, enforced by the existing import-contract check.
- **FR-071**: This milestone MUST NOT modify the kernel, ingest, extraction, grounding, validation, or
  artifact-envelope layers.
- **FR-072**: Run identifiers, timestamps, and lease deadlines MUST be produced in the `Runs` layer and
  passed downward as data; no layer at or below `Pipeline` may read a clock or a random source.
- **FR-073**: The existing determinism scan and audit hook MUST continue to pass unmodified — neither
  may be relaxed, narrowed, or granted an exemption for this milestone.
- **FR-074**: Every error surfaced by this milestone MUST be a typed, provider-neutral error consistent
  with the constitution's error model.

#### Packaging and documentation

- **FR-075**: The project MUST provide a container image serving as both process types, selected by
  entry point rather than by image.
- **FR-076**: The project MUST provide a development composition of the API, a worker, the run-state
  database, and an object store, runnable with no cloud credentials.
- **FR-077**: The composition MUST bring up a working deployment with no manual step beyond supplying
  a schema path and a provider credential.
- **FR-078**: Database schema changes MUST be applied by an explicit, repeatable, idempotent step, and
  MUST NOT be applied implicitly by a process starting.
- **FR-079**: The milestone MUST ship a concept document covering the two identities, the run
  lifecycle, redelivery, and the exact limits of cancellation.
- **FR-080**: The milestone MUST ship a runnable example that submits a run and polls it to completion.
- **FR-081**: The README roadmap, HTTP section, and configuration section MUST be updated, and the
  unauthenticated warning MUST be revised to state what is now true: that authentication exists, that
  it is off by default, and that a deployment which has not enabled it is exactly as exposed as
  before.
- **FR-082**: ADR-0013 MUST be written and accepted, amending ADR-0010 §6, before implementation
  begins.
- **FR-090**: ADR-0014 MUST be written and accepted before implementation begins, recording the tenant
  scoping decisions: per-tenant namespacing of the content-addressed store, the cross-tenant existence
  oracle it closes, the cross-tenant reuse it forfeits, and authentication defaulting to off. These are
  separated from ADR-0013 because they are a different question — they would be needed even if docdoc
  stayed synchronous — and because the ADR directory's own convention is one decision per record.
- **FR-083**: Every configuration setting introduced MUST follow the existing precedence rule —
  explicit argument over environment over default — and MUST gain a flag unless it is a credential or
  is meaningless outside one process type.

### Key Entities

- **Run**: One attempt to execute the pipeline over one document against one schema. Carries its own
  opaque identity, an owning tenant, a status from a closed set, an attempt count, a lease deadline, a
  retention deadline, and — once succeeded — the processing identity of its result. Mutable, which is
  what distinguishes it from every other persisted thing in this project.
- **Run identity**: An opaque identifier for an attempt. Distinct from processing identity in
  derivation, in lifetime, and in what it answers.
- **Lease**: A time-bounded claim by one worker on one run. Its expiry, not a worker's cooperation, is
  what makes redelivery possible after a process dies.
- **Principal**: What a credential resolves to. Carries exactly one tenant identifier and nothing that
  varies per request.
- **Tenant**: The scope within which runs, blobs, and results are visible. Recorded on a run at
  creation.
- **Worker**: A process that claims runs and executes them. Holds no state a restart cannot recover
  from the run table.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A result produced through the asynchronous path and the same result produced through the
  synchronous path agree on **100%** of values, verdicts, locations, and identities — asserted by a
  test that runs both and compares, in the manner of Milestone 7's SC-006. This is the criterion the
  milestone exists to satisfy; if it fails, nothing else here matters.
- **SC-002**: A run submission returns in under **200 ms** at the 95th percentile regardless of
  document size, measured with the worker pool stopped so that no run can complete during the
  measurement.
- **SC-003**: Killing a worker at each of the four stage boundaries yields, in **100%** of cases, a run
  that completes on redelivery with a `processing_id` identical to the uninterrupted run's.
- **SC-004**: Under redelivery, the number of billable provider invocations exceeds the uninterrupted
  count by **at most one**, and by **zero** when the interruption falls between stages.
- **SC-005**: With two workers sharing one object store, a second submission of an already-parsed
  document **by the same tenant** invokes the parser **zero** times, measured on a counter rather than
  on elapsed time.
- **SC-006**: A document that terminates the worker process comes to rest in a terminal failed state
  within its attempt limit in **100%** of cases, and the number of worker processes it terminates is
  bounded by that limit.
- **SC-007**: **100%** of failed runs record a failing stage and an error class; **0%** of run records
  contain document text, extracted values, claimed text, prompt bodies, or provider messages, verified
  over a document seeded with distinctive strings.
- **SC-008**: **0%** of cross-tenant access attempts succeed, across run identifiers, blob identifiers,
  and processing identifiers; and **100%** of them are byte-identical in status and body to the
  response for a non-existent identifier, so that no attempt can be used to prove existence.
- **SC-009**: With the run-state database stopped, liveness passes in **100%** of probes and readiness
  fails in **100%**, naming the dependency; run submission is refused with a retryable status in
  **100%** of attempts, and **0%** are accepted and lost.
- **SC-010**: Golden-set metrics are **bit-identical** before and after this milestone. It touches no
  stage, so any movement is evidence the scope was exceeded.
- **SC-011**: A run and a lease survive a restart of every process in the deployment: after restarting
  the API and every worker mid-run, **100%** of in-flight runs reach a terminal state without operator
  action.
- **SC-012**: The diff for this milestone touches **zero** files under the kernel, ingest, extraction,
  grounding, and validation layers, and **zero** lines of the artifact envelope's identity derivation.
- **SC-013**: A base install acquires **zero** new dependencies from this milestone, and the full
  offline suite passes with neither the object-store extra nor the database present.
- **SC-014**: The development composition reaches a state where a document can be submitted and a
  result retrieved, from a clean checkout, in under **10 minutes** of operator time and with no cloud
  credential beyond a model provider key.
- **SC-015**: Cancelling a queued run prevents execution in **100%** of cases; cancelling a running run
  prevents the **next** billable stage in 100% of cases and is documented as preventing nothing
  currently in flight.
- **SC-016**: Submitting the same idempotency key twice under one tenant produces **one** run in 100%
  of cases; the same key under two tenants produces **two**.
- **SC-017**: The cross-tenant existence oracle is closed: a tenant submitting a document that another
  tenant has already processed invokes the parser and the model adapter **exactly as many times as a
  first-ever submission**, in 100% of cases. Measured on invocation counters, because this is the one
  criterion a status code cannot satisfy — SC-008 makes the *responses* identical, and this makes the
  *cost and timing* identical. Numbered beside SC-008, which it completes.
- **SC-018**: An existing deployment upgrades with **zero** configuration changes: with authentication
  left at its default, 100% of previously stored blobs and artifacts remain readable, and 100% of
  existing routes return what they returned before. Enabling authentication afterwards leaves 0% of
  pre-existing content unreachable.

## Assumptions

- **A single deployment is the unit of isolation.** Tenants share a database, a store, and a worker
  pool. Isolation is enforced by scoping, not by separate infrastructure; a customer requiring
  physical separation runs a separate deployment.
- **Duplicate documents across tenants are paid for twice, deliberately.** Per-tenant namespacing
  forfeits cross-tenant reuse to close an existence oracle. This is a bet that identical bytes arriving
  from two customers is rare; a deployment where it is common — a shared industry clearing house, say
  — should record that as a new decision rather than quietly widen the namespace.
- **Authentication defaults to off, and that is the compatible default rather than the safe one.** A
  deployment that never enables it is exactly as exposed as Milestone 8 was, and the README must keep
  saying so. What the default buys is that upgrading breaks nothing; what it costs is that security
  is opt-in, which is stated here rather than discovered.
- **Static credentials are sufficient for this milestone.** Issuance, rotation, and runtime revocation
  are Milestone 10. Restarting a process to change a key is acceptable at this stage and is documented
  as a limitation rather than left to be discovered.
- **Polling is sufficient for this milestone.** Webhook delivery is Milestone 10; a client polls, and
  the documentation states a sane interval.
- **At-least-once delivery is accepted.** Content-addressing makes redelivery safe for every
  deterministic stage; the residual exposure is one billable stage per interruption. An at-most-once
  design would need an in-flight marker whose own crash window reintroduces the same problem one level
  down.
- **Run retention is recorded but not enforced.** `expires_at` is set at creation and nothing acts on
  it; run rows accumulate until Milestone 10 adds a sweep. A deployment that runs for a year before
  that lands has a year of rows, which is a disk-space question and not a correctness one — every row
  is small, and none holds document content.
- **The database is a dependency of asynchrony only.** A deployment that uses neither the run routes
  nor a worker needs no database, and the library and command line need none in any configuration.
- **One worker is a valid production deployment.** Multi-worker is supported and not required; the
  shared object store is what makes it safe, not what makes it possible.
- **Runs are not prioritised.** All runs share one queue and are claimed oldest-first. Priority classes
  and per-tenant fairness are deferred; nothing here forecloses them.

## Dependencies

- **ADR-0013 must be accepted first.** It amends ADR-0010 §6, which currently states the job model is
  synchronous and argues why. Implementation before that amendment would leave an Accepted ADR
  contradicted by merged code. *(Written and accepted 2026-08-28.)*
- **ADR-0014 must be accepted first.** Tenant scoping is a separable decision with its own security
  rationale, and per FR-090 it carries the namespacing choice and the authentication default.
- **ADR-0010 §4 and §5 are load-bearing.** The object-store implementations inherit those rules rather
  than restating them; a divergence there is a correctness bug, not a variation.
- **ADR-0012 constrains the API surface.** It is why `POST /v1/extract` gains no asynchronous form.
- **ADR-0003 constrains identity.** `processing_id` derivation is untouched, and redelivery safety
  depends on it staying untouched.
- **The existing import-contract, determinism scan, and audit hook gate this work** and are not
  modified by it.
- **`pipeline.run()` is the integration point** and its signature is treated as fixed for this
  milestone.

## Out of Scope

Deferred to Milestone 10, each because it can be added later without moving a process boundary:

- **OpenTelemetry export.** The *emission points* are in scope — `pipeline/observe.py`'s hook already
  exists and FR-092 adds `run.transition` beside it — but nothing exports to a tracing backend here.
  Milestone 10 binds an exporter to hooks that will already be emitting.
- **Deletion by document, retention cascade, and the run retention sweep itself.** Only the
  `expires_at` **column** is in scope, because a column added to a populated table later is the same
  migration problem `tenant_id` has. Nothing reads it here: there is no sweep, no `expired` state, and
  no deletion of any kind. An earlier draft put `expired` in the closed status set and described a
  sweep in the data model without giving any task the job of building it — a state no code path could
  reach, which `/speckit-analyze` raised as CRITICAL on 2026-08-28. Retention is one feature and it
  belongs whole to Milestone 10, which inherits a complete history because FR-007 forbids this
  milestone from deleting or re-transitioning anything.
- **Rate limiting, per-tenant quotas, and token budgets.** Worker-pool size already bounds concurrency;
  per-tenant limits need policy this milestone does not have.
- **Webhook callbacks**, including signing and delivery retry.
- **Key issuance, rotation, and runtime revocation.**
- **Human corrections and confidence-based routing.** Product features; mixing them into an
  infrastructure milestone would make SC-010 unmeasurable.
- **Run priority classes and per-tenant fairness.**
- **Horizontal scaling of the API process beyond what a stateless process gets for free.**
- **Any second database use.** The run table is the only one this milestone adds; artifacts stay in the
  artifact store, and results stay content-addressed.
- **Cross-tenant artifact reuse, and any billing-group construct that would restore it.** Closed by
  the 2026-08-28 clarification; reopening it means reopening the existence oracle and needs its own
  decision.
- **Route-scoped readiness, and deliberate degraded operation with the run-state database down.** A
  deployment wanting to keep serving synchronous routes while asynchrony is unavailable is choosing an
  availability posture, and that belongs in an ADR rather than in a health check.
