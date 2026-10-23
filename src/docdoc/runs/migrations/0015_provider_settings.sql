-- Provider settings per scope. tenant_id '' is the deployment default; any other
-- value is a tenant's override. A NULL model or key means "inherit".
CREATE TABLE IF NOT EXISTS provider_priorities (
    tenant_id text PRIMARY KEY,
    priority text[] NOT NULL,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_settings (
    tenant_id text NOT NULL,
    adapter_id text NOT NULL,
    model text,
    key_ciphertext bytea,
    key_last4 text,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    PRIMARY KEY (tenant_id, adapter_id)
);
