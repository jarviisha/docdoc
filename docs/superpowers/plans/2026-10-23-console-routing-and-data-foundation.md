# Console Routing and Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every console screen a real URL under `/console` (Back, Forward, reload and links all work), with filters as live URL state, and move every screen's data loading onto one shared layer.

**Architecture:** The server returns the console shell for any non-asset path under `/console/`. In the browser, a code-based TanStack Router tree (`basepath: "/console"`) replaces `Shell`'s `area`/`openRunId`/`presetTenant`/`visit` state. TanStack Query, whose query functions call the existing `request(intent)`, replaces each area's hand-rolled load and poll code and `usePolling.ts`. Every decision (search-param parsing, area↔path, query keys, invalidation, what polls) is plain TypeScript in `ui/src/console/model/`, tested with `node --test`.

**Tech Stack:** React 19, TypeScript 7, Vite 8, `@tanstack/react-router`, `@tanstack/react-query`, FastAPI `StaticFiles`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-console-routing-and-data-foundation-design.md`

## Global Constraints

- The console stays at `/console`. Vite `base` stays `"/console/"`. The router uses `basepath: "/console"`.
- No `/v1` API change. No styling change (ADR-0024, `console-ui` skill).
- Nothing at rest in the browser (specs/011 FR-007). No router scroll restoration, no `lazyRouteComponent`, no Query persister, no devtools.
- Every request goes through `request(intent)` → `requestFor` → `src/transport.ts`. No new `fetch` site.
- `console/model/` imports nothing from React, TanStack, Radix or components (`check-model-boundary.mjs`).
- No route or identifier may use the review-platform nouns (`check-console-boundary.mjs`): no `queue`, `assign…`, `workload`, `capacity`, `balance`, etc.
- Filter changes **push** a history entry. Selects push on change; text filters push only on Enter or Apply.
- Run-list cursor is **not** in the URL; changing any filter returns to page one.
- After sign-in (including after "Your session ended"), the operator stays on the current URL rather than being sent to Overview.
- New dependencies must get a row in `ui/LICENSES.md` (`check-licenses.mjs`). Both are MIT.
- Done means `make ui` and `make test` are green.

## Review Focus

1. **Another operator signs in on the same tab after sign-out or session end** and must not see the previous operator's cached lists. `queryClient.clear()` on every transition to signed-out (Task 3, manual check step).
2. **Hand-edited or stale URLs** (`?status=bogus`, `?tenant=%20%20`, `?kind=`, repeated params) must open the screen with those params dropped rather than crash or send them to the API (Task 2 tests).
3. **A run id containing a dot** (`/console/runs/abc.def`) must still get the shell, while `/console/assets/missing.js` still 404s (Task 1 tests).
4. **Deep link while signed out**: opening `/console/runs/<id>` with no session shows sign-in, then that run after signing in (Task 3, manual check step).
5. **Back after a filter change** restores the previous filters *and* the list matching them, with no flash of an empty table (Task 4, manual check step; `keepPreviousData`).

---

### Task 1: Server returns the shell for deep paths

**Files:**
- Modify: `src/docdoc/api/app.py` (`_mount_console`, ~line 788–853)
- Test: `tests/contract/test_console_mount.py`

**Interfaces:**
- Produces: `GET /console/<anything not under assets/>` → `index.html`, 200. `GET /console/assets/<missing>` → 404.

- [ ] **Step 1: Write the failing tests** in `tests/contract/test_console_mount.py`. Use a fixture that builds assets the way `test_http_ui_endpoints.py` does (`index.html` plus `assets/app.js` in `tmp_path`, `monkeypatch.setattr(ui_module, "chosen_assets", lambda: ("test", tmp_path))`) on an **authenticated** deployment like the existing `client` fixture.

```python
def test_a_deep_console_path_gets_the_shell(built_client):
    for path in ("/console/runs", "/console/runs/abc", "/console/runs/abc.def", "/console/credentials?tenant=acme"):
        response = built_client.get(path)
        assert response.status_code == 200, path
        assert "<div id=root>" in response.text, path

