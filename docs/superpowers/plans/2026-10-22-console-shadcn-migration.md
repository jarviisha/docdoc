# Console on shadcn/ui Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Astryx with shadcn/ui + Tailwind CSS v4 in the operations console. The screens
and their behaviour stay the same, except for a new grouped sidebar, a new look, and modal
confirmation of destructive actions.

**Architecture:**
- shadcn primitives are generated into `ui/src/components/ui/` and owned by the repo.
- A thin console layer in `ui/src/console/components/` wraps them: `fields.tsx`, `DataTable.tsx`
  and `ConfirmDialog.tsx`, so the twelve area components swap imports rather than reinvent
  labelling and tables.
- Astryx and shadcn coexist until the last task, so every task leaves a console that builds and
  runs.
- `src/console/model/` and `src/transport.ts` are not touched.

**Tech Stack:** React 19, TypeScript 7, Vite 8, Tailwind CSS 4.3 (`@tailwindcss/vite`), shadcn
CLI 4.22 (style `new-york`, base colour `neutral`), `radix-ui` 1.8, `lucide-react`,
`class-variance-authority`, `clsx`, `tailwind-merge`, `tw-animate-css`; `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-22-console-shadcn-migration-design.md`

## Global Constraints

- No change to `ui/src/console/model/**` or `ui/src/transport.ts`, and no change to their tests in
  `ui/test/console/` or `ui/test/transport.test.ts`.
- No new screens, no backend change, no URL routing, no dark mode, no toasts, no
  `@tanstack/react-table`, no `react-hook-form`, no shadcn `sidebar` component (it writes
  `document.cookie`) and no shadcn `select` (Radix Select rejects the `""` value that
  `languageChoices` returns). Selects are native `<select>`.
- No code, theme or tokens copied from Unstract (AGPL-3.0).
- Every new package is MIT or Apache-2.0 and gets a row in `ui/LICENSES.md`, verified against
  `npm view <pkg> license`.
- UI labels stay in English and as they are today, except the sidebar defined in Task 3.
- Accent colour is indigo `#4f46e5`, light theme only. Badge tones:
  - success: green
  - info: blue (used for `running`)
  - warning: amber
  - error: red
  - neutral: grey
- Every task ends with `cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run licenses && npm run build`
  green (this is `make ui`).
- The rendering layer has no automated tests, by an earlier decision (`specs/008`). Component
  tasks are checked by typecheck, the guards, the build, and the manual steps listed. Run the
  console with `make dev`; create an account first with `make user NAME=admin` if there isn't
  one.

### Astryx → new mapping (used by Tasks 3–7)

| Astryx | Replacement |
|---|---|
| `Stack direction="vertical" gap={n}` / `HStack gap={n}` | `<div className="flex flex-col gap-{2n}">` / `<div className="flex items-center gap-{2n}">` |
| `Text size="sm" color="secondary"` | `<p className="text-sm text-muted-foreground">` |
| `Code` | `<code className="font-mono text-xs">` |
| `Card` | `Card` + `CardContent` from `@/components/ui/card` |
| `Button label variant` | `<Button variant>` with the label as children. Mapping: `primary` → `default`, `secondary` → `outline`, `ghost` → `ghost`, `destructive` → `destructive`; `isDisabled` → `disabled` |
| `Link onClick` | `<Button variant="link" className="h-auto p-0">` |
| `Badge variant label` | `<Badge variant>{label}</Badge>` (variants from Task 1) |
| `Banner status title description` | `<Alert variant>` with `AlertTitle` / `AlertDescription`. Mapping: `error` → `destructive`, `warning` → `warning`, `info` → `info`. Keep every existing `role="status"` / `aria-live` wrapper exactly where it is |
| `TextInput` / `Selector` / `FileInput` | `TextField` / `SelectField` / `FileField` (Task 3) |
| `Table` + `pixel` / `proportional` | `DataTable` (Task 3) |
| `MetadataList` / `MetadataListItem` | `<dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">` with `<dt className="text-muted-foreground">` / `<dd>` |
| `AppShell` / `SideNav*` / `Center` | Hand-built in `Shell.tsx` (Task 3) |

## Review Focus

