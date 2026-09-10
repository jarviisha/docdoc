# Contract: The Python Surface — Deletion, Keys, Limits, Delivery, Routing, Telemetry

**Feature**: `010-operations-and-corrections` | **Date**: 2026-09-04

The in-process contracts this milestone adds, and the rules each one inherits rather than restates.
Six protocols, one bridge factory, one maintenance entry point. Every one of them is reachable only
from `docdoc.runs` or above, except the store deletion methods, which extend a protocol
`docdoc.artifacts` already owns.

## Layer position

```text
API, CLI → Recording, Runs → Evaluation → Pipeline → Validation → Grounding
         → Extraction → Ingest → Artifacts → Kernel
```

**Corrected 2026-09-04, during implementation.** This section previously read "No layer is added, so
Principle X's 'amend this text in the same change that adds a layer' does not fire." It does fire.
`docdoc.telemetry` is a package on disk, and `tests/unit/test_layer_boundaries.py` requires every
package on disk to be named in the contract — because one that is not is **unconstrained**, free to
import anything in any direction with CI green. The chain is therefore:

```text
API, CLI → Recording, Runs → Evaluation, Telemetry → Pipeline → Validation
         → Grounding → Extraction → Ingest → Artifacts → Kernel
```

`Telemetry` shares `Evaluation`'s position: below `Runs`, because `api` and `runs.worker` install the
bridge; above `Pipeline`, because nothing at or below the pipeline may reach an exporter. Constitution
**v1.8.0** carries the amendment, as Principle X's own rule requires. Making it a module rather than a
package would have passed the test — it walks directories — and that is precisely the shape of fix
FR-096a warns about.

What changes inside the chain:

- `docdoc.runs` gains `maintenance`, `keys`, `limits`, `delivery`, `routing`, `corrections`,
  `retention` — all siblings of the existing `queue`, `worker`, `health`.
- **`docdoc.runs` imports `docdoc.evaluation`** for the first time, to reach the `Correction` model
  (R13). Downward and legal. It is worth naming because it is a new edge, and because the reverse was
  impossible: `evaluation`'s `forbidden` contract bars `socket`, `urllib`, `http`, and
  `docdoc.artifacts`, so a database-backed correction store cannot live there. **The layers
  contract's own comment must be corrected in the same change** — it currently reads "the total order
  then forces `runs > evaluation`, which `runs` does not import", and after this milestone it does.
- **New `forbidden` contract**: outbound HTTP is confined. `http.client`, `socket`, and the OTLP
  exporter are reachable from `docdoc.runs.delivery` and `docdoc.telemetry` and from nowhere else in
  the deterministic layers (FR-099). A prohibition with no automated guard is a comment.
- **Milestone 9's contracts are untouched**: the broker prohibition, the `api ↛ runs.worker`
  forbidden contract, and the `recording : runs` independence contract are unmodified (FR-098).

## Store deletion

```python
class ArtifactStore(Protocol):
    def delete(self, artifact_id: str) -> bool: ...
    def delete_prefix(self, *, allow_store_root: bool = False) -> int: ...
```

**No `tenant_id` parameter, corrected 2026-09-04 against the code.** A store
instance **is** a tenant: `tenant_id` is a constructor argument and every path the
object derives is already inside its own namespace (ADR-0014 §3). A per-call
tenant would be a second source of truth about which namespace is being touched,
in the one operation where being wrong is unrecoverable.

`BlobStore` gains the same pair over `blob_id`. Implemented by `FileArtifactStore`, `S3ArtifactStore`,
the blob stores, and `NullArtifactStore` — which returns `False` and `0`, because a store that holds
nothing deleted nothing, and raising would make the null store the only one a sweep must branch for.

**Rules inherited, not restated:**

- **An unavailable store does not fail the operation.** ADR-0010 §4's rule — a store that cannot be
  reached runs without reuse and logs once — becomes: a sweep that cannot reach the store deletes no
  content, deletes no run row either, and reports the degradation once. Deleting the row while the
  content survives would lose the only record of what to delete.
- **`delete` returns whether something was there**, so `FR-012`'s counts are what was removed rather
  than what was attempted, without a second existence check that would race.