def test_a_missing_asset_is_still_a_404(built_client):
    assert built_client.get("/console/assets/missing.js").status_code == 404

def test_a_real_asset_is_served(built_client):
    assert built_client.get("/console/assets/app.js").status_code == 200

def test_the_deep_path_exemption_does_not_widen(built_client):
    assert built_client.get("/consoleroom").status_code == 401
```

- [ ] **Step 2: Run them, expect the deep-path test to fail with 404**

Run: `uv run pytest -n0 tests/contract/test_console_mount.py`

- [ ] **Step 3: Implement** a private `StaticFiles` subclass in `app.py` (e.g. `_ConsoleFiles`) whose `get_response(path, scope)` falls back to `index.html` when the parent raises a 404 **and** `path` does not start with `assets/`. Mount it in place of `StaticFiles(directory=assets, html=True)`. Update the comment above the mount to say why (client-side routes; ADR-0028).

- [ ] **Step 4: Run the file again, expect PASS.** Also run `uv run pytest -n0 tests/contract/test_http_ui_endpoints.py tests/contract/test_console_session.py`.

- [ ] **Step 5: Commit** `feat(api): serve the console shell for client-side routes`

---

### Task 2: Model — paths, areas and search params

**Files:**
- Modify: `ui/src/console/model/navigation.ts`
- Create: `ui/src/console/model/search.ts`
- Test: `ui/test/console/navigation.test.ts`, `ui/test/console/search.test.ts`

**Interfaces:**
- Produces (navigation.ts):
  - `type Area` (unchanged members).
  - `const AREA_PATHS: { readonly [A in Area]: AreaPath }` where `type AreaPath = "/" | "/runs" | "/submit" | "/quality" | "/tenants" | "/credentials" | "/callbacks" | "/parsers" | "/erasure"`.
  - `areaOf(pathname: string): Area | null`. Accepts a pathname with or without the `/console` prefix and with or without a trailing slash. `/runs/x` → `"runs"`.
  - `interface Jump { to: AreaPath; search: { tenant: string } }`. `jumpTo(area: Area, tenant: string): Jump`.
  - `TENANT_JUMPS` unchanged.
- Produces (search.ts):
  - `interface RunsSearch { tenant?: string; status?: RunStatus; kind?: RunKind }`, `runsSearch(raw: Record<string, unknown>): RunsSearch`.
  - `interface TenantSearch { tenant?: string }`, `tenantSearch(raw): TenantSearch` (credentials, callbacks, erasure).
  - `interface QualitySearch { tenant?: string; schema?: string; evaluation?: string }`, `qualitySearch(raw): QualitySearch`.
  - Rule for all three: strings are trimmed; empty, non-string or unknown-enum values are **omitted** (no `undefined` keys, `exactOptionalPropertyTypes` is on); unknown keys are dropped.

- [ ] **Step 1: Write the failing tests.**

```ts
// search.test.ts
test("runs search keeps valid filters", () => {
  assert.deepEqual(runsSearch({ tenant: " acme ", status: "failed", kind: "ocr" }), { tenant: "acme", status: "failed", kind: "ocr" });
});
test("runs search drops bad values and unknown keys", () => {
  assert.deepEqual(runsSearch({ tenant: "  ", status: "bogus", kind: "", page: 3 }), {});
  assert.deepEqual(runsSearch({ tenant: ["a", "b"], status: 5 }), {});
});
test("tenant search", () => {
  assert.deepEqual(tenantSearch({ tenant: "acme" }), { tenant: "acme" });
  assert.deepEqual(tenantSearch({}), {});
});
test("quality search", () => {
  assert.deepEqual(qualitySearch({ tenant: "acme", schema: "invoice@1", evaluation: "ev1", x: 1 }), { tenant: "acme", schema: "invoice@1", evaluation: "ev1" });
});

