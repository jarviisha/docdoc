# Console: real URLs and a shared data layer

Date: 2026-10-23
Status: design, awaiting approval

## Why

The console's URL never changes. `Shell.tsx` holds the open area, the open run and the preset
tenant in React state, so the browser has one history entry for the whole console:

- **Back leaves the console** instead of returning to the previous screen.
- **Reload drops you on Overview**, wherever you were.
- **No screen can be linked.** An operator cannot send a colleague a run.

The owner's words: the console "will grow a lot", and this is the moment to lay "the best
foundation" for it. ADR-0024 already names where it is going: Schemas and Providers (both shown as
"soon" in the sidebar), then deeper system administration, toward something like Unstract. So
this is not only a Back-button fix. It sets how every future screen gets a URL and how every
screen loads and refreshes its data, so the next ten screens are written one way.

## Goals

1. **Every screen has a real path under `/console`**, and the state worth sharing is in the URL.
   Back, Forward, reload, middle-click and a pasted link all land on the same screen.
2. **Typed routes and search params.** A wrong param name or a link to a route that doesn't exist
   is a type error at build time.
3. **One way to load data** for every area: caching, refetch after a mutation, polling, and the
   session-ended handling that today lives in `Shell.request`, all in one place.
4. **A documented recipe for adding a screen** (route, model, component), so Schemas and
   Providers follow it rather than invent their own.
5. **An ADR (0028)** recording these choices, alongside ADR-0024.

## Non-goals

- Building Schemas, Providers or any new screen. They come later, on this foundation.
- Changing the look. ADR-0024's style and the `console-ui` skill stay as they are.
- Moving the console off `/console`. The owner considered serving it at a subdomain root and
  decided to **keep `/console`**.
- Changing the API. No `/v1` route changes.

## Anti-goals

- **An admin framework** (Refine, react-admin). Rejected in ADR-0024; still rejected.
- **Decisions drifting into components.** `check-model-boundary.mjs` makes `console/model/` the
  only tested surface. A router or query library must not become the place where "what does this
  URL mean" or "when does this poll" is decided untested.
- **Anything at rest in the browser.** `check-no-persistence.mjs` (specs/011 FR-007). Neither
  library may be configured to write to `localStorage`, `sessionStorage`, IndexedDB or cookies.
  The guard only scans `src/`, so this is a configuration duty, not something it will catch.
- **A second `fetch` site.** Every request still goes through `src/transport.ts` via
  `requestFor(intent)`.

## Decisions (agreed with the owner)

- **Router: TanStack Router** (`@tanstack/react-router`, MIT). Chosen for typed, validated search
  params and nested layouts. React Router was the alternative; its search params are weaker.
- **Data: TanStack Query** (`@tanstack/react-query`, MIT). Caching, invalidation after mutations,
  interval refetch. Replaces `usePolling.ts` and the hand-rolled load/poll in each area.
- **Real paths, not `#` fragments.** The owner found hash URLs unprofessional.
- **Stays at `/console`.**

## The design

### URLs

| Path | Screen | Search params |
|------|--------|---------------|
| `/console/` | Overview | — |
| `/console/runs` | Run list | `tenant?`, `status?`, `kind?` |
| `/console/runs/$runId` | Run detail | — |
| `/console/submit` | Submit | — |
| `/console/quality` | Quality | `tenant?` |
| `/console/tenants` | Tenants | — |
| `/console/credentials` | Credentials | `tenant?` |
| `/console/callbacks` | Callbacks | `tenant?` |
| `/console/parsers` | Parsers | — |
| `/console/erasure` | Erase | `tenant?` |
| anything else under `/console/` | "Not found" inside the shell, with a link to Overview | — |

The tenants screen's jumps (`TENANT_JUMPS`) become links to these paths with `?tenant=`. The
`visit` counter that forces a re-mount on every jump goes away: a different URL is a different
screen state.

**Search params are the screen's live state, not only its starting point (owner's decision).**
Changing a filter on screen updates the URL, so a copied link shows exactly what the operator
sees. Concretely:

