-- A check is an answer: what is right about one field of one document.
--
-- The newest row per (tenant_id, schema_name, blob_id, field_path) is the answer.
-- Rows written before this migration keep NULLs and are not answers.
ALTER TABLE corrections ADD COLUMN IF NOT EXISTS blob_id text;
ALTER TABLE corrections ADD COLUMN IF NOT EXISTS schema_name text;

CREATE INDEX IF NOT EXISTS corrections_answers
    ON corrections (tenant_id, schema_name, blob_id, field_path, created_at);
