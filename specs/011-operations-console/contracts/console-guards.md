# Contract: The Build Guards, and What Each One Cannot See

**Feature**: `specs/011-operations-console/` | **Date**: 2026-09-11

Four source checks and one contract test stand between this milestone and the thing the constitution
defers. Each is listed with what it asserts, where it runs, and — the part that usually goes
unwritten — **what it cannot catch**. A guard whose ceiling is undocumented is one a reader trusts
past its limit.

---

## 1. `check-readonly.mjs` — unchanged, and that is the finding

**Asserts**: no `<input>`, `<textarea>`, `<form>`, `contentEditable`, or `onSubmit` under
`ui/src/components/`; and no `localStorage`, `sessionStorage`, IndexedDB, or Cache API anywhere under
`ui/src/`.

**Change required by this milestone**: **none**. The editing scan is already scoped to
`src/components/`, which is the viewer's own directory — so a console with forms under
`src/console/components/` does not trip it, and the viewer's claim keeps being checked exactly as
Milestone 8 made it (FR-040, ADR-0019 §5). The persistence scan is already scoped to all of `src/`,
so it covers the console's credential from the first line written (FR-007).

The only edit worth making is a **comment** saying why the editing scan is the viewer's directory and
not `src/**/components`, so that a future author reading a console full of forms does not "fix" the
guard by widening it. Widening it would break the console; narrowing the viewer's claim to match
would break Milestone 8's.

**Cannot catch**: an editing control built by a component library call rather than by a JSX tag —
`<TextInput>` reads as a component, not as `<input>`. This was already true in Milestone 8 and is why
the check is a fence around a claim rather than a proof of it.

---

## 2. `check-model-boundary.mjs` — extended by two directories

**Asserts**: the model imports nothing that renders; components reach the network through
`src/transport.ts` or not at all.

**Change required**: add `src/console/model` and `src/console/components` to the scanned directories.
The rules themselves do not change, because the property does not: FR-041 needs the console's single
network call site for the same reason Milestone 8 needed the viewer's, and the console has more to
lose — it sends a credential, and a second `fetch` site is a second place that can fail to.

**Cannot catch**: a network call reached through a dependency rather than written in the tree. The
rule is about this repository's code.

---

## 3. `check-console-boundary.mjs` — new, and it is the constitutional fence

**Asserts**: none of the five nouns ADR-0019 §2 forbids appears as an identifier, a type name, a
property, or a user-visible string under `ui/src/console/`:

| Forbidden | Matches on |
|-----------|-----------|
| reviewer assignment | `assign`, `assignee`, `assigned` |
| a review queue | `queue`, `worklist`, `work_list`, `inbox` |
| workload | `workload`, `capacity`, `balance` in a per-person sense |
| a review state | `review_state`, `reviewState`, `under_review`, `in_review` |
| a disposition | `disposition`, `approve`, `approved`, `reject`, `rejected`, `escalate` |

**A second rule set, in the same script**: result content. `claimed_text`, `claimedText`,
`grounding`, `grounding_score`, `field_path`, `fieldPath`, and `findings` may not appear as a field,
a property, or a rendered binding anywhere under `ui/src/console/`, and nothing there may import or
address the result endpoint (FR-037, FR-022a). SC-016 promises *"one check
with no exception list"* and this is that check — without it the sentence names nothing. One script
rather than a fifth: both rule sets answer the same question, *what may this console not contain*,
and a file per prohibition is how a guard directory becomes something nobody runs.

**What rule set 2 cannot catch**: SC-016's word *derived*. A figure computed from a result and then
rendered under an innocent name — a field count called `total`, a grounding rate called `score` —
passes this scan. The list also deliberately omits `value` and `outcomes`: `value` is the prop of
every controlled input the console has, and `outcomes` is what a delivery's attempts are called. A
guard that fails correct code is one somebody switches off, and this script carries the five nouns
too, so switching it off would cost SC-006 as well.

