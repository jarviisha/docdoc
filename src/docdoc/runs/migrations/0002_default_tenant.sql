-- Who owns the content that was written before tenants existed (FR-089).
--
-- An upgrading deployment has a store full of blobs and artifacts at
-- `<root>/blobs/…` with no tenant segment at all. ADR-0014 §3 leaves them there
-- — no copy, no move, no read-through fallback — which means something has to
-- say *whose* they are, and the system must not guess.
--
-- This table records the answer, taken from `DOCDOC_DEFAULT_TENANT` by
-- `docdoc migrate` and never inferred. It is written by that command rather than
-- by an INSERT here for the obvious reason: SQL cannot read an environment, and
-- a hard-coded value would be the inference this requirement forbids.
--
-- **Recording it is what makes the assignment safe to have made.** The value
-- decides where every read looks, so changing it after content exists strands
-- that content — correct answers, and a silent re-payment for every parse.
-- `docdoc migrate` compares against this row and refuses to move it, which turns
-- a misconfigured second deployment into an error at deploy time instead of a
-- cache that quietly stopped hitting.
--
-- No foreign key to `runs` and no relationship to it. A run's `tenant_id` is
-- recorded at creation and is never this value's business; this is about the
-- store's layout, which is why the column is a name and not a reference.

CREATE TABLE IF NOT EXISTS docdoc_default_tenant (
    -- One row, enforced. A second row would be a second answer to a question
    -- that has one, and the check is cheaper than the code that would otherwise
    -- have to pick between them.
    singleton   boolean     PRIMARY KEY DEFAULT true CHECK (singleton),

    -- The tenant whose namespace is the store root. `[a-z0-9_-]{1,64}`, which is
    -- validated at the authentication boundary; restated here because this row
    -- is written by a command rather than by an authenticated request, and that
    -- is the one path the boundary does not cover.
    tenant_id   text        NOT NULL CHECK (tenant_id ~ '^[a-z0-9_-]{1,64}$'),

    assigned_at timestamptz NOT NULL
);
