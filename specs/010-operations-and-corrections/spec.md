# Feature Specification: Retention, Credentials, Limits, Delivery, and the Human Loop

**Feature Branch**: `010-operations-and-corrections`

**Created**: 2026-09-04

**Status**: Implemented

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

**Input**: User description: "Dựn spec cho M10 nhé. toàn bộ các mục luôn" — draft Milestone 10,
every item on it. Asked after a question about whether the next work was the admin UI. It is not:
the roadmap's next entry is the list `specs/009-asynchronous-runs/spec.md` deferred by name, and a
usable operator interface depends on two things on that list rather than the other way round.

## What Milestone 10 is, and the sentence that bounds it

Milestone 9 ended with a list, and every item on it was deferred for the same stated reason: "it can
be added later without moving a process boundary." **This milestone is that list, built.** It moves
no process boundary. The same four process types, the same one table-backed queue, the same shared
object store, the same `pipeline.run()`. No fifth process, no broker, no coordinator, no scheduler —
and the `forbidden` import contract that says so stays exactly as Milestone 9 left it.

Nine items were deferred. Seven are in scope here, two are not, and the two are named with reasons
rather than dropped:

| Deferred by Milestone 9 | Here |
|---|---|
| OpenTelemetry export | **In scope.** The emission points exist; this binds an exporter. |
| Deletion by document, retention cascade, the run sweep | **In scope.** `expires_at` gets its first reader. |
| Rate limiting, per-tenant quotas, token budgets | **In scope**, and it needs a constitutional amendment. |
| Webhook callbacks, signing, delivery retry | **In scope.** |
| Key issuance, rotation, runtime revocation | **In scope**, and it supersedes a shipped requirement. |
| Human corrections and confidence-based routing | **In scope**, and it is the half that carries risk. |
| Run priority classes and per-tenant fairness | **In scope.** It amends Milestone 9's FR-024. |
| Horizontal scaling of the API beyond stateless | **Out.** Nothing here makes the API stateful; there is no work to do. |
| Cross-tenant artifact reuse and billing groups | **Out.** Closed by ADR-0014; reopening it reopens the existence oracle. |
| Route-scoped readiness, deliberate degraded operation | **Out.** An availability posture, and it belongs in an ADR nobody has asked for. |

Milestone 9's own measure was that it changed no value docdoc produces. **This milestone holds
itself to the same measure**, and that is a stronger claim here than it was there, because half of
what follows is product rather than infrastructure. SC-001 restates it and the rest of this document
is arranged so that it stays checkable.

## One milestone, two halves — stated because it is this milestone's risk

The first half is **operational**: retention, telemetry, credentials, limits, delivery, fairness.
None of it reads a document, calls a provider, or produces a value. The second half is **product**:
confidence routing and human corrections. Milestone 9 deferred those two together and gave a reason
that this milestone has to answer rather than ignore — "mixing them into an infrastructure milestone
would make SC-010 unmeasurable."

**The objection is answered by what the two features actually are, not by asserting they are small.**
A correction *annotates* a result and alters nothing it annotates; `src/docdoc/evaluation/corrections.py`
has enforced exactly that since Milestone 6, and its module docstring says why in the same words the
constitution does. A routing decision is a *predicate over a result that already exists* — it reads
grounding outcomes and validation severities, computes no value, writes no artifact, and cannot
change an identity. Neither one enters the pipeline. So the golden-set metrics can still be required
to be bit-identical, and SC-001 requires it.

**If either half becomes a write into the pipeline, this milestone has failed its own first
criterion**, and the split below stops being advisory.

**The split that is recommended and not taken.** Shipping 10a (operational) and 10b (routing and
corrections) as two milestones would be the safer sequencing, because the second half is the only
part of this document that changes what the product *means* to a user, and it is the only part whose
review needs a product opinion rather than an operational one. It is not taken here because the
whole list was asked for, and because the two halves touch in exactly one place that would otherwise
be specified twice: **the retention sweep has to know whether a correction pins a result**, and a
policy that deletes a result somebody corrected is a data-loss bug that only appears when both
features exist. FR-013 is where that meets. A reviewer who wants the split can take it along the
section boundaries in Requirements, which are drawn to permit it.

## The four constitutional questions, and which of them need an amendment

**Telemetry needs none.** The constitution's Observability paragraph already says "OpenTelemetry
where practical". What it does *not* permit is a fifth container: "Development Compose contains only
api, worker, postgres, and object storage." So the composition acquires no collector, and export is
to an OTLP endpoint the operator supplies — off by default, and absent configuration nothing is
exported and nothing is required.

**Quotas and token budgets need an amendment, and arguing otherwise is the wrong instrument.**
Constitution v1.6.0 says "metering, invoicing, and per-tenant pricing remain deferred". A per-tenant
token budget counts tokens per tenant, and that is metering under any honest reading. The
distinction this milestone actually wants — *counting in order to refuse* is not *counting in order
to bill* — is real, and it is exactly the kind of distinction Milestone 9 tried to settle by
interpretation and was told to settle by amendment: Governance says the constitution wins where a
spec conflicts with it, so a spec that reinterprets a constitutional sentence in order to comply
with it inverts that precedence. `/speckit-analyze` raised it as CRITICAL on 2026-08-28 and would
raise this. **FR-102 therefore makes constitution v1.8.0 a dependency of implementation**, carrying
that distinction and the error-model additions below.

**Corrections need no amendment and one boundary that must hold.** Principle IX requires the
correction model and requires corrections to be reusable as dataset signal; it also says in as many
words that supporting them "MUST NOT turn the MVP into a workflow or review platform", and the
deferred-technology list names "a full review UI". This milestone therefore adds routes that
*record* and *read* corrections and adds nowhere for a human to be *assigned* one. **The viewer stays
read-only.** It is not made writable here, and the operator interface the roadmap will eventually
want is not this milestone's — it needs the credential work below to exist first, which is the
reason the two were asked about in the opposite order.

**Credential lifecycle needs no amendment and supersedes a shipped requirement.** Milestone 9's
FR-061 reads: the mapping "MUST NOT be creatable or mutable through any route **in this milestone**".
The qualifier is load-bearing and this is the milestone it was pointing at. `src/docdoc/api/auth.py`
states the consequence in its docstring — "a table invites exactly the endpoint the requirement
forbids" — and that endpoint is now wanted. Superseding a merged milestone's requirement is recorded
in ADR-0016 and in this sentence rather than done quietly, and the file-backed key ring keeps
working (FR-038), because a deployment that never asks for the new surface must not be broken by it.

## Deletion is not the inverse of writing, which is the hardest thing here

An `artifact_id` is derived from content (ADR-0003). Within one tenant, two runs over the same
document and schema share **every** artifact, deliberately — that sharing is what makes a second
submission free, and it is the property Milestone 9's SC-005 measures. It follows that "delete this
document" cannot mean "delete the artifacts it produced", because some other run of the same tenant
may be the reason one of them exists.

Two designs answer this. **Reference counting** is exact and puts a mutable counter beside an
immutable store, which is the shape ADR-0010 §1 rejected for artifacts and would have to be argued
back in. **Reading the survivors** — take the artifact ids the remaining runs of this tenant already
recorded, and delete what no survivor names — needs no counter, is recomputable after any crash, and
is idempotent, which matters because a sweep that dies halfway must be safe to run again. This
milestone takes the second, and pays for it in sweep cost rather than in a new mutable structure.

**Reading, not re-deriving, and the difference is a correctness one.** A run row's `stage_outcomes`
already carries an `artifact_id` per stage, so the set a run holds is recorded rather than
recomputable. Re-deriving it from `blob_id` and `schema_identity` would need the parser version and
options hash that were in effect at the time, which a reconfigured deployment no longer has — and a
deletion routine that silently misses artifacts is the worst possible place for that failure.
Per-tenant deletion remains what ADR-0014 §5 already made it: a prefix operation, decided in
Milestone 9 precisely so that this milestone would not have to scan.