// navigation.test.ts (replace the jumpTo tests)
test("tenant jump goes to the area's path with the tenant", () => {
  assert.deepEqual(jumpTo("erasure", "acme"), { to: "/erasure", search: { tenant: "acme" } });
});
test("every area has a path and areaOf inverts it", () => {
  for (const [area, path] of Object.entries(AREA_PATHS)) assert.equal(areaOf(path), area);
});
test("areaOf reads nested, prefixed and unknown paths", () => {
  assert.equal(areaOf("/runs/abc"), "runs");
  assert.equal(areaOf("/console/runs/abc"), "runs");
  assert.equal(areaOf("/console/"), "overview");
  assert.equal(areaOf("/console"), "overview");
  assert.equal(areaOf("/nope"), null);
});
```

- [ ] **Step 2: Run, expect FAIL** (`cd ui && node --test test/console/search.test.ts test/console/navigation.test.ts`).
- [ ] **Step 3: Implement** the interfaces above. `RunStatus`/`RUN_STATUSES` come from `client.ts`; `RunKind` from `runs.ts`.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(console): model paths, areas and search params`

---

### Task 3: Router, Query client and the shell as a layout

Every area becomes a route. Areas still load data their own way. URL params are passed in as their starting values (the old `presetTenant`), and opening a run navigates.

**Files:**
- Modify: `ui/package.json`, `ui/package-lock.json`, `ui/LICENSES.md`
- Create: `ui/src/console/routes.tsx`, `ui/src/console/components/RequestContext.tsx`
- Create: `ui/src/console/model/queries.ts`, `ui/test/console/queries.test.ts`
- Modify: `ui/src/console/main.tsx`, `ui/src/console/components/Shell.tsx`, `TenantsArea.tsx`, `RunArea.tsx`, `OverviewArea.tsx`, `SubmitArea.tsx`, `QualityArea.tsx`, `CredentialArea.tsx`, `CallbacksArea.tsx`, `EraseArea.tsx`

**Interfaces:**
- Consumes: Task 2's `AREA_PATHS`, `areaOf`, `jumpTo`, `runsSearch`, `tenantSearch`, `qualitySearch`.
- Produces (model/queries.ts, pure):
  - `keyOf(intent: Intent): readonly [Intent["type"], Intent]`.
  - `invalidatedBy(intent: Intent): readonly (readonly [Intent["type"]])[] | "all"`. It returns the key prefixes a successful mutation makes stale:
    - `cancel-run` → `run-detail`, `run-delivery`, `list-runs`, `overview`
    - `submit-ocr`, `submit-extraction` → `list-runs`, `overview`
    - `issue-credential`, `revoke-credential` → `list-credentials`
    - `register-callback`, `revoke-callback` → `list-callbacks`
    - `record-check` → `run-checks`
    - `start-evaluation` → `list-evaluations`
    - `erase-tenant`, `erase-document` → `"all"`
    - any read → `[]`
- Produces (RequestContext.tsx): `RequestProvider` and `useRequest(): { request: Request; requestImage: ImageRequest }`. `Request`/`ImageRequest` are the types areas already declare; export them from here and delete the per-area copies as areas are touched.
- Produces (routes.tsx): `router` (from `createRouter({ routeTree, basepath: "/console", scrollRestoration: false })`) plus `declare module "@tanstack/react-router" { interface Register { router: typeof router } }`. Routes: `/`, `/runs` (validateSearch `runsSearch`), `/runs/$runId`, `/submit`, `/quality` (`qualitySearch`), `/tenants`, `/credentials` (`tenantSearch`), `/callbacks` (`tenantSearch`), `/parsers`, `/erasure` (`tenantSearch`). There is also a not-found component that says "This page does not exist." with a link to Overview.

