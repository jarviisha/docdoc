-- Retention: the tombstone, and the index Milestone 9 promised.
--
-- `0001_runs.sql` ends with a comment that names this file without knowing it:
-- "No index on expires_at. Nothing in this milestone queries it, and an index
-- with no reader is write amplification on every insert. Milestone 10 adds one
-- alongside the sweep that needs it." This is that sweep.

-- What remains of a removed run. Four columns, and the shortness is the
-- specification (FR-004): no status, no blob id, no schema identity, no stage
-- outcomes. A tombstone that carried what the run held would be a way of
-- retaining what was deleted.
--
-- A separate table rather than a flag on `runs` (ADR-0015 §4). Emptying the run
-- row in place would make every column nullable and force both of Milestone 9's
-- CHECK constraints -- `processing_id_belongs_to_success` and
-- `terminal_runs_hold_no_lease` -- to grow a special case for a row that is no
-- longer a run. Here they are untouched.
--
-- It is also what makes FR-011's "the two responses differ in kind" honest: the
-- run lookup misses and the tombstone lookup hits, so the route has two distinct
-- outcomes rather than one model with a mode flag.
CREATE TABLE IF NOT EXISTS run_tombstones (
    run_id     uuid        PRIMARY KEY,

    tenant_id  text        NOT NULL,

    deleted_at timestamptz NOT NULL,

    -- Which rule removed it: 'retention' for the sweep, 'erasure:tenant' or
    -- 'erasure:document' for an operator's request.
    --
    -- This column is why `RunStatus` gains no member (FR-004a). The distinction
    -- between ageing out and being erased has to be recorded somewhere, and once
    -- it is recorded here a second status carrying it would be a second source
    -- of truth about the same fact.
    policy     text        NOT NULL
);

-- Tenant-scoped listing, and the lookup that answers 410 to an owner.
CREATE INDEX IF NOT EXISTS tombstones_by_tenant
    ON run_tombstones (tenant_id, deleted_at);

-- The sweep's candidate scan.
--
-- Partial on the THREE TERMINAL STATES, which is the mirror image of
-- `runs_claimable` and deliberate: FR-003 forbids sweeping a run that is queued,
-- running, or under an unexpired lease, so those rows are not merely unwanted --
-- they can never be candidates, and indexing them would grow an index the only
-- query using it never reads.
CREATE INDEX IF NOT EXISTS runs_expiring
    ON runs (expires_at)
    WHERE status IN ('succeeded', 'failed', 'cancelled');