**Erasure and provenance disagree, and the disagreement is narrower than it looks.** Principle VIII
says "provenance MUST NOT be silently overwritten". Deleting is not overwriting, and *silently* is
where the two are reconciled: a deleted run leaves a **tombstone** carrying its identity, its tenant,
when it was deleted and under which policy — and nothing else. To the tenant that owned it, an
erased run is reported as having been here and being gone. To every other tenant it is reported
exactly as an identifier that never existed is reported, which is Milestone 9's FR-066 unchanged and
not weakened by a word here. Removing the row outright was the alternative, and it makes "was this
ever here?" unanswerable to the only party entitled to ask it.

**A tombstone is not a sixth run status**, and that is the second thing this decision fixes.
`RunStatus` stays closed at five. Milestone 9 left `expired` out because a state no code path could
reach is a state that lies; putting it back now that a code path exists would swap one lie for
another, because the value it would carry has none of a run's other fields to go with it. A removed
run is gone *as a run*, and the tombstone is a different thing with four fields — which is why
FR-004a can promise every existing client an exhaustive set it already handles.

## What a routing decision may read, and what it may not

Principle II: "MVP routing decisions MUST NOT read `model_confidence`." ADR-0004 keeps the trusted
signals (`grounding`, `grounding_score`) separate from the untrusted passthrough and forbids a
blended number. A router that reads a model's self-report would violate both, and it would do it
invisibly, because the resulting decisions would look reasonable.

So the routing input is exactly the set of signals docdoc computed itself: **grounding status and
score per field, validation severities and verdicts, and schema requiredness.** The policy is data,
not code: it is versioned as `routing_policy_version`, it is recorded on every decision, and the
same result under the same policy version routes the same way forever. That is what makes a routing
decision auditable, and it is why this is not a blended confidence wearing a different name — it is
a predicate over signals that are already trusted, and the constitution's requirement that
"confidence semantics MUST be documented and versioned" is satisfied by the policy record rather
than by a number nobody can decompose. ADR-0017 records it.

**The output is two words, and the shortness is the point.** `automatic` or `review` — the pair
Principle IX names, and nothing else. A third outcome would have to say what the deployment *does*
with a result, and docdoc does not know that; a `retry` outcome would send the decision back into the
pipeline, and the only reason routing belongs in this milestone at all is that it reads a finished
result and writes nothing.

## Clarifications

### Session 2026-09-04

- Q: Does deleting a document erase the source blob and derived content, or only the run records and
  results that point at them? → A: **Full cascade within the tenant.** Deletion removes the source
  blob, every artifact no surviving run of that tenant still names, and the run rows, which are
  replaced by tombstones. Deleting only the records would leave a deployment that answers "erased"
  while the bytes are still in the bucket — an answer that is worse than refusing, because it is
  believed. The cost is that deletion is a sweep rather than a statement, and that the sweep is the
  most expensive operation this milestone adds; FR-006 bounds it by tenant prefix so it never
  becomes a scan of the store.
- Q: What does a limit count, and when does it refuse? → A: **Three counters, all refusing at
  submission.** Requests per interval, concurrent non-terminal runs, and runs per calendar period,
  each per tenant. A token budget is the fourth and is different in kind: tokens are known only
  after a provider answers, so a budget can refuse the *next* submission and can never abort a run
  in flight. Refusing mid-run was rejected — it produces a run that is billed and discarded, which
  is the exact failure Milestone 9 existed to remove. What a deployment gets is a bound that is
  slightly late and never destructive, and FR-047 requires the documentation to say which.
- Q: Where does the mutable state this milestone introduces live — credentials, delivery attempts,
  corrections, limit counters, tombstones? → A: **Additional tables in the existing run-state
  database**, applied by the migration step Milestone 9 already built, with no second database, no
  second migration mechanism, and no ORM. Milestone 9's Out of Scope deferred "any second database
  use", and this is the milestone that spends it; spending it on one more schema in the database
  that already exists is the smallest form that can be spent. The cost is stated rather than
  discovered: **Milestone 9's assumption that "the database is a dependency of asynchrony only" ends
  here.** A deployment that serves only the synchronous routes but wants runtime key revocation now
  needs Postgres, where before it needed none. A deployment that enables none of this milestone's
  capabilities still needs none, which is FR-101 doing its job.
- Q: A run has been removed. What does its status say? → A: **Nothing — a tombstone is not a run
  state.** `RunStatus` keeps the five members Milestone 9's FR-006 closed it at, unchanged and
  unextended. A removed run is *gone as a run*: the owning tenant asking about it is told it was
  here and is not, with the deletion time and the policy name, and every other tenant is told what
  it would be told about an identifier that never existed. The alternative was a sixth state, and it
  reproduces the exact shape `runs/model.py` rejected when it left `expired` out — a `Run` carrying
  one real status and every other field empty is a state that lies about what a run is. It would
  also force the state machine, the command line, and every existing client to handle a member that
  shares none of the type's fields. The distinction between ageing out and being erased on request
  is carried by the tombstone's policy name, which FR-004 already records, rather than by two
  statuses that would need it recorded anyway.
- Q: Who executes the work nobody requests — the retention sweep, delivery retries, and revocation
  propagation — without a fifth process? → A: **The worker, in a time-bounded maintenance tick
  between claims, and a bounded cache for credentials.** Milestone 9's argument holds here without
  restatement: a reaper is a process to deploy, to monitor, and to fail silently, and an external
  cron is that process wearing the operator's scheduler. So the worker performs one bounded batch of
  sweep work and the deliveries that are due, each tick, and FR-062 is satisfied by bounding the tick
  rather than by concurrency — which is what keeps Milestone 9's FR-025 untouched, since no thread,
  subprocess, or event loop is introduced and no heartbeat can be delayed into losing a lease.
  Credential freshness is separated deliberately: the process that authenticates is the one that
  needs the current key set, and a deployment serving only the synchronous routes may have no worker
  at all, so revocation propagates as a bounded cache lifetime in every authenticating process and
  FR-028's documented bound is that lifetime. The command-line subcommands remain, because erasure is
  operator-initiated by nature and a sweep sometimes has to be run on demand. The cost: a deployment
  running no worker sweeps nothing and delivers nothing, which FR-117 states rather than leaves to be
  found.
- Q: Who sets a run's priority? → A: **The client, at submission, bounded by a per-tenant ceiling the
  operator configures.** The thing that knows a document is urgent is the submitter, and the case
  Story 8 describes — one urgent run behind that same tenant's batch — cannot be expressed by a
  priority attached to the tenant, because every run of that tenant would carry it. Letting a client
  choose freely was rejected on the obvious ground: every client chooses the top of the scale, and in
  a shared deployment that is self-service escalation past another customer's queue. The ceiling
  makes cross-tenant escalation impossible while leaving a tenant free to order its own work, and a
  request above the ceiling is **lowered to it and told so**, not refused — refusing would make a
  reconfigured ceiling break a client that was working yesterday. FR-089's starvation bound remains
  the backstop, and it is what makes a wrong ceiling a latency problem rather than a liveness one.
- Q: What are the members of the closed set of routing outcomes? → A: **Two — `automatic` and
  `review`.** Principle IX names exactly those ("high → automatic, low → human review") and every
  further member is a decision docdoc does not own. A `reject` outcome asserts what a deployment
  *does* with a bad result, which is the review-platform boundary FR-083 exists to hold. A `retry`
  outcome is worse than out of scope: it turns routing into a control-flow decision that re-enters
  the pipeline, and the reason routing could be admitted into this milestone at all is that it reads
  a finished result and writes nothing — that is what makes SC-001 measurable. Free-form outcome
  names were rejected because a set that cannot be enumerated cannot be exhaustively tested and
  cannot be a stable field in a webhook payload.
- Q: Where do corrections live, and what surface reaches them? → A: **A table in the run database,
  reached by tenant-scoped routes; the viewer is not made writable.** The model already exists in
  `evaluation/corrections.py` and needs no home to be valid, but a correction nobody can submit
  through the product's own interface is a correction that goes back into a spreadsheet, which is
  the failure that module's docstring names. Routes and a table give it a home; assignment, queues,
  reviewer workload, and an editing interface are the review platform Principle IX forbids and are
  out of scope by name.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stop paying for data nobody asked to keep (Priority: P1)

An operator has run docdoc for six months. Run rows, artifacts, and blobs have accumulated with
nothing ever removing them, because Milestone 9 recorded a retention deadline and deliberately gave
no code the job of acting on it. They set a retention period, and from then on runs and the content
derived from them age out on their own, on a schedule, with what was removed stated rather than
inferred.

