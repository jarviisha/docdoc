---
name: console-ui
description: House style and patterns for docdoc's operations console (`ui/`). Use when building, restyling or reviewing a console screen, table, filter bar, form, dialog, status display or run detail page.
---

# Console UI

The console is a **dense operations tool**: an operator scans many rows, filters, opens one, acts, and leaves. Every rule below serves _scanning_. Look is recorded in [ADR-0024](../../../docs/adr/0024-console-ui-on-shadcn.md), URLs and data loading in [ADR-0028](../../../docs/adr/0028-console-routing-and-data-layer.md). Where these patterns came from is in [SOURCES.md](SOURCES.md). Read it before borrowing from one of those repos, because several are not copyable (licence).

## Where things live

- **Tokens** (palette, radius, base size): `ui/src/styles.css`. Change a token there, never per component.
- **Primitives:** `ui/src/components/ui/`. These are generated shadcn components, owned and edited here. After a `npx shadcn add`, rewrite any `from "cn"` import to `@/lib/utils` and `npm uninstall cn`, because the CLI mis-resolves the alias.
- **Console wrappers:** `ui/src/console/components/`.
  - `fields.tsx`: `TextField`, `SelectField`, `FileField`.
  - `DataTable.tsx`
  - `ConfirmDialog.tsx`
  - `StatStrip.tsx`
  - `useIntent.ts`: `useIntent(intent, options)` is one read, `useAct()` is one write. Both go through the shell's `request`.

  Reach for these before a raw primitive.
- **Routes:** `ui/src/console/routes.tsx`. One code-based route per area under `/console`, plus the signed-in frame (sidebar, top bar). The sidebar list lives there too.
- **URL and cache decisions:** `model/search.ts` (`validateSearch` functions), `model/navigation.ts` (`AREA_PATHS`, tenant jumps), `model/queries.ts` (`keyOf`, `invalidatedBy`).
- **Every decision** (formatting, what is armed, which page comes next) lives in `ui/src/console/model/`, where `node --test` reaches it. Ids, times and durations are formatted there (`format.ts`: `shortId`, `when`, `duration`). A component only renders.
- **Done means `make ui` is green.** It runs typecheck, tests, the boundary, persistence and a11y guards, licences and the build.

## Look

- **Corners and depth:** 2px radius everywhere and no shadows. Separation comes from a 1px slate border or a slate-50 fill.
- **Colour:** slate neutrals. The indigo accent `primary` is reserved for the primary button, the selected nav item, links and focus. Colour carries meaning, never decoration: green succeeded, blue running, amber warning, red failed or destructive, slate neutral.
- **Type scale:**
  - Body text is 13px.
  - Labels and secondary text are 12px (`text-xs`) in `text-slate-500` or `text-slate-600`.
  - Table headers and section headings are 11px uppercase with `tracking-wider`.
  - One page title at most. The top bar's "Group / Area" trail already says where the operator is.
- **Cursor:** everything that acts shows the pointer hand, and a disabled control shows `not-allowed`. Both come from one rule in `styles.css`, so a new control inherits them as long as it is a `<button>` or carries `role="button"`/`"option"`.
- **Control height** is 26px for buttons, inputs and selects alike. Controls in one row share that height and bottom-align with `items-end`.
- **Mono** (`font-mono text-xs`) for every machine value: ids, tenant ids, hashes, URLs, schema identities.
- **Numbers** are `tabular-nums`. Numeric and duration columns are right-aligned.

## Lists

- **Order:** stat strip (when the area has counts), then the filter row, then the table, then the pager.
- **Filter row:** the existing filters, Refresh, then a spacer (`flex-1`), then the primary action on the right.
- **Applying filters:** a text filter applies on Enter or Apply, never per keystroke. Selects apply on change.
- **Table:**
  - Use `DataTable`.
  - Header: muted, uppercase.
  - Rows: about 32px, zebra striping, `whitespace-nowrap` except free-text columns.
  - Status column first, the run link last.
