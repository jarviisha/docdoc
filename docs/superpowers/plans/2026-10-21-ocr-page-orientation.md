# OCR Page Orientation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When PaddleX turned a page before reading it, the console shows that page upright with
the boxes on the text. To make that possible, the per-page turn is kept beside the cached parse,
and the browser rotates the page image.

**Architecture:**
- The PaddleX adapter reports a per-page angle through an optional parser method. The parse stage
  stores the angles as a small **sidecar artifact**, put through the normal `ArtifactStore.put`
  under an id derived from the `document_id`. The `Document` and its identity are untouched.
- Retention and document erasure delete the sidecar together with the parse artifact. Tenant
  erasure already removes it, because it deletes the whole prefix.
- The console result route adds `page_angles` to its body.
- The viewer rotates the image inside a box shaped like the straightened page and draws the boxes
  in that frame.

**Tech Stack:** Python 3 / FastAPI; React 19 + TypeScript + Astryx. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-21-ocr-page-orientation-design.md`.

## Execution shape

| Lane | Tasks | Worktree |
|---|---|---|
| Backend | 1 | its own |
| Frontend | 2 | its own |

Task 3 then integrates and checks the work visually against real PaddleX. The lanes' files are
disjoint: backend touches `src/` and `tests/`; frontend touches `ui/` and `docs/concepts/console.md`.

**Databases and processes:**
- Tests use `DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc`.
- The demo server on :8765 and the OCR worker use `docdoc_demo`, and the controller owns both.
- **Never start or stop a container, the server or the worker.** Never read
  `/tmp/docdoc-ocr-endpoints.json`.

## API contract (both lanes build to this)

**`GET /v1/console/runs/{run_id}/result`** for an OCR run returns the `Document` JSON exactly as
today, **plus one top-level key**:

- `"page_angles": [0, 90, 180, 270, …]` holds one integer per page, in `pages` order, in degrees
  clockwise. It is **the turn the viewer must apply to the page image so that it matches the
  frame the boxes are in**.
- `"page_angles": null` means unknown: a run parsed before this change, a parser that does not
  report orientation, or a lost sidecar.
- The tenant route `GET /v1/runs/{run_id}/result` does **not** change. It never carries
  `page_angles`.
- An extraction run's console result is unchanged.

**Viewer behaviour:**
- An angle of 90 or 270 shows the image rotated inside a box with the straightened page's aspect,
  that is the image aspect swapped.
- An angle of 180 rotates the image in place.
- An angle of 0 changes nothing.
- The boxes are drawn in the straightened frame, normalised 0..1, over that box.
- The "turned page" warning shows **only** when `page_angles` is null **and** `isTurned(…)` holds.

## Global Constraints

- **No new Python or npm dependency.**
- **`Document`, `document_id` and `processing_id` stay byte-identical** for the same input. No
  change under `kernel/`, `extraction/`, `grounding/` or `validation/`, nor to
  `artifacts/envelope.py` or `artifacts/derivation.py`
  (`tests/contract/test_protected_layers_untouched.py`).
- **The one OCR submit path stays.** No change to `/v1/*` tenant responses.
- **Clock and randomness:** only `docdoc.runs.identity` reads them.
- **Forbidden words** under `ui/src/console`: `queue` (`queued` is fine), `capacity`, `balance`,
  `assign`, `workload`, `approve`, `reject`, `escalate`, `disposition`. Components decide nothing.
  Use Astryx only.
- **Gates.**
  - Backend: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/docdoc`,
    `uv run lint-imports`, and the full suite once:
    `uv run pytest tests/unit tests/property tests/contract tests/integration -m "not perf" -q -o addopts=""`
    with the test database.
  - Frontend, from `ui/`:
    `npm run typecheck && npm test && npm run lint:boundaries && npm run build`.
- **Guard lists:** keep `test_documented_api_references_resolve.py` green by adding this plan to
  `CONFIG_DOCUMENTS` if it names `DOCDOC_*` settings. Also keep the data-model guard green.

## Review Focus

1. **A cache hit.** Submit the same bytes again after the change, and the angles come back from the
   sidecar with no second OCR call.
   Test: Task 1, `test_angles_survive_a_cache_hit`, which counts parser calls.
2. **A cached `Document` with no sidecar** (parsed before the change) gives `page_angles: null`,
   with no error and no re-parse.
   Test: Task 1, `test_a_parse_without_a_sidecar_reports_unknown`.
3. **Retention or document erasure** removes the sidecar along with the parse artifact. No orphan
   is left.
   Test: Task 1, `test_erasure_removes_the_orientation_sidecar`.
4. **A multi-chunk PDF.** The angles join across chunks in page order, one per page.
   Test: Task 1, `test_angles_join_across_pdf_chunks`.
5. **The browser draws boxes in the right frame for 90/180/270,** and shows no warning when the
   angle is known.
   Test: Task 2, `test_view_frame_for_each_angle` and `test_warning_only_when_angle_unknown`. Task 3
   checks it visually.

---

### Task 1 (backend lane): capture, store, erase and serve the angles

**Files:**
- Modify: `src/docdoc/ingest/parsers/paddlex.py`.
  - Keep each page's angle. `_oriented_size` already reads it; normalise it to 0, 90, 180 or 270,
    and treat anything else as 0.
  - Add `parse_with_orientation(self, source, options, transport, text_layer=None) -> tuple[Document, tuple[int, ...]]`.
    `parse()` stays and returns only the `Document`.
  - Joining across PDF chunks keeps page order.
  - **Sign convention:** pin it so the angle is the clockwise turn the viewer applies to the
    original page image to match the boxes' frame. Derive it from PaddleX's documented
    `doc_preprocessor_res.angle` meaning, and assert it in a unit test on a recorded response
    fixture. Task 3 confirms it visually.
- Modify: `src/docdoc/ingest/parse.py`. Add
  `execute_plan_oriented(plan, *, started=None) -> tuple[Document, tuple[int, ...] | None]`.
  - It calls `parser.parse_with_orientation` when the parser has it, and otherwise `parse`, giving
    `None`.
  - It runs the same `validate_output` and `log_parse` as `execute_plan`. `execute_plan` keeps its
    behaviour: it either delegates or is left as is, with no duplicated logic.
- Create: `src/docdoc/pipeline/orientation.py`, containing:
  - `class PageOrientation(BaseModel)`: frozen, `extra="forbid"`, with `angles: tuple[int, ...]`.
    A validator allows only 0, 90, 180 and 270.
  - `ORIENTATION_FORMAT_VERSION = 1`
  - `orientation_id_for(document_id: str) -> str`. It returns a `sha256:<hex>` id derived from
    `document_id` with a distinct domain tag, for example `content_id_for` over
    `{"v": 1, "orientation_of": document_id}`. That function is in the kernel and may be called but
    not edited. The id must never collide with a real artifact id.
  - `read_orientation(store, document_id: str) -> tuple[int, ...] | None`. It returns `None` when
    the sidecar is absent or the store is unreadable.
- Modify: `src/docdoc/pipeline/runner.py` `_parse_stage`.
  - On a miss, call `execute_plan_oriented`. Put the `Document` exactly as today, then, if the
    angles are not `None`, `reuse.put(orientation_id_for(document_id), PageOrientation(angles=…), stage="parse", input_artifact_id=document_id, processor_id=plan.parser.id, processor_version=plan.parser.version, options_hash=plan.options_hash, artifact_format_version=ORIENTATION_FORMAT_VERSION)`.
  - A hit is unchanged: no re-parse, and the sidecar is not touched.
  - The return type of `_parse_stage` and `parse_only` is unchanged.
- Modify: `src/docdoc/runs/retention.py`. `_artifact_ids` and its kept-set counterpart also include
  `orientation_id_for(aid)` for every artifact id they list, so `_remove()` deletes sidecars with
  their parse artifacts. Deleting an absent id is harmless.
- Modify: `src/docdoc/api/ocr.py` `_ocr_document`. It gains a keyword `with_angles: bool = False`.
  When true, it returns `{**document_json, "page_angles": list(angles) if angles is not None else None}`,
  using `read_orientation(store, run.processing_id)`. The console result route passes `True`; the
  tenant route keeps the default.
- Modify: data-model or documented-reference guard docs, if a new persisted record must be listed.
- Test: `tests/unit/test_paddlex_orientation.py`, `tests/unit/test_orientation_sidecar.py`, and
  `tests/contract/test_console_result_angles.py` (all new). Extend the retention or erasure tests.

**Interfaces:** the produced names are exactly as above.

- [ ] **Step 1: Write the failing tests**

```python
def test_paddlex_reports_one_angle_per_page():            # recorded-style response with doc_preprocessor_res.angle 0/90/180/270 → angles tuple, Document identical to parse()'s
def test_unknown_angle_value_means_zero():                # angle 45 or missing → 0
def test_angles_join_across_pdf_chunks():                 # max_pages=1 endpoint, 2-page PDF, fake _post returning per-chunk angles → (90, 180)
def test_document_bytes_unchanged():                      # model_dump of parse() vs parse_with_orientation()[0] identical
def test_orientation_id_is_derived_and_distinct():        # orientation_id_for(d) startswith "sha256:", != d, stable
def test_angles_survive_a_cache_hit(tmp_path):            # parse_only twice with a counting fake oriented parser + FileArtifactStore: parser called once; read_orientation → angles both times
def test_a_parse_without_a_sidecar_reports_unknown():     # Document stored without sidecar → read_orientation None
def test_erasure_removes_the_orientation_sidecar():       # document erasure / sweep path: sidecar file gone along with the parse artifact
def test_console_result_carries_page_angles(signed_in…):  # seeded OCR run whose processing_id has a sidecar → body["page_angles"] == [90]; without sidecar → None
def test_tenant_result_never_carries_page_angles(…):      # GET /v1/runs/{id}/result (Bearer) → "page_angles" not in body
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates and the full suite once.** Expected: green.
- [ ] **Step 5: Commit** with the message `feat(ocr): keep PaddleX page angles beside the cached parse; console serves them`.

---

### Task 2 (frontend lane): rotate the page image by the known angle

**Files:**
- Modify: `ui/src/console/model/result.ts`.
  - `ResultPage` gains `angle: number | null`.
  - `toResultPages` reads the body's `page_angles`: an array gives each page its own angle, and
    null or missing gives `null`.
  - Add `viewFrame(angle: number | null, imageWidth: number, imageHeight: number): { boxWidth: number; boxHeight: number; rotate: number }`.
    For 90 and 270, `boxWidth = imageHeight` and `boxHeight = imageWidth`; otherwise the image
    size. `rotate` is the angle, or 0 when null.
  - Add `showsTurnedWarning(page: ResultPage, imageWidth: number, imageHeight: number): boolean`,
    which is `page.angle === null && isTurned(page, imageWidth, imageHeight)`.
- Modify: `ui/src/console/components/ResultViewer.tsx`.
  - **Outer box:** `position: relative`, `width: 100%`, with `aspect-ratio: boxWidth / boxHeight`,
    and a border as today.
  - **Inner image:** absolutely centred, sized to the **unrotated** image proportions relative to
    the box. For 90 and 270, its width is `boxHeight / boxWidth * 100 %` of the box width and its
    height is `boxWidth / boxHeight * 100 %` of the box height, with
    `transform: translate(-50%, -50%) rotate(<rotate>deg)` and `transform-origin: center`.
  - **SVG overlay:** stays `inset: 0` over the **outer** box, with `viewBox="0 0 1 1"` and
    `preserveAspectRatio="none"`. The boxes are already in the straightened frame.
  - Before the image's natural size is known, render the box with the page's own aspect
    (`page.width / page.height`).
  - The warning uses `showsTurnedWarning`.
  - Hover, the pager and Retry are unchanged.
- Modify: `docs/concepts/console.md`. One sentence: turned pages are shown upright when the OCR
  host reported the turn.
- Test: `ui/test/console/result.test.ts`.

- [ ] **Step 1: Write the failing tests**

```ts
test("view frame for each angle", () => {
  assert.deepEqual(viewFrame(0, 800, 1200), { boxWidth: 800, boxHeight: 1200, rotate: 0 });
  assert.deepEqual(viewFrame(90, 520, 1200), { boxWidth: 1200, boxHeight: 520, rotate: 90 });
  assert.deepEqual(viewFrame(180, 1200, 520), { boxWidth: 1200, boxHeight: 520, rotate: 180 });
  assert.deepEqual(viewFrame(270, 520, 1200), { boxWidth: 1200, boxHeight: 520, rotate: 270 });
  assert.deepEqual(viewFrame(null, 520, 1200), { boxWidth: 520, boxHeight: 1200, rotate: 0 });
});
test("page angles are read per page", () => { /* body with page_angles [0, 90] → pages[0].angle 0, pages[1].angle 90; missing → null */ });
test("warning only when angle unknown", () => {
  const p = { index: 0, width: 1200, height: 520, lines: [], angle: 90 };
  assert.equal(showsTurnedWarning(p, 520, 1200), false);
  assert.equal(showsTurnedWarning({ ...p, angle: null }, 520, 1200), true);
});
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend gates.**
- [ ] **Step 5: Visual check.** No real angles exist in this lane. Serve this worktree's `ui/dist`
  on port 8766 against `docdoc_demo`, and intercept `/result` in Playwright to inject
  `page_angles` of `[90]` and `[180]` for the spike runs `16c5c5fe-e065-477e-a800-308339dd25eb`
  and `777fb7cf-4731-412e-bf72-f4bf8b9c1b69`.
  - Screenshot each and read the PNGs. The page must appear upright, with boxes over the text.
  - The sign may come out wrong. Report what you see; Task 3 settles it against real data.
  - Stop your server afterwards.
- [ ] **Step 6: Commit** with the message `feat(ui): show turned pages upright with aligned boxes`.

---

### Task 3 (after merging both lanes): real PaddleX, 0/90/180/270

- [ ] **Step 1: Merge the lanes.** Run the backend gates, the full suite and the frontend gates.
- [ ] **Step 2: Restart the demo server and the OCR worker on the merged code.** The controller
  does this, because the worker now runs the new adapter code.
- [ ] **Step 3: Check against real PaddleX.**
  - Generate fresh images with Pillow: three Vietnamese lines, rotated 0, 90, 180 and 270 (Pillow
    `rotate(n, expand=True)`). Add a 2-page PDF in which page 2 is turned 90°.
  - Submit each through the console with parser `paddle`, and wait for success.
  - Screenshot the viewer and read every PNG. Each page must be **upright**, with boxes on the
    text, and no warning.
  - Over HTTP, `page_angles` must match the turns.
  - Re-submit one of the files, a cache hit, and confirm `page_angles` is still present and no new
    OCR call appears in the worker log.
  - If the sign is wrong (pages upside down or mirrored), fix the convention in the adapter, which
    is the single source, together with its unit test.
- [ ] **Step 4: Commit any fixes** with the message `fix(ocr): <what>`.
