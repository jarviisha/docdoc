-- The run table: the first mutable persisted thing in this project.
--
-- Everything else docdoc stores is content-addressed and immutable, and ADR-0010
-- §5 makes refusing to overwrite an existing id a correctness guarantee. A run
-- is defined by overwriting -- queued, running, succeeded -- so it cannot live
-- there, and this is the "persistence genuinely required" the sanctioned stack
-- allows for (ADR-0013 §2).
--
-- No foreign keys. `blob_id` names content in an object store and
-- `processing_id` names an artifact; neither is a row in this database, and a
-- constraint that cannot be checked is a comment with a syntax error.

CREATE TABLE IF NOT EXISTS runs (
    run_id           uuid        PRIMARY KEY,

    -- From creation, and this is the column that could not be added later
    -- (FR-062): a backfill would have to invent an owner for every existing row.
    tenant_id        text        NOT NULL,

    blob_id          text        NOT NULL,

    -- Stored opaquely. Nothing in this schema or in the code that reads it
    -- branches on the value (Principle VI).
    schema_identity  text        NOT NULL,

    -- Five states, closed. There is deliberately no 'expired': Milestone 9 ships
    -- no sweep to set one, and a state no code path can reach lies to everyone
    -- who reads the enum. Retention is Milestone 10's.
    status           text        NOT NULL
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),

    attempts         integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    worker_id        text,
    lease_until      timestamptz,

    processing_id    text,
    failed_stage     text,
    error_class      text,

    -- A projection of PipelineResult.outcomes, narrowed to the four fields that
    -- survive the no-content rule. No value, no claimed text, no message.
    stage_outcomes   jsonb       NOT NULL DEFAULT '[]'::jsonb,

    -- Cancellation of a *running* run is a request, not a transition: the run
    -- keeps reading 'running' until the worker reaches a stage boundary, because
    -- a provider call already in flight completes and is billed (FR-029). So the
    -- request needs somewhere to live that is not `status`.
    --
    -- data-model.md described the behaviour and omitted this column; the gap
    -- surfaced the moment `is_cancelled` had to be implemented against a real
    -- table rather than an in-memory set.
    cancel_requested boolean     NOT NULL DEFAULT false,

    request_id       text,
    idempotency_key  text,

    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,

    -- Written at creation and read by nothing in this milestone (FR-015). The
    -- column exists now because adding it to a populated table later is the
    -- migration problem tenant_id has.
    expires_at       timestamptz NOT NULL,

    -- The one invariant a careless UPDATE could break. `processing_id` present
    -- exactly when the run succeeded -- which is what keeps ADR-0013 §1's two
    -- identities distinguishable. Enforced here as well as in the model, because
    -- the model is not the only thing that can write this row.
    CONSTRAINT processing_id_belongs_to_success
        CHECK ((status = 'succeeded') = (processing_id IS NOT NULL)),

    -- A terminal run holds no lease. Without this, an abandoned row with a stale
    -- lease_until stays eligible for the claim query forever.
    CONSTRAINT terminal_runs_hold_no_lease
        CHECK (status IN ('queued', 'running') OR lease_until IS NULL)
);

-- The claim query's candidate scan (research R8).
--
-- Partial, and that is the point rather than a refinement: terminal rows
-- dominate this table within a day of running, and indexing them would grow an
-- index that the only query using it never reads.
CREATE INDEX IF NOT EXISTS runs_claimable
    ON runs (created_at)
    WHERE status IN ('queued', 'running');

-- FR-011 and SC-016. Scoped to the tenant, so the same key under two tenants is
-- two runs; partial, so the common case of no key at all stays out of the index.
--
-- The database enforces this rather than a read-then-insert in application code,
-- because two API processes handling one client's retry would both read "not
-- present" and both insert (research R15).
CREATE UNIQUE INDEX IF NOT EXISTS runs_idempotency
    ON runs (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Tenant-scoped listing.
CREATE INDEX IF NOT EXISTS runs_by_tenant
    ON runs (tenant_id, created_at);

-- No index on expires_at. Nothing in this milestone queries it, and an index
-- with no reader is write amplification on every insert. Milestone 10 adds one
-- alongside the sweep that needs it.