1. **"Parser default" language (value `""`) on Submit** must stay selectable and must submit as
   today. It is the reason selects are native. Manual check: Task 6, step 3.
2. **A destructive request that fails** must leave the failure visible on the page, not hidden
   behind the modal overlay. Example: revoking an already revoked key, or erasing an unknown
   tenant. `ConfirmDialog` closes when the request settles, whatever the outcome. Manual checks:
   Tasks 5, 6 and 7.
3. **A run that finishes while "Cancel this run?" is open**: polling moves the run out of
   `queued`/`running`. The dialog must close itself rather than offer to cancel a finished run.
   Manual check: Task 5, step 4.
4. **Double-clicking Confirm** must send one request, not two. `ConfirmDialog` disables its
   confirm button while `onConfirm`'s promise is pending. Manual check: Task 6, step 3.
5. **A shadcn `<Input>` with no label** in a console component must fail the a11y guard. Today the
   guard only looks at lowercase `<input>`. Covered by a guard test in Task 2.

---

### Task 1: Tailwind, theme and shadcn primitives alongside Astryx

**Files:**
- Modify:
  - `ui/package.json`
  - `ui/package-lock.json`
  - `ui/vite.config.ts`
  - `ui/tsconfig.json`
  - `ui/src/console/main.tsx`
  - `ui/LICENSES.md`
- Create:
  - `ui/components.json`
  - `ui/src/styles.css`
  - `ui/src/lib/utils.ts`
  - `ui/src/components/ui/{button,input,label,table,badge,card,alert,alert-dialog}.tsx`

**Interfaces:**
- Produces:
  - The `@/*` → `./src/*` import alias, in both TypeScript and Vite.
  - `cn(...inputs: ClassValue[]): string` from `@/lib/utils`.
  - shadcn `Button`, `Input`, `Label`, `Table*`, `Badge`, `Card*`, `Alert*`, `AlertDialog*` from
    `@/components/ui/*`.
  - `Badge` variants: `success | info | warning | error | neutral`, plus shadcn's own.
  - `Alert` variants: `default | destructive | warning | info`.

- [ ] **Step 1: Install packages**

```bash
cd ui
npm install tailwindcss@^4.3.3 @tailwindcss/vite@^4.3.3 radix-ui@^1.8.1 class-variance-authority@^0.7.1 clsx@^2.1.1 tailwind-merge@^3.7.0 lucide-react@^1.61.0 tw-animate-css@^1.4.0
```

`tailwindcss` and `@tailwindcss/vite` go in `devDependencies`; the rest in `dependencies`.
Astryx stays installed.

- [ ] **Step 2: Wire Tailwind and the alias**
  - `vite.config.ts`: add `tailwindcss()` from `@tailwindcss/vite` to `plugins`, and
    `"@": fileURLToPath(new URL("./src", import.meta.url))` to `resolve.alias`. Replace the comment
    "Astryx ships pre-built CSS… no PostCSS" with one sentence: Tailwind runs as a Vite plugin, so
    there is still no PostCSS configuration.
  - `tsconfig.json` `paths`: add `"@/*": ["./src/*"]`.
  - `components.json`: `style: "new-york"`, `rsc: false`, `tsx: true`, `tailwind.css: "src/styles.css"`,
    `tailwind.baseColor: "neutral"`, `tailwind.cssVariables: true`, `iconLibrary: "lucide"`,
    `aliases: { components: "@/components", ui: "@/components/ui", lib: "@/lib", utils: "@/lib/utils", hooks: "@/hooks" }`.

- [ ] **Step 3: Generate the primitives**

```bash
npx shadcn@4.22.1 add button input label table badge card alert alert-dialog
```

If the CLI refuses the TypeScript 7 `tsconfig` (no `baseUrl`), write the files from the
new-york registry by hand. The result must be the same files.

