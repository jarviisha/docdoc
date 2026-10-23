# ADR-0028: The Console Has Real URLs and Loads Through One Data Layer

- **Status**: Accepted
- **Date**: 2026-10-23
- **Relates to**: [ADR-0019](0019-operations-console-boundary.md) (the guards the new libraries must
  satisfy), [ADR-0023](0023-console-login-and-cross-tenant-operations.md) (the session and the
  screens), [ADR-0024](0024-console-ui-on-shadcn.md) (the look, and where the console is heading)
- **Design**: `docs/superpowers/specs/2026-10-23-console-routing-and-data-foundation-design.md`

## Context

The console's URL never changed. `Shell.tsx` held the open area, the open run and the preset
tenant in React state, so the browser had one history entry for the whole console. Back left the
console, a reload dropped the operator on Overview, and no screen could be linked to a colleague.

Each area also loaded its own data: its own load, its own poll through `usePolling`, its own
refresh after a mutation. The owner expects the console to grow a lot (Schemas, Providers, then
deeper administration), so how a screen gets a URL and how it loads data has to be settled once.

## Decision

1. **TanStack Router, code-based routes, `basepath: "/console"`.** The route tree is
   `ui/src/console/routes.tsx`. Every area has a path; `/console/runs/$runId` is the run detail;
   an unknown path is "not found" inside the frame. The root route is the signed-in frame (sidebar,
   top bar, notice region) around an `<Outlet />`. Sidebar entries are `<Link>`s, so
   `aria-current` and middle-click come from real anchors.
2. **The session gate stays outside the routes.** `Shell` asks `who-am-i` first and shows the
   sign-in form until there is a session. The URL is untouched meanwhile, so signing in returns
   the operator to the screen they were on.
3. **Search params are live screen state.** Changing a filter updates the URL and pushes a
   history entry, so Back undoes it and a copied link shows what the operator sees. A text filter
   pushes only on Enter or Apply. **The cursor is not in the URL**: cursors are opaque and
   short-lived, so a reload returns to page one with the same filters. Erase's `?tenant=` only
   pre-fills the target; the confirmation is still required.
4. **URL meaning is model code.** Each route's `validateSearch` calls a plain function in
   `model/search.ts` that drops unknown or invalid values and imports nothing from TanStack, so
   the model boundary holds and `node --test` covers it. `model/navigation.ts` holds the area
   list, `AREA_PATHS` and the tenant jumps.
5. **TanStack Query through `request`.** One `QueryClient` in `main.tsx`, in memory, no
   persister. `useIntent` (a read) and `useAct` (a write) in `components/useIntent.ts` wrap the
   shell's `request`, so session-ended handling still happens in one place and a refused answer
   is a thrown `Failure`. `model/queries.ts` decides the key (`keyOf(intent)`) and which keys a
   write makes stale (`invalidatedBy(intent)`); both are tested.
6. **Refetch policy.**
   - `retry` is off: a 4xx is an answer, not a blip.
   - Focus and reconnect refetch are off for the whole client. Only a polled query refetches when
     its tab is shown again; `useIntent` opts it back in.
   - `staleTime` is 0, so mounting a screen reads fresh.
   - A hidden tab does not poll (`refetchIntervalInBackground: false`).
   - `useIntent` hands a `refetchInterval` function `(data, error)`, so a `410` on a run that is
     gone stops its polling.
   - Lists keep the previous answer while a different one loads (`keepPrevious`), so a table is
     never blanked.
7. **The server answers the shell for client routes.** `_mount_console` in
   `src/docdoc/api/app.py` mounts a `StaticFiles` subclass: a path naming a built file is served;
   any other 404 under `/console/` returns `index.html` with 200; a miss under `assets/` stays a
   404, so a stale deploy is not answered with HTML where a script was expected. The credential
   exemption is unchanged (`/console` and `/console/...` only).
8. **`usePolling` and the helpers in `model/polling.ts` are retired.** Only `POLL_MS` remains.
   Interval refetch, no overlapping requests and pausing in a hidden tab come from Query.

## Alternatives rejected

- **Hash routing (`/console/#/runs`).** Works with the old static mount, but the owner found hash
  URLs unprofessional.
- **React Router.** Its search params are weaker: no typed, validated search per route.
- **File-based routing.** Same type safety, but a generated route file to commit and explain.
- **Serving the console at a subdomain root.** The owner considered it and kept `/console`.
- **Admin framework (Refine, react-admin).** Rejected in ADR-0024; unchanged.

## Consequences

- **Storage audit.** Nothing may come to rest in the browser (specs/011 FR-007), and the guard
  scans `src/` only, so this is a configuration duty. Checked against `@tanstack/router-core`
  1.171, `@tanstack/react-router` 1.170 and `@tanstack/query-core`:
  - The router's only `sessionStorage` writes are scroll restoration (on `pagehide`, when
    enabled) and `lazyRouteComponent` (on a failed chunk load). Scroll restoration is off, and no
    route is lazy. It *reads* `sessionStorage` once at load, which stores nothing.
  - Query touches no storage.
  - **Re-check on every upgrade of either library.** No devtools in the build.
- **A build-size warning is accepted.** The bundle passes Vite's 500 kB threshold. Splitting by
  route would need lazy routes, which the audit rules out. Do not silence it by adding them.
- **A route name is a path, so it counts for the console boundary.** No `/console/queue` or other
  review-platform noun (ADR-0019 §2).
- **Query's cache is cleared on every way a session ends**, so the next account never sees the
  last one's answers.
- **New screens follow one recipe** (the `console-ui` skill, "Adding a screen").
- **Licences:** both libraries are MIT; `check-licenses.mjs` passes.