- **`delete_prefix` raises `RetentionError` for the default tenant** unless
  `allow_store_root=True` is passed. Its prefix is the store root (ADR-0014 §3); the flag exists at
  exactly one call site, on the command line, and at no HTTP route (R2).

## `docdoc.runs.retention`

```python
def sweep(queue: RunQueue, stores_for: Callable[[str], Stores], *, now: datetime,
          batch: int, corrections: CorrectionStore | None = None,
          policy: str = POLICY_RETENTION) -> SweepReport: ...

def erase(queue: RunQueue, stores: Stores, *, tenant_id: str, now: datetime,
          blob_id: str | None = None, limit: int = 10_000,
          allow_store_root: bool = False,
          is_default_tenant: bool = False) -> SweepReport: ...
```

`sweep` takes a **factory** and not a `Stores`: one batch may span tenants, and a
store instance is bound to one. `erase` finds its own runs rather than being handed
a list — an erasure is "this customer's data", and a caller passing run ids could
pass a stale set.

**A set difference, never a complement** (R1, and it is the safety property of this milestone):

```text
candidates = ⋃ stage_outcomes[].artifact_id over the runs being removed
survivors  = ⋃ stage_outcomes[].artifact_id over that tenant's remaining runs
delete       candidates − survivors
```

A complement deletes every artifact `docdoc extract` ever wrote from the command line, everything a
library caller produced, and every artifact the recorder wrote — none of which has a run row. An
artifact becomes a candidate only by having been named by a run being removed.

**Order is fixed and a crash between any two steps is safe**: artifacts and blobs, then the tombstone,
then the run row. The next sweep recomputes the difference from what remains, which is what makes
FR-002's idempotence true rather than asserted.

**Never sweeps** a non-terminal run, a run under an unexpired lease (FR-003), or a run whose result
carries a live correction (FR-013).

`SweepReport` carries counts by kind and nothing else — no identifiers of what was deleted, which
would be a way of retaining it.

## `docdoc.runs.keys`

```python
class KeyStore(Protocol):
    def resolve(self, digest: str) -> Principal | None: ...
    def issue(self, *, tenant_id: str, scopes: frozenset[str],
              label: str | None, now: datetime) -> tuple[str, Credential]: ...
    def revoke(self, credential_id: UUID, *, now: datetime) -> bool: ...
    def list_for(self, tenant_id: str) -> tuple[Credential, ...]: ...
```

`issue` returns the plaintext **once**, as the first element, and it is the only place in the codebase
where a key exists outside a caller's memory (FR-025). `Credential` carries no key and no digest.

**Resolution is cached with a bounded lifetime** (R11). `CachedKeyStore` wraps any `KeyStore`:

- A **hit** is cached for `DOCDOC_CREDENTIAL_TTL` (default 30 s). This is FR-028's propagation bound
  and it is what the documentation states as a number.
- A **miss is not cached**, so issuance takes effect immediately (FR-025) while revocation takes up
  to the TTL. The asymmetry is safe in exactly one direction and the direction is stated.
- The cache holds digests and principals. It never holds a key.

**`KeyRing` — the Milestone 9 file ring — implements the same `resolve`** and raises
`CredentialError` for the three mutating methods (FR-038). A deployment configured as Milestone 9
configured it authenticates exactly as before and gains no administrative surface, which SC-002
measures.

**Precedence when both are configured**: the file ring is consulted first and the key store second.
A deployment mid-migration therefore keeps its file keys working while table-issued keys start to
work, and no key silently stops working because a table appeared.

## `docdoc.runs.limits`

```python
class Limiter(Protocol):
    def check(self, *, tenant_id: str, now: datetime) -> LimitVerdict: ...
    def record_submission(self, *, tenant_id: str, now: datetime) -> None: ...
    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None: ...
```

`now` and not `at`, matching every other verb in this package; and a `cost`
parameter was dropped because nothing has a use for one — a submission is one
submission. `record_submission` is separate from `check` so that a refusal costs
one read and a **replayed idempotency key costs nothing at all**.