**Why this priority**: P1 because it is the only item on the deferred list that gets worse with
time, and because `expires_at` is already populated on every row — a column with a value nobody
reads is a promise the deployment is not keeping.

**Independent Test**: Configure a short retention period, create runs, advance the clock, run the
sweep, and confirm that expired runs are gone, that surviving runs are untouched, and that a second
sweep over the same state changes nothing.

**Acceptance Scenarios**:

1. **Given** runs past their retention deadline, **When** the sweep runs, **Then** those runs are
   removed, a tombstone remains for each, and no run inside its deadline is affected.
2. **Given** the same state, **When** the sweep runs a second time, **Then** nothing further changes
   and the operation reports zero removals.
3. **Given** an expired run whose artifacts a surviving run of the same tenant still needs, **When**
   the sweep runs, **Then** those artifacts remain and the surviving run still completes without
   re-invoking a billable provider.
4. **Given** a sweep interrupted partway, **When** it is run again, **Then** it completes the work
   and produces the same end state as an uninterrupted sweep.

---

### User Story 2 - Erase one customer's data because they asked (Priority: P1)

A customer leaves, or exercises a right, and asks for their data to be removed. The operator names
the tenant — or one document within it — and the deployment removes the source bytes, everything
derived from them, and the records that describe them, then reports what it removed. Afterwards the
customer's identifiers behave, to every other tenant, exactly as identifiers that never existed.

**Why this priority**: P1 because it is the item most likely to be needed on a deadline that is not
the operator's, and because doing it by hand over a content-addressed store is how a tenant's data
gets deleted along with somebody else's.

**Independent Test**: Submit runs under two tenants over byte-identical documents, erase one tenant,
and confirm the other tenant's runs still resolve and still reuse their artifacts.

**Acceptance Scenarios**:

1. **Given** a tenant with runs, blobs, and artifacts, **When** the tenant is erased, **Then** the
   blobs, the derived artifacts, and the run records under that tenant are removed and the operation
   reports counts of each.
2. **Given** two tenants that submitted byte-identical documents, **When** one is erased, **Then**
   the other's runs remain retrievable and its artifacts remain reusable.
3. **Given** a document erased within a tenant, **When** that tenant requests a run that referenced
   it, **Then** it is told the run was erased, when, and under which policy — and nothing else.
4. **Given** the same erased identifier, **When** a different tenant requests it, **Then** the
   response is byte-identical to the response for an identifier that never existed.
5. **Given** an erasure request naming a tenant that does not exist, **When** it is executed,
   **Then** it succeeds having removed nothing, rather than failing.

---

### User Story 3 - Revoke a leaked credential without restarting anything (Priority: P1)

A key is committed to a repository by mistake. The operator revokes it and it stops working within
seconds, on every process, without a deployment, a restart, or a file edit — and every request that
key made afterwards is refused and recorded as having been refused.

**Why this priority**: P1 because today it is a *security* defect that Milestone 9 documented rather
than fixed: the key file is read once at startup, so deleting a compromised key produces no error,
no warning, and no change, and the revoked key keeps working until the process restarts. That
sentence is in `specs/009`'s task T117 because it was too important to leave in a docstring, and it
is the reason this story outranks quotas and webhooks.

**Independent Test**: Issue a key, use it successfully, revoke it, and confirm the next request with
it is refused by an already-running process that was never restarted.

**Acceptance Scenarios**:

1. **Given** a running deployment, **When** a key is issued for a tenant, **Then** it authenticates
   immediately and the plaintext key is returned exactly once and never retrievable again.
2. **Given** an issued key in use, **When** it is revoked, **Then** every process refuses it within
   the configured propagation bound, with no restart.
3. **Given** a tenant rotating keys, **When** a second key is issued before the first is revoked,
   **Then** both authenticate during the overlap, so rotation needs no window of failed requests.
4. **Given** any credential operation, **When** logs, run records, and error bodies are inspected,
   **Then** none contains a key or a fragment of one.
5. **Given** a deployment configured with the Milestone 9 key file and no database-backed keys,
   **When** it starts, **Then** it authenticates exactly as it did before.

---

### User Story 4 - Keep one tenant from consuming the whole deployment (Priority: P2)

One customer submits ten thousand documents in a minute. Another customer's single urgent document
is accepted, is executed within a bounded wait, and does not sit behind the batch. The noisy tenant
is refused at the door once its limits are reached, and is told which limit it hit and when it may
retry.

**Why this priority**: P2 because a single-tenant deployment does not need it and a shared one
cannot be operated without it. It sits above webhooks because the failure it prevents is other
customers' outage.

**Independent Test**: Drive one tenant past each configured limit and confirm refusals name the
limit and carry a retry hint, while a second tenant's submissions continue to be accepted and
claimed.

**Acceptance Scenarios**:

1. **Given** a tenant at its submission-rate limit, **When** it submits again, **Then** it is
   refused with a retryable status naming the limit and the time to retry, and no run is created.
2. **Given** a tenant at its concurrency limit, **When** it submits again, **Then** it is refused
   and existing runs are unaffected.
3. **Given** one tenant with a thousand queued runs and another with one, **When** workers claim,
   **Then** the second tenant's run is claimed within a bounded number of claims rather than after
   the batch.
4. **Given** a tenant over its token budget, **When** it submits, **Then** it is refused at
   submission and no run already executing is aborted or discarded.
5. **Given** a deployment with no limits configured, **When** it runs, **Then** nothing is counted,
   nothing is refused, and behaviour is identical to Milestone 9.

---

### User Story 5 - Be told when a run finishes instead of asking repeatedly (Priority: P2)

A client submits a run and registers a callback instead of polling. When the run reaches a terminal
state the deployment posts to that URL, signs what it posts so the receiver can prove it came from
docdoc, and retries on failure until it succeeds or gives up — and the client can see which of those
happened.

**Why this priority**: P2 because polling works and is documented; what webhooks buy is efficiency
and latency, not capability. It is above telemetry because it is the one item here a *client* can
observe.

**Independent Test**: Register a callback, complete a run, and assert the receiver got exactly one
signed delivery whose signature verifies; then make the receiver fail and confirm the retry schedule
and the eventual give-up are both visible.

**Acceptance Scenarios**:

1. **Given** a run with a registered callback, **When** it reaches a terminal state, **Then** a
   delivery is attempted carrying the run identity, the terminal state, and the processing identity
   where one exists — and no document content, value, or provider message.
2. **Given** a delivery, **When** the receiver verifies its signature with the shared secret,
   **Then** verification succeeds, and it fails for a body altered by one byte.
3. **Given** a receiver returning errors, **When** deliveries are retried, **Then** attempts back
   off, stop at a configured limit, and the final outcome is readable by the owning tenant.
4. **Given** a receiver that responds twice slowly, **When** duplicate deliveries arrive, **Then**
   each carries the same delivery identifier, so the receiver can discard the repeat.
5. **Given** a callback URL pointing at a private network address, **When** it is registered,
   **Then** it is refused unless the deployment has explicitly allowed that destination.

---

### User Story 6 - See a slow run in the tracing backend you already run (Priority: P2)

An operator's runs are slower than they were and nobody knows which stage. They point docdoc at the
OTLP endpoint they already operate, and the stage events and run transitions that Milestone 9
already emits appear there as spans, with the identifiers needed to correlate a trace with a run and
nothing that would put a document into a tracing backend.

**Why this priority**: P2 because the events already exist and are already logged; this is a binding,
not an instrumentation project. It is deliberately below the four above because none of them can be
diagnosed *by* it.

**Independent Test**: Point the deployment at a collector, execute one run, and confirm one trace
containing a span per stage and per run transition, correlated by run and processing identity.

**Acceptance Scenarios**:

1. **Given** an exporter configured, **When** a run executes, **Then** a trace exists whose spans
   cover the four stages and the run's state transitions.
2. **Given** the same trace, **When** its attributes are inspected, **Then** they carry identifiers,
   counts, states, and class names only — no document text, no extracted value, no prompt body, no
   credential, no provider message.
3. **Given** no exporter configured, **When** a run executes, **Then** nothing is exported, no
   telemetry dependency is required, and behaviour is unchanged.
