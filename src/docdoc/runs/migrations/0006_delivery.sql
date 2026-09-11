-- Webhook delivery (ADR-0018). Two tables: where to send, and what was sent.

-- A registered destination and the digest of its signing secret.
--
-- `secret_digest` rather than the secret: the signature is computed from a value
-- the operator supplies through configuration, and what this table holds is
-- enough to tell two registrations apart and not enough to sign with (FR-066).
-- No route returns it after registration.
CREATE TABLE IF NOT EXISTS callbacks (
    callback_id   uuid        PRIMARY KEY,
    tenant_id     text        NOT NULL,
    url           text        NOT NULL,
    secret_digest text        NOT NULL,
    created_at    timestamptz NOT NULL,
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS callbacks_by_tenant
    ON callbacks (tenant_id, created_at);

CREATE TABLE IF NOT EXISTS deliveries (
    -- The value a receiver deduplicates on (FR-056). Stable across every retry
    -- because it is the primary key, allocated once when the run reaches a
    -- terminal state. An id regenerated per attempt would turn at-least-once
    -- DELIVERY into at-least-once PROCESSING on the receiver's side, which is the
    -- one thing at-least-once is supposed to let them avoid.
    delivery_id     uuid        PRIMARY KEY,

    tenant_id       text        NOT NULL,
    run_id          uuid        NOT NULL,
    callback_id     uuid        NOT NULL,

    -- A DELIVERY's state, not a run's. A failed delivery changes no run state
    -- (FR-063), and polling remains the record (FR-065).
    state           text        NOT NULL
                    CHECK (state IN ('pending', 'delivered', 'failed')),

    attempts        integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL,

    last_status     integer,

    -- A CLASS NAME, never the receiver's response body. The same rule
    -- `runs.error_class` follows: a receiver's body is somebody else's content
    -- and docdoc has no business storing it.
    last_error      text,

    created_at      timestamptz NOT NULL,
    updated_at      timestamptz NOT NULL,

    -- FR-058's ordering guarantee, expressed as a constraint rather than as
    -- worker discipline. One run produces one delivery; retries are attempts on
    -- this row. Two deliveries for one run cannot overlap because there are
    -- never two.
    CONSTRAINT one_delivery_per_run UNIQUE (run_id)
);

-- What the maintenance tick claims. Partial, because a delivered or failed row
-- is never due again.
CREATE INDEX IF NOT EXISTS deliveries_due
    ON deliveries (next_attempt_at)
    WHERE state = 'pending';