- [ ] **Step 1: Write the failing `queries.test.ts`**. It asserts `keyOf({type:"run-detail",runId:"r1"})` deep-equals `["run-detail", {type:"run-detail",runId:"r1"}]`, plus every row of the `invalidatedBy` table above, including `invalidatedBy({type:"list-runs"})` → `[]` and `invalidatedBy({type:"erase-tenant",tenantId:"t"})` → `"all"`.
- [ ] **Step 2: Run, expect FAIL. Implement `queries.ts`, run, expect PASS.**
- [ ] **Step 3: Add dependencies** with `cd ui && npm install @tanstack/react-router @tanstack/react-query` and add their `LICENSES.md` rows. Run `npx tsc --noEmit` on a minimal `routes.tsx` first. If TypeScript 7 rejects the router's types, stop and report: that would be a stack decision for the owner.
- [ ] **Step 4: Wire it up.**
  - `main.tsx` creates one `QueryClient` with `defaultOptions: { queries: { retry: false, refetchIntervalInBackground: false } }` and wraps `<Shell />` in `QueryClientProvider`.
  - `Shell` keeps the session gate. When signed in, it renders `<RequestProvider><RouterProvider router={router} /></RequestProvider>`. The sidebar, top bar and notice region move into the root route's component with an `<Outlet />`. That component gets `notice`/`signOut`/`username` through `RouterProvider`'s `context` or through a second small context; the builder picks.
  - Sidebar entries become `<Link to={AREA_PATHS[name]}>` with `aria-current="page"` when `areaOf(location.pathname) === name`.
  - The top-bar trail uses `areaOf`. It shows nothing for not-found.
  - Remove `area`, `openRunId`, `presetTenant`, `visit` and `jump` from `Shell`. `signIn` no longer resets the area.
  - **On every transition to signed-out (sign-out, and `unauthenticated` in `request`/`requestImage`), call `queryClient.clear()`.**
  - Areas: `onOpenRun(id)` becomes navigation to `/runs/$runId`. The Runs route renders the list. The `/runs/$runId` route renders `RunDetail` (export it from `RunArea.tsx`), and its "← Runs" control is a `<Link to="/runs">`. `presetTenant` comes from `Route.useSearch().tenant ?? null`. `TenantsArea` jumps become `<Link {...jumpTo(area, tenant)}>` styled with `buttonVariants`. The `"submit"` primary action on Runs links to `/submit`.
  - Each route component passes `key={JSON.stringify(search)}` to its area, so a URL change re-applies the preset the way `visit` did.
- [ ] **Step 5: Verify.** Run `make ui`, expected green. Then `make dev` and check in the browser at `http://localhost:5173/console/`:
  - each sidebar entry changes the URL, and Back returns to the previous screen;
  - reload keeps the screen;
  - `/console/runs/<real id>` opens that run;
  - `/console/nope` shows not-found inside the shell;
  - a Tenants jump lands on `/console/credentials?tenant=<t>` with the list shown;
  - **Review Focus 4:** in a private window, open `/console/runs/<id>`, sign in, and the run is shown;
  - **Review Focus 1:** sign out, sign in as a second console account, and no list from the first appears before its own loads.
- [ ] **Step 6: Commit** `feat(console): real URLs with TanStack Router; shell as layout`

---

### Task 4: Runs on Query, with live filters

**Files:**
- Modify: `ui/src/console/model/runs.ts`, `ui/test/console/runs.test.ts`
- Create: `ui/src/console/components/useIntent.ts`
- Modify: `ui/src/console/components/RunArea.tsx`, `ui/src/console/routes.tsx`

**Interfaces:**
- Consumes: `RunsSearch` (Task 2), `keyOf`/`invalidatedBy` (Task 3), `useRequest` (Task 3).
- Produces (components/useIntent.ts):
  - `useIntent<T>(intent: Intent | null, options?: { refetchInterval?: number | false | ((data: T | undefined) => number | false); select?: (body: unknown) => T; keepPrevious?: boolean })`. It wraps `useQuery` with `queryKey: keyOf(intent)` and `enabled: intent !== null`, and its queryFn calls `request(intent)`, **throwing the `Failure`** on `ok: false`. The error type is `Failure`, registered once through `declare module "@tanstack/react-query" { interface Register { defaultError: Failure } }`.
  - `useAct()` returns `(intent: Intent, body?: BodyInit) => Promise<Answer>`. It calls `request` and, on success, invalidates `invalidatedBy(intent)` (`"all"` → `queryClient.invalidateQueries()`).
