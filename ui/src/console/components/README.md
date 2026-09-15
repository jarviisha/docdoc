# `src/console/components` — where the console renders, and edits

This directory contains forms. That is not an oversight in the read-only guard;
it is the reason this directory exists separately from `src/components/`.

**Which guards scan this directory:**

- `scripts/check-console-a11y.mjs` — every control carries an accessible name
  and is operable by keyboard; no interactive handler sits on a non-interactive
  element without a role and a tab stop (FR-045a, FR-045b, SC-014a).
- `scripts/check-console-boundary.mjs` — the five nouns and the result-content
  rule set, as for the model.
- `scripts/check-model-boundary.mjs` — components reach the network through
  `src/transport.ts` or not at all.
- `scripts/check-readonly.mjs` — **its editing scan does not cover this
  directory, and must not be extended to it.** That scan is scoped to
  `src/components/`, the viewer's own tree, which is what keeps Milestone 8's
  claim machine-checked while this console does the editing Milestone 11
  permits (FR-040, ADR-0019 §5). Widening it breaks the console; narrowing the
  viewer's claim to match breaks Milestone 8's.

**No decision belongs here.** If a component is deciding something — when a
credential expires, whether an erasure is armed, which page comes next — the
decision belongs in `../model/`, where a test can reach it.
