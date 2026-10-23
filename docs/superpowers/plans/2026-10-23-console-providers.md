# Console Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators configure the extraction model providers in the console, for the whole deployment or for one tenant. They can set the priority order, the model, and an encrypted, write-only API key. New runs use the settings at once.

**Architecture:** Two new tables in the run database hold, for each scope, the priority list and the settings of each adapter. Scope `''` is the deployment default; any other scope is a tenant id. One resolution function, `resolve_adapter(tenant_id, …)` in `docdoc.runs.providers`, merges four layers in order: tenant, default, environment, shipped default. It builds the `AdapterRegistry` for that tenant. The worker, the API's synchronous extraction and `Settings.adapter()` all call it at the start of a run. Keys are sealed with Fernet under `DOCDOC_SECRETS_KEY`.

**Tech Stack:** Python, FastAPI, psycopg, `cryptography` (Fernet), google-genai, React 19 with TanStack and shadcn.

**Spec:** `docs/superpowers/specs/2026-10-23-console-providers-design.md`

## Global Constraints

- No endpoint, log line, error message, run record, artifact or cache key ever contains an API key. Responses show at most `key_last4`.
- Keys are stored only as Fernet ciphertext. The master key comes from `DOCDOC_SECRETS_KEY` (urlsafe-base64 Fernet key).
- Without `DOCDOC_SECRETS_KEY`:
  - Saving a key answers `409 {"error": "secrets_not_configured", "message": "set DOCDOC_SECRETS_KEY to store API keys"}`.
  - Everything else works.
- A stored key that cannot be decrypted makes that adapter unavailable for that scope, with the reason `"the stored API key for <adapter> cannot be decrypted with DOCDOC_SECRETS_KEY"`. It never falls back to another scope's key or to the env key.
- Resolution:
  - Model and key are resolved field by field: tenant, then default, then env, then the adapter's shipped default.
  - Priority is resolved as a whole list: the tenant's list, else the default's, else `DOCDOC_MODEL_ADAPTERS` or `--adapter`, else `DEFAULT_PRIORITY`.
- `echo` is never auto-selected. It answers only when the resolved priority names it, which is the existing `_NEVER_AUTO_SELECT` rule.
- With nothing saved, behaviour is identical to today. A deployment with no run database keeps today's env-only path.
- The adapter is resolved at run start. A run already in flight keeps its adapter. A redelivered run re-resolves.
- Tenant erasure removes the tenant's provider rows. This happens in `PostgresRunQueue.purge_tenant_rows`, which every erase path already calls.
- UI:
  - Decisions live in `ui/src/console/model/`.
  - Nothing goes to browser storage.
  - No ADR-0019 nouns in identifiers (approve, queue, assign, workload, capacity, balance, disposition, escalate, review state).
- Before reporting done, run `make lint`, `make test` and `make ui`.

## Review Focus

1. **A tenant inherits the default key, and that ciphertext is unreadable** (master key rotated). The tenant's runs fail as unavailable and never use the env key. Test: Task 4, `test_unreadable_inherited_key_is_unavailable_not_env`.
2. **Only the model is saved for a tenant.** The tenant gets its model plus the default or env key. Test: Task 4, `test_partial_override_inherits_key`.
3. **A worker started with no env key**, while the console has a key saved. The worker starts, and runs succeed. Test: Task 5, `test_worker_starts_without_env_key_when_db_configured`.
4. **The "Test connection" error text contains the key.** It is redacted before it is returned. Test: Task 6, `test_probe_error_is_redacted`.
5. **A priority list names an unknown adapter, or is empty.** The answer is `422`. Test: Task 6, `test_priority_rejects_unknown_and_empty`.

---

### Task 1: The worker's first registry build waits for the database (schemas residual)

**Files:** Modify `src/docdoc/cli/commands/worker.py`. Test: `tests/unit/test_worker_registry_reload.py`.

**Interfaces:**
- Uses the existing `merged_registry(files, store, *, strict: bool = False)` and `Settings.registry(console, strict=…)`. Check the current names in `src/docdoc/runs/console_schemas.py` and `cli/config.py`.

