# Data Model: Read-Only Grounding Viewer

**Feature**: `008-grounding-viewer` | **Date**: 2026-08-25

Everything here is browser-side and derived. **No entity in this document is persisted, transmitted to
the server, or added to any docdoc model.** The engine's types are unchanged; these are the shapes the
view model produces from a run's result so that a renderer has nothing left to decide (FR-041, FR-043).

The type names below are the tested surface. The rendering layer consumes them and adds nothing.

---

## `RunView`

What one completed run becomes. Produced once per result; replaced wholesale, never mutated (FR-028).

| Field | Type | Notes |
|---|---|---|
| `values` | `ValueRow[]` | Every value in the result, in the schema's field order. Length is the criterion of SC-003. |
| `boxesByPage` | `Map<number, BoxEntry[]>` | Grouped for rendering; the flattened count must equal the sum of every `ValueRow.boxes`. |
| `pagesToRender` | `number[]` | Exactly the pages named by located values (FR-051, SC-016). |
| `pageCount` | `number` | The document's full length, so the interface can say what it is *not* showing (FR-054). |
| `selection` | `string \| null` | The selected `field_path`, or nothing selected. |

**Invariant**: `pagesToRender` contains no page absent from `boxesByPage`, and no page in
`boxesByPage` is absent from `pagesToRender`. The two are derived from one traversal precisely so they
cannot disagree.

---

## `ValueRow`

One row in the authoritative list. Under FR-055 this carries every fact the overlay conveys, so a
reader who never sees a rectangle loses nothing.

| Field | Type | Notes |
|---|---|---|
| `fieldPath` | `string` | The identity used for selection in both directions (FR-021). |
| `value` | `string \| null` | Rendered form. `null` only where the model reported the field absent. |
| `presence` | `'asserted' \| 'absent'` | The distinction of FR-019. An absent field has no grounding outcome at all — it is not "ungrounded". |
| `verdict` | `string` | The validation verdict as produced. Never recomputed here. |
| `status` | `'exact' \| 'fuzzy' \| 'ungrounded' \| null` | `null` when there is no grounding outcome — either the field was absent, or grounding never ran (a partial result). `labels.status` tells those two apart; this field does not. Closed vocabulary; ADR-0005 makes a fourth member an amendment. |
| `score` | `ScoreView \| null` | See below. Never a bare number. |
| `geometry` | `GeometryState` | See below. Three states, never two. |
| `pages` | `number[]` | May hold more than one entry (FR-022). |
| `boxes` | `BoxEntry[]` | Empty for anything not located. Length equals the run's box count for this value — the criterion of SC-001. |
| `labels` | `Record<string, string>` | Textual equivalents for status, verdict, and geometry state, so no distinction can depend on colour (FR-057, SC-017). |

**Invariant**: a row's labels never contradict its own fields. `labels.status` and `labels.geometry`
are derived from **one** value — which of three situations the row is in: the field was reported
absent, grounding never ran, or grounding reached a conclusion — so a row cannot say `asserted` and
"Reported absent" at the same time. Three defects in this milestone were the same defect reached by
different routes, each a label derived from `outcome === undefined`, which means "grounding has
nothing to say" and not "there is nothing here". `test/labels.test.ts` holds the rule over every stage
combination a run can fail in.

---

## `ScoreView`

| Field | Type | Notes |
|---|---|---|
| `value` | `number` | As produced. |
| `tier` | `'exact' \| 'fuzzy'` | Travels with the number, always. |

**Invariant**: the view model exposes **no ordering, comparison, or aggregation across tiers**, and no
combined confidence. An exact score is `1.0` by definition and a fuzzy score is a measured similarity;
ranking one against the other is meaningless and ADR-0004 forbids it (FR-020). A sort control over
`score` would violate this, which is why the view model offers none to violate.

---

## `GeometryState`

A discriminated union with exactly three members, because the engine keeps three facts apart and
FR-018 forbids collapsing them.

| Member | Comes from | Means |
|---|---|---|
| `{ kind: 'unavailable' }` | `geometry === null` | The parser supplied no geometry at all. |
| `{ kind: 'empty' }` | `geometry === []` | Geometry exists; this range covers no tokens. |
| `{ kind: 'located', boxes }` | `geometry === [...]` | Boxes, at least one. |

**Invariant**: `'unavailable'` and `'empty'` both render no rectangle and **must not** produce the same
label. A viewer that shows either as "not found" has destroyed a distinction the engine spent code to
preserve, and SC-004 counts exactly this.

---

## `BoxEntry`

One rectangle. A 1:1 image of one `Geometry` from the result — never a merge, never a hull.

| Field | Type | Notes |
|---|---|---|
| `pageIndex` | `number` | From the result. |
| `x0`, `y0`, `x1`, `y1` | `number` | Normalized `0.0..1.0`, top-left origin, **as received**. |
| `fieldPath` | `string` | The reverse lookup of FR-021. |