- Produces (runs.ts, replacing `ListingState`, `Listing`, `initialListing`, `filtered`, `byKind`, `forTenant`, `reloaded`, `loaded`, `failed`, `polled`):
  - `type RunFilters = RunsSearch`.
  - `listIntent(filters: RunFilters, cursor: string | undefined): Intent`, moved from `RunArea.tsx`.
  - Pager over a cursor stack: `canGoBack(stack: string[]): boolean`, `forward(stack: string[], nextCursor: string | null): string[]`, `back(stack: string[]): string[]`, `currentCursor(stack: string[]): string | undefined`.
  - `autoRefreshes(stack: string[]): boolean`. It returns true on page one only.
  - `emptyNotice(filters: RunFilters): string`. The wording is unchanged.
  - Kept as they are: `kindLabel`, `isCancellable`, `toRecord`, `detailPolls`, `showsResult`, `showsFields`, `cancelNotice`, `refetchAfterCancel`, `resultNotice`, `toFailure`, `toPage`, `timeline`, `reasonLabel`.

- [ ] **Step 1: Rewrite the listing tests in `runs.test.ts`** for the new signatures, keeping the existing behaviours:
  - `forward([], "c1")` → `["c1"]`, and `forward(["c1"], null)` → `["c1"]`;
  - `back(["c1","c2"])` → `["c1"]`, and `back([])` → `[]`;
  - `autoRefreshes([])` is true and `autoRefreshes(["c1"])` is false;
  - `listIntent({tenant:"acme",status:"failed"}, "c1")` deep-equals `{type:"list-runs",tenant:"acme",status:"failed",cursor:"c1"}`, and `listIntent({}, undefined)` deep-equals `{type:"list-runs"}`;
  - the existing `emptyNotice` sentences, now taking filters.

  Delete the tests for removed functions.
