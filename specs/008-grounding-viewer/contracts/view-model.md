# Public Contract: The View Model

**Feature**: `008-grounding-viewer` | **Date**: 2026-08-25

This is the tested surface of Milestone 8. Under the first clarification of 2026-08-25 the rendered
interface carries no automated test, so everything the viewer *decides* lives here, behind functions
that take data and return data, and the rendering layer decides nothing (FR-041, FR-043).

Treating this as a contract rather than as internal structure is deliberate. It is the boundary of
what is verified, and a boundary nobody wrote down is a boundary that moves.

## 1. The rule

> **Any conditional that changes which value, box, page, state, or message a user sees belongs in the
> view model.**

A component may map over a list, apply a style, and attach a handler. The moment it decides *whether*
to show something, *which* thing to show, or *what to call* it, a requirement has escaped the tested
surface and has no coverage at all.

Enforced by automated checks, not by review (R9). Principle XII requires dependency boundaries to be
machine-checked, and this repository already holds the Python graph to that standard; `ui/scripts/`
holds the browser client to it. Four rules, and the last three were added after this section was
first written — a contract that describes less than the check does is the same defect as one that
describes more:

| Rule | Where | Serves |
|---|---|---|
| `src/model/**` imports nothing from `src/components/**`, React, or the renderer | `check-model-boundary.mjs` | FR-043 |
| Components call no network primitive — `fetch`, `XMLHttpRequest`, `sendBeacon`, `EventSource`, `WebSocket` | `check-model-boundary.mjs` | SC-013 |
| Components render no editing control — `<input>`, `<textarea>`, `<form>`, `contentEditable`, `onSubmit` | `check-readonly.mjs` | FR-029 |
| Nothing under `src/` touches `localStorage`, `sessionStorage`, `indexedDB`, or the Cache API | `check-readonly.mjs` | FR-032 |

The network rule exists because SC-013's claim — that across every user action, zero requests are
constructed that write to a store — is a guarantee only while construction happens in one tested
place. It did not: a component assembled the URL and called `fetch` itself, so the claim rested on
nobody adding a second call site. Requests are now built in `src/model/client.ts` and executed in
`src/transport.ts`, which decides nothing.

The storage rule is scoped to all of `src/` rather than to components, because a model file caching a
result would put the document at rest just as effectively.

The single exemption is the file picker: `<input type="file">` is the platform's only way for a
document to enter the viewer (FR-013), and a line claiming it must say so within four lines of use, so
the exemption is visible where it is taken rather than buried in the checker.

## 2. The functions

Names are indicative; the signatures are the contract.

| Function | Signature | Requirement |
|---|---|---|
| `toRunView` | `(result, pageCount) => RunView` | FR-015..FR-020, FR-055, FR-057 |
| `initialRequests` | `(RunView) => PageRequests` | FR-051, FR-053 |
| `requestPage` | `(PageRequests, pageIndex) => PageRequests` | FR-052 |
| `rendered` | `(PageRequests) => number[]` | FR-051, FR-052 |
| `selectivityNotice` | `(RunView) => string \| null` | FR-054 |
| `select` | `(RunView, fieldPath \| null) => RunView` | FR-021, FR-022 |
| `fieldForBox` | `(BoxEntry) => fieldPath` | FR-021, the reverse direction |
| `pagesForSelection` | `(RunView) => number[]` | FR-021, FR-022, US1/AC3 |
| `reduce` | `(RunState, Event) => RunState` | FR-028, FR-045..FR-049 |
| `viewOf` | `(RunState) => RunView \| null` | FR-025 |
| `toFailureView` | `(errorBody, pageCount) => FailureView` | FR-025 |
| `toDocumentView` | `(bytes) => DocumentView` | FR-013, FR-014, FR-058 |
| `toSchemaChoices` | `(listing) => SchemaChoice[]` | FR-026 |
| `requestFor` | `(intent) => Request` | FR-030, FR-032, SC-013 |

**`pagesToRender` is a field of `RunView`, not a function, and this table said otherwise** until
T100's pass. `toRunView` computes it from the same traversal that fills `boxesByPage`, which is what
makes `data-model.md`'s invariant between the two hold by construction rather than by maintenance; the
functions above act on it. `select` likewise takes `fieldPath | null` and never a box — the box
direction is `fieldForBox`, which was in the code, tested, and absent from this table. Three earlier
convergence passes found artifact drift in this milestone and the answer each time was that a claim
nobody maintains is a claim that lies. It applies with more force here than anywhere else: this
document is the definition of what is verified, so a reader checking coverage against it would look
for a `pagesToRender` function, fail to find one, and have no way to tell whether the contract or the
code had moved.