**Invariant**: coordinates are passed through unchanged. Per R1 they are already in *displayed* space —
the parsers resolved rotation before normalizing — so applying `Page.rotation`, flipping an axis, or
composing any transform beyond scaling to the rendered page size is a defect, not an adjustment
(FR-023, FR-024). Scaling belongs to the renderer at paint time and never to this value.

---

## `RunState`

The state machine. Its transitions are what SC-011 and SC-015 measure, and it takes elapsed time as an
**input** rather than reading a clock, which is what keeps the view model pure (FR-042).

| State | Fields | Meaning |
|---|---|---|
| `idle` | — | A document and schema may be chosen. |
| `running` | `token`, `elapsedMs` | A run is in flight. |
| `complete` | `view: RunView` | A result arrived and is current. |
| `failed` | `failure: FailureView` | A run failed; completed stages survive. |

**Transitions**

| From | Event | To | Rule |
|---|---|---|---|
| `idle` | run started | `running` | Issues a fresh `token`. |
| `running` | tick | `running` | Only `elapsedMs` changes. No proportion, percentage, stage count, or estimate is ever derived (FR-046, SC-015). |
| `running` | result, matching `token` | `complete` | |
| `running` | result, **stale** `token` | *unchanged* | **Discarded.** The document is no longer the selected one (FR-049, SC-015). |
| `running` | failure, matching `token` | `failed` | |
| any | document chosen | `idle` | Clears the prior `RunView` **before** any new box entry exists (FR-028, SC-011), and invalidates the outstanding `token` by leaving none to match. `idle` and not `running`: choosing a document does not start a run, and `canStartRun` is `kind !== 'running'`, so a `running` target would leave the run control permanently disabled and make US1 unreachable. |

**Invariant**: no state carries box entries from more than one run. The stale-token rule and the
clear-on-choose rule are two doors onto the same failure — a rectangle from one document drawn over
another — and both are closed here rather than in a component, because a component closing them is a
decision outside the tested surface.

There is no `cancelling` state and no cancel event: stopping the wait does not stop the run, and the
provider is paid either way (FR-047).

---

## `FailureView`

| Field | Type | Notes |
|---|---|---|
| `stage` | `string` | The stage at fault, as the server named it. |
| `errorClass` | `string` | The typed docdoc error class. |
| `message` | `string` | docdoc's own message. Never a provider's error text. |
| `completed` | `StageResult[]` | Which stages ran and how they ended — `{ stage, status }`, from the response's `outcomes`. |
| `survivors` | `RunView \| null` | **What those stages produced** (FR-025), from the response's `results`. `null` when the run stopped before anything survived. |

The last two rows are two different facts and the split is the point. "extract executed" is not the
extracted values, and until T091 only the first was carried — so a mid-run failure could report that
three stages had completed while having discarded everything they produced, and the notice said their
results were shown.

`survivors` is an ordinary `RunView`, produced by `toRunView` from the same stage results a successful
run returns under different key names (`extract`/`ground`/`validate` against
`extraction`/`grounding`/`validation`). A partial result is therefore listed, labelled and drawn by the
same tested code as a complete one; there is deliberately no second presentation, because two
presentations are how the two come to disagree about what a grounding status means.

**Invariant**: a failure is never rendered as an empty result. Milestone 7's FR-066 preserves partial
results precisely so that this view can show them, and discarding them here would waste the guarantee
at the last step.

**Invariant**: an omitted stage and a stage that produced nothing stay distinct. A response with no
`extract` key yields `survivors: null`, never an empty `RunView` — the same distinction FR-018 spends
a union on for geometry, one layer up.

---

## `DocumentView`

What the browser can do with the bytes the user picked, decided from the bytes and never from the
file's name or the type the picker declared.

| Field | Type | Notes |
|---|---|---|
| `kind` | `'pdf' \| 'image' \| 'unrenderable'` | Which rendering path, or none. |
| `mediaType` | `string \| null` | From the magic bytes. `null` when no signature matches. |
| `notice` | `string \| null` | Why there is no picture. Never why a document was *rejected* — it was not. |

**Invariant**: every media type the picker offers has a rendering path. Until T092 it offered three and
the code opened one: `accept` listed PNG and JPEG and every file went to a PDF renderer that opens
neither, with the rejection unhandled — so an image produced no page, no message, and a run control
that still spent the deployment's provider budget. The accepted set and the dispatch are now one fact
in one module, and a test fails if they diverge.

**Invariant**: a document the browser cannot draw is still extracted and still listed. This is FR-058
rather than leniency — the list is authoritative and the overlay is an aid, so removing the aid must
remove no fact. A TIFF, which the engine accepts and no browser draws, runs and lists every value with
its status and verdict; only the rectangles are missing, and the interface says so.

---

## `SchemaChoice`

| Field | Type | Notes |
|---|---|---|
| `identity` | `string` | `name@version`, exactly as `POST /v1/extract` accepts it. |

Carries nothing else. No path, no field list — the listing endpoint is unauthenticated (R10), and a
choice needs only the string an extraction request takes.

**Empty case**: an empty list is a valid deployment state, not an error, and the view model produces a
message naming the setting that populates it (FR-026).