- [ ] **Step 2: Run, expect FAIL. Implement, run, expect PASS.**
- [ ] **Step 3: Rewrite the list half of `RunArea.tsx`.**
  - Filters come from `Route.useSearch()`. Kind and State selects call `navigate({ search: (prev) => ({ ...prev, kind }) })`, which pushes (no `replace`). The tenant text field pushes on Enter or Apply.
  - The cursor stack is local `useState<string[]>`. The route component keys `RunArea` by the filters (drop Task 3's whole-search key here), so a filter change resets to page one.
  - The page comes from `useIntent(listIntent(filters, currentCursor(stack)), { select: toPage, keepPrevious: true, refetchInterval: autoRefreshes(stack) ? POLL_MS : false })`.
  - Refresh calls `refetch()`.
  - A background refetch error shows the existing "could not be refreshed" warning while rows stay. A first-load error shows the existing destructive alert.
  - The `LiveStatStrip` overview fetch also moves to `useIntent`.
- [ ] **Step 4: Rewrite `RunDetail`.**
  - `run-detail` uses `useIntent` with `refetchInterval: (run) => detailPolls(run) ? POLL_MS : false` and `select: toRecord`.
  - `run-delivery`, `run-result` (enabled when `showsResult`) and `run-checks` (enabled when `showsFields`) each use `useIntent`.
  - Cancel and `record-check` go through `useAct()`.
  - Remove `reads`/`checkReads`, since invalidation replaces them. A `410` (`failure.kind === "removed"`) still shows the tombstone and stops polling.
  - Downloads stay imperative through `request`.
- [ ] **Step 5: Verify.** Run `make ui`, expected green. Then `make dev`:
  - change State to failed, then Kind to OCR, then press Back twice. Each Back restores the previous filters and their rows with no blank table (**Review Focus 5**);
  - copy the URL into a new tab and the same list shows;
  - Next then Back (browser) leaves the console screen's filters intact;
  - a running run's detail updates by itself and stops once it finishes;
  - cancel shows the new state;
  - hiding the tab pauses polling (Network panel).
- [ ] **Step 6: Commit** `feat(console): runs on TanStack Query with filters in the URL`

---

### Task 5: Credentials, Callbacks, Erase — tenant as live URL state

**Files:**
- Modify: `ui/src/console/model/credentials.ts`, `ui/src/console/model/callbacks.ts` and their tests
- Modify: `ui/src/console/components/CredentialArea.tsx`, `CallbacksArea.tsx`, `EraseArea.tsx`

**Interfaces:**
- Consumes: `tenantSearch`, `useIntent`, `useAct`.
- Model: remove the listing-state parts that Query now owns. These are the "late answer for a tenant no longer chosen" logic in `listedFor`/`listFailedFor`, `showing` and `presetCredentials`, since Query keys by tenant. Keep the decisions:
  - in `credentials.ts`: `needsWarning`/`warned`/`withDisclosure`/`acknowledged`/`listingNote`/`scopesOf`/`isActive`;
  - in `callbacks.ts`: `unavailableNote`/`listProblem`, which now take `Failure | null` instead of a `CallbackListing`. Update their tests to the new argument.

- [ ] **Step 1: Update the model tests** for the changed signatures and delete the tests of removed functions. **Run, expect FAIL.**
- [ ] **Step 2: Implement the model changes, run, expect PASS.**
- [ ] **Step 3: Credentials.**
  - The listed tenant is `search.tenant`. The "Show" field pushes `?tenant=` on Enter or Show.
  - The issue form's tenant field initialises from `search.tenant` and is local afterwards.
  - The list uses `useIntent(tenant ? {type:"list-credentials",tenant} : null, { select: toRecords })`.
  - Issue and revoke go through `useAct()`.
- [ ] **Step 4: Callbacks.** The tenant select pushes `?tenant=`. The list uses `useIntent`, and the tenant options use `useIntent({type:"list-tenants"}, { select: toTenants })`. Register and revoke go through `useAct()`.
- [ ] **Step 5: Erase.** The tenant identifier initialises from `search.tenant`. Nothing else is in the URL. The erase call goes through `useAct()`, so on success it invalidates everything. The confirm dialog is unchanged.
- [ ] **Step 6: Verify.** Run `make ui`, expected green. Then `make dev`:
  - switch tenants on Callbacks, and Back restores the previous tenant's list;
  - revoking a credential updates the list;
  - `/console/erasure?tenant=x` pre-fills, and nothing happens until the dialog is confirmed.
- [ ] **Step 7: Commit** `feat(console): credentials, callbacks and erase read the tenant from the URL`

---

### Task 6: Quality — tenant, schema and evaluation in the URL

**Files:**
- Modify: `ui/src/console/model/quality.ts`, `ui/test/console/quality.test.ts`
- Modify: `ui/src/console/components/QualityArea.tsx`

**Interfaces:**
- Consumes: `qualitySearch`, `useIntent`, `useAct`.
- `shownEvaluation(list, chosen)` stays the decision. `chosen` is now `search.evaluation ?? null`. When the URL has no schema, the schema shown is the first answer's, as today, and it is **not** written to the URL until the operator picks one.
- Polling: `list-evaluations` uses `refetchInterval: (list) => list && stillRunning(list) ? POLL_MS : false`.

- [ ] **Step 1: Add a test** pinning the default-schema rule as a model function `shownSchema(answers: { value: string }[], fromUrl: string | undefined): string`. It returns `fromUrl` if it is among the answers, else the first answer's value, else `""`. **Run, expect FAIL.**
- [ ] **Step 2: Implement it, run, expect PASS.**
- [ ] **Step 3: Rewrite `QualityArea.tsx`.**
  - The tenant select, schema select and evaluation picks push their search param. Changing the tenant drops `schema` and `evaluation`, and changing the schema drops `evaluation`.
  - All reads go through `useIntent`. `start-evaluation` goes through `useAct()`.
  - Opening a run navigates to `/runs/$runId`.
- [ ] **Step 4: Verify.** Run `make ui`, expected green. In `make dev`, pick a tenant, schema and evaluation, reload, and the same view returns. Back walks back through the picks.
- [ ] **Step 5: Commit** `feat(console): quality's tenant, schema and evaluation in the URL`

---

### Task 7: Overview, Tenants, Parsers, Submit on Query; retire `usePolling`

**Files:**
- Modify: `ui/src/console/components/OverviewArea.tsx`, `TenantsArea.tsx`, `ParsersArea.tsx`, `SubmitArea.tsx`
- Delete: `ui/src/console/components/usePolling.ts`
- Modify: `ui/src/console/model/polling.ts` (keep `POLL_MS` only), `ui/test/console/polling.test.ts` (keep only what tests `POLL_MS`, or delete the file if nothing is left)

- [ ] **Step 1: Overview.**
  - Use `useIntent({type:"overview"}, { select: toOverview, refetchInterval: POLL_MS, keepPrevious: true })`. "Last refreshed" comes from `dataUpdatedAt`.
  - A `toOverview` throw surfaces through the query's error. Since `select` errors are not `Failure`s, wrap it so the alert shows the message as today.
  - Remove `sent`/`shown`/`isNewer`, because Query orders answers by key.
- [ ] **Step 2: Tenants, Parsers and Submit's three lists** (`list-tenants`, `list-parsers`, `list-schemas`) use `useIntent`. Parsers' Refresh calls `refetch()`. Submit sends through `useAct()`, so `list-runs` and `overview` are invalidated, then navigates to `/runs/$runId`.
- [ ] **Step 3: Delete `usePolling.ts`**, and remove `nextPoll`, `onVisible`, `startsNow` and `isNewer` with their tests. Grep for leftovers:

  ```
  grep -rn "usePolling\|nextPoll\|onVisible\|startsNow\|isNewer\|presetTenant\|openRunId" ui/src ui/test
  ```

  It should print nothing, except `presetTenant` if Task 5 kept the name as a local initial value.
- [ ] **Step 4: Verify.** Run `make ui`, expected green. In `make dev`, Overview refreshes every 5 s while visible and pauses while hidden. Submitting a file opens its run.
- [ ] **Step 5: Commit** `refactor(console): every area loads through TanStack Query; usePolling retired`

---

### Task 8: ADR-0028 and the add-a-screen recipe

**Files:**
- Create: `docs/adr/0028-console-routing-and-data-layer.md` (format of ADR-0024: Status Accepted, Date 2026-10-23, Relates to ADR-0019/0023/0024, Design → the spec path)
- Modify: `docs/adr/README.md` (index row)
- Modify: `.claude/skills/console-ui/SKILL.md`: under "Where things live", add the routes file and `useIntent`/`useAct`, plus a short "Adding a screen" section with the spec's five-step recipe
- Modify: `ui/src/console/components/README.md`, `ui/src/console/model/README.md`, wherever they describe navigation, presets or polling
- Modify: `CONTEXT.md` only if it defines console navigation terms that changed

- [ ] **Step 1: Write the ADR.**
  - Decisions: TanStack Router with code-based routes and `basepath /console`; TanStack Query through `request`; filters as live URL state that push history; cursor not in the URL; the server's shell fallback with the `assets/` rule.
  - Rejected alternatives: hash routing (owner: unprofessional), React Router (weaker typed search), file-based routing (generated file), serving at a subdomain root (owner kept `/console`).
  - Consequences: the storage audit result from the spec, to be re-checked on router upgrades; and `usePolling` being retired.
- [ ] **Step 2: Update the skill and READMEs.**
- [ ] **Step 3: Run the whole suite.** `make ui` and `make test`, both green.
- [ ] **Step 4: Commit** `docs: ADR-0028 console routing and data layer; add-a-screen recipe`
