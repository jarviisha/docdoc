# Phase 1 Data Model: Retention, Credentials, Limits, Delivery, and the Human Loop

**Feature**: `010-operations-and-corrections` | **Date**: 2026-09-04

Five new tables, three columns and two indexes added to `runs`, one field added to `Principal`, and
one new pure model. Everything mutable lives in the run-state database (FR-112); nothing here is an
artifact and no artifact is mutated to carry any of it, which is ADR-0013 §2's rule held to.

The `runs` table's own shape is otherwise unchanged: **`RunStatus` gains no member** (FR-004a), the
two `CHECK` constraints Milestone 9 added are untouched, and no existing column changes type or
nullability.

---

## Changes to `runs`

```sql
ALTER TABLE runs ADD COLUMN IF NOT EXISTS priority    smallint NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS tokens_used integer;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS callback_id uuid;
```

- **`priority`** — the closed set as a small integer, `0` ordinary and `10` urgent, defaulting to
  ordinary so every existing row is exactly what it was (FR-087). A `smallint` rather than an enum
  because the claim query orders by it and because a third class, if one is ever justified, is a
  number rather than a migration of a type.
- **`tokens_used`** — the run's total, recorded by the worker in the same statement that records the
  terminal state (R9). Nullable: a run that failed before reaching a provider used none, and `0` and
  "never got there" are different facts.
- **`callback_id`** — the destination registered at submission, or null. A soft reference; there is
  no foreign key here for the same reason Milestone 9's migration gives for having none.

```sql
CREATE INDEX IF NOT EXISTS runs_expiring
    ON runs (expires_at)
    WHERE status IN ('succeeded', 'failed', 'cancelled');

CREATE INDEX IF NOT EXISTS runs_tenant_active
    ON runs (tenant_id)
    WHERE status IN ('queued', 'running');
```

`runs_expiring` is the index Milestone 9's migration named in a comment and deliberately did not
create — *"No index on expires_at. Nothing in this milestone queries it … Milestone 10 adds one
alongside the sweep that needs it."* It is partial on the **terminal** states, the mirror image of
`runs_claimable`, because the sweep never considers a run that is not terminal (FR-003).

`runs_tenant_active` serves the concurrency limit's `COUNT(*)` (R9). Also partial, and for the same
reason: terminal rows dominate the table and the only query that reads this never wants them.

---

## `credentials`

```sql
CREATE TABLE IF NOT EXISTS credentials (
    credential_id uuid        PRIMARY KEY,
    tenant_id     text        NOT NULL,
    digest        text        NOT NULL UNIQUE,
    scopes        text[]      NOT NULL DEFAULT '{}',
    label         text,
    created_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS credentials_by_tenant ON credentials (tenant_id, created_at);
```

- **`digest`** is `sha256(key)`, the same derivation `api/auth.py:digest_of` already uses, so the file
  ring and the table agree on what a stored credential is. The plaintext is returned once at issuance
  and never stored (FR-025, FR-026).
- **`credential_id` is never reused and a revoked row is never deleted** (FR-036). Revocation sets
  `revoked_at`; it does not remove the row, because "this key was revoked on the 4th" is the answer an
  incident review needs and a missing row cannot give it.
- **`scopes`** is a text array holding at most `admin` today (R12). An array rather than a boolean
  because a second scope should not be a migration, and not a role table because nothing here asks
  for one.
- **`last_used_at`** is written best-effort and is explicitly **not** transactional with the request
  it describes. It exists for FR-030's listing; making it exact would put a write on every
  authenticated request.

**Uniqueness is on `digest`, deployment-wide, not per tenant.** Two tenants cannot hold the same key,
which is what makes a digest lookup resolve to exactly one principal without a tenant hint the caller
has not yet been authenticated to give.

---

## `run_tombstones`

```sql
CREATE TABLE IF NOT EXISTS run_tombstones (
    run_id     uuid        PRIMARY KEY,
    tenant_id  text        NOT NULL,
    deleted_at timestamptz NOT NULL,
    policy     text        NOT NULL
);

CREATE INDEX IF NOT EXISTS tombstones_by_tenant ON run_tombstones (tenant_id, deleted_at);
```

