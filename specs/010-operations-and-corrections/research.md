# Phase 0 Research: Retention, Credentials, Limits, Delivery, and the Human Loop

**Feature**: `010-operations-and-corrections` | **Date**: 2026-09-04 | **Spec**: [spec.md](./spec.md)

Sixteen questions. Three of them changed the design rather than confirming it, and they are R1, R2,
and R4 — each found by reading the code the spec assumed, not by reasoning about the spec. They are
first for that reason.

---

## R1. How does the sweep know which artifacts survive?

**Decision**: It **reads** them. A run row's `stage_outcomes` is a `jsonb` projection of
`PipelineResult.outcomes` and each entry already carries `artifact_id`
(`src/docdoc/runs/model.py:82`). The set of artifacts a run holds is therefore recorded, not derived,
and the sweep needs no access to a parser, a schema registry, an adapter, or an options hash.

**And the deletion set is a difference, never a complement.** The sweep computes:

```text
candidates = ⋃ artifact_ids of the runs being removed
survivors  = ⋃ artifact_ids of the runs that remain, for that tenant
delete     = candidates − survivors
```

**Rationale, and this is the part that matters**: the obvious implementation — "delete every artifact
no surviving run references" — **deletes every artifact `docdoc extract` ever wrote from the command
line.** A CLI extraction writes into the same store under the same tenant and creates no run row, so
under a complement it is unreferenced by construction. The same is true of anything a library caller
produced, and of the recorder's evaluation artifacts. A set difference cannot reach them, because an
artifact only becomes a candidate by having been named by a run that is being deleted.

This satisfies the spec's FR-007 ("survivorship MUST be derived from the runs that remain, and MUST
NOT depend on a reference count") more cheaply than the spec anticipated. An earlier draft of
spec.md said the sweep would "derive the artifact chain each one would use"; the honest finding is
that no derivation is needed at all, and that re-deriving would be *wrong* rather than merely
wasteful, because it needs a parser version a reconfigured deployment no longer has. The spec was
corrected on 2026-09-04 and now says "reading".

**Alternatives considered**: *Reference counting* — exact, and it puts a mutable counter beside an
immutable store, which is the shape ADR-0010 §1 rejected for artifacts and which no crash-safe sweep
wants to maintain. *Re-deriving the chain from `blob_id` + `schema_identity`* — needs the parser
version and options hash that were in effect at the time, which a reconfigured deployment no longer
has; it would silently miss artifacts whose derivation inputs have since changed, which is the worst
possible failure for a deletion routine. *Complement over the whole tenant prefix* — rejected above,
and it is worth naming as a bug the review would probably not have caught, because a store with
no CLI use in the test fixtures passes it.

---

## R2. What happens when the tenant being erased is the default one?

**Decision**: Erasing the default tenant does **not** use the prefix path. It falls back to R1's set
difference over that tenant's runs. A separate, explicitly named destructive flag exists for an
operator who genuinely wants the bucket emptied, and it is never the default and never reachable
through an HTTP route.

**Rationale**: ADR-0014 §3 makes the default tenant's namespace the store root itself, so its prefix
is `<root>/` and a prefix delete is `rm -rf` over everything the deployment has ever stored —
including other tenants' `t/<id>/` subtrees. ADR-0014's own Consequences section predicted this in as
many words: *"The default tenant is the one whose deletion is dangerous. Its prefix is the store
root, so a naive 'delete tenant' implementation in Milestone 10 would delete everything. That is the
correct semantics … and it is written down here because it is the kind of correctness that reads as a
bug at review time."*

The ADR is right that it is the correct *semantics* and that is exactly why it cannot be the default
*behaviour*: in a deployment that never enabled authentication, "the default tenant" is not a
customer an operator is erasing, it is **everything**, and the operator asking to erase it almost
certainly means "the content my runs produced". The set difference gives them that. The flag gives
the other reading to whoever actually wants it, once, in a place where they have to type it.

**Alternatives considered**: *Refuse to erase the default tenant at all* — leaves a deployment with
no way to comply with a deletion request, and pushes operators to `aws s3 rm` where no guard exists
at all. *Always prefix-delete and document the danger* — the documentation would be correct and the
first person to run it would still lose a bucket.

---