4. **Given** an unreachable collector, **When** runs execute, **Then** they succeed and the export
   failure is reported once rather than per span.

---

### User Story 7 - Send the doubtful results to a person, and take the correction back (Priority: P2)

A result comes back with two fields ungrounded and one validation error. Instead of being treated
the same as a clean result, it is marked as needing review, on a rule the operator wrote and can
read. A reviewer states what the right values were; the correction is recorded against that result,
changes nothing about it, and is available as evaluation signal for the next golden-set promotion.

**Why this priority**: P2 as capability and highest in *consequence* — it is the only story here
that changes what a user does with docdoc's output. It is not P1 because every deployment can
already read grounding status and validation verdicts itself and route on them externally; what this
adds is that the rule is versioned and the correction comes back.

**Independent Test**: Run a document that grounds partially, confirm the routing decision names the
rule and the signals that fired it, submit a correction against the result, and confirm the result
is byte-identical before and after.

**Acceptance Scenarios**:

1. **Given** a completed run, **When** its routing decision is read, **Then** it names the outcome,
   the policy version, and the signals that produced it, and it reads no model self-reported
   confidence.
2. **Given** two runs with identical results and one policy version, **When** both are routed,
   **Then** both decisions are identical.
3. **Given** a result, **When** a correction is recorded against it, **Then** the result, its
   artifacts, and its identities are unchanged, and the correction is retrievable under the owning
   tenant only.
4. **Given** recorded corrections, **When** golden-set metrics are computed without an explicit
   promotion, **Then** no metric moves.
5. **Given** a result under retention, **When** a correction exists against it, **Then** the sweep
   does not remove it while the correction is retained.

---

### User Story 8 - Let an urgent run past a batch (Priority: P3)

A client submits one urgent document while its own batch of ten thousand is still queued, and marks
it as such on the submission. It is claimed before the batch. It cannot mark itself above the ceiling
its operator set, so it cannot jump another customer's queue. And ordinary runs still eventually run
— one that has waited long enough is claimed ahead of a newer urgent one rather than waiting forever.

**Why this priority**: P3 because per-tenant fairness (Story 4) already prevents the failure that
hurts, and priority classes are an optimisation on top of it. It is listed separately rather than
folded in because it is the only item here that **amends a Milestone 9 requirement** — FR-024 fixed
claim order as ascending creation time, and it said in its own text that nothing was ever unequal.
Something is now.

**Independent Test**: Queue a batch of ordinary runs and one urgent run, and confirm the urgent one
is claimed next; then age an ordinary run past the starvation bound and confirm it is claimed ahead
of a newly arrived urgent one.

**Acceptance Scenarios**:

1. **Given** queued runs of two priorities, **When** a worker claims, **Then** the higher-priority
   eligible run is claimed first.
2. **Given** an ordinary run that has waited beyond the starvation bound, **When** a worker claims,
   **Then** it is claimed ahead of higher-priority runs that arrived later.
3. **Given** a deployment that sets no priorities, **When** workers claim, **Then** the order is
   ascending creation time, exactly as Milestone 9 specified.
4. **Given** a tenant whose ceiling is ordinary, **When** it submits asking for urgent, **Then** the
   run is accepted at ordinary and the response says which priority it was given.
5. **Given** two tenants, **When** one submits at its highest permitted priority, **Then** the
   other's runs are still claimed within the fairness bound of Story 4.

---

### Edge Cases

- **A deployment runs no worker at all.** Nothing is swept and nothing is delivered, because the
  maintenance tick lives in the worker. Credentials still revoke within their bound, because that is
  a cache lifetime rather than a scheduled task. This is stated in the operator documentation, since
  a synchronous-only deployment that enables retention and then observes nothing being removed would
  otherwise have no way to learn why.
- **A maintenance tick overruns its bound.** It is cut short and resumes on the next tick. A sweep
  that must complete in one pass would be a sweep that can delay a claim without limit, and FR-002's
  idempotence is what makes stopping partway safe.
- **A sweep and a worker touch the same run.** A run that a worker is executing is never removed by
  the sweep, regardless of its retention deadline; the deadline is honoured at the next sweep after
  it becomes terminal. A retention policy that can delete work in flight is a policy that loses paid
  work.
- **A tenant is erased while one of its runs is executing.** The run is cancelled first and the
  erasure completes afterwards; erasure never leaves a worker writing artifacts into a namespace
  that has just been removed.
- **Two erasures for the same tenant run concurrently.** The second finds nothing to remove and
  succeeds. Erasure is idempotent because the operator invoking it twice under time pressure is the
  normal case, not the exceptional one.
- **An artifact is shared by an expiring run and a surviving one.** It survives. Survivorship is
  derived from the runs that remain, not from a count that could drift.
- **A credential is revoked mid-request.** The request in flight completes; revocation is observed at
  the next authentication. Aborting an in-flight request would make revocation latency a property of
  request duration, and the propagation bound is what is documented instead.
- **Every admin credential is revoked.** The deployment can no longer issue keys through a route, and
  recovery is the command line acting directly on the database. Locking oneself out is possible and
  is documented, because the alternative — an unrevokable key — is worse.
- **The key store is unreachable but the file-backed key ring is configured.** Authentication
  continues against the file, and readiness reports the degradation. A deployment that never adopted
  database-backed keys is unaffected by their outage.
- **A webhook receiver is slow enough that deliveries overlap.** Deliveries for one run are ordered
  and never concurrent; a receiver sees at most one in-flight delivery per run.
- **A webhook receiver is a docdoc endpoint.** Refused by the destination policy. A deployment that
  can be made to call itself is one request away from an amplification loop.
- **A quota is reduced below a tenant's current usage.** No run is aborted. New submissions are
  refused until usage falls under the new limit, and the documentation says so, because the opposite
  behaviour discards work that was already paid for.
- **A token budget is exceeded partway through a run.** The run completes. The budget refuses the
  next submission. A budget that could abort in flight would reintroduce the paid-and-discarded
  failure Milestone 9 was built to remove.
- **The OTLP collector is down for an hour.** Runs are unaffected, export failures are reported
  once, and no telemetry buffer grows without bound.
- **A correction is submitted against a result that has been erased.** It is refused, naming the
  erasure. Recording an annotation against something that no longer exists produces a correction
  nobody can interpret.
- **A correction exists against a run that reaches its retention deadline.** The run is retained
  until the correction's own retention allows both to go, and FR-013 states which retention wins.
- **A tenant asks for a priority above its ceiling.** The run is accepted at the ceiling and told
  what it got. Refusing would turn an operator lowering a ceiling into an outage for a client that
  changed nothing.
- **Every tenant is given the top ceiling.** Priority stops distinguishing anything and claim order
  falls back to creation time within the class, which is Milestone 9's behaviour. A misconfiguration
  here costs ordering, not correctness or liveness.
- **A routing policy is edited.** Existing decisions are unchanged and keep naming the version they
  were made under. Re-routing is an explicit act that produces a new decision, never a rewrite.

## Requirements *(mandatory)*

### Functional Requirements

#### Retention, deletion, and erasure

- **FR-001**: The system MUST provide a retention sweep that removes runs whose `expires_at` has
  passed, invocable both as a subcommand of the existing command-line interface and automatically by
  a worker's maintenance tick (FR-114). A deployment MUST NOT have to schedule anything externally
  to get retention, and MUST be able to run a sweep on demand when it wants one.
- **FR-002**: The sweep MUST be idempotent and MUST be safe to interrupt: re-running it after a
  failure MUST reach the same end state as an uninterrupted run.
- **FR-003**: The sweep MUST NOT remove a run that is not terminal, and MUST NOT remove a run a
  worker currently holds a lease on.
- **FR-004**: Removing a run MUST leave a tombstone carrying the run identity, the owning tenant, the
  deletion timestamp, and the policy under which it was deleted — and no other field of the run. The
  policy name is what distinguishes a run that aged out from one erased on request; no second
  mechanism may record that distinction.
