# ADR-0030: Model Providers Are Configured in the Console, per Tenant

- **Status**: Accepted
- **Date**: 2026-10-24
- **Relates to**: [ADR-0023](0023-console-login-and-cross-tenant-operations.md) (the session and who
  may edit), [ADR-0028](0028-console-routing-and-data-layer.md) (routes and data loading),
  [ADR-0029](0029-console-authored-schemas.md) (the run database as the console's store)
- **Design**: `docs/superpowers/specs/2026-10-23-console-providers-design.md`

## Context

Which model adapter answers, its order, the Gemini model and its API key were environment
variables (`DOCDOC_MODEL_ADAPTERS`, `DOCDOC_GEMINI_MODEL`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`), and
the API and each worker built one process-wide adapter from them. Changing any of it meant a
redeploy, and every tenant shared one model and one key. The owner wants operators to set these in
the console, and each tenant to be able to use its own model and key. An API key must never be
readable back, and `echo` must never be chosen without someone naming it.

A related fix is recorded here because it shares the same startup path. A worker that started
while Postgres was down built its schema registry from files alone, which could run a file whose
identity a console row (ADR-0029) shadows. The worker now starts with an empty registry on an
outage: the first claimed run misses, takes the strict reload, and is left for retry until the
database answers.

## Decision

1. **Storage is Postgres, in the run database.** Migration `0015_provider_settings.sql` adds
   `provider_priorities` (one row per scope, primary key `tenant_id`, `priority` text[]) and
   `provider_settings` (one row per scope and adapter, primary key `(tenant_id, adapter_id)`:
   `model`, `key_ciphertext` bytea, `key_last4`, `updated_at`, `updated_by`). The scope `''` is the
   deployment default; any other value is a tenant. A missing value means "inherit"; a settings row
   with neither model nor key is deleted. Tenant erasure (`purge_tenant_rows`, shared by the console,
   `DELETE /v1/admin/tenants/{id}` and `docdoc erase`) deletes both tables' rows for the tenant.
2. **Resolution order, one field at a time.** Model and key: tenant row, deployment-default row,
   environment, the adapter's shipped default. The priority list is resolved as a whole, never
   merged: the tenant's list, else the default's, else `--adapter`/`DOCDOC_MODEL_ADAPTERS`, else the
   shipped priority. A saved empty list is refused; `null` clears it. `echo` is shown and ordered
   but takes no saved model or key, and still answers only if a priority list names it.
3. **Keys are sealed with Fernet under `DOCDOC_SECRETS_KEY`.** `cryptography>=42` joins the
   `postgres` extra beside `psycopg`. The variable is environment-only (no CLI flag, since argv is
   readable on the host), read once at API and worker startup; a malformed value stops the process
   there. Without it the screen loads and every non-key setting saves, and storing a key answers
   `409 secrets_not_configured` naming the variable. A saved key that does not unseal, or unseals
   to blank, makes that adapter *unavailable* for that scope with that reason; it never falls back
   to a lower layer's key or the environment, so a tenant's run cannot go out on another's key.
   Only the last 4 characters are ever returned; the key is out of every repr, response and error.
4. **The adapter is chosen per tenant at run start.** One function, `runs/providers.resolve`,
   serves the worker (`adapter_for`, called before `execute_one` for every non-OCR run), the
   API's synchronous `/v1` extraction (`Deployment.adapter(tenant_id)`) and `Settings.adapter()`.
   A run keeps its adapter until it finishes. A caller with no run database gets the environment
   alone, as before. If resolution finds no usable adapter, the worker ends the run `FAILED` at
   `extract`, as for an unavailable adapter. If the settings cannot be read, `RunStateUnavailableError`
   leaves the run unfinished and its lease lapses, so a database blip is retried; the `/v1`
   synchronous paths answer `503` while the run database is unreachable.
5. **Our call: `--adapter` is the env layer.** `docdoc worker --adapter X` and
   `DOCDOC_MODEL_ADAPTERS` are one setting, so a priority saved in the console wins over both.
   The console's "env" source is the API process's view; a worker started with a different flag
   can differ only until a priority is saved.
6. **Our call: the default tenant is an ordinary tenant** in the picker, with its own optional
   override, separate from "(deployment default)".
7. **Our call: a redelivered run re-resolves.** After a lost lease, finished stages are reused and
   the remaining ones read the current settings. No snapshot is stored on the run; provenance
   still records the model that answered. This is the one place a run can change configuration
   mid-way, and only after a crash or a lost lease.
8. **Routes** (all under `/v1/console`, operator session): `GET /providers`,
   `PUT /providers/priority`, `PUT /providers/{adapter}`, `DELETE /providers` (tenant only; the
   deployment default is never reset), `POST /providers/{adapter}/test`; the scope is the optional
   `tenant` query parameter. Bodies are read raw under a 16 KiB cap (`413`) so a malformed field
   answers `422` with a field name and never echoes the input. `PUT /providers/echo` is `404`.
   "Test connection" overlays the form's model and key on the scope's settings, stores nothing,
   and returns the provider's text with the key redacted.

## Alternatives rejected

- **Keys in plaintext, or reversible without a deployment secret.** A table dump would be a dump of
  working keys.
- **Falling back to the environment key when a saved key is unreadable.** A tenant would silently
  run on a key it never chose.
- **A per-run snapshot of the configuration.** More state on every run for a case that needs a
  crash to occur.
- **Master-key rotation tooling.** Out of scope; a changed key makes saved keys unreadable until
  re-entered.

## Consequences

- A deployment without a run database has no console and keeps the environment-only behaviour.
- With nothing saved, the adapter, model and key are those the environment always gave.
- Losing or changing `DOCDOC_SECRETS_KEY` makes every saved key unreadable (adapters unavailable)
  until the keys are entered again.
- Every extraction run reads up to two scopes from the run database at its start.
- Any signed-in operator can edit, as with schemas; there are no roles.
