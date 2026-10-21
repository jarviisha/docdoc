# Console Parsers, Tenants and Callbacks (Plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The console gains three screens. **Parsers** shows the OCR hosts' availability and
health. **Tenants** lists known tenants and leads straight to that tenant's runs, credentials or
erasure. **Callbacks** lists, registers and revokes webhook destinations per tenant.

**Architecture:**
- **Parsers and Tenants** reuse routes that already exist (`GET /v1/console/parsers`,
  `GET /v1/console/tenants`).
- **Callbacks** needs one new store read and three session-gated console routes. They mirror the
  tenant callback routes and take the tenant explicitly.
- **UI:** the sidebar gains the three items. Other areas accept a preset tenant, so the Tenants
  screen can jump into them.

**Tech Stack:** Python 3 / FastAPI / psycopg; React 19 + TypeScript + Astryx. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`, goals "Parsers",
"Tenants" and "Credentials, Callbacks, Erasure: usable without typing a tenant by hand. Callbacks
gain list, create, and delete."

## Execution shape

| Lane | Tasks | Worktree |
|---|---|---|
| Backend | 1 | its own |
| Frontend | 2 → 3 | its own |

Task 4 integrates, then checks the result end to end with screenshots.

**Databases and processes:**
- Tests use `DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc`.
- The demo server on :8765 and the OCR worker use `docdoc_demo`, and the controller owns both.
- Never start or stop containers, the server or the worker. Never read
  `/tmp/docdoc-ocr-endpoints.json`.

## API contract (both lanes build to this)

**Existing, unchanged:**
- `GET /v1/console/tenants` returns `{"tenants": [...]}`.
- `GET /v1/console/parsers` returns `{"parsers": [{id, available, unavailable_reason?, capabilities: {text, geometry, tables, handwriting}, media_types, languages?, healthy?}]}`.

**New, session-gated, `{tenant_id}` checked against `TENANT_PATTERN` (otherwise
`422 {"error":"invalid_tenant_id"}`):**

- **`GET /v1/console/tenants/{tenant_id}/callbacks`** returns
  `200 {"callbacks": [{"callback_id", "tenant_id", "url", "created_at"}]}`.
  - It lists active callbacks only (not revoked), newest first.
  - It never includes the secret or its digest.
- **`POST /v1/console/tenants/{tenant_id}/callbacks`** takes the body `{"url", "secret"}` and needs
  `X-Docdoc-Console: 1`. Its answers are those of `POST /v1/callbacks` for that tenant:
  - `201 {"callback_id", "url", "created_at"}`, plus `tenant_id`;
  - `422 invalid_callback` when the url or secret is missing;
  - `422 {"error":"destination_refused","detail":…}` when SSRF validation refuses the destination
    or the secret is not configured;
  - `503 DeliveryError` (`_delivery_unconfigured`) when the deployment delivers no callbacks.
  - It logs `console.callback_register`: actor, tenant_id, callback_id. The URL is logged, the
    secret never.
- **`DELETE /v1/console/tenants/{tenant_id}/callbacks/{callback_id}`** needs `X-Docdoc-Console: 1`
  and returns `204`, idempotently, as `DELETE /v1/callbacks/{id}` does.
  - It logs `console.callback_revoke` when a callback was actually revoked.
- When delivery is unconfigured, the GET also answers the same `503`.

## Global Constraints

- **No new Python or npm dependency.** Clock and randomness are read only in
  `docdoc.runs.identity`.
- **The secret is never returned or logged,** on any route, list or log line (FR-066).
- **Tenant routes under `/v1/*` are unchanged.** The new console routes reuse the deliverer's
  `register` and `revoke` and do not copy validation.
- **Route paths avoid the words in `test_no_review_platform.py`.** Keep the guard lists green:
  `CONSOLE_ADDED`, `ROUTES`, `test_jobs_routes_unchanged`, and `CONFIG_DOCUMENTS` (add this plan if
  it names `DOCDOC_*`).
- **Forbidden words** under `ui/src/console`: `queue` (`queued` is fine), `capacity`, `balance`,
  `assign`, `workload`, `approve`, `reject`, `escalate`, `disposition`. Components decide nothing.
  Use Astryx only, with no raw `<input>`/`<select>`.
- **Gates.**
  - Backend: ruff check, ruff format --check, mypy src/docdoc, lint-imports, and the full suite once
    on the test database.
  - Frontend, from `ui/`: `npm run typecheck && npm test && npm run lint:boundaries && npm run build`.
- **Visual check is mandatory for UI tasks.** Use Playwright screenshots, read with
  `cd /tmp && uvx --with playwright python <script>`.

## Review Focus

1. **Registering a callback with a secret the deployment doesn't know** shows the readable refusal
   sentence, not a raw code.
   Tests: Task 1, `test_console_register_refuses_an_unknown_secret`; Task 2,
   `test_destination_refused_reads_as_a_sentence`.
2. **A deployment with no delivery configured** makes Callbacks show the 503 sentence, which says
   what to configure, and the rest of the console keeps working.
   Tests: Task 1, `test_console_callbacks_without_delivery_is_503`; Task 2,
   `test_unconfigured_delivery_is_unavailable`.
3. **Jumping from Tenants to Runs, Credentials or Erasure** arrives with that tenant already
   applied. On Erasure the tenant is prefilled, but the retype confirmation is still required.
   Test: Task 2, `test_tenant_jump_presets_the_area`. Task 4 checks it visually.
4. **Parser health is probed live** and can be slow. The Parsers screen shows loading, does not
   poll automatically, and has a Refresh button.
   Test: Task 2, `test_parsers_never_poll`. Task 4 checks it visually.
5. **Revoking a callback asks for confirmation inline,** like revoking a credential, and a double
   click sends one request.
   Test: Task 2, `test_cannot_revoke_while_sending`.

---

### Task 1 (backend lane): callback list and console callback routes

**Files:**
- Modify: `src/docdoc/runs/delivery.py`. Add
  `PostgresDeliverer.callbacks_for(self, tenant_id: str) -> tuple[Callback, ...]`, which lists
  active callbacks ordered by `created_at DESC, callback_id DESC`. Update any in-memory or fake
  deliverer the tests use to match.
- Modify: `src/docdoc/api/console.py`. Add the three routes from the contract.
  - Reuse `deployment.deliverer()`, `register`, `revoke` and the tenant route's error mapping:
    either factor a small shared helper out of `api/runs.py`, or call it, so the 422 and 503
    bodies are not copied.
  - The handlers are plain `def`.
- Modify: the guard lists.
- Test: `tests/contract/test_console_callbacks.py` (new); a Postgres case for `callbacks_for` in
  `tests/integration/test_delivery_postgres.py`.

**Interfaces:**
- Produces `callbacks_for` as above.
- Produces the routes exactly as in the contract.
- Produces the log events `console.callback_register` and `console.callback_revoke`, in the
  `_log_cancel` JSON style.

- [ ] **Step 1: Write the failing tests**

```python
def test_console_lists_a_tenants_active_callbacks_newest_first(signed_in, …)   # two registered, one revoked → two listed, newest first, no "secret"/"secret_digest" keys anywhere in the body
def test_console_registers_a_callback(…)                                        # POST with a configured secret + public-looking url → 201 with tenant_id; appears in the list; caplog console.callback_register without the secret
def test_console_register_refuses_an_unknown_secret(…)                          # → 422 destination_refused, nothing stored
def test_console_register_refuses_a_private_destination(…)                      # http://127.0.0.1/hook with allow_private False → 422 destination_refused
def test_console_revoke_is_idempotent(…)                                        # 204 twice; gone from list; one console.callback_revoke event
def test_console_callbacks_without_delivery_is_503(…)                           # deliverer None → GET and POST 503 DeliveryError body
def test_console_callback_routes_refuse_a_bearer_key(…)
def test_console_callback_writes_need_the_console_header(…)
def test_console_callbacks_are_tenant_scoped(…)                                 # acme's callback not listed for globex; DELETE via globex path leaves acme's active
```

  Build the deliverer the way `tests/contract` tests of `/v1/callbacks` do; grep for
  `SecretBook` in `tests/`. Inject a `transport`, so no network is touched.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates and the full suite once.**
- [ ] **Step 5: Commit** with the message `feat(api): console callbacks — list, register, revoke per tenant`.

---

### Task 2 (frontend lane): model for parsers, tenants navigation and callbacks

**Files:**
- Create: `ui/src/console/model/callbacks.ts`, `ui/src/console/model/navigation.ts`, and their
  tests in `ui/test/console/`.
- Modify: `ui/src/console/model/submit.ts`. `ParserOption` gains `unavailableReason: string | null`
  and
  `capabilities: { text: boolean; geometry: boolean; tables: boolean; handwriting: boolean }`;
  `toParsers` reads both. Also export
  `parserStatus(p: ParserOption): "unavailable" | "not responding" | "healthy" | "unknown"`.
- Modify: `ui/src/console/model/client.ts`. Add the intents:
  - `list-callbacks {tenantId}` → `GET /v1/console/tenants/{t}/callbacks`;
  - `register-callback {tenantId}` → `POST` to the same path, with a body;
  - `revoke-callback {tenantId; callbackId}` → `DELETE /v1/console/tenants/{t}/callbacks/{id}`.

  Update `ALLOWED` and `allIntents`.
- Modify: `ui/src/console/model/failure.ts`. A string `error: "destination_refused"` with a string
  `detail` shows the detail. (Check whether the existing `detail` preference already does this.
  If it does, just add the test.)

**Interfaces:**
- **In `callbacks.ts`:**
  - `interface CallbackRecord { callback_id: string; tenant_id: string; url: string; created_at: string }`
  - `toCallbacks(body: unknown): CallbackRecord[]`
  - `registerBody(url: string, secret: string): string`. It trims both and gives the JSON
    `{url, secret}`.
  - `canRegister(url: string, secret: string, sending: boolean): boolean`. True when both are
    non-empty after trimming and nothing is sending.
  - `canRevoke(sending: boolean): boolean`
- **In `navigation.ts`:**
  - `type Area = "overview" | "runs" | "submit" | "parsers" | "tenants" | "credentials" | "callbacks" | "erasure"`.
    Shell's own `Area` type moves here.
  - `interface Jump { area: Area; tenant: string | null }`
  - `jumpTo(area: Area, tenant: string): Jump`
  - `TENANT_JUMPS: { area: Area; label: string }[] = [{area:"runs",label:"Runs"},{area:"credentials",label:"Credentials"},{area:"callbacks",label:"Callbacks"},{area:"erasure",label:"Erase…"}]`
- **In `submit.ts`:** `parserStatus` as above, in this order: not `available` gives
  "unavailable"; `healthy === false` gives "not responding"; `healthy === true` gives "healthy";
  otherwise "unknown".
- **Parsers screen policy,** in the model as well: `PARSERS_POLL = false` and
  `parsersRefreshLabel = "Refresh"`. These are trivial, but `test_parsers_never_poll` pins them so
  nobody wires polling to a live-probing route.

- [ ] **Step 1: Write the failing tests.** Key assertions:

```ts
test("destination refused reads as a sentence", () => { /* 422 {error:"destination_refused", detail:"…private address…"} → describe gives the detail */ });
test("unconfigured delivery is unavailable", () => { /* 503 {error:{class:"DeliveryError", message:"this deployment delivers no callbacks; set …"}} → kind unavailable, message kept */ });
test("tenant jump presets the area", () => { assert.deepEqual(jumpTo("erasure", "acme"), { area: "erasure", tenant: "acme" }); });
test("parsers never poll", () => { assert.equal(PARSERS_POLL, false); });
test("cannot revoke while sending", () => { assert.equal(canRevoke(true), false); assert.equal(canRevoke(false), true); });
test("parser status order", () => { /* unavailable beats unhealthy; healthy true → "healthy"; absent → "unknown" */ });
test("callback intents address the tenant's callbacks", () => {
  assert.deepEqual(requestFor({ type: "revoke-callback", tenantId: "acme", callbackId: "c 1" }), { method: "DELETE", path: "/v1/console/tenants/acme/callbacks/c%201", query: {}, hasBody: false });
});
test("callbacks list never carries a secret", () => { /* toCallbacks drops unknown keys: output objects have exactly the 4 fields */ });
```

- [ ] **Step 2: Run them and confirm they fail.** **Step 3: Implement.** **Step 4: Run the
  frontend gates.** **Step 5: Commit** with the message `feat(ui): model for parsers, tenant jumps and callbacks`.

---

### Task 3 (frontend lane): Parsers, Tenants and Callbacks areas

**Files:**
- Create: `ui/src/console/components/ParsersArea.tsx`, `TenantsArea.tsx`, `CallbacksArea.tsx`.
- Modify: `ui/src/console/components/Shell.tsx`.
  - Sidebar Monitoring: Overview, Runs, Submit OCR, **Parsers**.
  - Sidebar Admin: **Tenants**, Credentials, **Callbacks**, Erasure.
  - Shell holds `Jump`. It passes `presetTenant` to Runs, Credentials, Callbacks and Erasure.
- Modify: `RunArea.tsx` (apply `presetTenant` as the tenant filter on mount), `CredentialArea.tsx`
  (preset tenant, then auto-Show), `EraseArea.tsx` (prefill the tenant id, with the retype still
  required).
- Modify: `docs/concepts/console.md`. Add a short section per screen.

**Interfaces:**
- **ParsersArea**
  - An Astryx table: Parser, Status (a badge from `parserStatus`, with `unavailableReason` in muted
    text), Media types, Languages, Capabilities (the true ones, comma-joined).
  - Loading state, and a **Refresh** button. No polling.
- **TenantsArea**
  - A table of tenant ids from `list-tenants`.
  - Each row has `TENANT_JUMPS` buttons that call `onJump(jumpTo(area, tenant))`.
  - An empty state.
- **CallbacksArea**
  - A Tenant `Selector` (from `list-tenants`, preset when given), then a table: URL, created, and a
    Revoke action with inline confirm ("Revoke this callback?", then Confirm or Keep), disabled
    while sending.
  - A register form in a Card: URL and Secret `TextInput`s (the secret with `type="password"`,
    `autoComplete="off"`), and a Register button gated by `canRegister`.
  - After a successful register, clear the secret and refresh the list.
  - Failures show `describe(failure)` in a Banner. A 503 shows the unavailable sentence in place of
    the table.

- [ ] **Step 1: Implement.** **Step 2: Run the frontend gates.**
- [ ] **Step 3: Visual check.**
  - Run your own server from this worktree on :8766 against `docdoc_demo`, read-only apart from
    signing in.
  - Screenshot Parsers, Tenants, a jump to Credentials and one to Erasure, and Callbacks. Callbacks
    shows the 503 state, because the lane's server has no delivery secrets and no backend routes.
  - Read each PNG and iterate. Stop the server afterwards.
- [ ] **Step 4: Commit** with the message `feat(ui): Parsers, Tenants and Callbacks areas`.

---

### Task 4 (after merging both lanes): integration with delivery configured

- [ ] **Step 1: Merge the lanes.** Run all the gates.
- [ ] **Step 2: Restart the demo server on the merged code with delivery configured.** The
  controller does this: it writes a delivery secrets file under `/tmp` with one secret, sets
  `DOCDOC_DELIVERY_SECRETS_FILE`, and gives the secret to the Task 4 agent in its dispatch.
- [ ] **Step 3: Drive it with Playwright,** reading every screenshot.
  - **Parsers:** paddle and tesseract show healthy.
  - **Tenants:** jump to each area, and check the tenant is applied.
  - **Callbacks for `mmerp`:**
    - register `https://example.com/docdoc-hook` with the configured secret, and confirm it is
      listed;
    - try an unknown secret, and confirm the refusal sentence;
    - try `http://127.0.0.1/hook`, and confirm the refusal sentence;
    - revoke with confirm, and confirm it is gone.
  - Over HTTP, no response body or log line contains the secret.
- [ ] **Step 4: Commit any fixes** with the message `fix(console): <what>`.