- **FR-004a**: A tombstone MUST NOT be a run status. `RunStatus` MUST remain the five members
  Milestone 9's FR-006 closed it at — `queued`, `running`, `succeeded`, `failed`, `cancelled` — with
  nothing added, so that the state machine, the command line, and every existing client keep an
  exhaustive set they already handle. A removed run MUST be reported as no longer being a run rather
  than as a run in a sixth state, because a `Run` carrying a status and no other field is the shape
  `src/docdoc/runs/model.py` rejected when it left `expired` out.
- **FR-005**: The system MUST support erasure by tenant and erasure by document, each removing the
  source blob, the artifacts derived from it that no surviving run of that tenant references, and the
  run records that describe them.
- **FR-006**: Erasure of a tenant MUST be a prefix operation over the store, never a scan of it, per
  ADR-0014 §5.
- **FR-007**: Survivorship MUST be derived from the runs that remain, and MUST NOT depend on a
  reference count or any other mutable structure beside the artifact store.
- **FR-008**: Erasure MUST NOT remove content belonging to any other tenant, including when two
  tenants submitted byte-identical documents.
- **FR-009**: Erasure MUST be idempotent, and erasing a tenant that does not exist MUST succeed
  having removed nothing.
- **FR-010**: A run in flight MUST be cancelled before its tenant's erasure proceeds.
- **FR-011**: An erased identifier MUST be reported to its owning tenant, on the route that served
  the run, as having existed and no longer existing — naming when and under which policy, and
  carrying nothing else. To every other tenant it MUST be indistinguishable from an identifier that
  never existed, preserving Milestone 9's FR-066 unchanged. The two responses MUST differ in kind,
  not in a field of one shape, so that no client can read a removed run as a run.
- **FR-012**: Every deletion and erasure MUST report counts of what it removed, by kind, and MUST
  emit a structured event carrying those counts and no content.
- **FR-013**: A run whose result carries a recorded correction MUST NOT be removed by the retention
  sweep while that correction is retained. Correction retention MUST be configurable independently
  of run retention, and where the two disagree the longer one MUST win.
- **FR-014**: Retention MUST be configurable per deployment, and a deployment that configures none
  MUST behave exactly as Milestone 9 did: nothing is swept and nothing is removed.
- **FR-015**: The sweep MUST bound the work it performs in one invocation, so that a deployment with
  a year of accumulated rows can make progress without one invocation running unboundedly.
- **FR-016**: Deletion MUST NOT mutate any artifact, envelope, or identity derivation. Content is
  removed or it is not; nothing is rewritten.

#### Telemetry export

- **FR-017**: The system MUST be able to export the existing `pipeline.stage` and `run.transition`
  events to an OTLP endpoint, binding to the observer hooks that already exist.
- **FR-018**: Export MUST be disabled by default, and a deployment that configures no endpoint MUST
  require no telemetry dependency and MUST behave identically to Milestone 9.
- **FR-019**: Telemetry integration MUST ship as an optional extra and MUST NOT enter the base
  install.
- **FR-020**: Exported spans MUST carry identifiers, hashes, states, counts, durations, and class
  names only — never document text, extracted values, claimed text, prompt bodies, credentials, or
  provider messages.
- **FR-021**: A trace MUST be correlatable to a run by run identity, and to a result by processing
  identity where one exists.
- **FR-022**: An unreachable or failing exporter MUST NOT fail a run, MUST NOT block a stage, and
  MUST be reported once rather than per event.
- **FR-023**: No new container may be added to the development composition for telemetry; the
  endpoint is the operator's.
- **FR-024**: Adding export MUST NOT add, remove, or reorder any existing emission point, and MUST
  NOT change any existing log line's payload.

#### Credential lifecycle

- **FR-025**: The system MUST support issuing a credential for a named tenant, returning the
  plaintext exactly once and never again.
- **FR-026**: Stored credentials MUST be stored as digests, never as plaintext, matching what the
  file-backed key ring already does.
- **FR-027**: The system MUST support revoking a credential, and revocation MUST take effect in every
  running process within a documented propagation bound, with no restart and no file edit.
- **FR-028**: The propagation bound MUST be configurable and MUST be stated in the operator
  documentation as a number, not as "immediately".
- **FR-029**: A tenant MUST be able to hold more than one active credential, so that rotation needs
  no interval during which requests fail.
- **FR-030**: The system MUST support listing a tenant's credentials by identifier, creation time,
  last-used time, and revocation state — and MUST NOT be able to return the credential itself.
- **FR-031**: Credential administration routes MUST require an administrative scope that an ordinary
  tenant credential does not carry.
- **FR-032**: An administrative credential MUST NOT be issuable through the routes it authorises
  without an existing administrative credential; the first one MUST be created by an explicit
  command-line act against the deployment.
- **FR-033**: A credential MUST NOT appear in a log line, a run record, an error body, a telemetry
  attribute, a webhook payload, or a process argument list.
- **FR-034**: An absent, malformed, unrecognised, or revoked credential MUST produce one
  indistinguishable refusal, preserving the property `api/auth.py` already holds.
- **FR-035**: Every credential operation MUST emit a structured audit event naming the actor, the
  operation, the affected credential identifier, and the tenant — and never the credential.
- **FR-036**: A revoked credential MUST NOT be reissuable, and its identifier MUST NOT be reused.
- **FR-037**: Credentials MUST remain resolvable to exactly one tenant, preserving Milestone 9's
  FR-060; an administrative scope is an additional capability on a principal, never a second tenant.
- **FR-038**: The file-backed key ring MUST continue to work unchanged. A deployment configured as
  Milestone 9 configured it MUST authenticate exactly as before and MUST NOT require the key store.
  This requirement supersedes `specs/009` FR-061, which forbade route-based mutation *in that
  milestone*; the supersession MUST be recorded in ADR-0016.

#### Limits: rate, concurrency, quota, and budget

- **FR-039**: The system MUST support a per-tenant limit on submissions per interval.
- **FR-040**: The system MUST support a per-tenant limit on concurrent non-terminal runs.
- **FR-041**: The system MUST support a per-tenant limit on runs per calendar period.
- **FR-042**: The system MUST support a per-tenant token budget per calendar period, enforced at
  submission.
- **FR-043**: A refusal MUST name which limit was reached and MUST carry a retry hint, and MUST be a
  retryable status distinct from an authentication or authorisation refusal.
- **FR-044**: A refused submission MUST create no run, consume no queue position, and touch no store.
- **FR-045**: No limit may abort, cancel, or discard a run that is already executing.
- **FR-046**: Reducing a limit below a tenant's current usage MUST refuse new submissions and MUST
  NOT affect runs in flight.
- **FR-047**: The documentation MUST state that a token budget is enforced one submission late,
  because token usage is known only after a provider answers.
- **FR-048**: Counting MUST be for enforcement only. The system MUST NOT price, invoice, or produce a
  billing record, and the amendment recorded under FR-102 MUST say so.
- **FR-049**: A deployment that configures no limits MUST count nothing, refuse nothing, and behave
  identically to Milestone 9.
- **FR-050**: Limits MUST be configurable per tenant, with a deployment-wide default, following the
  existing precedence rule.
- **FR-051**: Limit state MUST NOT be held in a worker's memory, so that adding a worker does not
  multiply a limit.
- **FR-052**: A limit refusal MUST emit a structured event carrying the tenant, the limit, and the
  observed value, and no content.

#### Webhook delivery

- **FR-053**: A run submission MUST be able to carry a callback destination, and the system MUST
  deliver to it when the run reaches a terminal state.
- **FR-054**: A delivery payload MUST carry the run identity, the terminal state, the failing stage
  and error class where the run failed, and the processing identity where one exists — and MUST NOT
  carry document text, extracted values, prompt bodies, credentials, or provider messages.
- **FR-055**: Every delivery MUST be signed with a per-destination secret such that a receiver can
  verify origin and detect any alteration of the body.
- **FR-056**: Every delivery MUST carry a delivery identifier that is stable across retries, so a
  receiver can discard duplicates.
- **FR-057**: Delivery MUST be at-least-once, retried with backoff up to a configured attempt limit,
  after which the delivery MUST come to rest in a terminal failed state.
- **FR-058**: Deliveries for one run MUST be ordered and MUST NOT overlap.
- **FR-059**: Delivery outcomes MUST be readable by the owning tenant, including attempt count and
  final state.
