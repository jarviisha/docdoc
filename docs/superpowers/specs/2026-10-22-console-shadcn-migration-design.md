# Console: replace Astryx with shadcn/ui

Date: 2026-10-22
Status: design, awaiting approval

## Why

The operations console (`ui/`) is built on Astryx (`@astryxdesign/core` 0.5.0). The owner no
longer wants it, and it is pre-1.0 with no stable release, so every upgrade risks an API break.

The longer-term direction is for docdoc to grow toward something like
[Unstract](https://github.com/Zipstack/unstract): a console where people *build* extraction (a
schema/prompt editor next to the document, provider configuration), not only operate runs.
Unstract itself moved off Ant Design to **shadcn/ui + Tailwind CSS v4** in September 2026 (their
PR #2212), with no admin framework on top: plain HTTP calls and local state. docdoc follows the
same direction.

shadcn/ui is not a versioned dependency in the usual sense: its components are copied into this
repo and owned by docdoc. Upstream churn cannot break the console, which removes the reason
Astryx is being dropped.

## Scope

This is project 1 of three, in this order:

1. **This document.** Move the existing console to shadcn/ui, with the same screens and the same
   behaviour.
2. A provider configuration screen (later, separate design; needs new backend API).
3. A schema editor, the docdoc counterpart of Unstract's Prompt Studio (later, separate design;
   needs new backend API).

Projects 2 and 3 affect this one only through the sidebar layout, which leaves room for them.

## Goals

- Astryx is gone: no `@astryxdesign/*` package, import, stylesheet, type shim
  (`src/astryx-theme-css.d.ts`), or comment that describes the current stack as Astryx.
- Every existing screen works as it does today: Overview, Runs (list, detail, polling, delivery
  record, result viewer, page images), Submit OCR, Parsers, Tenants (with its jumps), Credentials,
  Callbacks, Erasure, login and sign-out.
- New look (decided with the owner, see "Look" below).
- `make ui` passes: typecheck, `npm test`, `npm run lint:boundaries`, `npm run licenses`, build.

## Non-goals

- No new screens, no new backend endpoints, no behaviour changes. The one exception is
  destructive confirmations moving into a dialog (below).
- No admin framework (Refine, react-admin). Considered and rejected: Unstract does not use one,
  and `src/console/model/` already does what one would do.
- No URL routing. Navigation stays the in-memory `Area` state from `model/navigation.ts`.
- No dark mode and no theme toggle.

## Anti-goals (would make this a failure even if it works)

- Any change to `src/console/model/` or `src/transport.ts` beyond what a guard script rename
  forces. The model is the tested surface and stays untouched.
- Copying code, theme, or the "Midnight Bloom" tokens from Unstract. Unstract is AGPL-3.0;
  docdoc is Apache-2.0. Learn from the layout only.
- Adding a copyleft dependency (`check-licenses.mjs` must keep passing honestly).
- Losing an accessibility property the current console has: every control named and keyboard
  reachable, and a live region wherever a status message appears (`check-console-a11y.mjs`).

## Decided with the owner

- **Library:** shadcn/ui (on Radix UI) with Tailwind CSS v4.
- **Sidebar grouped by task, like Unstract** (layout A from the mockups):
  - Overview (top, ungrouped)
  - **Vận hành / Operate:** Runs, Submit
  - **Build:** Schemas, shown greyed and not clickable, marked "coming soon"
  - **Quản lý / Manage:** Tenants, Credentials, Callbacks
  - **Settings:** Parsers, Providers (greyed, "coming soon"), Erase… (last, in red)
- **Top bar** on every screen: the screen name on the left, the signed-in account (with sign-out)
  on the right.
- **List screens:** controls in one row above a table. No page numbers or totals, because the API
  returns `next_cursor` and no count.
  - **This is a restyle of the controls each screen already has.** The mockup's search box and
    "Submit" button were only illustrations. Runs keeps exactly its current filters (Tenant + Apply,
    Kind, State, Refresh) and its **Previous / Next** pager: `canGoBack`/`back` in
    `model/runs.ts` stay in use. Screens that have no search today don't get one. Inline forms
    such as "issue a credential" and "register a callback" stay where they are.
- **Destructive actions confirm in a modal dialog** (shadcn `AlertDialog`, built on Radix, which
  traps focus and returns it to the trigger on close). There are four today, all confirmed inline:
  - **Revoke a credential** (`CredentialArea.tsx`, the in-row "Confirm").
  - **Revoke a callback** (`CallbacksArea.tsx`, the in-row "Confirm").
  - **Cancel a run** (`RunArea.tsx`, "Confirm / Keep running").
  - **Erase a tenant or a document** (`EraseArea.tsx`, retype the identifier).

  The dialog takes over the confirm step only. What is asked and when the action is allowed don't
  change.

  *Designer's call:* for Erase, the "type the identifier again" field moves into the dialog, and
  the dialog's Erase button stays disabled until `retyped` from `model/erasure.ts` says it matches,
  exactly as the card does today. The dialog text is `confirmationPrompt(intent)`. Cancel is the
  default-focused button in every dialog, so Enter never confirms by accident.