- [ ] **Step 4: Theme**
  - `src/styles.css`:
    - Starts with `@import "tailwindcss"; @import "tw-animate-css";`.
    - Has shadcn's neutral light tokens on `:root`.
    - `--primary: #4f46e5`, `--primary-foreground: #ffffff`, `--ring: #4f46e5`.
    - No `.dark` block.
  - Add variants to the cva in `badge.tsx`:
    - success: `bg-green-100 text-green-800`
    - info: `bg-blue-100 text-blue-800`
    - warning: `bg-amber-100 text-amber-800`
    - error: `bg-red-100 text-red-800`
    - neutral: `bg-neutral-100 text-neutral-700`
  - Add variants to the cva in `alert.tsx`:
    - warning: `border-amber-300 bg-amber-50 text-amber-900`
    - info: `border-blue-300 bg-blue-50 text-blue-900`
  - `main.tsx`: add `import "../styles.css";` after the Astryx CSS imports. Astryx imports stay
    until Task 7.

- [ ] **Step 5: Licences.** Add one row per package from Step 1 to `ui/LICENSES.md`, with the
  version range and the licence `npm view` reports.

- [ ] **Step 6: Verify**

Run: `cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run licenses && npm run build`
Expected: all green. `check-no-persistence` passes (nothing generated touches storage or cookies).
`make dev` still shows the Astryx console working.

- [ ] **Step 7: Commit**

```bash
git add ui/
git commit -m "build(console): add Tailwind v4 and shadcn/ui primitives beside Astryx"
```

---

### Task 2: Guards follow the new stack

**Files:**
- Modify:
  - `ui/scripts/check-model-boundary.mjs`
  - `ui/scripts/check-console-a11y.mjs`
- Test:
  - `ui/test/guards.test.mjs`
  - `ui/test/guards-can-fail.test.mjs`

**Interfaces:**
- Consumes: the `@/components/ui/*` path from Task 1.
- Produces: guards that later tasks must keep green.
  - The a11y guard treats `<Alert` as a status message.
  - It treats `<Input`, `<Textarea` and `<select` as controls that need a name.

- [ ] **Step 1: Write the failing tests**

In `guards-can-fail.test.mjs`, with the existing `fixture`/`guard` helpers:

```js
test("the a11y guard fails an Alert with nowhere to be heard", async () => {
  const directory = await fixture({ "Thing.tsx": '<Alert variant="destructive"><AlertTitle>it broke</AlertTitle></Alert>\n' });
  const { code, output } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /nowhere to be heard/);
});

test("the a11y guard does not mistake an AlertDialog for a status message", async () => {
  const directory = await fixture({ "Thing.tsx": "<AlertDialog open={x}>\n</AlertDialog>\n" });
  assert.equal((await guard("check-console-a11y.mjs", directory)).code, 0);
});

test("the a11y guard fails an unnamed shadcn Input", async () => {
  const directory = await fixture({ "Thing.tsx": '<Input value={v} onChange={f} />\n' });
  const { code, output } = await guard("check-console-a11y.mjs", directory);
  assert.equal(code, 1);
  assert.match(output, /accessible name/);
});

test("the a11y guard passes an Input a Label points at", async () => {
  const directory = await fixture({ "Thing.tsx": '<Label htmlFor={id}>Key</Label>\n<Input id={id} />\n' });
  assert.equal((await guard("check-console-a11y.mjs", directory)).code, 0);
});
```