`LimitVerdict` is `allowed` or a refusal naming the limit, the observed value, the allowed value, and
a retry hint — the four fields the `429` body carries (FR-043).

- **Checked at submission and nowhere else** (FR-045). No limit aborts, cancels, or discards a run
  that is already executing, and there is no interposition point that could.
- **Fixed windows, one row per tenant per kind per window** (R9), incremented by a single
  `INSERT … ON CONFLICT DO UPDATE`. The burst across a window boundary is up to twice the limit and is
  documented rather than engineered away.
- **Concurrency is counted from `runs`**, not stored, because a counter beside a table that already
  knows is a fact that can drift.
- **`record_tokens` is called by the worker in the transaction that records the terminal state**
  (R9). It is why the token budget refuses the *next* submission and never this one (FR-047).
- **`NullLimiter` allows everything** and is the default, so a deployment configuring no limits counts
  nothing (FR-049).

**Counting here is for enforcement.** Nothing in this module prices, invoices, or emits a billing
record, and the constitutional amendment this milestone requires says so (FR-048, FR-102).

## `docdoc.runs.delivery`

```python
class Deliverer(Protocol):
    def enqueue(self, run: Run, callback: Callback, *, at: datetime) -> Delivery: ...
    def due(self, *, at: datetime, limit: int) -> tuple[Delivery, ...]: ...
    def attempt(self, delivery: Delivery, *, at: datetime) -> Delivery: ...
```

**The payload** carries `run_id`, terminal `status`, `failed_stage` and `error_class` where the run
failed, `processing_id` where one exists, `routing.outcome` where a policy is configured, and
`delivery_id`. It carries no document text, no extracted value, no prompt body, no credential, and no
provider message (FR-054) — the same rule every observer in this project follows.

**Signing**: `X-Docdoc-Signature: t=<unix>,v1=<hex>` over `f"{t}.{body}"`, HMAC-SHA256, stdlib only
(R7). The timestamp is inside the signed material so a captured delivery is not valid forever.

**Destination policy, applied at registration and again before every attempt** (R8, FR-060):

1. `getaddrinfo` the host.
2. Refuse if **any** resolved address is loopback, link-local, private, multicast, reserved, or
   unspecified. Every address, not the first — a host resolving to one public and one private address
   is the attack.
3. Connect **to the validated address**, presenting the original hostname in `Host` and in SNI.
4. Do not follow redirects (FR-061). A public URL answering `302 → 169.254.169.254` is the other half
   of the same attack.

Re-validating before each attempt is what closes DNS rebinding; validating once at registration does
not.

**One delivery per run** (`UNIQUE (run_id)`), so FR-058's ordering guarantee is a constraint rather
than worker discipline: retries are attempts on one row, and two deliveries for one run cannot
overlap because there are never two.

**A failing receiver changes no run state** (FR-063, FR-065). Polling remains the record; a lost
delivery loses no information.

## `docdoc.runs.routing`

```python
def decide(result: PipelineResult, policy: RoutingPolicy) -> RoutingDecision: ...
```

**Pure.** No I/O, no clock, no store. It reads grounding status and score, validation severity and
verdict, and schema requiredness, and it reads nothing else — in particular not `model_confidence`,
which Principle II forbids and which SC-017 tests by varying that field alone and requiring the
decision not to move.

`RoutingPolicy` is **data**, loaded from configuration, carrying a version string that every decision
records (FR-070). Editing a policy alters no existing decision; re-routing is an explicit act
producing a new one (FR-074).

**Two outcomes, `automatic` and `review`** (FR-067). Not three: a `reject` outcome states what a
deployment should *do*, which is the caller's, and a `retry` outcome would send the decision back into
the pipeline — which FR-073 forbids and which is the whole reason routing could be admitted to this
milestone at all.

**It computes no value and writes no artifact.** The decision is stored on the run's projection, never
in the ADR-0003 chain; an artifact carrying it would make `processing_id` a function of a threshold.

## `docdoc.runs.corrections`

```python
class CorrectionStore(Protocol):
    def record(self, correction: Correction, *, tenant_id: str,
               run_id: UUID, at: datetime, expires_at: datetime) -> UUID: ...
    def for_run(self, *, tenant_id: str, run_id: UUID) -> tuple[Correction, ...]: ...
    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]: ...
```

