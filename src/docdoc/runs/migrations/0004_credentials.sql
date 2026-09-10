-- Credentials with a lifetime (ADR-0016).
--
-- Milestone 9 kept these in a file and said why: "a table invites exactly the
-- endpoint the requirement forbids" (`api/auth.py`). The endpoint is now wanted,
-- and `specs/009` FR-061's own text -- "MUST NOT be creatable or mutable through
-- any route **in this milestone**" -- named the milestone that would change it.
--
-- The file ring keeps working and is consulted first (FR-038). This table is
-- additive in the strongest sense: a deployment that never creates a row here
-- authenticates exactly as it did before.
CREATE TABLE IF NOT EXISTS credentials (
    credential_id uuid        PRIMARY KEY,

    tenant_id     text        NOT NULL,

    -- sha256(key), never the key. The same derivation `api/auth.py:digest_of`
    -- already uses, so the file ring and this table agree on what a stored
    -- credential is.
    --
    -- UNIQUE deployment-wide rather than per tenant: a digest lookup has to
    -- resolve to exactly one principal, and it happens before the caller has
    -- been authenticated to name a tenant.
    digest        text        NOT NULL UNIQUE,

    -- At most 'admin' today. An array rather than a boolean so a second scope is
    -- not a migration; not a role table, because nothing here asks for one. A
    -- capability on a credential, never a second tenant -- Milestone 9's FR-060
    -- stands (ADR-0016 §4).
    scopes        text[]      NOT NULL DEFAULT '{}',

    label         text,

    created_at    timestamptz NOT NULL,

    -- Written best-effort and explicitly NOT in the transaction of the request
    -- it describes. Exactness would put a write on every authenticated request;
    -- this exists for the listing, and the listing says so.
    last_used_at  timestamptz,

    -- Revocation is an UPDATE, never a DELETE (ADR-0016 §2). The row stays, the
    -- identifier is never reused, and a revoked credential is never reissuable.
    -- "This key was revoked on the 4th" is what an incident review needs, and a
    -- missing row cannot say it.
    revoked_at    timestamptz
);

CREATE INDEX IF NOT EXISTS credentials_by_tenant
    ON credentials (tenant_id, created_at);