- Each area's tenant/filter state moves out of local `useState` and into the route's search
  params (`RunArea`'s `state`/`tenantInput`, `CredentialArea`'s `listing`, `CallbacksArea`'s
  `listing`, `QualityArea`'s tenant). The model's listing functions (`forTenant`, `filtered`,
  `byKind`, `polled`, …) are reshaped around that as needed.
- **A filter change pushes a history entry**, so Back undoes it. Selects push on change; a text
  filter pushes only on Enter or Apply (the `console-ui` rule), never per keystroke.
- **Credentials:** `?tenant=` is the listed tenant. The issue form's tenant field only defaults
  to it. *(My call.)*
- **Erase:** `?tenant=` pre-fills the tenant target, as a Tenants jump does today. A pasted link
  cannot erase anything on its own: the confirm dialog is still required. The document-erase
  fields are not in the URL. *(My call.)*

**Builder's call:** whether the run list's cursor goes into the URL. My default is no (filters
yes, cursor no), because cursors are opaque and short-lived; reloading returns to page one with
the same filters.

**Builder's call:** whether Quality's open evaluation gets its own path
(`/console/quality/$evaluationId`) if the area has such a sub-view. Follow the run list/detail
pattern if it does.

### Server

`_mount_console` in `src/docdoc/api/app.py` mounts `StaticFiles(html=True)`, so today
`/console/runs` is a 404. Change it so:

- a path under `/console/` that names a real built file is served as now;
- any other path under `/console/` returns `index.html` (200), so the client router can take it;
- a missing **asset** (e.g. `/console/assets/missing.js`) still 404s, so a broken build doesn't
  come back as HTML with a 200. The exact rule ("has a file extension" or "under `assets/`") is
  the builder's call;
- the credential exemption is unchanged: `/console` and `/console/...` only, `/consoleroom` still
  401 (`test_console_mount.py`).

New contract tests: a deep path returns the shell; a missing asset 404s; the existing mount tests
keep passing. The 501 "not installed" branch already covers `/console/{path}`.

Vite: `base` stays `/console/`. The dev server already falls back to `index.html`.

### Routing layer

- **Code-based route tree**, not the file-based Vite plugin: no generated file to commit or
  explain, and the type safety is the same. Lives in `ui/src/console/routes.tsx` (or a small
  `routes/` folder if it grows); builder's call on exact layout.
- **`basepath: "/console"`.**
- **Search-param validation and URL meaning are model code.** Each route's `validateSearch` calls
  a plain function in `console/model/` (e.g. `runsSearch(raw: Record<string, unknown>)` returns
  `{ tenant?: string; status?: RunStatus; kind?: RunKind }`, dropping unknown or invalid values).
  These functions import nothing from TanStack, so the model boundary holds and `node --test`
  covers them. `navigation.ts` keeps the area list and the tenant jumps, now expressed as paths.
- **Root layout = today's signed-in frame**: sidebar, top bar with the "Group / Area" trail, the
  notice region, then `<Outlet />`. Sidebar entries become `<Link>`s (real anchors, so
  middle-click opens a new tab, and `aria-current="page"` comes from the active match).
- **Session gate stays outside the router's screens.** `Shell` still asks `who-am-i` first and
  shows the sign-in form until there is a session. The URL is untouched while signed out, so after
  signing in (or after "Your session ended") the operator lands on the screen they were on, not
  Overview. This replaces `signIn`'s `setArea("overview")`.
- **No scroll restoration, no lazy route components, no devtools in the build.** Checked against
  `@tanstack/router-core` 1.171 / `@tanstack/react-router` 1.170: the only `sessionStorage`
  writes are scroll restoration (on `pagehide`, only when enabled) and `lazyRouteComponent`
  (only on a failed chunk load). With both unused, nothing is written. The module does *read*
  `sessionStorage` once at load; that stores nothing, so FR-007 holds. `@tanstack/query-core`
  touches no storage. Re-check on upgrades.

### Data layer

- One `QueryClient`, created in `main.tsx`, no persister.
- **`request` stays the only way out.** The query functions call the same `request(intent)` that
  areas use today, so session-ended handling still happens in one place (FR-010). A failed answer
  becomes a thrown `Failure` for Query, and comes back to the component as `error`.
- **Query keys come from intents.** A model function (e.g. `keyOf(intent)`) builds them, so
  "which mutation invalidates which list" is a model decision with a test, e.g. revoking a
  credential invalidates that tenant's `list-credentials`.
- **Polling uses `refetchInterval`**, with the interval decided by model functions that already
  exist: `autoRefreshes(state)` for the run list, `detailPolls(run)` for run detail, `POLL_MS` for
  overview. Query already doesn't overlap requests and stops refetching in a hidden tab
  (`refetchIntervalInBackground: false`) and refetches on focus, which is what
  `usePolling` + `model/polling.ts` do by hand. The now-unused helpers in `polling.ts` (`nextPoll`,
  `onVisible`, `startsNow`, `isNewer`) and their tests are deleted with `usePolling.ts`.
- **"Never blank the table on refresh"** (console-ui skill) comes from showing cached data while
  refetching (`placeholderData: keepPreviousData` for paged/filtered lists).
- `retry` off, or one retry for network errors only: a 4xx is an answer, not a blip. Builder's
  call within that.

### Migration

The doc doesn't require a big-bang rewrite. Suggested order, each step leaving `make ui` green:

1. Server fallback + tests.
2. Add both libraries, router with the root layout and every area mounted as a route, URL params
   wired in place of `presetTenant`/`openRunId`/`visit`, and filters moved into search params.
   Areas still load data their old way.
3. Move areas to Query one at a time, Runs first (it has list, detail, polling and a mutation, so
   it proves every pattern), then the rest. Delete `usePolling.ts` when the last user is gone.
4. ADR-0028, update `console-ui` skill's "Where things live" with the add-a-screen recipe, and
   `ui/src/console/components/README.md` / `model/README.md` where they describe navigation or
   polling.

`check-licenses.mjs` must pass with the two new dependencies (both MIT).

### The add-a-screen recipe (to go in the skill)

1. Add the intent(s) to `model/client.ts` and their request plans; test them.
2. Add the screen's decisions (search params, what polls, what a mutation invalidates) to a model
   file; test them.
3. Add the route to the route tree with `validateSearch` from step 2.
4. Write the component: `useQuery`/`useMutation` around `request`, render the four states.
5. Add the sidebar entry (or remove its "soon").

## Constraints

- All existing guards stay green: model boundary, no persistence, console boundary (the five
  review-platform nouns: route names count, so no `/console/queue`), a11y, licences.
- `make ui` and `make test` green at the end.
- No change to `/v1`, to the login flow's server side, or to the console's styling.

## Left to the builder

File layout of the route tree; the exact missing-asset rule on the server; whether the cursor is
in the URL; Quality's sub-path; retry policy within the bound above; naming of the new model
functions.
