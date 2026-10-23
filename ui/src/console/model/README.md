# `src/console/model` — where the console's decisions live

Every decision the console makes is here, and nothing here renders.

**Which guards scan this directory, and what each one is enforcing:**

- `scripts/check-model-boundary.mjs` — this directory imports nothing that
  renders (no React, no `@/components/ui/*` or `radix-ui`, no `@components/*`), and it reaches
  the network only through `src/transport.ts` (FR-041).
- `scripts/check-console-boundary.mjs` — none of ADR-0019 §2's five nouns
  appears here (FR-002), and no result content does either (FR-037, SC-016).
- `scripts/check-no-persistence.mjs` — covers all of `src/`, so nothing here
  may reach `localStorage`, `sessionStorage`, IndexedDB, the Cache API, or a
  cookie (FR-007, SC-004). This was the persistence half of `check-readonly.mjs`
  until ADR-0020; the editing half guarded the viewer and went with it.

**Why the split is load-bearing.** The rendering layer carries no automated test
by decision (`specs/008`), so this directory is the entire tested surface. A
decision that drifts into a component is a requirement that silently loses its
coverage — which is why the erasure confirmation's comparison and the idle
bound's expiry both live here rather than in the components that display them.

**URLs and caching are decided here too** (ADR-0028), without importing the
router or Query. `search.ts` turns raw URL search into typed filters and drops
what is invalid; `navigation.ts` holds the areas, their paths and the tenant
jumps; `queries.ts` holds each read's cache key and which writes make which
reads stale. `polling.ts` is only the overview's interval: Query does the
rest of polling.
