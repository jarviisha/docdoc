# Licences of the console's dependencies

docdoc is Apache-2.0. Every dependency below was checked against that, and the check runs as
`npm run licenses` rather than living only in this file — a licence obligation acquired by accident is
discovered at the worst possible moment (FR-039, SC-014).

Verified 2026-10-22 against the registry, not copied from documentation.

| Package | Version | Licence | Compatible with Apache-2.0 |
|---|---|---|---|
| `react` | ^19.2.8 | MIT | yes |
| `react-dom` | ^19.2.8 | MIT | yes |
| `@types/node` (dev) | ^26.3.0 | MIT | yes |
| `@types/react` (dev) | ^19.2.18 | MIT | yes |
| `@types/react-dom` (dev) | ^19.2.5 | MIT | yes |
| `typescript` (dev) | ^7.0.2 | Apache-2.0 | yes |
| `vite` (dev) | ^8.2.2 | MIT | yes |
| `radix-ui` | ^1.8.1 | MIT | yes |
| `class-variance-authority` | ^0.7.1 | Apache-2.0 | yes |
| `clsx` | ^2.1.1 | MIT | yes |
| `tailwind-merge` | ^3.7.0 | MIT | yes |
| `lucide-react` | ^1.61.0 | ISC | yes |
| `tw-animate-css` | ^1.4.0 | MIT | yes |
| `tailwindcss` (dev) | ^4.3.3 | MIT | yes |
| `@tailwindcss/vite` (dev) | ^4.3.3 | MIT | yes |

## Why this file exists at all

`pdfjs-dist` was the reason, and it is no longer in the table: the viewer rendered PDF pages in the
browser to avoid the server-side alternative, which in this repository means PyMuPDF — **AGPL-3.0**,
quarantined behind the opt-in `docdoc[pdf]` extra for exactly that reason (`pyproject.toml`). The
viewer was removed with ADR-0020 and the renderer went with it. The console's dependency set is now
React, Radix UI and the small styling helpers shadcn/ui components use (ADR-0024).

The file stays, because the obligation it guards was never specific to that one package. A licence
acquired by accident is discovered at the worst possible moment, and the check below is what makes
that impossible rather than unlikely.

A file that only *asserts* compatibility is worth little, so `scripts/check-licenses.mjs` fails the
build when a dependency appears in `package.json` and not here, or when one carries a licence from the
denylist. The denylist is deliberately short and deliberately blunt: strong copyleft licences are not
"reviewed case by case" in a project that ships Apache-2.0.
