# Console: Providers, configured in the UI, per tenant

## Intent

The sidebar's "Providers — soon" entry becomes **Settings / Providers**. A "provider" here is a model
adapter that does extraction: today `gemini` is the one real adapter, and `echo` gives canned
answers for tests. Which adapter answers, its order, the Gemini model, and the API key are all
environment variables today (`DOCDOC_MODEL_ADAPTERS`, `DOCDOC_GEMINI_MODEL`,
`GEMINI_API_KEY`/`GOOGLE_API_KEY`). Changing any of them means a redeploy. The owner wants these
**configured from the console**, and wants each **tenant able to use its own model and key**.

## Goals

- Two layers of configuration, both editable in the console:
  - **Deployment default.** Applies to every tenant without an override. The current environment
    variables are its starting values and stay as the fallback when nothing has been saved.
  - **Per-tenant override.** Overrides the default, and may override only part of it. For example, a
    tenant can change only the model and keep the default key.
- Each layer configures three things: the adapter priority order, the model name per adapter, and
  the API key per adapter.
- The screen shows, per adapter and per layer, the effective value and where it comes from (env,
  default, or tenant), plus whether the adapter is usable and why not.
- "Test connection" makes one real call with the settings in the form, before saving.
- New runs use the new configuration as soon as it is saved, in the API and in already-running
  workers, with no restart.

## Non-goals

- No new adapters (OpenAI, Anthropic, and so on). Only adapters the code already supports can be
  configured.
- OCR parsers are out of scope. Settings / Parsers stays as it is.
- No master-key rotation tooling.
- No role-based permissions: any signed-in operator can edit.

## Anti-goals: it fails even if it "works" when

- An API key can be read back through any endpoint, log line, error message, run record, artifact or
  cache key. Keys are **write-only**. The most that is ever shown is the last 4 characters.
- A key is stored in plaintext.
- `echo` gets picked without someone choosing it. Its rule stays: it is never auto-selected, and it
  answers only if a layer's priority list names it explicitly (see `adapter_registry.py`).
- A tenant's run goes out on another tenant's key.
- A run already in progress switches configuration halfway through.

## Decided

1. **Storage: Postgres, in the run database.** New migration(s) hold, per scope (deployment default
   or a tenant id): the priority list, and per adapter the model, the encrypted key, the key's last 4
   characters, `updated_at` and `updated_by`. A missing value means "inherit from the layer below".
2. **Resolution order, one field at a time:** tenant override, then deployment default, then
   environment variables, then the adapter's shipped default. The priority list is resolved as a
   whole, not merged: the tenant's list if it has one, otherwise the default's, otherwise
   `DOCDOC_MODEL_ADAPTERS`.
3. **Encryption.** Keys are encrypted with Fernet (`cryptography`) using a master key from a new
   environment variable (suggested name `DOCDOC_SECRETS_KEY`). Add `cryptography` as an explicit
   dependency where `psycopg` is declared. Today it is only pulled in transitively by Google SDKs.
   Without the master key, the screen still loads and every non-key setting can be saved. Saving a
   key is refused with a message naming the variable. A stored key that cannot be decrypted makes
   that adapter *unavailable* for that scope, with that reason. The run fails the same way a missing
   key fails today, and never falls back to the other scope's key.
4. **The adapter is chosen per tenant, at the start of a run.** Today `Deployment.adapter()` and the
   worker each build one process-wide adapter. Both places must resolve the adapter for the run's
   tenant: the worker's `execute_one` and the API's synchronous extraction in `api/documents.py`. The
   configuration is read when the run starts, and a run keeps that adapter until it finishes.
   Provenance already records `model_id` and `model_version`, so it shows which model answered. The
   API key must never enter provenance or cache keys.
5. **Screen (from the mockup the owner saw).** A tenant picker at the top, defaulting to
   "(deployment default)" and reusing ADR-0028's `tenantSearch` param. Below it, a table with one row
   per adapter: priority position, adapter, effective model, key as `••••last4` plus its source,
   status, and Edit. The Edit form has the model, a key input whose placeholder shows the saved last
   4 (an empty input means "don't change"), "Test connection", Save, and, on a tenant, "Reset to
   default", which removes the override. Priority is reordered with up and down controls. `echo` is
   labelled "test only" and is added to a priority list only by an explicit action.
6. **Tenant erasure removes that tenant's provider settings.** Do it in the shared
   `retention.erase`, so the console route, `DELETE /v1/admin/tenants/{id}` and `docdoc erase` all
   remove them.
7. **Our call: `--adapter` is the env layer.** `docdoc worker --adapter X` and
   `DOCDOC_MODEL_ADAPTERS` are one setting (FR-031), so the flag sits where env sits: below saved
   settings. A priority saved in the console therefore also wins over `make dev` workers. The screen's
   "env" source is the API process's view, and a worker started with a different flag can differ
   only while nothing is saved.
8. **Our call: one resolution function, used by every caller.** The worker, the API's synchronous
   extraction, and `Settings.adapter()` (which serves `docdoc extract` and the recording tools) all
   resolve through it. A caller with no run database gets today's env-only answer.
9. **Our call: the default tenant (`root_tenant()`) is an ordinary tenant in the picker**, with its
   own optional override, separate from "(deployment default)".
10. **Our call: a redelivered run re-resolves the configuration.** When a lease is lost and the
    run is picked up again, stages that already finished are reused as today, and the remaining
    stages read the current settings. No snapshot is stored on the run. Provenance still records
    the model that actually answered. This is the one place a run can change configuration partway
    through, and it can only happen after a crash or a lost lease.

## Left to the builder

- Table shapes, endpoint paths and payloads under `/v1/console/…`. The constraint is that no response
  ever contains a key.
- How "Test connection" calls Gemini. Pick the cheapest call that proves both the key and the model,
  such as fetching the model's metadata. Give it a timeout and show the provider's error text with
  the key redacted.
- How workers pick up changes without a restart. Reading the scope's row at run start is enough, and
  caching adapter instances keyed by the resolved settings is fine.
- Gemini already accepts `model=` and `api_key=` in its constructor, so per-tenant construction
  needs no adapter changes. Confirm that `echo` still needs only `DOCDOC_ECHO_FIXTURES`. Fixtures are
  not configured in the UI.
- Write an ADR (next number) recording decisions 1–4. Follow the `console-ui` skill and ADR-0028 for
  the screen.

## Constraints

- A deployment without a run database has no console, and keeps today's env-only behaviour.
- With nothing saved, behaviour is identical to today: same adapter, model and key, chosen from the
  environment.
- Run the full suite with `make test` before reporting done (AGENTS.md).