Four columns, and the shortness is the specification (FR-004). No status, no blob id, no schema
identity, no stage outcomes — a tombstone that carried what the run held would be a way of retaining
what was deleted.

**`policy`** is the name of the rule that removed it — `retention` for the sweep, `erasure:tenant` or
`erasure:document` for an operator's request. It is the field that distinguishes ageing out from being
erased, which is why no second status exists to carry that distinction (FR-004).

A separate table rather than a flag on `runs` (R16): `runs` keeps its two `CHECK` constraints
unweakened, and the run lookup and the tombstone lookup are two distinct outcomes rather than one
model with a mode.

---

## `corrections`

```sql
CREATE TABLE IF NOT EXISTS corrections (
    correction_id uuid        PRIMARY KEY,
    tenant_id     text        NOT NULL,
    run_id        uuid        NOT NULL,
    processing_id text        NOT NULL,
    field_path    text        NOT NULL,
    payload       jsonb       NOT NULL,
    created_at    timestamptz NOT NULL,
    expires_at    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS corrections_by_run    ON corrections (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS corrections_expiring  ON corrections (expires_at);
```

- **`payload`** is the serialised `Correction` from `docdoc.evaluation.corrections`, stored whole and
  not decomposed. The model is Milestone 6's and this milestone does not redefine it (FR-077); the
  three columns lifted out of it — `run_id`, `processing_id`, `field_path` — are lifted because they
  are queried, not because they are more important.
- **`expires_at`** is the correction's own retention deadline, configured independently of a run's
  (FR-013). It is why the sweep's run query joins here: a run whose result carries a live correction
  is not removed, and where the two deadlines disagree the longer wins.
- **Corrections are scoped by `tenant_id` on every read and write** (FR-079). The `run_id` alone is
  not a permission.

---

## `deliveries`

```sql
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id  uuid        PRIMARY KEY,
    tenant_id    text        NOT NULL,
    run_id       uuid        NOT NULL,
    callback_id  uuid        NOT NULL,
    state        text        NOT NULL
                 CHECK (state IN ('pending', 'delivered', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL,
    last_status  integer,
    last_error   text,
    created_at   timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL,

    CONSTRAINT one_delivery_per_run UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS deliveries_due
    ON deliveries (next_attempt_at)
    WHERE state = 'pending';
```

- **`delivery_id` is the identifier a receiver deduplicates on** (FR-056). It is stable across
  retries because it is the primary key, allocated once when the run reaches a terminal state.
- **`one_delivery_per_run`** is FR-058's ordering guarantee expressed as a constraint rather than as
  worker discipline. One run produces one delivery; retries are attempts on that row, so two
  deliveries for one run cannot overlap because there are never two.
- **`last_error`** holds a **class name**, never a receiver's response body — the same rule
  `error_class` follows on `runs` (Milestone 9 FR-035). A receiver's body is somebody else's content.
- **`state`** is three values and is a delivery's state, not a run's. A run's terminal state is
  already recorded and a failed delivery does not change it (FR-065).

## `callbacks`

```sql
CREATE TABLE IF NOT EXISTS callbacks (
    callback_id   uuid        PRIMARY KEY,
    tenant_id     text        NOT NULL,
    url           text        NOT NULL,
    secret_digest text        NOT NULL,
    created_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);
```

The destination and its signing secret, registered once and referenced by submissions.
**`secret_digest`** rather than the secret: the signature is computed from a secret the operator
supplies through configuration, and what the table holds is enough to tell two registrations apart
and not enough to sign with (FR-066). A route never returns it.

---

## `limit_counters`

```sql
CREATE TABLE IF NOT EXISTS limit_counters (
    tenant_id    text        NOT NULL,
    kind         text        NOT NULL,
    window_start timestamptz NOT NULL,
    value        bigint      NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, kind, window_start)
);
```

One row per tenant per limit kind per window, incremented by a single
`INSERT … ON CONFLICT (tenant_id, kind, window_start) DO UPDATE SET value = limit_counters.value + …`
(R9). `kind` is `submissions`, `runs_per_period`, or `tokens`; concurrency is not here because it is
counted from `runs` rather than stored.