Change the two existing `<Banner` fixtures (the "nowhere to be heard" test and the "inside a live
region" test) to `<Alert variant="destructive"><AlertTitle>it broke</AlertTitle></Alert>`.

In `guards.test.mjs`, under "the view-model boundary guard":

```js
it("fails when the model imports a shadcn primitive", () => {
  plant(PROBE_MODEL, 'import { Button } from "@/components/ui/button";\nexport const y = Button;\n');
  const result = run("check-model-boundary.mjs");
  assert.equal(result.status, 1);
  assert.match(result.stderr, /rendering concern/);
  rmSync(PROBE_MODEL, { force: true });
});
```

Add the same test again for `import { Dialog } from "radix-ui";`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd ui && node --test test/guards.test.mjs test/guards-can-fail.test.mjs`
Expected: these fail:
- the two shadcn/radix model tests
- the "Alert with nowhere to be heard" test
- the "unnamed shadcn Input" test
- the rewritten "nowhere to be heard" Banner→Alert test

- [ ] **Step 3: Update the guards**
  - `check-model-boundary.mjs` `FORBIDDEN`:
    - Replace the `@astryxdesign/` rule with `/^@\/components\//`, `/^radix-ui(\/|$)/`,
      `/^@radix-ui\//`, `/^lucide-react(\/|$)/`, all with
      `why: "component libraries are a rendering concern"`.
    - Keep an `@astryxdesign/` rule until Task 7 deletes it. Until then both stacks are in the tree.
    - Add `join(ROOT, "src", "components")` to `COMPONENT_DIRS`, so the network rule covers the
      primitives too.
  - `check-console-a11y.mjs`:
    - `TRANSIENT` becomes `[/<Banner\b/, /<Alert\b/, /Loading…|Loading\.\.\./]`. Drop `Banner` in
      Task 7.
    - Rule 2's line test becomes `/^\s*<(input|select|textarea|Input|Textarea)\b/`.
    - Rule 2's hint text becomes "Add aria-label, aria-labelledby, or an id a <Label htmlFor>
      points at."
    - The FR-045b hint says "shows an Alert or a loading message".
  - The a11y guard keeps scanning `src/console/components/` only (spec: primitives take their
    names at the call site).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd ui && npm test && npm run lint:boundaries`
Expected: all green, and the real tree is still clean.

- [ ] **Step 5: Commit**

```bash
git add ui/scripts ui/test
git commit -m "test(console): guards cover shadcn primitives, Alert and Input"
```

---

### Task 3: Console building blocks, the shell and login

**Files:**
- Create:
  - `ui/src/console/components/fields.tsx`
  - `ui/src/console/components/DataTable.tsx`
- Modify:
  - `ui/src/console/components/Shell.tsx`
  - `ui/src/console/components/LoginForm.tsx`

**Interfaces:**
- Consumes: the primitives from Task 1.
- Produces:
  - `fields.tsx`:
    - `TextField(props: { label: string; value: string; onChange: (value: string) => void; type?: string; autoComplete?: string; spellCheck?: boolean; disabled?: boolean })`.
      Renders `<Label htmlFor={id}>` plus `<Input id={id}>`, with `id` from `useId()`.
    - `SelectField(props: { label: string; value: string; onChange: (value: string) => void; options: (string | { value: string; label: string; disabled?: boolean })[]; placeholder?: string })`.
      Renders a native `<select id={id}>`, styled with the same classes as `Input`. A string
      option means `{ value: s, label: s }`. `placeholder` becomes a first, disabled
      `<option value="">`, shown only when `value === ""` and `""` is not among the options.
    - `FileField(props: { label: string; value: File | null; onChange: (file: File | null) => void; accept?: string })`.
      Renders `<Input type="file" id={id}>`. `value` is used only to clear the input (reset its
      `key`) when it becomes `null`.
  - `DataTable.tsx`:
    - `interface Column<T> { key: string; header: string; width?: string; cell: (row: T) => ReactNode }`.
      `width` is any CSS width, such as `"180px"` or `"auto"`.
    - `DataTable<T>(props: { rows: T[]; rowKey: (row: T) => string; columns: Column<T>[]; label: string })`.
      Renders shadcn `Table` with `aria-label={label}` and compact cells (`py-1.5`).
  - Areas use these instead of `as unknown as Record<string, unknown>[]` casts.

- [ ] **Step 1: Write `fields.tsx` and `DataTable.tsx`** to the signatures above.

- [ ] **Step 2: Rebuild `Shell.tsx`'s frame.** No Astryx imports may remain in this file.
  - **Layout:** a full-height flex. On the left, a 224px sidebar (`bg-neutral-50 border-r`)
    headed "docdoc console". On the right, a 48px top bar (`border-b`) above the scrolling
    content.
  - **Sidebar:** a `<nav aria-label="Console">` with these groups:

    | Group | Items |
    |---|---|
    | (no heading) | `overview` "Overview" |
    | **Operate** | `runs` "Runs", `submit` "Submit OCR" |
    | **Build** | "Schemas" (coming soon) |
    | **Manage** | `tenants` "Tenants", `credentials` "Credentials", `callbacks` "Callbacks" |
    | **Settings** | `parsers` "Parsers", "Providers" (coming soon), `erasure` "Erase…" |

    - Each real item is a `<button type="button">` with `aria-current="page"` when selected. The
      selected item is `bg-primary/10 text-primary font-medium`.
    - "Erase…" is `text-red-600`.
    - A coming-soon item is a `<span aria-disabled="true" className="text-muted-foreground">`,
      followed by a small "soon" tag. It is not a button and has no handler.
    - Group headings are `<p className="px-2 pt-4 pb-1 text-xs uppercase text-muted-foreground">`.
  - Keep the existing behaviour: the same `Area` values, the same click → area restart (commit
    f9c5b31: a sidebar click restarts an area), and the same jump handling.
  - **Top bar:** on the left, the current area's label. On the right, "Signed in as
    {session.username}" and `<Button variant="outline" size="sm">Sign out</Button>`.
  - The Shell's existing status `Banner`s become `Alert`s inside their existing live regions.
    `Center` becomes `flex min-h-screen items-center justify-center`.

- [ ] **Step 3: `LoginForm.tsx`.** Use a `Card` about 380px wide. The fields become
  `TextField label="Username" autoComplete="username" spellCheck={false}` and
  `TextField label="Password" type="password" autoComplete="current-password"`. The notice becomes
  an `Alert variant="warning"` in the same live region. The submit button becomes
  `<Button type="submit">`.

- [ ] **Step 4: Verify**

Run the `make ui` command line from Global Constraints. Expected: green.

Manual, with `make dev`:
- Sign in with a wrong password: the warning is announced.
- Sign in properly. Every sidebar item opens its area, which still renders in Astryx inside the
  new frame. Clicking the current item restarts it.
- The coming-soon items do nothing, and keyboard Tab skips them.
- Sign out.

- [ ] **Step 5: Commit**

```bash
git add ui/src/console/components
git commit -m "feat(console): shadcn shell with grouped sidebar, top bar and login"
```

---

### Task 4: Read-only areas: Overview, Parsers, Tenants

**Files:**
- Modify:
  - `ui/src/console/components/OverviewArea.tsx`
  - `ui/src/console/components/ParsersArea.tsx`
  - `ui/src/console/components/TenantsArea.tsx`

**Interfaces:**
- Consumes: `DataTable`, `Column` (Task 3), `Badge` variants (Task 1).

- [ ] **Step 1: Rewrite the three files with the mapping table.**
  - The tables become `DataTable`:
    - `pixel(n)` → `` `${n}px` ``
    - `proportional(1)` → `"auto"`
    - `renderCell` → `cell`, typed on the real row type
  - The run links in Overview become `Button variant="link"`.
  - Parsers' `TONE` keeps its values, which are now `Badge` variants.
  - The tenant jump buttons keep their order from `TENANT_JUMPS`.

- [ ] **Step 2: Verify.** Run the `make ui` line, which must be green. Then, with `make dev`:
  - Overview shows the failed and longest-running lists, and a run link opens that run.
  - Parsers shows status badges, with the unavailable reason in muted text.
  - Each tenant jump lands in the right area with the tenant preset.

- [ ] **Step 3: Commit** with `git commit -m "feat(console): Overview, Parsers and Tenants on shadcn"`.

---

### Task 5: Runs: list, detail, delivery, result viewer, and the cancel dialog

**Files:**
- Create: `ui/src/console/components/ConfirmDialog.tsx`
- Modify:
  - `ui/src/console/components/RunArea.tsx`
  - `ui/src/console/components/DeliveryRecord.tsx`
  - `ui/src/console/components/ResultViewer.tsx`

**Interfaces:**
- Consumes: `fields`, `DataTable` (Task 3), `AlertDialog*` (Task 1).
- Produces `ConfirmDialog(props: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: ReactNode; confirmLabel: string; confirmDisabled?: boolean; onConfirm: () => Promise<unknown>; children?: ReactNode })`.
  - It renders `AlertDialog` → `AlertDialogContent` → header (title, description) → `children` →
    footer.
  - The footer has `AlertDialogCancel` "Cancel", which Radix focuses by default, and a plain
    `<Button variant="destructive">{confirmLabel}</Button>`. The confirm button is **not**
    `AlertDialogAction`, so the dialog does not close on click.
  - The confirm button is disabled while `confirmDisabled` is true or `onConfirm`'s promise is
    pending (local `pending` state).
  - Whether the dialog stays open is the parent's decision, through `open`. Every caller closes
    it when the request settles, success or failure.

- [ ] **Step 1: Write `ConfirmDialog.tsx`** to the interface above.

- [ ] **Step 2: Rewrite `RunArea.tsx`, `DeliveryRecord.tsx` and `ResultViewer.tsx`** with the
  mapping table.
  - `TONE` in `RunArea` changes `running: "warning"` to `running: "info"`. This is the spec's
    blue running.
  - The filters keep their exact set and order: Tenant `TextField` plus an Apply button, then Kind
    and State `SelectField`s (options as today, `ALL` = `"all"`), then Refresh. The Previous/Next
    pager stays and still uses `canGoBack`/`back`.
  - The detail metadata becomes the `<dl>` from the mapping table.
  - **Cancel run** opens `ConfirmDialog`. Its fields: title "Cancel this run?", confirmLabel
    "Cancel run", `open={confirming && <the same cancellable condition that shows the button today>}`,
    and `onOpenChange={setConfirming}`.
  - `onConfirm` sends the existing cancel request and calls `setConfirming(false)` when it
    settles, whatever the outcome.

- [ ] **Step 3: Verify.** Run the `make ui` line, which must be green. Then, with `make dev`:
  - Filter by tenant, kind and state. Page forward and back.
  - Open a running run and watch it poll to done.
  - See the delivery record. Open the result and page images.
  - Cancel a queued run through the dialog. Escape returns focus to "Cancel run", Tab stays
    inside the dialog, and Cancel has focus when it opens.

- [ ] **Step 4: Review Focus 3.** Submit an OCR run, open it while it is running and open "Cancel
  this run?". Leave it until polling shows it succeeded. Expected: the dialog closes by itself.

- [ ] **Step 5: Commit** with `git commit -m "feat(console): Runs on shadcn; cancelling a run confirms in a dialog"`.

---

### Task 6: Credentials, Callbacks and Submit

**Files:**
- Modify:
  - `ui/src/console/components/CredentialArea.tsx`
  - `ui/src/console/components/CallbacksArea.tsx`
  - `ui/src/console/components/SubmitArea.tsx`

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 5), `fields`, `DataTable` (Task 3).

- [ ] **Step 1: Rewrite the three files with the mapping table.**
  - The issue form in Credentials and the register form in Callbacks stay inline where they are.
  - **Revoke** in each row becomes a `<Button variant="outline">Revoke</Button>`. It sets
    `confirming` to the row id, as today.
  - One `ConfirmDialog` per area, outside the table, with `open={confirming !== null}` and
    `onOpenChange={(o) => { if (!o) setConfirming(null); }}`.
  - Credentials: title "Revoke this key?", confirmLabel "Revoke".
  - Callbacks: title "Revoke this callback?", confirmLabel "Revoke".
  - In both, `onConfirm` sends the existing revoke request and sets `confirming` to `null` when it
    settles.
  - The rule that a new key's secret is shown once stays exactly as today.
  - Submit's three selectors become `SelectField`, and the file input becomes `FileField`.

- [ ] **Step 2: Verify.** Run the `make ui` line, which must be green. Then, with `make dev`:
  - Issue a key and see its secret once, then revoke it through the dialog.
  - Register a callback and revoke it.
  - On Submit, choose a tenant, a parser and a language, and attach a file.

- [ ] **Step 3: Review Focus 1, 2 and 4.**
  - **Focus 1:** on Submit, pick a parser that has languages, choose a language, then choose
    "Parser default" again and submit. Expected: the run starts with no language, as today.
  - **Focus 2:** stop the API (Ctrl-C the `make dev` API only, or point at a dead port) and revoke
    a key. Expected: the dialog closes and the failure Alert is visible on the page.
  - **Focus 4:** double-click Revoke in the dialog. Expected: one DELETE request in the browser
    network tab.

- [ ] **Step 4: Commit** with `git commit -m "feat(console): Credentials, Callbacks, Submit on shadcn; revoke confirms in a dialog"`.

---

### Task 7: Erase in a dialog, then remove Astryx

**Files:**
- Modify:
  - `ui/src/console/components/EraseArea.tsx`
  - `ui/src/console/main.tsx`
  - `ui/package.json`
  - `ui/package-lock.json`
  - `ui/LICENSES.md`
  - `ui/scripts/check-model-boundary.mjs`
  - `ui/scripts/check-console-a11y.mjs`
  - `ui/src/console/components/README.md`
  - `ui/src/console/model/README.md`
- Delete: `ui/src/astryx-theme-css.d.ts`
- Create: `docs/adr/0024-console-ui-on-shadcn.md`

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 5), `TextField` (Task 3).

- [ ] **Step 1: `EraseArea.tsx`.**
  - The target form (Tenant/Document toggle, identifiers, Continue) stays inline, with its current
    logic.
  - **Continue** sets `intent` as today. The confirmation card becomes a `ConfirmDialog` with:
    - `open={intent !== null}` and `onOpenChange={(o) => { if (!o) setIntent(null); }}`
    - title "Erase permanently?"
    - `description={confirmationPrompt(intent)}`
    - confirmLabel "Erase"
    - `confirmDisabled={!armed(intent)}`
    - children: `TextField label="Type the identifier again" autoComplete="off"`, wired through
      `retyped` as today
  - `onConfirm` sends the erase request.
    - On success: as today (`setOutcome`, `setIdentifier("")`), and `setIntent(null)`.
    - On failure: `onFailure(answer.failure)` and `setIntent(null)`, so the failure is not behind
      the overlay.
  - The outcome Alert stays inside the existing `aria-live="assertive"` region.

- [ ] **Step 2: Remove Astryx.**
  - Run `npm uninstall @astryxdesign/core @astryxdesign/theme-neutral @astryxdesign/cli`.
  - Drop the Astryx CSS imports and `<Theme>` from `main.tsx`.
  - Delete `src/astryx-theme-css.d.ts`.
  - In `LICENSES.md`, remove the three Astryx rows and the "On Astryx's `postinstall`" section.
  - Remove the `@astryxdesign/` rule from `check-model-boundary.mjs`, and `/<Banner\b/` from
    `TRANSIENT`.
  - Update the two READMEs' mentions of `@astryxdesign/*` to `@/components/ui/*`. In
    `components/README.md`, also say that `src/components/ui/` now holds generated shadcn
    primitives, scanned by the model-boundary network rule and the no-persistence guard but not
    by the a11y guard.

- [ ] **Step 3: ADR.** Write `docs/adr/0024-console-ui-on-shadcn.md` in the format of 0023:
  - **Context:** Astryx is pre-1.0, and the owner wants out.
  - **Decision:** shadcn/ui + Tailwind v4, with the primitives owned in the repo and no admin
    framework.
  - **Rejected:**
    - Ant Design: heavy, distinctive look, and Unstract left it in PR #2212.
    - Refine / react-admin: the model already does their job, and Unstract uses neither.
  - **Consequences:**
    - Native selects, because Radix Select rejects `""`.
    - A hand-built sidebar, because shadcn's writes a cookie.
    - Destructive actions confirm in an `AlertDialog`.
    - Unstract is AGPL, so layout ideas only.

  Add it to `docs/adr/README.md` if that file indexes ADRs.

- [ ] **Step 4: Verify**

```bash
cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run licenses && npm run build
grep -ri astryx . --exclude-dir=node_modules --exclude-dir=dist
```

Expected: everything green, and the grep prints nothing.

Then, with `make dev`:
- Erase a document on the demo tenant. Continue opens the dialog with Cancel focused. Erase stays
  disabled until the identifier is typed exactly, and the outcome is announced.
- Erase an unknown tenant (Review Focus 2): the dialog closes and the failure is visible.
- Do a keyboard-only pass over every area: sign in, navigate, filter, page, cancel, issue,
  revoke, submit, erase, sign out.

- [ ] **Step 5: Commit**

```bash
git add -A ui docs/adr
git commit -m "feat(console): erasure confirms in a dialog; remove Astryx (ADR-0024)"
```
