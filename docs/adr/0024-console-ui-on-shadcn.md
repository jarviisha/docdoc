# ADR-0024: The Console's UI Is Built on shadcn/ui

- **Status**: Accepted
- **Date**: 2026-10-22
- **Supersedes**: the choice of Astryx (`@astryxdesign/core` 0.5.0) as the console's component
  library, made in `specs/008` research R2 and carried by ADR-0020
- **Relates to**: [ADR-0019](0019-operations-console-boundary.md) (the console boundary and its
  guards), [ADR-0023](0023-console-login-and-cross-tenant-operations.md) (the screens being moved)
- **Design**: `docs/superpowers/specs/2026-10-22-console-shadcn-migration-design.md`

## Context

The console was built on Astryx. Astryx is pre-1.0 with no stable release, so every upgrade could
break its API, and the owner no longer wants it.

The console is also expected to grow toward something like Unstract, a place where people build
extraction (a schema editor beside the document, provider configuration) rather than only operate
runs. Unstract moved its own frontend from Ant Design to shadcn/ui and Tailwind CSS in September
2026 (their PR #2212), and puts no admin framework on top.

## Decision

1. **shadcn/ui on Radix UI, styled with Tailwind CSS v4.** The primitives are generated into
   `ui/src/components/ui/` and owned by this repository. They are not a versioned dependency, so
   upstream churn cannot break the console. Radix, `class-variance-authority`, `clsx`,
   `tailwind-merge`, `lucide-react` and `tw-animate-css` are the runtime dependencies that come
   with them. All are MIT, Apache-2.0 or ISC.
2. **No admin framework.** `ui/src/console/model/` already does what Refine or react-admin would
   do, and it is the tested surface. The rendering layer stays a thin layer over it.
3. **Thin console wrappers.** `fields.tsx` (labelled text, select and file fields), `DataTable.tsx`
   and `ConfirmDialog.tsx` give every area the same labelling, tables and confirmation.
4. **Destructive actions confirm in a modal `AlertDialog`.** This covers revoking a credential or
   a callback, cancelling a run, and erasure. Radix traps focus and puts it on the decline button.
   The dialog closes when the request settles, so a failure is read on the page and not behind
   the overlay.
5. **Light theme only, neutral palette, indigo `#4f46e5` accent.** The sidebar is grouped by task:
   Operate, Build, Manage, Settings. Schemas and Providers are shown as "soon".

## Alternatives rejected

- **Ant Design.** A complete admin kit, but heavy and visually distinctive, and Unstract, the
  product the console is heading toward, has just left it.
- **Refine or react-admin.** They would replace a model layer that already exists and is tested,
  and would not help with the screens the console most needs next. A schema editor is bespoke
  either way.

## Consequences

- **Selects are native `<select>`.** Radix Select refuses an empty-string value, and the model's
  "Parser default" language is exactly that.
- **The sidebar is hand-built.** shadcn's `sidebar` component writes `document.cookie`, which
  `check-no-persistence.mjs` forbids.
- **The generated `Alert` has no `role="alert"`.** The console wraps every Alert in its own
  `role="status"` region and chooses polite or assertive there, so a built-in role would make
  every message assertive.
- **Guards:**
  - The model-boundary guard forbids `@/components/*`, `radix-ui`, `@radix-ui/*` and
    `lucide-react` in the model.
  - Its network rule now also scans `src/components/`.
  - The a11y guard treats `<Alert` as a status message.
- **Unstract is AGPL-3.0.** It informs layout only; no code, theme or tokens come from it.
