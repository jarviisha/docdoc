-- An evaluation: a tenant's answer set re-run and, once every run is terminal,
-- scored. `golden` is the stamped `GoldenSet`; `report` is the `EvaluationReport`,
-- NULL until settled. `document_ids` is parallel to `run_ids`.
--
-- Erasing a document removes it from `golden` and from the report's per-document
-- detail and counts it in `erased_documents`; the aggregates stay as computed.
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id    uuid        PRIMARY KEY,
    tenant_id        text        NOT NULL,
    schema_name      text        NOT NULL,
    schema_identity  text        NOT NULL,
    golden           jsonb       NOT NULL,
    run_ids          uuid[]      NOT NULL,
    document_ids     text[]      NOT NULL,
    report           jsonb,
    erased_documents integer     NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL,
    settled_at       timestamptz
);

CREATE INDEX IF NOT EXISTS evaluations_by_schema
    ON evaluations (tenant_id, schema_name, created_at DESC);

-- One unfinished evaluation per (tenant, schema name): two concurrent starts
-- cannot both pass the check before either has written its row.
CREATE UNIQUE INDEX IF NOT EXISTS evaluations_one_unfinished
    ON evaluations (tenant_id, schema_name) WHERE report IS NULL;