- **FR-060**: A destination MUST be validated against a configured policy at registration and again
  before each attempt, and destinations resolving to loopback, link-local, or private network ranges
  MUST be refused unless explicitly allowed.
- **FR-061**: Redirects MUST NOT be followed.
- **FR-062**: A delivery attempt MUST be bounded in time, and the maintenance tick that performs it
  MUST be bounded in total, so a worker's next claim is delayed by at most that bound. The bound is
  what satisfies this requirement — not concurrency, which FR-114 forbids for the reason Milestone 9
  gave.
- **FR-063**: A failing or slow receiver MUST NOT affect run execution, run state, or any other
  tenant's deliveries.
- **FR-064**: A deployment that registers no callbacks MUST perform no outbound request and MUST
  require no delivery dependency.
- **FR-065**: Delivery MUST NOT be the only record of a terminal state; polling MUST continue to work
  unchanged, so a lost delivery loses no information.
- **FR-066**: A destination secret MUST be stored as configuration or as a digest-protected record,
  MUST NOT be returned by any route after registration, and MUST NOT appear in any log or event.

#### Confidence routing

- **FR-067**: The system MUST produce a routing decision for every completed run, drawn from a closed
  set of exactly two outcomes: `automatic` and `review`. The set MUST NOT be extended in this
  milestone, and MUST NOT be configurable — a routing outcome that a policy can name freely is one no
  client can switch on exhaustively and no test can cover.
- **FR-067a**: A routing outcome MUST NOT express what the deployment should *do* with a result.
  `reject` is a disposition and belongs to the caller; `retry` would make routing a control-flow
  decision that re-enters the pipeline, which FR-073 forbids and SC-001 depends on it not doing.
- **FR-068**: A routing decision MUST be computed from grounding status, grounding score, validation
  severities and verdicts, and schema requiredness only.
- **FR-069**: A routing decision MUST NOT read `model_confidence` or any model self-reported signal,
  per Principle II and ADR-0004.
- **FR-070**: The routing policy MUST be data, MUST be versioned, and the version MUST be recorded on
  every decision.
- **FR-071**: Two identical results evaluated under one policy version MUST produce identical
  decisions.
- **FR-072**: A routing decision MUST name the signals that produced it, so a reader can see why
  without re-deriving it.
- **FR-073**: Producing a routing decision MUST NOT alter the result, its artifacts, its verdicts, or
  any identity, and MUST NOT produce a blended confidence value.
- **FR-074**: Editing a policy MUST NOT alter existing decisions; re-routing MUST be an explicit act
  producing a new decision that records the new version.
- **FR-075**: A deployment that configures no policy MUST produce no routing decision and MUST behave
  identically to Milestone 9.
- **FR-076**: The routing outcome MUST be readable through the run's status representation, and MUST
  be deliverable in a webhook payload as an outcome name rather than as a computed score.

#### Human corrections

- **FR-077**: The system MUST accept a correction recorded against a completed run's result, using
  the existing `Correction` model without redefining it.
- **FR-078**: A correction MUST alter nothing it annotates — not the extraction, not the grounding,
  not the validation result, not any identity.
- **FR-079**: A correction MUST be readable and writable only under the tenant that owns the run it
  annotates, indistinguishably from non-existence otherwise.
- **FR-080**: A correction MUST move no evaluation metric until an explicit promotion, preserving
  Milestone 6's FR-053.
- **FR-081**: The system MUST be able to export a tenant's corrections in a form the existing
  promotion path accepts, so that a correction becomes dataset signal without a second model.
- **FR-082**: A correction against an erased or non-existent result MUST be refused, naming which.
- **FR-083**: Corrections MUST NOT introduce assignment, reviewer queues, workload tracking,
  review states, or any editing interface. Principle IX permits the model and forbids the platform,
  and the deferred-technology list names "a full review UI".
- **FR-084**: The browser viewer MUST remain read-only and MUST NOT gain a write path in this
  milestone.
- **FR-085**: A correction MUST record its annotator as supplied by the caller, and the system MUST
  NOT infer one from a credential.
- **FR-086**: Correction records MUST be subject to retention and erasure exactly as run records are,
  per FR-005 and FR-013. Erasure MUST also remove the tenant's callbacks, deliveries, and limit
  counters: every table this milestone adds MUST hold nothing for an erased tenant. Corrections are
  named first because they are the only one that can hold a value taken from a document.

#### Priority and fairness

- **FR-087**: A run MUST be able to carry a priority drawn from a closed, small set, defaulting to
  ordinary.
- **FR-087a**: Priority MUST be settable by the submitting client on the submission itself, and MUST
  be bounded by a per-tenant ceiling set in deployment configuration. A submission requesting a
  priority above its tenant's ceiling MUST be accepted at the ceiling and MUST be told what it was
  given, rather than refused — a ceiling lowered by an operator must not break a client that was
  working the day before.
- **FR-087b**: A tenant MUST NOT be able to raise its own ceiling through any route, so that priority
  cannot become self-service escalation past another tenant's queue.
- **FR-088**: Claiming MUST prefer higher-priority eligible runs.
- **FR-089**: Claiming MUST NOT starve: a run that has waited beyond a configured bound MUST be
  claimed ahead of higher-priority runs that arrived later.
- **FR-090**: Claiming MUST distribute across tenants such that one tenant's backlog cannot delay
  another tenant's run beyond a bounded number of claims.
- **FR-091**: A deployment that sets no priorities and has **one tenant with queued work** MUST claim
  in ascending creation time, exactly as Milestone 9's FR-024 specified. This requirement amends
  FR-024 rather than contradicting it: that requirement's own text noted it described an ordering
  because "nothing else is ever unequal", and something now is.

  *Amended 2026-09-04, during a code review that read this against the implementation.* It said "no
  priorities **and no fairness policy**", which assumed fairness would be something an operator
  configures. It is not, and it cannot be: FR-090 requires a bound on how long one tenant's backlog
  may delay another, and a switch that turned that off would restore exactly the starvation FR-090
  exists to forbid. The two requirements cannot both hold with a fairness toggle in between.

  So the honest statement is the one above, and the honest **cost** is stated with it: a Milestone 9
  deployment serving **more than one tenant** does observe a different claim order after this
  milestone — strict global FIFO becomes per-tenant fairness. That is a behavioural change on an
  upgrade, and it is the only one in this milestone that FR-101 does not cover, because it is the
  thing User Story 4 exists to deliver rather than a capability somebody switches on. A deployment
  with one tenant sees no difference at all.
- **FR-092**: Claiming MUST remain a single statement that two workers cannot satisfy for the same
  run, preserving Milestone 9's FR-016 and its `SKIP LOCKED` guarantee.
- **FR-093**: Priority MUST NOT affect any value, verdict, location, or identity a run produces.

#### Layering, guards, and compatibility

- **FR-094**: This milestone MUST NOT modify the kernel, ingest, extraction, grounding, or validation
  layers, and MUST NOT modify the artifact envelope's identity derivation.
- **FR-095**: The existing determinism scan and runtime audit hook MUST continue to pass unmodified,
  and no exemption may be granted for this milestone.
- **FR-096**: Clocks, random sources, and network calls introduced by this milestone MUST live at or
  above the `Runs` layer and MUST travel downward as data.
- **FR-096a**: Within the `Runs` layer, `identity.py` MUST remain the **only** module that reads a
  clock or a random source, which is stricter than FR-096 and is what
  `tests/unit/test_runs_clock_confinement.py` already enforces over every module in the package. Every
  module this milestone adds — `retention`, `keys`, `limits`, `delivery`, `routing`, `corrections`,
  `maintenance` — MUST receive instants and identifiers as parameters, exactly as `queue.py` does.
  **That test MUST NOT gain a second entry in its `PERMITTED` set.** Widening it is the fix that
  presents itself and it is the one that removes the guard: the same test's docstring records that an
  alias written to avoid a false positive had already disabled the check once, and ADR-0013 §4's
  redelivery safety rests on the layers below staying pure.
- **FR-097**: The layers contract MUST be amended in the same change as any layer added, and the
  constitution's Principle X text MUST be amended in that same change, as that principle requires.
- **FR-098**: The prohibition on a broker, coordinator, or scheduler MUST remain enforced by the
  existing `forbidden` contract, unmodified.
