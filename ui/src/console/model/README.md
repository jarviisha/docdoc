# `src/console/model` — where the console's decisions live

Every decision the console makes is here, and nothing here renders.

**Which guards scan this directory, and what each one is enforcing:**

- `scripts/check-model-boundary.mjs` — this directory imports nothing that
  renders (no React, no `@astryxdesign/*`, no `@components/*`), and it reaches
  the network only through `src/transport.ts` (FR-041).
- `scripts/check-console-boundary.mjs` — none of ADR-0019 §2's five nouns
  appears here (FR-002), and no result content does either (FR-037, SC-016).
- `scripts/check-readonly.mjs` — its **persistence** scan covers all of
  `src/`, so nothing here may reach `localStorage`, `sessionStorage`,
  IndexedDB, or the Cache API (FR-007, SC-004). Its *editing* scan does not
  cover this tree, and must not be widened to: see `../components/README.md`.

**Why the split is load-bearing.** The rendering layer carries no automated test
by decision (`specs/008`), so this directory is the entire tested surface. A
decision that drifts into a component is a requirement that silently loses its
coverage — which is why the erasure confirmation's comparison and the idle
bound's expiry both live here rather than in the components that display them.
