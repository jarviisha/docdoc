# `src/console/components` — where the console renders, and edits

This directory contains forms. It sat beside `src/components/`, the viewer's
read-only tree, and the separation was what let one guard say "the viewer edits
nothing" while this one edited (ADR-0019 §5). The viewer is gone (ADR-0020) and
the path stays: the model/component split below is the one that was always doing
the work.

**Which guards scan this directory:**

- `scripts/check-console-a11y.mjs` — every control carries an accessible name
  and is operable by keyboard; no interactive handler sits on a non-interactive
  element without a role and a tab stop (FR-045a, FR-045b, SC-014a).
- `scripts/check-console-boundary.mjs` — the five nouns and the result-content
  rule set, as for the model.
- `scripts/check-model-boundary.mjs` — components reach the network through
  `src/transport.ts` or not at all.
- `scripts/check-no-persistence.mjs` — nothing under `src/` may write to
  browser storage or a cookie (FR-007, SC-004). It does **not** forbid forms:
  the editing scan that would have was `check-readonly.mjs`'s, scoped to the
  viewer's tree and deleted with it (ADR-0020 §3).

**No decision belongs here.** If a component is deciding something — when a
credential expires, whether an erasure is armed, which page comes next — the
decision belongs in `../model/`, where a test can reach it.