`viewOf` is on this list because of what its absence cost. A component read
`state.kind === "complete" ? state.view : null` and so could not reach a failed run's surviving values
at all, although the model already produced them — the question "which view is current?" is a decision,
and it had escaped to the one layer nothing tests (T091).

`pagesForSelection` was on neither this list nor any call path: it existed, it was tested, and no
component used it, so selecting a field lit its rectangles without going to them (T094). A tested
function with no caller is not coverage of anything.

Every one is pure: same input, same output, no clock, no network, no global state. Elapsed time enters
`reduce` as an event field rather than being read, which is what makes SC-015 testable without waiting.

## 3. The invariants

These are the claims the tests exist to check, stated once so a test can be read against them.

1. **Box count is preserved.** For each located value, the number of `BoxEntry` values emitted equals
   the number of boxes the run returned. Not "at least one" — *equal*. A value carrying three boxes
   that yields one is the failure SC-001 is written to catch.
2. **Coordinates pass through unchanged.** No transform, no rounding, no clamping, no merging of
   adjacent boxes into a hull. Per R1 the coordinates are already in displayed space; scaling to the
   rendered page belongs to the renderer at paint time.
3. **Ungrounded values emit no box, and are never omitted.** Both halves, counted together (SC-002).
4. **The three geometry states stay three.** `unavailable`, `empty`, and `located` produce three
   distinct outputs with three distinct labels. Neither of the first two is labelled "not found"
   (FR-018, SC-004).
5. **Absent is not ungrounded.** A field the model reported absent has no grounding outcome at all and
   is listed as absent (FR-019, SC-003).
6. **Scores never cross tiers.** No comparison, no ordering, no aggregation, no blended number. A score
   is emitted only with its tier (FR-020).
7. **Every fact is in the list.** Field, value, verdict, status, and pages appear in `ValueRow`
   regardless of whether any box exists, so the overlay is never the only path to a fact (FR-055,
   SC-017).
8. **Every distinction has a label.** Status, verdict, and geometry state each carry a textual
   equivalent, so no distinction can depend on a colour the model never supplied (FR-057).
9. **No run's boxes outlive it.** Choosing a document clears the prior view before any new box entry
   exists, and a result arriving under a stale token is discarded. Two doors, one failure, both closed
   here (FR-028, FR-049, SC-011, SC-015).
10. **Progress is elapsed time only.** No proportion, percentage, stage count, or estimate is derived
    in any state (FR-046).
11. **No request writes.** `requestFor` can construct no request that writes to a store and no
    correction, over the full set of user intents (FR-030, SC-013).
12. **Pages rendered are pages named.** `RunView.pagesToRender` holds exactly the pages carrying
    located values, and its length does not grow with document length; `requestPage` reaches every
    other page without adding to it (FR-051, FR-052, SC-016).
13. **A failure carries what survived it, and says so only when it did.** `toFailureView` reads the
    response's `results` into a `RunView`, and `failureNotice` claims results are shown exactly when
    `survivors` is non-null. An omitted stage yields `null`, never an empty view (FR-025).
14. **Everything offered can be drawn, and what cannot be drawn is still listed.** Every media type in
    `ACCEPTED` has a rendering path, and a document with none is extracted and listed anyway, with a
    notice about the missing picture rather than a refusal (FR-013, FR-014, FR-058).
15. **A row's verdict is the worst of its field's findings.** Not the first. Findings reach the viewer
    in `check_id` order, which within one field is alphabetical and therefore unrelated to severity,
    so a value that is both ungrounded (`warning`) and invalid (`error`) must still read `error`
    (FR-016, SC-017).

## 4. What this contract does not cover

Stated plainly, because a contract that implies more coverage than it has is the failure mode this
whole milestone was rearranged to avoid.

Nothing here verifies that a `BoxEntry` is *painted* at the right place, that a rotated page displays
as expected, that a labelled distinction is visually distinguishable, that the list is reachable
without a pointing device, or that a stale rectangle does not survive in the rendering layer after the
model cleared it. Those are properties of the rendered interface, and under clarification 1 a person
confirms them once rather than a test confirming them forever.

The seam is left here deliberately: a browser test, if one is ever added, attaches to these functions'
outputs and compares them against what is on screen. Nothing in this contract would have to change.
