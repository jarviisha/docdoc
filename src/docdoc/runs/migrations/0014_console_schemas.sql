-- Schemas authored in the console. A row is one immutable identity (name, version):
-- it is inserted once and never updated or deleted. `base` is the identity it was
-- derived from, if any.
CREATE TABLE IF NOT EXISTS console_schemas (
    name text NOT NULL,
    version int NOT NULL CHECK (version >= 1),
    document jsonb NOT NULL,
    prompt text NOT NULL,
    base text,
    created_at timestamptz NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (name, version)
);
