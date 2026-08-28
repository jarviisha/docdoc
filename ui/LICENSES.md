# Licences of the viewer's dependencies

docdoc is Apache-2.0. Every dependency below was checked against that, and the check runs as
`npm run licenses` rather than living only in this file — a licence obligation acquired by accident is
discovered at the worst possible moment (FR-039, SC-014).

Verified 2026-08-25 against the registry, not copied from documentation.

| Package | Version | Licence | Compatible with Apache-2.0 |
|---|---|---|---|
| `@astryxdesign/core` | 0.5.0 | MIT | yes |
| `@astryxdesign/theme-neutral` | 0.5.0 | MIT | yes |
| `@astryxdesign/cli` (dev) | 0.5.0 | MIT | yes |
| `pdfjs-dist` | ^6.2.108 | Apache-2.0 | yes |
| `react` | ^19.2.8 | MIT | yes |
| `react-dom` | ^19.2.8 | MIT | yes |
| `@types/node` (dev) | ^24 | MIT | yes |
| `@types/react` (dev) | ^19.2.18 | MIT | yes |
| `@types/react-dom` (dev) | ^19.2.5 | MIT | yes |
| `typescript` (dev) | ^7.0.2 | Apache-2.0 | yes |
| `vite` (dev) | ^8.2.2 | MIT | yes |

## On Astryx's `postinstall`

`@astryxdesign/core` and `@astryxdesign/cli` both declare a `postinstall` script, and npm 11 will not
run one without approval — so installs here use `--ignore-scripts` and CI's `npm ci` does the same by
default. **Nothing is lost by that**, which was checked rather than assumed (T086): the script only
prints a one-line suggestion to run `astryx init`. It is explicitly non-interactive, never fails an
install, and produces no build artefact. `dist/`, including the pre-built `astryx.css` this project
imports, ships inside the published package.

## Why this file exists at all

`pdfjs-dist` is the reason. The rejected alternative to it was rendering pages on the server, which in
this repository means PyMuPDF — **AGPL-3.0**, and already quarantined behind the opt-in `docdoc[pdf]`
extra for exactly that reason (`pyproject.toml`). Choosing a renderer was therefore a licensing
decision before it was a technical one, and the spec's first clarification records it as such.

A file that only *asserts* compatibility is worth little, so `scripts/check-licenses.mjs` fails the
build when a dependency appears in `package.json` and not here, or when one carries a licence from the
denylist. The denylist is deliberately short and deliberately blunt: strong copyleft licences are not
"reviewed case by case" in a project that ships Apache-2.0.