**Old windows are swept by the same maintenance tick that sweeps runs.** A counter table that only
grows is the failure this milestone exists to prevent, and it would be an embarrassing one.

**The fixed window's cost, stated**: a tenant may issue up to twice its limit across a boundary. That
is documented rather than engineered away, because the bound that actually protects the deployment is
the worker pool size and it is unaffected.

---

## `Principal` gains a scope set

```python
@dataclass(frozen=True)
class Principal:
    tenant_id: str
    scopes: frozenset[str] = frozenset()
```

One scope exists, `admin` (R12, FR-031). `KeyRing`, the file-backed ring Milestone 9 shipped,
constructs principals with the default — so a deployment that upgrades and changes nothing gains no
administrative access (FR-038).

The dataclass stays frozen and still carries no name and no credential, which is the property its
docstring protects: a principal that held the key it resolved from would put one within reach of
every log line that ever holds a request context.

---

## `RoutingDecision` — the one new pure model

```python
class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RoutingOutcome          # automatic | review — exactly two (FR-067)
    policy_version: str              # recorded on every decision (FR-070)
    reasons: tuple[RoutingReason, ...]  # which signals fired, in policy order
```

Frozen, `extra="forbid"`, and computed from a completed result — grounding status and score,
validation severity and verdict, schema requiredness — and from nothing else (FR-068). It reads no
`model_confidence`, which Principle II forbids and which a test asserts by varying that field alone
and requiring the decision not to move (SC-017).

**`reasons`** is what makes a decision auditable without re-deriving it (FR-072). Each names the
field path, the signal, and the threshold it crossed. It carries no value and no claimed text — the
no-content rule reaches here exactly as it reaches run records.

**It is stored on the run row's projection, not as an artifact.** A routing decision is not an output
of a stage, has no place in the ADR-0003 chain, and must not enter one: an artifact carrying it would
make the terminal artifact id a function of a policy, and `processing_id` would move when a threshold
was edited.

---

## The store protocols gain deletion

```python
class ArtifactStore(Protocol):
    def get(...) -> ...
    def put(...) -> ...
    def delete(self, artifact_id: str, *, tenant_id: str) -> bool: ...
    def delete_prefix(self, *, tenant_id: str) -> int: ...
```

`BlobStore` gains the same pair. Four implementations follow — `FileArtifactStore`,
`NullArtifactStore` (both no-ops returning `False` and `0`), `S3ArtifactStore`, and the blob stores
(R3).

**`delete` returns whether something was there**, so an idempotent sweep can count what it actually
removed (FR-012) without a second existence check that would race.

**`delete_prefix` refuses the default tenant.** Its prefix is the store root (ADR-0014 §3), so the
operation would remove everything written before authentication was enabled and everything the
command line ever wrote. (It leaves `<root>/t/` alone — artifacts live at `<base>/artifacts/`, so
other tenants survive by construction; corrected 2026-09-04 during implementation, see ADR-0015 §5.) It
raises unless an explicit destructive flag is passed, and tenant erasure for the default tenant uses
the set-difference path instead (R2). This is the guard ADR-0014's Consequences section asked for by
name.

---

## What the sweep computes

Not a state transition — a set difference (R1):

```text
for each batch of terminal runs whose expires_at has passed,
        and which no live correction pins (FR-013),
        and which no worker holds a lease on (FR-003):

    candidates = ⋃ stage_outcomes[].artifact_id over the batch
    survivors  = ⋃ stage_outcomes[].artifact_id over that tenant's remaining runs
    delete artifacts (candidates − survivors)
    delete blobs referenced only by the batch
    insert one tombstone per run, delete the run rows
```

**The difference is the safety property.** A complement — "delete every artifact no surviving run
references" — deletes everything `docdoc extract` ever wrote from the command line, everything a
library caller produced, and every artifact the recorder wrote, because none of those has a run row
(R1). An artifact becomes a candidate only by having been named by a run that is being removed.

**Order matters and is not negotiable**: artifacts and blobs first, tombstone second, run row last.
A crash between any two steps leaves the run still present and its artifacts partly gone, which the
next sweep completes because the difference is recomputed from what remains (FR-002). The reverse
order would lose the list of what to delete.