- **Ids:** short form (`shortId`) in mono. The full value goes in `title` and is copyable. When adding copy, follow the copy-on-hover pattern: an icon button with `opacity-0 group-hover:opacity-100`, plus an `aria-label`.
- **Time:** absolute local time from `when`, with UTC and local in a `title` tooltip. Durations come from `duration` (`4 s`, `2 min 05 s`). An empty value is `—`.
- **Status:** an outlined uppercase tag (`Badge` variants `success | info | warning | error | neutral`). The word is always present; colour sits beside it.
- **Refresh:** a polled `useIntent` (`refetchInterval`) replaces rows in place; pass `keepPrevious` for filtered lists. Never blank the table to show a spinner, and say a failed refresh in an `Alert` above it.
- **Pager:** cursor-based, Previous and Next only, with no page numbers, because the API returns no total.

## States

Every async area has four states. Write all four.

- **Loading:** a `Loading…` line inside the live region.
- **Empty:** an `Alert variant="info"` with one sentence that says _why_ it is empty and what to do. Example: "This tenant has no callbacks registered." An empty list and a failed request must read differently.
- **Error:** an `Alert variant="destructive"` with a title naming what failed and a description giving `describe(failure)`, in the deployment's words.
- **Content.**

Each status message sits inside a `role="status"` region. Use `aria-live="polite"`, or `"assertive"` only after the operator destroyed something. `Alert` has no role of its own, so the region does the announcing. Give an empty region `className="empty:-mb-3"` so it costs no gap.

## Forms

- **Fields:** use `TextField`, `SelectField` or `FileField`. Each pairs a 12px label with its control through `useId`, so every field is named.
- **Spacing:** `gap-1` label to control, `gap-3` between fields. A form sits in a `Card` about 480–520px wide.
- **Select empty values:** `SelectField` handles `""` options (for example "Parser default"). Pass the model's choices straight through.
- **Busy:** disable the submit button while sending, and say it with a verb in "-ing" form ("Submitting…").
- **Secrets are shown once:**
  - A read-only mono field holding the secret.
  - A warning `Alert` above it saying it will not be shown again.
  - An explicit "I have copied it" acknowledgement before it disappears.

## Destructive actions

- **Every destructive action goes through `ConfirmDialog`.** That covers revoke, cancel, erase and delete.
- **Title** is a question naming the action and the object: "Revoke this key?", "Cancel this run?".
- **Description:** one sentence of consequence, plus any warning the page shows. The page behind the overlay is hidden from screen readers, so the warning has to be in the dialog too.
- **Confirm button:** `destructive`, labelled with the verb ("Revoke", "Cancel run", "Erase").
- **Decline label:** says what staying means ("Keep running", "Keep").
- **Irreversible actions** (erasure) require typing the identifier again. The confirm button stays disabled until the model's `armed()` says the text matches exactly.
- **While the request is in flight**, the dialog holds open and every button is disabled. The parent closes it when the request settles, either way, so the outcome is read on the page.

## Detail pages

- **Header row:** a "← Back" ghost button, then the status tag. Below it, the full id in mono.
- **Timeline sentence:** "Submitted … → Started … → Finished …".
- **Metadata:** a `<dl>` grid (`grid-cols-[max-content_1fr] gap-x-6 gap-y-1`), muted `dt`, and mono `dd` for machine values.
- **Actions:** a row of outline buttons under the metadata. Destructive actions confirm.
- **Subsections** (Delivery, Result) come below, each with an uppercase 11px heading.

## Navigation

The sidebar is hand-built (shadcn's writes a cookie, which the persistence guard forbids).

- **Size:** 208px wide on slate-50.
- **Group headings:** 10px uppercase with wide tracking.
- **Selected item:** `bg-indigo-100 text-indigo-800` with `aria-current="page"`.
- **Unbuilt areas:** shown as non-interactive "soon" entries, never as dead buttons.
- **Group order:** Overview, Operate, Build, Manage, Settings. Erase… is last, in red.

## Adding a screen

1. Add the intent(s) to `model/client.ts` with their request plans. Test them.
2. Add the screen's decisions to a model file: search params, what polls, what a write invalidates (`invalidatedBy`). Test them.
3. Add the route in `routes.tsx` with `validateSearch` from step 2. Filters live in search params, not `useState`. A filter change pushes history. The cursor stays out of the URL.
4. Write the component. Read with `useIntent`, write with `useAct`, render the four states. Never call `fetch` or `request` directly.
5. Add the sidebar entry in `NAV`, or replace its "soon".

Never use `lazyRouteComponent`, scroll restoration or a persister: they write to browser storage. A route path may not contain a review-platform noun.
