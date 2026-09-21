# Licences of the console's dependencies

docdoc is Apache-2.0. Every dependency below was checked against that, and the check runs as
`npm run licenses` rather than living only in this file — a licence obligation acquired by accident is
discovered at the worst possible moment (FR-039, SC-014).

Verified 2026-08-25 against the registry, not copied from documentation.

| Package | Version | Licence | Compatible with Apache-2.0 |
|---|---|---|---|
| `@astryxdesign/core` | 0.5.0 | MIT | yes |
| `@astryxdesign/theme-neutral` | 0.5.0 | MIT | yes |
| `@astryxdesign/cli` (dev) | 0.5.0 | MIT | yes |
| `@fontsource-variable/inter` | ^5.3.0 | OFL-1.1 | yes |
| `react` | ^19.2.8 | MIT | yes |
| `react-dom` | ^19.2.8 | MIT | yes |
| `@types/node` (dev) | ^26.3.0 | MIT | yes |
| `@types/react` (dev) | ^19.2.18 | MIT | yes |
| `@types/react-dom` (dev) | ^19.2.5 | MIT | yes |
| `typescript` (dev) | ^7.0.2 | Apache-2.0 | yes |
| `vite` (dev) | ^8.2.2 | MIT | yes |

## On the bundled typeface

`@fontsource-variable/inter` is the one dependency whose licence imposes an obligation that shipping
the package does not discharge by itself. OFL-1.1 §2 requires the copyright notice and the licence to
travel **with the font files**, and what a deployment receives is `ui/dist` — woff2 files and a
stylesheet, with `node_modules` nowhere in sight. So the text is copied to `public/OFL-Inter.txt`,
which Vite emits at the root of the build, and the obligation is met by the artefact rather than by
this file.

Nothing about that is a restriction on docdoc: OFL is permissive about bundling and Apache-2.0
distribution, and the only thing it forbids — selling the font on its own — is not something this
repository could do by accident. It is recorded because an obligation that lives only in a
`node_modules` directory is one that disappears on the first `npm ci --omit=dev`.

## On Astryx's `postinstall`

`@astryxdesign/core` and `@astryxdesign/cli` both declare a `postinstall` script, and npm 11 will not
run one without approval — so installs here use `--ignore-scripts` and CI's `npm ci` does the same by
default. **Nothing is lost by that**, which was checked rather than assumed (T086): the script only
prints a one-line suggestion to run `astryx init`. It is explicitly non-interactive, never fails an
install, and produces no build artefact. `dist/`, including the pre-built `astryx.css` this project
imports, ships inside the published package.

## Why this file exists at all

`pdfjs-dist` was the reason, and it is no longer in the table: the viewer rendered PDF pages in the
browser to avoid the server-side alternative, which in this repository means PyMuPDF — **AGPL-3.0**,
quarantined behind the opt-in `docdoc[pdf]` extra for exactly that reason (`pyproject.toml`). The
viewer was removed with ADR-0020 and the renderer went with it, so the console's dependency set is
now Astryx and React and nothing else.

The file stays, because the obligation it guards was never specific to that one package. A licence
acquired by accident is discovered at the worst possible moment, and the check below is what makes
that impossible rather than unlikely.

A file that only *asserts* compatibility is worth little, so `scripts/check-licenses.mjs` fails the
build when a dependency appears in `package.json` and not here, or when one carries a licence from the
denylist. The denylist is deliberately short and deliberately blunt: strong copyleft licences are not
"reviewed case by case" in a project that ships Apache-2.0.
