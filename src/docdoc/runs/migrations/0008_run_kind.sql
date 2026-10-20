-- 014 (ADR-0022): a run has a kind. `extraction` is every run before this
-- migration and the default for every caller that names none; `ocr` is the
-- parse stage alone, which has no schema and carries the caller's parser and
-- language instead.
--
-- Idempotent, like every migration before it: a deployment pipeline re-runs it.

ALTER TABLE runs
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'extraction';
ALTER TABLE runs ADD COLUMN IF NOT EXISTS ocr_parser text;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS ocr_language text;
ALTER TABLE runs ALTER COLUMN schema_identity DROP NOT NULL;

ALTER TABLE runs DROP CONSTRAINT IF EXISTS run_kind_is_known;
ALTER TABLE runs ADD CONSTRAINT run_kind_is_known
    CHECK (kind IN ('extraction', 'ocr'));

-- The schema belongs to an extraction run and to nothing else, enforced here
-- as well as by the model, as `processing_id_belongs_to_success` is.
ALTER TABLE runs DROP CONSTRAINT IF EXISTS schema_belongs_to_extraction;
ALTER TABLE runs ADD CONSTRAINT schema_belongs_to_extraction
    CHECK ((kind = 'extraction') = (schema_identity IS NOT NULL));

-- What the OCR caps count, on every submission: active runs of a kind,
-- overall and per tenant.
CREATE INDEX IF NOT EXISTS runs_active_by_kind
    ON runs (kind, tenant_id)
    WHERE status IN ('queued', 'running');