- **FR-099**: Outbound HTTP introduced for delivery and for telemetry export MUST NOT be reachable
  from any deterministic layer, and MUST be enforced by a `forbidden` contract rather than by review.
- **FR-100**: Every error introduced MUST be typed and provider-neutral, consistent with the
  constitution's error model, and any error name added to that model MUST arrive with the amendment
  under FR-102.
- **FR-101**: Every capability in this milestone MUST be off by default. A Milestone 9 deployment
  that changes no configuration MUST observe no behavioural change whatsoever — **with one stated
  exception**: a deployment serving more than one tenant observes per-tenant claim fairness in place
  of strict global FIFO, because FR-090's bound cannot be made conditional without reintroducing the
  starvation it forbids. FR-091 records the reasoning and the cost. Every other capability here —
  retention, erasure, runtime credentials, limits, delivery, telemetry, routing, corrections — is
  inert until configured.
- **FR-112**: Every piece of mutable state this milestone introduces — credentials, delivery
  attempts, corrections, limit counters, and tombstones — MUST live as additional tables in the
  existing run-state database, applied by the same explicit, repeatable, idempotent migration step
  Milestone 9's FR-078 requires. No second database, no second migration mechanism, and no ORM.
  *(Numbered after FR-111 because it arrived with the 2026-09-04 clarifications; it belongs beside
  FR-094.)*
- **FR-113**: Enabling any capability of this milestone MAY therefore require the database, and the
  documentation MUST say so. This supersedes Milestone 9's assumption that "the database is a
  dependency of asynchrony only": runtime credential revocation, limits, delivery, and corrections
  each need it, and a deployment serving only the synchronous routes previously needed none. A
  deployment enabling none of them MUST continue to require none, in any configuration, including
  the library and the command line.
- **FR-114**: A worker MUST perform periodic maintenance between claims — one bounded batch of
  retention sweep work and the deliveries that are due — and each tick MUST be bounded in wall-clock
  time. Maintenance MUST NOT run in a thread, a subprocess, or an event loop, so that Milestone 9's
  FR-025 and its heartbeat argument remain untouched, and MUST NOT delay a claim beyond the tick
  bound. *(Belongs beside FR-001 and FR-057; numbered here because it arrived with the 2026-09-04
  clarifications.)*
- **FR-115**: Credential freshness MUST be a bounded cache lifetime in every process that
  authenticates, and FR-028's documented propagation bound MUST be that lifetime. Revocation MUST NOT
  depend on a worker running, because a deployment serving only the synchronous routes may run none.
- **FR-116**: This milestone MUST NOT introduce a fifth process type. The development composition
  MUST still contain exactly the four containers Milestone 9's composition contains, and no
  scheduler, reaper, or maintenance process may be added.
- **FR-117**: A deployment running no worker performs no retention sweep and attempts no delivery,
  and the operator documentation MUST say so plainly. Its credentials still revoke on time, because
  FR-115 does not route through a worker.

#### Governance, packaging, and documentation

- **FR-102**: Constitution v1.8.0 MUST be written and adopted before implementation begins, recording
  that counting for enforcement is permitted while metering for billing, invoicing, and pricing
  remain deferred, and carrying any additions to the error-model list.
- **FR-103**: ADR-0015 MUST be written and accepted before implementation begins, recording deletion
  and erasure over a content-addressed store: survivorship by derivation, the tombstone, and the
  tenant-prefix operation.
- **FR-104**: ADR-0016 MUST be written and accepted before implementation begins, recording the
  credential lifecycle, the administrative scope, the propagation bound, and the supersession of
  `specs/009` FR-061.
- **FR-105**: ADR-0017 MUST be written and accepted before implementation begins, recording the
  routing policy: which signals it may read, why `model_confidence` is not among them, and how the
  policy is versioned.
- **FR-106**: ADR-0018 MUST be written and accepted before implementation begins, recording webhook
  delivery semantics: at-least-once, signing, ordering, and the destination policy.
- **FR-107**: Every configuration setting introduced MUST follow the existing precedence rule —
  explicit argument over environment over default — and MUST gain a flag unless it is a credential or
  is meaningless outside one process type.
- **FR-108**: Every optional integration introduced MUST ship as an optional extra, and the base
  install MUST acquire no new dependency.
- **FR-109**: The milestone MUST ship concept documentation covering retention and erasure, the
  credential lifecycle, limits, delivery, and routing — and MUST state each capability's limits where
  it states its behaviour.
- **FR-110**: The milestone MUST ship runnable examples for at least: erasing a tenant, rotating a
  credential, and receiving a signed webhook.
- **FR-111**: The README roadmap, configuration section, and the authentication warning MUST be
  updated. The warning MUST state what becomes true: that credentials can now be issued and revoked
  at runtime, that the viewer still does not work under authentication, and that a deployment which
  has enabled none of this is exactly as exposed as Milestone 9 left it.

### Key Entities

- **Retention policy**: The rule that decides when a run and its derived content may be removed.
  Configuration, not a record, and named on every tombstone so a deletion can be explained later.
- **Tombstone**: What remains of a removed run. Identity, tenant, deletion time, policy — and
  nothing else, so it can answer "was this here?" without preserving what was deleted. **Not a run
  and not a run status**: it is a separate thing with four fields, which is why `RunStatus` gains no
  member.
- **Credential**: An issued secret resolving to one tenant and a set of capabilities. Stored as a
  digest; identified by an identifier that outlives it and is never reused.
- **Administrative scope**: A capability on a principal permitting credential administration. Not a
  second tenant, and not a role system.
- **Limit**: A counted bound per tenant — rate, concurrency, period count, token budget. Counted to
  refuse, never to price.
- **Delivery**: One attempt to notify a destination of a terminal run, carrying a stable identifier
  across retries and a signature over its body.
- **Routing decision**: One of two outcomes — `automatic` or `review` — computed from trusted signals
  over a completed result, carrying the policy version and the signals that produced it. Not a
  confidence, not a value, and not a disposition: it says how sure docdoc is, never what to do next.
- **Correction**: A reviewer's statement that a recorded value was wrong. Already modelled; this
  milestone gives it a home and a route and changes nothing about what it is.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Golden-set metrics are **bit-identical** before and after this milestone, and a result
  produced with every capability here enabled agrees with one produced with all of them disabled on
  **100%** of values, verdicts, locations, and identities. This is the criterion Milestone 9's
  deferral of routing and corrections was protecting; it is restated rather than relaxed.
- **SC-002**: A Milestone 9 deployment upgrades with **zero** configuration changes and observes
  **zero** behavioural differences: nothing swept, nothing counted, nothing refused, nothing
  exported, nothing routed, nothing delivered.
- **SC-003**: The retention sweep is idempotent in **100%** of runs: a second invocation over the
  same state removes zero further items, and an invocation interrupted at any point and re-run
  reaches the same end state.
- **SC-004**: Erasing one tenant removes **100%** of that tenant's blobs, artifacts, and run records
  and **0%** of any other tenant's, verified over two tenants holding byte-identical documents.
- **SC-005**: After erasure, **100%** of the erased tenant's identifiers are reported to other
  tenants byte-identically to identifiers that never existed, and **100%** are reported to the owning
  tenant as erased.
- **SC-006**: A revoked credential is refused by every already-running process within the configured
  propagation bound in **100%** of attempts, with **zero** process restarts.
- **SC-007**: Across credential issuance, rotation, and revocation, **0%** of log lines, run records,
  error bodies, telemetry attributes, and webhook payloads contain a credential or a fragment of one,
  verified over credentials seeded with distinctive strings.
- **SC-008**: Rotation is seamless: during an overlap window with two active credentials, **0%** of
  requests are refused.
- **SC-009**: A tenant driven past each configured limit is refused in **100%** of over-limit
  submissions, each refusal naming the limit; **0%** of runs already executing are aborted; and a
  second tenant's acceptance rate is unaffected.
- **SC-010**: With one tenant holding 1000 queued runs and another submitting one, the second
  tenant's run is claimed within a bounded number of claims, measured rather than asserted, and
  **0%** of runs are starved indefinitely under any priority configuration.
- **SC-011**: **100%** of terminal runs with a registered callback produce at least one delivery
  attempt; **100%** of delivered bodies verify against their signature; and a body altered by one
  byte verifies in **0%** of cases.
