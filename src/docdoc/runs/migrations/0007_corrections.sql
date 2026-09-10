-- Corrections, and the three columns `runs` gains.
--
-- `RunStatus` gains NOTHING here (FR-004a). Milestone 9 closed it at five and
-- left `expired` out because a state no code path can reach lies to everyone who
-- reads the enum; now that a code path exists, putting it back would swap one lie
-- for another, because the value would carry none of a run's other fields. A
-- removed run is gone AS A RUN -- see `run_tombstones` in 0003.

-- Storage for a model this package does not own. `Correction` is Milestone 6's,
-- defined in `docdoc.evaluation.corrections`, imported and never redefined
-- (FR-077).
--
-- It cannot live beside its model: the "evaluation reaches no network and no
-- provider" contract bars `socket`, `urllib`, `http`, and `docdoc.artifacts` from
-- that layer, so a database-backed store cannot be there and weakening the
-- contract to host one would trade a machine-checked property for a convenience
-- (research R13).
CREATE TABLE IF NOT EXISTS corrections (
    correction_id uuid        PRIMARY KEY,

    -- On every read and write (FR-079). A `run_id` is not a permission.
    tenant_id     text        NOT NULL,

    run_id        uuid        NOT NULL,
    processing_id text        NOT NULL,
    field_path    text        NOT NULL,

    -- The serialised `Correction`, stored whole. The three columns above are
    -- lifted out of it because they are queried, not because they matter more.
    payload       jsonb       NOT NULL,

    created_at    timestamptz NOT NULL,

    -- The correction's OWN retention deadline, configured independently of a
    -- run's (FR-013). A run whose result carries a live correction is not swept,
    -- and where the two deadlines disagree the longer one wins. This is the one
    -- place this milestone's two halves touch, and a policy that deleted a result
    -- somebody corrected is a data-loss bug that appears only once both features
    -- exist.
    expires_at    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS corrections_by_run
    ON corrections (tenant_id, run_id);

-- What the sweep reads to learn a run is pinned, and what expires the
-- corrections themselves.
CREATE INDEX IF NOT EXISTS corrections_expiring
    ON corrections (expires_at);

-- Three columns on `runs`, each with a default that makes every existing row
-- exactly what it already was (FR-101).

-- 0 ordinary, 10 urgent. A smallint rather than an enum because the claim query
-- orders by it, and because a third class -- if one is ever justified -- is a
-- number rather than a migration of a type.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS priority smallint NOT NULL DEFAULT 0;

-- Recorded by the worker in the same statement that records the terminal state
-- (research R9). Nullable, because a run that failed before reaching a provider
-- used none -- and `0` and "never got there" are different facts.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS tokens_used integer;

-- The destination registered at submission, or null. A soft reference: no
-- foreign key, for the reason 0001 gives for having none anywhere.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS callback_id uuid;