The model is Milestone 6's `docdoc.evaluation.corrections.Correction`, imported and not redefined
(FR-077). `record` stores it whole as `jsonb` and lifts three fields out for querying.

**`pinned_runs` is the one method the sweep calls** (FR-013): it is how retention learns that a run
carries a live correction and must not be removed. Naming it here rather than leaving retention to
join a table is deliberate — the coupling is real and a named method is where a reviewer can see it.

**Every method takes `tenant_id`** (FR-079). A `run_id` is not a permission.

**Nothing here assigns, queues, or tracks a reviewer** (FR-083). The absent methods are the contract.

## `docdoc.telemetry`

```python
def bridge(*, endpoint: str, headers: Mapping[str, str] | None = None
           ) -> Callable[[dict[str, Any]], None]: ...
```

Returns a callable that `pipeline.observe.set_observer` and the new `runs.observe.set_observer`
accept. `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` are imported **inside** the
factory, behind `docdoc[otel]` (R5), so a base install neither imports nor requires them (SC-021).

**It does not install itself.** `pipeline/observe.py` documents one observer slot as a decision — *"A
deployment that wants two can write a function that calls two"* — so an exporter that called
`set_observer` behind the operator's back would silently take a slot from whatever was in it. When an
endpoint is configured and the slot is empty, the API and worker install the bridge and log once that
they did; when the slot is occupied, they do not displace it and log that instead.

**`runs/observe.py` gains the same single slot it did not have.** Today it only calls `logging`, so
`run.transition` could not reach an exporter at all (R4). The addition mirrors the pipeline module
exactly — one slot, same signature, return value ignored, a raising observer cannot fail a run — so a
deployment learns one pattern.

**Span attributes carry identifiers, hashes, states, counts, durations, and class names only**
(FR-020). SC-015 verifies it over a document seeded with distinctive strings, which is the same
technique Milestone 9 used for run records.

**An unreachable collector fails no run and blocks no stage** (FR-022), and reports once per outage
rather than once per event — the pattern `pipeline/observe.py` already uses for a failing observer.

## `docdoc.runs.maintenance`

```python
def tick(deps: MaintenanceDeps, *, now: datetime, budget_ms: int) -> TickReport: ...
```

Called by the worker between claims (R15, FR-114). One tick performs, in order and checking the
budget between items:

1. due deliveries, up to `DOCDOC_MAINTENANCE_DELIVERY_BATCH` (default 32);
2. one retention sweep batch, up to `DOCDOC_SWEEP_BATCH` (default 500 runs);
3. expired limit-counter windows.

**Bounded between items, not within one.** A delivery has its own timeout and cutting it mid-flight
would produce an attempt nobody can classify, so the worst case is one budget plus one delivery
timeout — and that is the number the operator documentation states.

**No thread, no subprocess, no event loop** (FR-114). Milestone 9's FR-025 forbids them in the worker
and its reason — a GIL-holding stage delaying a sibling's heartbeat into losing a lease it still
holds — applies to maintenance identically.

**Skipped entirely** when fewer than `DOCDOC_MAINTENANCE_INTERVAL` seconds (default 60) have passed,
so a busy worker claiming continuously does not sweep on every iteration.

**A deployment running no worker performs no maintenance** (FR-117): nothing is swept and nothing is
delivered. Credentials still revoke on time, because that is a cache lifetime and not a scheduled
task.

## Errors

Four typed, provider-neutral additions, carried into the constitution's error model by the amendment
FR-102 requires:

| Error | Raised when |
|---|---|
| `RetentionError` | a sweep or erasure cannot proceed — unreachable store, refused default-tenant prefix |
| `CredentialError` | issuance, revocation, or listing is impossible or refused |
| `LimitExceededError` | a configured limit is reached; carries the limit, observed, allowed, retry hint |
| `DeliveryError` | a delivery cannot be attempted — destination refused by policy, unresolvable host |

Each subclasses the existing `RunError`, so every existing handler that catches it keeps working.
None carries a credential, a document, a provider message, or a receiver's response body.