- **SC-012**: A receiver failing every attempt causes **0%** of runs to fail, **0%** of workers to
  block, and the delivery to come to rest at the configured attempt limit in **100%** of cases.
- **SC-013**: **0%** of destinations resolving to loopback, link-local, or private ranges are
  accepted without explicit allowance, and **0%** of redirects are followed.
- **SC-014**: With an exporter configured, **100%** of executed runs produce a trace correlatable to
  the run and to its result; with none configured, **0%** of runs require any telemetry dependency
  and the offline suite passes without it installed.
- **SC-015**: **0%** of exported spans contain document text, extracted values, claimed text, prompt
  bodies, credentials, or provider messages, verified over a document seeded with distinctive
  strings.
- **SC-016**: With the collector unreachable, **100%** of runs still succeed and export failures are
  reported once per outage rather than once per event.
- **SC-017**: Two identical results under one policy version produce identical routing decisions in
  **100%** of cases, and **0%** of routing decisions read a model self-reported signal — verified by
  a test that varies `model_confidence` alone and asserts the decision does not move. The outcome set
  has exactly **two** members, asserted by name.
- **SC-018**: Recording a correction changes **0** bytes of the result, its artifacts, and its
  identities, and moves **0** golden-set metrics absent an explicit promotion.
- **SC-019**: A run carrying a correction is removed by the sweep in **0%** of cases while that
  correction is retained.
- **SC-020**: The diff for this milestone touches **zero** files under the kernel, ingest,
  extraction, grounding, and validation layers, and **zero** lines of the artifact envelope's
  identity derivation.
- **SC-021**: A base install acquires **zero** new dependencies, and the full offline suite passes
  with no database, no object store, no telemetry extra, and no delivery extra present.
- **SC-022**: The development composition still contains exactly four containers, and reaches a
  working deployment from a clean checkout in under **10 minutes** of operator time.
- **SC-023**: `RunStatus` gains **zero** members and the documented run state machine gains **zero**
  transitions, verified by a test that asserts the status set against Milestone 9's five by name — so
  that a client switching exhaustively on it keeps compiling and keeps being right.
- **SC-024**: With maintenance enabled and a backlog of expired runs and due deliveries, a worker's
  claim latency increases by **at most the configured tick bound**, and **zero** runs lose a lease to
  maintenance work, measured with the bound set deliberately small and deliberately large.
- **SC-025**: The deployment still has **four** process types and the composition **four**
  containers, with no scheduler, reaper, or maintenance process among them.
- **SC-026**: **0%** of submissions are granted a priority above their tenant's ceiling, and **100%**
  of submissions that asked for one are accepted at the ceiling with the granted priority stated in
  the response.

## Assumptions

- **Every capability here is off by default, and that is a compatibility choice rather than a safe
  one.** The same reasoning Milestone 9 applied to authentication applies to all seven items: an
  upgrade must change nothing, and the cost is that a deployment gets none of this until an operator
  asks. The README says so rather than leaving it to be discovered.
- **Deletion is a sweep, not a statement.** Erasure over a content-addressed store means deriving
  what survives, which costs more than dropping rows. That cost is accepted because the alternative
  is a mutable counter beside an immutable store.
- **A tombstone is not a privacy leak.** It carries an identity, a tenant, a time, and a policy name.
  A deployment for which the *existence* of a deleted run is itself sensitive needs a different
  answer, and should record that as a new decision rather than widen this one.
- **Revocation is bounded, not instant.** Propagation is a number, configurable, and documented as a
  number. A design promising instant revocation would need a check on every request against a shared
  store, which trades a documented bound for an undocumented dependency on that store's availability.
- **Limits are enforced at submission, and the token budget is enforced one submission late.** Tokens
  are known only after a provider answers. Enforcing mid-run would abort work already paid for, which
  is the failure Milestone 9 was built to remove.
- **Counting is not billing.** The counters exist to refuse. Nothing here prices, invoices, or
  produces a billing record, and the constitutional amendment under FR-102 states that boundary
  rather than leaving it to a reviewer's reading.
- **The database stops being asynchrony's alone.** Milestone 9 could say a deployment using neither
  the run routes nor a worker needed no database; this milestone cannot, because runtime revocation,
  limits, delivery, and corrections are all state that outlives a request. What survives is the
  weaker and still useful form: a deployment that enables none of these needs no database, and the
  library and command line need none in any configuration.
- **Polling remains the contract.** Webhooks are an optimisation over it. A lost delivery loses no
  information because the run row is still the record, exactly as it was in Milestone 9.
- **Routing reads only what docdoc computed.** A model's self-report is not an input, now or later,
  without an ADR that reopens ADR-0004.
- **Corrections are a model with a home, not a workflow.** There is no assignment, no reviewer queue,
  no review state, and no editing interface. Where a human does the reviewing is outside docdoc.
- **The operator interface the roadmap will want is not this milestone.** It needs runtime credentials
  and a session mechanism; this milestone builds the first and not the second, and the viewer stays
  read-only and still unavailable under authentication.

## Dependencies

- **Constitution v1.8.0 must be adopted first** (FR-102). Without it, the quota and budget work
  conflicts with a deferral this document cannot reinterpret away.
- **ADR-0015, ADR-0016, ADR-0017, and ADR-0018 must be accepted first** (FR-103 – FR-106). Each
  records one decision, per the ADR directory's own convention.
- **ADR-0003 constrains deletion.** Artifact identity is derived from content and is not touched;
  survivorship is derived from it rather than stored beside it.
- **ADR-0004 and Principle II constrain routing.** Trusted and untrusted confidence stay separate and
  no blended value is produced.
- **ADR-0010 §4 and §5 remain load-bearing.** Deletion removes content; it never rewrites an artifact
  or relaxes the refusal to overwrite one.
- **ADR-0014 §5 is what makes tenant erasure affordable.** The prefix layout was chosen in Milestone
  9 for this milestone, and this milestone depends on it.
- **`specs/009` FR-061 is superseded** by FR-038, recorded in ADR-0016.
- **`specs/009` FR-024 is amended** by FR-091, which preserves its default.
- **`specs/009` FR-006 is preserved unchanged** by FR-004a: the five-member closed status set is not
  extended, and the run state machine documented in `specs/009/contracts/` keeps every transition it
  has and gains none.
- **Milestone 6's `Correction` model and promotion path are the integration point** for corrections,
  and neither is redefined here.
- **The existing import-contract, determinism scan, and audit hook gate this work** and are not
  modified by it.

## Out of Scope

Deferred to Milestone 11, each because it can be added later without changing anything this
milestone decides:

- **An operator or administrative interface.** It needs a session mechanism the viewer does not have,
  and the deferred-technology list names "a full review UI". This milestone makes such an interface
  *possible* by giving credentials a lifecycle; it does not build one.
- **A writable viewer.** Milestone 8's read-only claim stands unchanged.
- **Reviewer assignment, review queues, workload, and review states.** The review platform Principle
  IX forbids.
- **Billing, metering for invoicing, and per-tenant pricing.** Still deferred, and the amendment
  under FR-102 states that explicitly rather than by omission.
- **Metrics and dashboards as a docdoc-owned surface.** Export binds to an endpoint the operator
  runs; docdoc ships no collector, no dashboard, and no container for either.
- **Webhook delivery of anything other than terminal run state.** Per-stage callbacks would make
  delivery a second observability channel with its own ordering guarantees.
- **Cross-tenant artifact reuse and billing groups.** Closed by ADR-0014; reopening it reopens the
  existence oracle.
- **Route-scoped readiness and deliberate degraded operation.** An availability posture, and it needs
  its own ADR before it needs an implementation.
- **Horizontal scaling of the API beyond what a stateless process gets for free.** Nothing here makes
  it stateful, so there is no work to defer — it is listed to record that the item was checked.
- **Any routing outcome beyond `automatic` and `review`, and any disposition.** What a deployment
  does with a result it was told to review is the deployment's, and a third outcome would be docdoc
  claiming otherwise.
- **Automatic re-routing of historical results when a policy changes.** Re-routing is explicit
  (FR-074); doing it in bulk is a batch operation nobody has asked for.