**Both rule sets take an optional directory argument**, defaulting to `ui/src/console/`, so that
`ui/test/guards-can-fail.test.mjs` can point them at a fixture and watch them fail. A guard whose
scan path is hard-coded cannot be tested, and an untested guard is an assertion about an assertion.

**Why a word list and not a review**: because a principle is argued at review time and an identifier
is grepped. This is the same instrument, and the same argument, that `check-readonly.mjs` used to
keep Milestone 8's claim true for a year.

**Cannot catch**: the five concepts implemented under different names. A per-operator inbox called
`myThings` passes this check. What stops that is the constitution, the ADR, and a reviewer — the
guard catches the drift, not the deliberate evasion.

`# ponytail: word list. Upgrade path is nothing — a smarter check would start reasoning, and a guard
that reasons is one a future author argues past.`

---

## 4. `check-console-a11y.mjs` — new, and honest about its ceiling

**Asserts**, under `ui/src/console/components/`:

- no interactive handler (`onClick`, `onKeyDown`) attached to a non-interactive element without both
  a `role` and a tab stop;
- every control that takes input carries an accessible name — an `aria-label`, an `aria-labelledby`,
  or a `Field` wrapper that supplies one;
- the erasure confirmation and the credential entry are native form controls;
- **a file that renders a transient status message has a live region in it** — a `Banner`, or the
  literal loading message, requires an `aria-live` or a `role="status"` somewhere in the same file.

**The third rule was added by T076, after convergence found the requirement unwatched.** FR-045b
asks that a list loading, an erasure completing, and a credential being refused reach a screen
reader rather than only the eye. The console had **zero** live regions while the viewer had one, and
nothing was checking — which is the condition every guard in this directory exists to end.

**Why at the source**: the rendering layer carries no automated test by decision (`specs/008`), and
this milestone adds no browser driver. The only inspection available is reading the source, so it is
the one that runs — the reasoning `check-readonly.mjs` already records about itself.

**Cannot catch**: focus order, contrast, a dialog that does not trap focus — and, for the third rule
specifically, **whether the region wraps the message that matters**. It proves a live region exists
*in the file*, not that the right text is inside it and not that it fires at the right moment.
Matching a region to its message needs the tree this script deliberately does not build, and a
parser would cost the dependency research R4 spent an argument avoiding.

So the third rule catches the failure that actually happened — a console with no live region anywhere
in it — and not the subtler one where a region exists and announces the wrong thing. SC-014a claims
**reachable and named**, which is what rules one and two measure. FR-045c claims **no conformance
level**, which is what the documentation says. The gap between those sentences is deliberate and
stated rather than implied.

`# ponytail: file-level match. A real check needs a rendered accessibility tree, which needs a
browser driver — a dependency, a CI browser, and a class of flake this milestone declined.`

---

## 5. `tests/contract/test_no_review_platform.py` — untouched, and that is a requirement

**Asserts**: no route path contains any of fifteen words; the correction routes are exactly two; no
route accepts `PATCH`; `/ui*` routes accept only `GET`; `RunStatus` has exactly five members.

**Change required**: **none**, and FR-003 forbids one. The test was written by Milestone 10 without
knowing this milestone would be measured against it, which is what makes it evidence rather than
decoration.

**Two notes for the implementer**:

- `GET /v1/runs` contains none of the fifteen words. It passes as named.
- `test_the_viewer_has_no_write_path` checks paths starting with `/ui`. The console mounts at
  `/console` and its writes go to `/v1/admin/*`, so the assertion stays true and stays about the
  viewer. Do not widen it to `/console`: the console **does** write, by design, and a guard changed
  to accommodate the thing it was guarding against has stopped being one.

---

## What none of them check

That the console is **useful**. Every guard here is a prohibition; none asserts that a run list
loads, that a revocation reaches the API, or that an operator can find a failed run. That is what
`tests/contract/test_console_http_api.py`, `tests/integration/test_run_listing.py`, and the console
model's `node --test` suite are for, and it is why the guards are listed separately from them: a
milestone can pass every fence in this document and ship nothing that works.
