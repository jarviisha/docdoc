-- Per-tenant limits: counted in order to refuse, never in order to bill.
--
-- Constitution v1.8.0 draws that line explicitly, because a per-tenant token
-- counter is metering under the plainest reading of the sentence it amends. What
-- stays deferred is what these numbers may be USED for: no price, no invoice, no
-- billing record.

-- One row per tenant per limit kind per window, incremented by a single
-- INSERT ... ON CONFLICT DO UPDATE (research R9).
--
-- Fixed windows, and the cost is stated rather than engineered away: a tenant may
-- issue up to twice its limit across a boundary. A sliding window needs either a
-- row per submission -- making the limiter the largest table in this database --
-- or a background decay process, which is the fifth process FR-116 forbids.
--
-- Old windows are swept by the same maintenance tick that sweeps runs. A counter
-- table that only grows is the failure this milestone exists to prevent, and it
-- would be an embarrassing one.
CREATE TABLE IF NOT EXISTS limit_counters (
    tenant_id    text        NOT NULL,

    -- 'submissions', 'runs_per_period', or 'tokens'. Concurrency is deliberately
    -- absent: it is COUNTED FROM `runs` rather than stored, because a counter
    -- beside a table that already knows is a fact that can drift -- and drift
    -- here means either refusing valid work forever or never refusing anything.
    kind         text        NOT NULL,

    window_start timestamptz NOT NULL,

    value        bigint      NOT NULL DEFAULT 0,

    PRIMARY KEY (tenant_id, kind, window_start)
);

-- What the concurrency limit counts (FR-040). Partial for the same reason
-- `runs_claimable` is: terminal rows dominate this table within a day, and the
-- only query reading this never wants them.
CREATE INDEX IF NOT EXISTS runs_tenant_active
    ON runs (tenant_id)
    WHERE status IN ('queued', 'running');
