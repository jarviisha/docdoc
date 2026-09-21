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

/**
 * And one for this project's own stylesheet, which is a different problem with
 * the same shape.
 *
 * `./fonts.css` has no declaration to be missing: a stylesheet imported for its
 * side effect is a bundler convention, and TypeScript resolves modules with no
 * knowledge that Vite will turn this one into a `<link>`. Nothing upstream will
 * ever fix it, so unlike the line above this one does not exist to be deleted.
 *
 * `*.css` and not the one path, because the alternative — a relative specifier
 * in an ambient declaration — does not match the import it looks like it
 * matches, and the version that appears to work does so by declaring a module
 * nobody imports. Vite ships the same declaration in `vite/client`; it is spelt
 * out here rather than pulled in with a triple-slash reference, which would also
 * bring `import.meta.env` and the rest of a client runtime this console does not
 * use.
 */
declare module "*.css";
