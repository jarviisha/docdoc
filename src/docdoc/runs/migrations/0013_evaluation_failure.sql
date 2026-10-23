-- An evaluation that could not be scored: its schema version was removed or
-- edited between start and settle, or `evaluate` refused the pair. `failure` is
-- the exception class only (never a message, which can quote document content),
-- written once with `settled_at`. A failed evaluation is finished: it no longer
-- holds the one-unfinished slot for its (tenant, schema name).
ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS failure text;

DROP INDEX IF EXISTS evaluations_one_unfinished;
CREATE UNIQUE INDEX IF NOT EXISTS evaluations_one_unfinished
    ON evaluations (tenant_id, schema_name) WHERE report IS NULL AND failure IS NULL;
