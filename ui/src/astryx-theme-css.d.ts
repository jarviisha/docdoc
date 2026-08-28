/**
 * A declaration Astryx 0.5.0 should ship and does not.
 *
 * `@astryxdesign/theme-neutral`'s `exports` map points `./theme.css` at
 * `"types": "./theme.css.d.ts"`, and **that file is absent from the published
 * package** — `@astryxdesign/core` ships `astryx.css.d.ts` and `reset.css.d.ts`
 * for its own stylesheets, so this is an omission in the theme package rather
 * than a convention this project is fighting.
 *
 * This is the beta risk the spec's Assumptions recorded, arriving in its
 * smallest possible form: a missing type declaration, worked around in four
 * lines, with no effect on behaviour. Recorded here rather than suppressed with
 * a `@ts-expect-error` so that the next person upgrading Astryx can delete this
 * file, find the check still green, and know the upstream bug is fixed.
 *
 * Scoped to the one specifier rather than declared as `*.css`, so it disappears
 * the moment it stops being needed instead of silently covering future gaps.
 */

declare module "@astryxdesign/theme-neutral/theme.css";