## R3. The stores cannot delete anything

**Decision**: `BlobStore` and the `ArtifactStore` protocol gain deletion, additively:
`delete(id) -> bool` and `delete_prefix(tenant_id) -> int`. Four implementations follow —
`FileArtifactStore`, `NullArtifactStore` (a no-op that reports nothing deleted), `S3ArtifactStore`,
and `BlobStore`/`S3BlobStore`.

**Rationale**: `src/docdoc/artifacts/store.py` defines `ArtifactStore` as a `Protocol` with `get` and
`put` and nothing else, and `blobs.py` the same. Every deletion requirement in the spec — FR-001,
FR-005, FR-006 — is unimplementable without this, and it is the one place where a layer the milestone
otherwise does not touch has to change. It is permitted: FR-094 protects the kernel, ingest,
extraction, grounding, validation, and the *identity derivation* of the artifact envelope, and none of
those is this.

**The protocol change is a public break under ADR-0011's rules**, so it ships with a changelog entry
naming what moved. It is a `0.x` minor, which ADR-0011 permits to break any public API; the on-disk
format and the identity derivations — the two surfaces that get a deprecation path instead — are
untouched.

**Alternatives considered**: *A separate `DeletableStore` protocol implemented only by the two real
stores* — a caller then has to narrow before deleting, and every call site grows a branch for a case
that only `NullArtifactStore` exercises. *Deleting through the filesystem and `boto3` directly from
the sweep* — puts key derivation in two places, which is how a deletion routine ends up deleting the
wrong prefix.

---

## R4. There is one observer slot, and `run.transition` has no hook at all

**Decision**: The telemetry bridge is a **callable the deployment installs**, and docdoc ships the
factory rather than installing it behind the operator's back. `docdoc.telemetry.bridge()` returns a
callable that `pipeline.observe.set_observer` accepts. When the API or worker is configured with an
OTLP endpoint **and no observer is already installed**, it installs the bridge and says so once in
the log; when an observer is already installed, it does **not** displace it and reports that instead.

Separately, `runs/observe.py` gains the same single-slot hook `pipeline/observe.py` has, so that
`run.transition` can reach an exporter at all.

**Rationale**: `pipeline/observe.py:77` is explicit that there is one observer and that this is a
decision, not an oversight — *"One observer, not a list of them. A deployment that wants two can
write a function that calls two, and a registry of subscribers would be an event bus — infrastructure
with no present-tense reason to exist."* An exporter that quietly calls `set_observer` takes that
slot from whatever the deployment had in it, which is a silent regression of somebody else's
observability. Nothing in the spec authorises that, and FR-024 forbids changing existing emission
behaviour.

The second half is the smaller surprise and the more necessary change: `runs/observe.py` only calls
`logging`. It has `EVENT_NAME`, `log_transition`, and no `set_observer` at all, so FR-017's promise to
export `run.transition` cannot be met by binding to something that exists. The addition mirrors the
pipeline module exactly — one slot, same signature, same "called and its return ignored" contract —
so a deployment learns one pattern rather than two.

**Alternatives considered**: *Make `set_observer` a list* — reverses a documented decision for the
convenience of one caller, and the module's own rationale already gives the answer ("write a function
that calls two"). *Export by parsing log records via a `logging.Handler`* — turns a structured payload
into a string and back, and couples the exporter to the log configuration, which a deployment owns.

---

## R5. Which OpenTelemetry packages, and behind what

**Decision**: `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http`, behind a new
`docdoc[otel]` extra, imported lazily inside the bridge factory. Nothing in the base install, nothing
in `docdoc[api]`, nothing in `docdoc[postgres]`.

**Rationale**: the constitution's Observability paragraph says "OpenTelemetry where practical", so no
amendment is needed for the technology — only for the quota counting in R9. The HTTP/protobuf
exporter rather than gRPC: it is the smaller dependency tree, it traverses the proxies operators
already have, and a collector accepts both. Lazy import behind an extra is the pattern every provider
integration in this project already uses (`boto3` in `S3ArtifactStore.__init__`), and SC-021 measures
that the offline suite passes without it.

**Alternatives considered**: *`opentelemetry-instrumentation-*` auto-instrumentation* — pulls a
framework-wide dependency to instrument code that already emits exactly the events we want. *Writing
OTLP by hand over `urllib`* — saves a dependency and re-implements a wire format that changes.

