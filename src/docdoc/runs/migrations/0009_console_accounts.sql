-- Console accounts and sessions (ADR-0023 §1).
--
-- An operator authenticates with a username and password; there is no other
-- console credential, and pasting an API key into the console is gone. A
-- password is never stored -- `password_hash` is `hashlib.scrypt` over it,
-- self-describing its own cost parameters, so a deployment that tunes them
-- later can still verify an older row.
--
-- `failed_logins` and `locked_until` live on the account rather than in a
-- side table: five consecutive failures lock the username for five minutes,
-- counted against the username regardless of where the attempts came from,
-- and that is a property of the account, not of any one session.
CREATE TABLE IF NOT EXISTS console_users (
    username       text        PRIMARY KEY,

    password_hash  text        NOT NULL,

    created_at     timestamptz NOT NULL,

    -- Set, never cleared, by `docdoc console-user disable`. There is no
    -- "enable": a disabled account is re-created by `console-user add`
    -- (ADR-0023 §1).
    disabled_at    timestamptz,

    failed_logins  int         NOT NULL DEFAULT 0,

    locked_until   timestamptz
);

-- A session is a bearer token; only `sha256(token)` is ever written here, so
-- a dumped sessions table does not hand over a usable session (ADR-0023 §1).
-- `ON DELETE CASCADE` exists for completeness -- nothing in this milestone
-- deletes a `console_users` row, `disable` only sets `disabled_at` -- but a
-- session referencing a user that no longer exists is not a state this
-- schema should be able to reach.
CREATE TABLE IF NOT EXISTS console_sessions (
    digest        text        PRIMARY KEY,

    username      text        NOT NULL REFERENCES console_users ON DELETE CASCADE,

    created_at    timestamptz NOT NULL,

    -- The idle clock. `resolve` compares this against the server's `now`,
    -- never the cookie's claimed age, and touches it on every live read.
    last_seen_at  timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS console_sessions_by_user
    ON console_sessions (username);