- **Look:** light theme, neutral greys (shadcn default neutral palette), with **indigo `#4f46e5`**
  as the accent for the primary button, the selected sidebar item, focus rings and links.
  Status badges keep semantic colours (green succeeded, blue running, red failed) independent of
  the accent.

## Builder's calls (the builder may revisit)

These were settled by the designer, not the owner:

- **Language of UI labels:** keep the console's current English labels ("Runs", "Submit OCR"…);
  group headings in English too (Operate, Build, Manage, Settings). The Vietnamese names above
  were for the conversation only.
- **"Coming soon" items** render disabled (`aria-disabled`, not focusable as a link), so they
  don't break the a11y guard and don't lead nowhere.
- **Where shadcn components live:** `ui/src/components/ui/` (shadcn's convention), with the
  `cn()` helper at `ui/src/lib/utils.ts`. Add a `@/*` → `./src/*` path in `tsconfig.json` and the
  matching Vite alias, because generated shadcn code imports through `@/`.
- **Only the components actually used** get added (expected: button, input, label, select,
  table, badge, card, alert, alert-dialog, dropdown-menu). No component "for later".
- **Sidebar is hand-built**, not shadcn's stock `sidebar` component: that component writes
  `document.cookie`, which `check-no-persistence.mjs` rejects.
- **Toasts:** none. Messages stay where they are today (an `Alert` in place of each `Banner`).
- **Supporting packages** that shadcn components pull in, all MIT: `tailwindcss`,
  `@tailwindcss/vite`, `radix-ui` (or the individual `@radix-ui/*` packages the CLI picks),
  `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`. The shadcn CLI may be run
  through `npx` and need not be a devDependency.
- **Tables and forms:** plain shadcn `Table` and controlled inputs, the way the components do it
  today. Do not add `@tanstack/react-table` or `react-hook-form` in this project; they can come
  with project 3 if the schema editor needs them.
- **ADR:** add `docs/adr/0024-console-ui-on-shadcn.md` recording the switch from Astryx, the
  rejected alternatives (Ant Design, Refine/react-admin), and the AGPL caution about Unstract.

## What has to change, as far as the designer could see

- `ui/package.json`: drop the three `@astryxdesign/*` packages; add the packages above.
- `ui/LICENSES.md`: remove the Astryx rows and the "On Astryx's postinstall" section; add a row
  for every new package, verified against the registry.
- `ui/vite.config.ts`: add the Tailwind plugin and the `@` alias; rewrite the comment that says
  "Astryx ships pre-built CSS… no PostCSS".
- `ui/src/console/main.tsx`: drop the Astryx CSS imports and `<Theme>`; import the Tailwind
  stylesheet that defines the theme tokens (indigo accent).
- `ui/src/astryx-theme-css.d.ts`: delete.
- `ui/src/console/components/*.tsx` (12 files, ~2,600 lines): rewrite rendering onto the new
  components. `Shell.tsx` gets the new grouped sidebar and top bar.
- `ui/src/console/components/README.md` and `ui/src/console/model/README.md`: update mentions
  of `@astryxdesign/*`.
- `ui/scripts/check-model-boundary.mjs`: replace the `@astryxdesign/` rule with rules forbidding
  the model from importing `@radix-ui/*`, `radix-ui`, `@/components/*`, and
  `src/components/ui`. The model must still not depend on any rendering code.
- `ui/scripts/check-console-a11y.mjs`: keep all three rules **actually working**, not just renamed:
  - It keeps scanning `src/console/components/` only. The generated primitives in
    `src/components/ui/` are not scanned: they spread props and take their names from the call
    site, which is where the guard checks.
  - Its hint mentions an Astryx `<Field>`; change it to the shadcn `<Label htmlFor>` pattern.
  - The live-region rule currently keys on `<Banner` and "Loading…". Re-key it on `<Alert`, the
    replacement for `Banner`, so it still catches a status message with no live region.
  - Update the `<Banner` fixtures in `ui/test/guards-can-fail.test.mjs` to match, so the test
    still proves the guard can fail.
- `ui/scripts/check-no-persistence.mjs` must still pass. No localStorage, so no theme toggle
  (one reason dark mode is out of scope).

## Verification

- `make ui` green.
- Manual pass through every screen with `make dev` (`http://localhost:5173/console/`): sign in,
  list and filter runs, open a run and watch it poll to completion, view the result and page
  images, submit an OCR file, issue and revoke a credential, add and delete a callback, use each
  tenant jump, erase a document on a demo tenant, sign out.
- Keyboard-only pass on the same flows. For each of the four dialogs, check three things: focus
  moves into the dialog, Tab stays inside it, and Escape or Cancel returns focus to the button that
  opened it. No guard can check this.
- `grep -ri astryx ui/ --exclude-dir=node_modules --exclude-dir=dist` returns nothing.