---

## R6. What performs an outbound HTTP request for a webhook

**Decision**: the standard library — `http.client` driven through `socket`, with the destination
resolved and validated first (R8). **No new dependency, and no new extra.**

**Rationale**: the worker is synchronous, the maintenance tick is synchronous, and a delivery is one
POST with a timeout. `httpx` would be a base-install-adjacent dependency acquired to do what
`http.client` does; `requests` likewise. More decisively, R8's destination policy needs to connect to
an *IP address it has already validated* while presenting the original `Host` header, and the
standard library gives that directly while both high-level clients hide it behind a transport that
would have to be subclassed.

**Alternatives considered**: *`httpx` behind a `docdoc[webhooks]` extra* — a real option, and it
loses on R8: the pinned-address connection is the security property, and it is easier to get right
with `http.client.HTTPSConnection(host=ip, ...)` than by writing a custom transport. *Delivering from
the API process with `async` and `httpx`* — the API already has `httpx` nowhere, and it would put
retry state in a process that FR-116 keeps stateless.

---

## R7. How a delivery is signed

**Decision**: HMAC-SHA256 over `f"{timestamp}.{body}"`, sent as
`X-Docdoc-Signature: t=<unix-seconds>,v1=<hex>`, with a per-destination secret. `hmac` and `hashlib`,
both standard library. The timestamp is inside the signed material so a receiver can reject replays,
and the scheme is versioned in the header so a second one can exist later without ambiguity.

**Rationale**: it is the scheme receivers already know how to verify, it needs no key distribution
beyond the secret the operator registers, and it costs nothing. Signing the timestamp with the body
is what makes replay detectable; signing the body alone leaves a captured delivery valid forever.

**Alternatives considered**: *Asymmetric signatures (Ed25519)* — better key handling, and it needs a
key distribution story and a dependency, for a receiver population of one per destination. *TLS
client certificates* — moves the problem into the operator's infrastructure and cannot be tested
offline. *No signature, HTTPS only* — leaves a receiver unable to distinguish docdoc from anything
that learned the URL.

---

## R8. Keeping a webhook from becoming a request forgery

**Decision**: resolve, validate, then **pin**. At registration and again before every attempt:
`socket.getaddrinfo` the host, refuse if **any** resolved address is loopback, link-local,
private, multicast, reserved, or unspecified, then open the connection **to the validated address**
with the original hostname in `Host` and in TLS SNI. Redirects are not followed (FR-061). Non-HTTPS
destinations are refused unless explicitly allowed.

**Rationale**: validating the hostname and then calling a client that resolves it again is a
time-of-check-to-time-of-use bug with a name — DNS rebinding — and it is the whole reason FR-060
requires validation "again before each attempt". Checking *every* address rather than the first is
what closes a host that resolves to one public and one private address. Refusing redirects removes
the other half of the same attack, where a public URL answers `302` to `169.254.169.254`.

**Alternatives considered**: *An allowlist of destination hosts only* — safest and unusable for a
product whose customers each have their own endpoint; it remains available as the explicit-allowance
configuration FR-060 mentions. *Validating at registration only* — fails to rebinding, which is the
attack this control exists for.

---

## R9. What a limit counts, and where the count lives

**Decision**: three mechanisms, deliberately different because the questions are different.

| Limit | Mechanism |
|---|---|
| Submissions per interval (FR-039) | Fixed-window counter row, incremented by one `INSERT … ON CONFLICT DO UPDATE` |
| Concurrent non-terminal runs (FR-040) | `COUNT(*)` over `runs` where status is `queued`/`running`, on a new partial index by tenant |
| Runs per calendar period (FR-041) | The same counter table, a coarser window |
| Token budget (FR-042) | The same counter table, incremented by the worker at run completion from `PipelineResult` usage |

**Rationale**: a fixed window is one statement, holds one row per tenant per window, and is swept by
the same maintenance tick that sweeps runs. A sliding window would need either a row per submission —
turning the limiter into the largest table in the database — or a background decay process, which is
the fifth process FR-116 forbids. The cost is the fixed-window burst: a tenant may issue up to twice
its limit across a window boundary. That is stated in the operator documentation rather than
engineered away, because the bound that matters (worker pool size) is unaffected by it.