- [ ] **Step 1: Write the failing test.** `test_worker_starting_with_db_down_does_not_serve_a_shadowed_file`:
  - At startup the store raises `RunStateUnavailableError`; afterwards it holds `invoice@2`, which shadows the file `invoice@2`.
  - The first run names `invoice@2`.
  - Assert that the run used the console row (its `schema_hash` equals the console row's), not the file.
- [ ] **Step 2: Run it and confirm it fails.**
- [ ] **Step 3: Implement.** Build the first registry with `strict=True`. On `RunStateUnavailableError`, start from an empty `SchemaRegistry()`. The first run then misses, which triggers the strict reload that already exists.
- [ ] **Step 4: Run it and confirm it passes.** Run: `uv run pytest -n0 tests/unit/test_worker_registry_reload.py`
- [ ] **Step 5: Commit** with the message `fix(worker): first schema registry waits for the database`.

### Task 2: Sealing keys

**Files:**
- Create `src/docdoc/runs/secrets.py`.
- Modify `pyproject.toml`: add `cryptography>=42` to the `postgres` extra, next to psycopg, and run `uv lock`.
- Modify `src/docdoc/cli/config.py`: add `DOCDOC_SECRETS_KEY` to the documented settings table in the module docstring, in the existing style.
- Modify `tests/unit/test_documented_api_references_resolve.py`: add this plan, `docs/superpowers/plans/2026-10-23-console-providers.md`, to `CONFIG_DOCUMENTS`. Until this task lands, that test fails because the plan names `DOCDOC_SECRETS_KEY`. Task 1 may see that failure, and it is expected there.
- Test: `tests/unit/test_secrets.py`.

**Interfaces (Produces):**
- `SECRETS_KEY_ENV = "DOCDOC_SECRETS_KEY"`
- `class SecretUnreadable(Exception)`
- `class Sealer` has these members:
  - `from_env(environ: Mapping[str, str] = os.environ) -> Sealer | None`. It returns `None` when the variable is unset or blank, and raises `ValueError` naming the variable when the value is not a valid Fernet key.
  - `seal(text: str) -> bytes`
  - `unseal(token: bytes) -> str`. It raises `SecretUnreadable` on an invalid token.
- `last4(text: str) -> str`. It returns the last 4 characters, or the whole text when it is shorter.

- [ ] **Step 1: Write the failing tests:**
  - `test_round_trip`
  - `test_wrong_master_key_is_unreadable`
  - `test_unset_is_none`
  - `test_malformed_master_key_names_the_variable`
  - `test_last4`
  - `test_repr_of_sealer_has_no_key`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Also run `uv run pytest -n0 tests/unit/test_documented_api_references_resolve.py`.
- [ ] **Step 5: Commit** with the message `feat(runs): seal provider keys with DOCDOC_SECRETS_KEY`.

### Task 3: Provider settings store

**Files:**
- Create `src/docdoc/runs/migrations/0015_provider_settings.sql`.
- Create `src/docdoc/runs/provider_settings.py`.
- Create `tests/fixtures/provider_store.py` (`InMemoryProviderStore`).
- Modify `src/docdoc/runs/postgres.py`: add both tables to the `purge_tenant_rows` tuple.
- Update `tests/unit/test_data_model_matches_the_code.py` and any data-model doc section that test requires (follow the 0014 precedent).
- Test: `tests/integration/test_provider_settings_postgres.py` (postgres marker).

**Interfaces.** The migration:

```sql
CREATE TABLE IF NOT EXISTS provider_priorities (
    tenant_id text PRIMARY KEY,          -- '' is the deployment default
    priority text[] NOT NULL,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_settings (
    tenant_id text NOT NULL,             -- '' is the deployment default
    adapter_id text NOT NULL,
    model text,                          -- NULL: inherit
    key_ciphertext bytea,                -- NULL: inherit
    key_last4 text,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL,
    PRIMARY KEY (tenant_id, adapter_id)
);
```

The Python side:
- `DEFAULT_SCOPE = ""`
- `AdapterSettings` is a frozen dataclass with these fields: `model: str | None`, `key_ciphertext: bytes | None`, `key_last4: str | None`, `updated_at: datetime`, `updated_by: str`.
- `ScopeSettings` is a frozen dataclass with `priority: tuple[str, ...] | None` and `adapters: Mapping[str, AdapterSettings]`.
- The `ProviderStore` Protocol, with `PostgresProviderStore(execute)` and `InMemoryProviderStore` implementing it, has these methods:
  - `scope(tenant_id: str) -> ScopeSettings`. It returns an empty `ScopeSettings(None, {})` when nothing is saved.
  - `set_priority(tenant_id: str, priority: tuple[str, ...] | None, *, by: str, now: datetime) -> None`. `None` deletes the row.
  - `set_adapter(tenant_id: str, adapter_id: str, *, model: str | None, key: tuple[bytes, str] | None | KeepKey, by: str, now: datetime) -> None`. `KEEP_KEY` is a sentinel. A `key` of `None` clears the key, and `(ciphertext, last4)` sets it. The method upserts. When `model`, the ciphertext and `last4` all end up NULL, it deletes the row.
  - `reset(tenant_id: str) -> None`. It deletes both tables' rows for the tenant and refuses `DEFAULT_SCOPE` with `ValueError`.

- [ ] **Step 1: Write the failing tests:**
  - `test_empty_scope`
  - `test_set_and_read_priority`
  - `test_priority_none_deletes`
  - `test_set_model_keeps_key`
  - `test_clear_key`
  - `test_all_null_deletes_row`
  - `test_reset_tenant`
  - `test_reset_default_refused`
  - `test_erase_purges_provider_rows` (`purge_tenant_rows("acme")` reports both tables)
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Run: `DOCDOC_TEST_DATABASE_URL=… uv run pytest -n0 tests/integration/test_provider_settings_postgres.py tests/unit/test_data_model_matches_the_code.py`
- [ ] **Step 5: Commit** with the message `feat(runs): store provider settings per scope`.

### Task 4: Resolution, the per-tenant registry and the probe

**Files:**
- Create `src/docdoc/runs/providers.py`.
- Modify `src/docdoc/extraction/adapter_registry.py`. `default_adapter_registry(priority=None, *, configure: Mapping[str, AdapterConfig] | None = None)`.
- Modify `src/docdoc/extraction/adapters/gemini.py`: add a `probe`.
- Test: `tests/unit/test_provider_resolution.py`.

**Interfaces:**
- In `adapter_registry.py`:
  - `AdapterConfig` is a frozen dataclass with `model: str | None`, `api_key: str | None` and `unavailable: str | None`.
  - When `unavailable` is set, the registry calls `register_unavailable(id, reason=unavailable)` instead of constructing the adapter.
  - Gemini is constructed as `GeminiAdapter(model=cfg.model, api_key=cfg.api_key)`. When `api_key` is `None`, the existing env fallback applies. That env fallback is the "env layer".
- `GeminiAdapter.probe(timeout_s: float = 10.0) -> Availability`. It first returns `available()` if that is unusable. Otherwise it calls `client.models.get(model=self._model)` with an HTTP timeout and returns usable, or unusable with the provider's message. It never raises.
- In `docdoc.runs.providers`:
  - `Source = Literal["tenant", "default", "env", "shipped"]`
  - `ResolvedAdapter` is a frozen dataclass with these fields:
    - `id: str`
    - `model: str | None`
    - `model_source: Source`
    - `key_last4: str | None`
    - `key_source: Source`, where `"env"` means unknown or env
    - `config: AdapterConfig`
  - `Resolved` is a frozen dataclass with `priority: tuple[str, ...]`, `priority_source: Source` and `adapters: tuple[ResolvedAdapter, ...]`. The adapters are those `default_adapter_registry` knows: today `gemini` and `echo`.
  - `resolve(tenant_id: str | None, store: ProviderStore | None, sealer: Sealer | None, *, env_priority: tuple[str, ...] | None = None) -> Resolved`. A `tenant_id` of `None` or `DEFAULT_SCOPE` means the default scope only. `env_priority` carries `--adapter`.
  - `registry_for(resolved: Resolved) -> AdapterRegistry`
  - `resolve_adapter(tenant_id: str | None, store, sealer, *, env_priority=None) -> ModelAdapter`, which is `registry_for(resolve(...)).select()`. It raises `ModelProviderError` as `select()` does today.
  - `KNOWN_ADAPTERS: tuple[str, ...] = ("gemini", "echo")`

- [ ] **Step 1: Write the failing tests.** Use `InMemoryProviderStore` and a real `Sealer`, with no network.
  - `test_nothing_saved_matches_today`: same priority as `_configured_priority()`, `model_source == "shipped"` or `"env"`, `key_source == "env"`.
  - `test_tenant_model_overrides_default`
  - `test_partial_override_inherits_key`: the tenant sets only the model, so its `key_last4` is the default's and `key_source == "default"`.
  - `test_priority_whole_list_tenant_then_default_then_env`
  - `test_unreadable_inherited_key_is_unavailable_not_env`: monkeypatch `GEMINI_API_KEY`. Assert that `registry_for(...).candidates()` shows gemini unavailable with the cannot-be-decrypted reason, and that `resolve_adapter` raises `ModelProviderError`.
  - `test_echo_only_when_named`
  - `test_no_db_is_env_only`: `store=None`.
  - `test_gemini_probe_reports_provider_error_without_raising`: a fake client whose `models.get` raises.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Run: `uv run pytest -n0 tests/unit/test_provider_resolution.py tests/unit/test_gemini_mapping.py` and `uv run lint-imports` (`extraction` must not import `runs`).
- [ ] **Step 5: Commit** with the message `feat: resolve the model adapter per tenant`.

### Task 5: Runs use the tenant's adapter

**Files:**
- Modify `src/docdoc/runs/worker.py`. `Worker(…, adapter_for: Callable[[str], Any] | None = None)`. When it is set, `_execute_with_heartbeat` passes `adapter=self._adapter_for(run.tenant_id)` for extraction runs. OCR runs do not need it.
- Modify `src/docdoc/cli/commands/worker.py`. With a database, the worker builds `adapter_for = lambda t: resolve_adapter(t, PostgresProviderStore(queue._execute), Sealer.from_env(), env_priority=settings.adapters or None)` and no longer calls `settings.adapter()` at startup. Without a database, nothing changes.
- Modify `src/docdoc/cli/config.py`. `Settings.adapter(tenant_id: str | None = None, store: Any = None)` delegates to `resolve_adapter`.
- Modify `src/docdoc/api/app.py`. Add `provider_store()` (the same lazy pattern as `console_schemas()`, with an explicit `providers=` constructor argument for tests) and `sealer()` (`Sealer.from_env()`, built once). Change `adapter()` to `adapter(tenant_id: str | None = None)`: an injected `adapter=` still wins, which keeps the tests; otherwise it calls `resolve_adapter(tenant_id, self.provider_store(), self.sealer())`.
- Modify `src/docdoc/api/documents.py`: both `deployment.adapter()` calls become `deployment.adapter(principal.tenant_id)`.

**Failure semantics.** A resolution that raises `ModelProviderError` must end the run **FAILED**, with that message. Classify it exactly as an adapter that raises `ModelProviderError(reason="unavailable")` from `complete()` is classified today; read `execute_one` to see how. `RunStateUnavailableError` during resolution must behave like the registry reload: the lease lapses and the run is retried, never failed.

- [ ] **Step 1: Write the failing tests** (unit, `InMemoryRunQueue` with `InMemoryProviderStore`):
  - `test_run_uses_its_tenants_adapter`: two tenants with echo priority and different fixtures dirs, or a fake adapter factory. Each run records its own adapter's `model_id`.
  - `test_unusable_tenant_adapter_fails_the_run_with_reason`
  - `test_provider_db_blip_retries_not_fails`
  - `test_worker_starts_without_env_key_when_db_configured`: the CLI wiring builds a `Worker` with `GEMINI_API_KEY` unset and does not raise.
  - Contract test `test_v1_extract_uses_callers_tenant_adapter`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Then run `make test`.
- [ ] **Step 5: Commit** with the message `feat: runs resolve the tenant's model adapter at start`.

### Task 6: Console provider routes

**Files:**
- Modify `src/docdoc/api/console.py`.
- Record the new routes in `tests/contract/test_console_adds_one_read.py` and `tests/contract/test_http_ui_endpoints.py`.
- Test: `tests/contract/test_console_providers.py`.

**Interfaces.** In every route, `tenant` is an optional query param. When it is absent, the route acts on the deployment default. When it is present, it must match `TENANT_PATTERN`, otherwise `422`.
- `GET /v1/console/providers?tenant=` returns:

  ```json
  {"scope": "default" | "<tenant>",
   "secrets_configured": bool,
   "priority": {"value": [...], "source": Source},
   "adapters": [{"id", "test_only": bool,
                 "model": {"value": str|null, "source": Source},
                 "key": {"last4": str|null, "source": Source},
                 "status": {"usable": bool, "reason": str|null},
                 "updated_at": str|null, "updated_by": str|null}]}
  ```

  `test_only` is true for `echo`. Read `status` from `registry_for(resolved).candidates()`.
- `PUT /v1/console/providers/priority?tenant=` takes `{"priority": [str] | null}`. `null` clears a tenant's list, or the default's list, which then falls back to env. An unknown id or an empty list gives `422`. It answers `200` with the GET body.
- `PUT /v1/console/providers/{adapter_id}?tenant=` takes `{"model": str|null, "api_key"?: str|null}`:
  - `api_key` absent: keep the key.
  - `api_key` `null`: clear the key.
  - `api_key` a string: seal it, giving `409 secrets_not_configured` when there is no sealer.
  - An unknown `adapter_id` gives `404`, and a blank model string gives `422`.
  - It answers `200` with the GET body.
- `DELETE /v1/console/providers?tenant=X` resets the tenant. Without a tenant it gives `422`. It answers `200` with the GET body.
- `POST /v1/console/providers/{adapter_id}/test?tenant=` takes `{"model"?: str, "api_key"?: str}`. It resolves the scope, overlays the given values, builds that one adapter and calls `probe()`. It answers `200 {"ok": bool, "message": str}`. The message has every occurrence of the effective key replaced with `"••••"`. A non-Gemini adapter answers with its `available()`.
- All of these routes need a session. Without a run database they give `503`, the console's existing behaviour.

- [ ] **Step 1: Write the failing tests:**
  - `test_get_default_with_nothing_saved`: sources are env or shipped, and `secrets_configured` is false.
  - `test_put_model_then_get_shows_tenant_source`
  - `test_put_key_without_secrets_is_409`
  - `test_put_key_with_secrets_shows_last4_only`: `monkeypatch.setenv("DOCDOC_SECRETS_KEY", Fernet.generate_key().decode())`. Assert that the response text does not contain the key.
  - `test_priority_rejects_unknown_and_empty`
  - `test_reset_tenant_and_reset_default_is_422`
  - `test_probe_error_is_redacted`: monkeypatch `GeminiAdapter.probe` to return a reason containing the key, then assert `"••••"` appears in the message and the key does not.
  - `test_provider_routes_need_a_session`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and confirm they pass.** Run: `uv run pytest -n0 tests/contract/test_console_providers.py tests/contract/test_console_adds_one_read.py tests/contract/test_http_ui_endpoints.py`
- [ ] **Step 5: Commit** with the message `feat(console): read and configure model providers over HTTP`.

### Task 7: The console model for providers

**Files:**
- Create `ui/src/console/model/providers.ts`.
- Modify `client.ts`. Add these intents:
  - `list-providers {tenantId: string | null}`, a GET.
  - `set-provider-priority {tenantId}`, a PUT with a body.
  - `set-provider {tenantId; adapterId}`, a PUT with a body.
  - `reset-providers {tenantId: string}`, a DELETE.
  - `test-provider {tenantId; adapterId}`, a POST with a body. It is a read-like write: it invalidates nothing.
  - Add all of them to the exhaustive lists and the `ALLOWED` patterns.
- Modify `queries.ts`. The three mutating intents invalidate `[["list-providers"]]`.
- Modify `navigation.ts`. Add Area `"providers"` with path `"/providers"`.
- Test: `ui/test/console/providers.test.ts`, and update the client, queries and navigation tests.

**Interfaces (exported from `providers.ts`):**
- `type Source = "tenant" | "default" | "env" | "shipped"`
- `interface ProviderRow {id; testOnly: boolean; model: string | null; modelSource: Source; keyLast4: string | null; keySource: Source; usable: boolean; reason: string | null; updatedAt: string | null; updatedBy: string | null}`
- `interface ProvidersView {scope: string; secretsConfigured: boolean; priority: string[]; prioritySource: Source; rows: ProviderRow[]}`. `rows` are ordered by priority first, then the rest by id.
- `toProvidersView(body: unknown): ProvidersView | null`
- `SOURCE_LABEL: Record<Source, string>`, which is `{tenant: "this tenant", default: "deployment default", env: "environment", shipped: "built-in"}`.
- `keyDisplay(row): string`, giving `"••••" + last4`, or `"not set"`.
- `movePriority(priority: string[], id: string, delta: -1 | 1): string[]`. It is a no-op at the ends.
- `withAdapter(priority, id)` and `withoutAdapter(priority, id)`. Removing the last entry returns the list unchanged, because the server refuses empty lists.
- `adapterUpdate(form: {model: string; apiKey: string; clearKey: boolean}): {model: string | null; api_key?: string | null}`. A blank model becomes `null`. A blank `apiKey` with `clearKey` false omits `api_key`. `clearKey` sets `api_key: null`. A non-blank `apiKey` sets it.
- `isOverride(view): boolean`, true when any source is `"tenant"`.

- [ ] **Step 1: Write the failing tests** for every export, including the `adapterUpdate` cases and the `movePriority` edges.
- [ ] **Step 2: Run them and confirm they fail.** Run: `cd ui && npm test`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the checks and confirm they pass.** Run: `cd ui && npm test && npx tsc --noEmit && npm run lint:boundaries`
- [ ] **Step 5: Commit** with the message `feat(console): providers model`.

### Task 8: The Providers screen

**Files:**
- Create `ui/src/console/components/ProvidersArea.tsx`.
- Modify `routes.tsx`: replace `{ soon: "Providers" }` with `{ name: "providers", label: "Providers" }`, and add the route `/providers` with `validateSearch: tenantSearch`.

**Behaviour.** Follow `.claude/skills/console-ui/SKILL.md`, matching the mockup in the spec's decision 5.
- **Tenant picker.** The picker reads `list-tenants`. Its first option is "(deployment default)". Changing it updates `?tenant=`.
- **Table.** It has these columns:
  - The position in the priority list, or "—" when the adapter is not listed.
  - The id, with a "test only" badge for echo.
  - The effective model with its source.
  - `keyDisplay` with its source.
  - The status: "in use" for the first usable adapter in the priority list, otherwise usable or unavailable with the reason.
  - Up and down buttons that reorder the priority list.
  - "Add to priority" and "Remove", used for echo.
  - Edit.
- **Edit form**, inline or in a dialog:
  - A model input.
  - A password input for the key, whose placeholder reads "••••last4 saved — type to replace", plus a "Remove saved key" checkbox. Saving a key is disabled when `secretsConfigured` is false, with the 409 message shown beside the field.
  - "Test connection", which shows ok or the message.
  - Save.
- **Reset.** On a tenant with an override, "Reset to deployment default" goes through `ConfirmDialog`.

- [ ] **Step 1: Build the screen.**
- [ ] **Step 2: Run the checks.** Run `make ui`; it must pass.
- [ ] **Step 3: Check by hand.** Run `make dev`, with `DOCDOC_SECRETS_KEY` exported if possible. Use the API through curl with a console session, and the browser if available:
  - Set the default model.
  - Override a tenant's model.
  - Reset it.
  - Save a key, and check that responses show only the last 4 characters.
  - Report what was and was not exercised.
- [ ] **Step 4: Commit** with the message `feat(console): Providers area`.

### Task 9: Record it

**Files:**
- Create `docs/adr/0030-console-configured-providers.md`, covering spec decisions 1–4 and 7–10, and the `DOCDOC_SECRETS_KEY` contract. Add it to the `docs/adr/README.md` index.
- Add a `CHANGELOG.md` entry, "### Added — Providers in the console (ADR-0030)".
- Add the new ADR to `CONFIG_DOCUMENTS` in `tests/unit/test_documented_api_references_resolve.py`.
- Re-point the guard-the-guard in `tests/contract/test_protected_layers_untouched.py` to `src/docdoc/runs/providers.py`, with a dated paragraph. The protected sets stay unchanged.
- Note `DOCDOC_SECRETS_KEY` in `packaging/docker/compose.yml` comments and `packaging/docker/secrets/README.md`, if they list env settings.

- [ ] **Step 1: Write the ADR, the changelog entry and the doc updates.**
- [ ] **Step 2: Run the checks.** Run `make lint && make test && make ui`; all must be green.
- [ ] **Step 3: Commit** with the message `docs: ADR-0030 console-configured providers`.