Concurrency is counted rather than stored because the runs table already knows: a counter would be a
second copy of a fact that can drift, and drift here means either refusing valid work forever or
never refusing anything.

**Token accounting is the one that needed a decision.** Usage is known only after a provider answers,
and Milestone 9's FR-092 forbids `run.transition` from carrying token counts — so the number exists
in `pipeline.stage` events and nowhere durable. The worker therefore records a run's total into the
counter row when it records the run's terminal state, in the same transaction. **This is counting for
enforcement and not metering for billing**, which is the distinction the constitutional amendment in
R14 has to carry.

**Alternatives considered**: *Redis with `INCR` and TTL* — the natural tool, not in the sanctioned
stack, and it would need an amendment to add a dependency for something one SQL statement does.
*In-process counters* — FR-051 forbids them by name, because two API replicas would each grant the
full limit.

---

## R10. Priority, starvation, and per-tenant fairness in one claim statement

**Decision**: one `ORDER BY` inside the existing `FOR UPDATE SKIP LOCKED` claim,
over three derived terms:

```sql
WITH served AS (
  SELECT tenant_id, count(*) AS recent
    FROM runs
   WHERE updated_at > %(now)s - %(window)s::interval AND status <> 'queued'
   GROUP BY tenant_id
), eligible AS (
  SELECT runs.run_id, runs.priority, runs.created_at,
         COALESCE(served.recent, 0) AS recent,
         (runs.created_at < %(now)s - %(starvation)s::interval) AS starved
    FROM runs LEFT JOIN served ON served.tenant_id = runs.tenant_id
   WHERE (runs.status = 'queued' OR (runs.status = 'running' AND runs.lease_until < %(now)s))
     AND runs.attempts < %(max_attempts)s
)
… ORDER BY starved DESC, recent, priority DESC, created_at
  FOR UPDATE OF runs SKIP LOCKED LIMIT 1
```

**Corrected 2026-09-04, by a test written to check the bound this section
claims.** The original decision was `row_number() OVER (PARTITION BY tenant_id
ORDER BY priority DESC, created_at)` — each tenant's queue ranked on its own — and
the argument was that a tenant's oldest run then competes with other tenants'
oldest rather than with their thousandth.

**That is wrong, and it fails in the obvious case.** The rank is computed over the
runs that are still *waiting*. Claim the backlog's rank-0 run and the next one
becomes rank 0, and it is still older than the newcomer's — so the newcomer waits
for the entire backlog. `tests/unit/test_claim_order.py::test_a_thousand_runs_do_not_delay_one`
measured 201 claims against a stated bound of 2.

What produces alternation is a **deficit**, not a position: prefer the tenant that
has been served *least* in a recent window. `recent` counts that tenant's
non-queued rows updated inside `DEFAULT_FAIRNESS_WINDOW` (10 minutes). A tenant
that just had a run yields to one that has had none, and the bound becomes the
number of tenants with work.

**Derived from the table rather than from a counter beside it**, which is the same
choice R1 makes about survivorship and for the same reason: nothing can drift, and
a crash reconciles nothing.

`starved DESC` first is FR-089: once a run passes the bound it outranks
everything, so a misconfigured priority ceiling is a latency problem rather than a
liveness one. With one tenant `recent` is constant across candidates and drops
out; with no priorities and nothing starved the order collapses to `created_at`,
which is Milestone 9's FR-024 exactly — the default is the old behaviour rather
than a special case of the new one (FR-091).

**The cost** is a `GROUP BY` over recently-updated rows on every claim, which the
partial index on non-terminal rows does not serve. The window bounds it: ten
minutes of a deployment's finished runs, not the table. Measured rather than
assumed — see the plan's performance goals.

**Alternatives considered**: *Per-tenant position ranking* — above; it is the
version that looks right. *Weighted round-robin held in the worker* —
worker-local, so N workers make N independent decisions and the bound stops
holding. *A per-tenant credit column reconciled after every crash* — mutable state
beside a table that can already answer the question.

## R11. How a revocation reaches a process that is already running

**Decision**: a credential table, and a **bounded cache** in every authenticating process. The cache
holds the digest→principal mapping with a lifetime configured as `DOCDOC_CREDENTIAL_TTL` (default 30
seconds), and FR-028's documented propagation bound **is** that lifetime. A revocation is a row
update; every process picks it up within one TTL, with no restart, no file, and no signal.

**Rationale**: the alternatives are a query per request or a notification channel. A query per request
makes every route depend on database availability — including, today, routes that need no database at
all, which would be a much larger regression than the one FR-113 already records. A `LISTEN/NOTIFY`
channel gives near-instant propagation and needs a dedicated connection per process held open forever,
a reconnect path, and a fallback for when it has been down — which is a TTL, arrived at the long way.
Thirty seconds is short enough that "revoke and it stops working" is true in the sense an operator
means it, and the number is configurable for a deployment that wants five.

**A negative cache is not kept.** An unknown digest is not cached as unknown, so issuing a credential
takes effect immediately (FR-025's "authenticates immediately") while revoking one takes up to the
TTL. The asymmetry is deliberate and is the safe direction only for issuance; for revocation the
bound is what is documented.

**Alternatives considered**: *`LISTEN/NOTIFY`* — above. *Signalling processes on revocation* — needs
process discovery, which is an orchestrator's job and not docdoc's. *Short-lived tokens with
refresh* — a session mechanism, which the spec puts out of scope by name.

---

## R12. Where the administrative scope lives

**Decision**: `Principal` gains `scopes: frozenset[str]`, defaulting to empty. One scope exists:
`admin`. The file-backed `KeyRing` produces principals with no scopes, so a Milestone 9 deployment
gains no administrative access by upgrading.

**Rationale**: `Principal` is a frozen dataclass carrying `tenant_id` alone
(`src/docdoc/api/auth.py`), and its docstring is explicit that it carries no name and no key
deliberately. A scope set is neither of those — it is a capability, it varies per credential rather
than per request, and it is what FR-031 needs to exist. Making it a set rather than a boolean costs
nothing now and avoids a migration when a second scope appears; making it a *role* would be an
authorisation system, which nothing here asks for.

**The first administrative credential is created by `docdoc credential issue --admin`**, acting on
the database directly (FR-032). A route that could mint the first admin key is a route that needs no
credential, which is the hole the whole feature exists to close.

**Alternatives considered**: *A separate admin key file* — reintroduces the restart-to-rotate problem
for exactly the credential whose compromise is worst. *Tenant `admin` as a magic tenant id* — conflates
identity with capability, and `TENANT_PATTERN` would happily accept a customer named `admin`.

---

## R13. Where correction storage lives, given the layer contracts

**Decision**: `docdoc.runs.corrections` — the storage — importing `Correction` from
`docdoc.evaluation.corrections`, which is the model. The evaluation layer is not touched.

**Rationale**: this was decided by a contract rather than by preference. `pyproject.toml`'s
`"evaluation reaches no network and no provider"` contract forbids `docdoc.evaluation` from importing
`docdoc.artifacts`, `socket`, `urllib`, `http`, `sqlalchemy`, and more — a database-backed store
cannot live there, and weakening that contract to host one would trade a machine-checked property for
a convenience. The layers chain permits the other direction: `Runs` sits above `Evaluation`, so
`runs → evaluation` is downward and legal, and it is the same shape as `runs → pipeline` today.

**Alternatives considered**: *A third sibling layer for corrections* — a layer holding one table and
one model import, which Principle XI's "every abstraction MUST have a concrete, present-tense reason"
rejects. *Storing corrections as artifacts* — they are mutable in the sense that matters (a second
reviewer disagrees, a correction is withdrawn), and ADR-0010 §5 makes overwriting an artifact an
error rather than an update.

---

## R14. What the constitution has to say before this can be built

**Decision**: one amendment, **v1.8.0**, carrying two changes: the counting/billing distinction, and
the error-model additions.

**Rationale**: v1.6.0's sentence is "metering, invoicing, and per-tenant pricing remain deferred", and
R9 records a per-tenant token counter. Calling that "not metering" is a reinterpretation, and
Milestone 9 was told — by `/speckit-analyze`, as CRITICAL — that a spec reinterpreting a
constitutional sentence to comply with it inverts Governance's precedence. So the sentence is amended
to distinguish **counting in order to refuse** from **metering in order to bill**, and to say that the
second remains deferred.

The error model is the second half because it is the same class of change: the constitution
enumerates the typed errors, and this milestone adds `RetentionError`, `CredentialError`,
`LimitExceededError`, and `DeliveryError`. Adding names to an enumerated list in a governing document
is a MINOR amendment, and doing it in the same change as the first avoids two amendments in one
milestone.

**Alternatives considered**: *Argue that enforcement counters are not metering* — the argument is
correct and the instrument is wrong, which is exactly the finding of the 1.6.0 amendment.
*Ship without the token budget* — removes one of the seven items the milestone was asked to carry,
to avoid a paragraph.

---

## R15. Where the maintenance tick runs, and what bounds it

**Decision**: `runs/maintenance.py`, called from the worker loop between claims. One tick performs, in
order: due deliveries up to `DOCDOC_MAINTENANCE_DELIVERY_BATCH` (default 32), then one sweep batch of
up to `DOCDOC_SWEEP_BATCH` (default 500 runs). The whole tick is bounded by
`DOCDOC_MAINTENANCE_BUDGET_MS` (default 5000) checked between items, and it is skipped entirely if
less than `DOCDOC_MAINTENANCE_INTERVAL` (default 60s) has passed since the last one.

**Rationale**: the bound is what satisfies FR-062 and FR-114, and it has to be checked *between*
items rather than enforced over one, because a single delivery to a slow receiver already has its own
timeout and cutting it mid-flight would produce a delivery nobody can classify. Checking between
items means the worst case is one tick budget plus one delivery timeout, which is the number the
operator documentation states.

**The five deferred numbers from the clarify session, decided here** because each was deferred for
being a mechanism choice:

| Setting | Default | Why this number |
|---|---|---|
| Credential TTL (FR-028) | 30 s | Short enough that "revoked" is true in the sense an operator means; long enough that a busy process is not querying per request |
| Maintenance budget (FR-114) | 5000 ms | An order of magnitude below the shortest sensible lease, so a tick cannot threaten a heartbeat |
| Sweep batch (FR-015) | 500 runs | Deletes a year of a small deployment's rows in minutes of ticks, and holds a transaction for a bounded time |
| Starvation bound (FR-089) | 1 h | Long enough that priority means something under load; short enough that "eventually" is a business-day promise |
| Fairness bound (FR-090) | *derived, not configured* | R10's `rank` makes it the number of tenants with queued work; a setting would imply a choice the design does not offer |

**Alternatives considered**: *A dedicated thread in the worker* — Milestone 9's FR-025 forbids
threads in the worker, and the reason given (a GIL-holding stage delaying a sibling's heartbeat)
applies to maintenance identically. *Running maintenance in the API process* — the API is stateless
and horizontally scaled, so N replicas would each sweep.

---

## R16. Whether a tombstone is a row, a table, or a status

**Decision**: a `run_tombstones` table. Four columns and nothing else: `run_id`, `tenant_id`,
`deleted_at`, `policy`.

**Rationale**: the clarify session settled that it is not a status (`RunStatus` stays closed at five,
FR-004a). What remains is whether the deleted run's row is emptied in place or replaced. Emptying in
place means every column of `runs` becomes nullable, and the two `CHECK` constraints that Milestone 9
added deliberately — `processing_id_belongs_to_success` and `terminal_runs_hold_no_lease` — either
have to be relaxed or gain a special case for a row that is no longer a run. A separate table keeps
those constraints exactly as they are, keeps the `runs` table meaning "runs", and makes the tombstone
cheap: four columns for something a deployment may accumulate a great many of.

It also makes FR-011's "the two responses MUST differ in kind" easy to implement honestly: the run
lookup misses, the tombstone lookup hits, and the route has two distinct outcomes rather than one
model with a mode flag.

**Alternatives considered**: *A `deleted_at` column on `runs`* — above; the constraints are the
argument. *No tombstone, just delete* — FR-011 requires the owning tenant to be told, and this is what
tells them.

---

## Open items carried into Phase 1

None. Every question the spec deferred to planning is answered above, and the five numbers the
clarify session deferred are set in R15 with the reasoning that picked them.
