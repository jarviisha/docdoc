# OCR page orientation: keep PaddleX's turn so the result viewer lines up

Status: approved. Date: 2026-10-21.

## Intent

The console's result viewer draws each recognised line as a box over the page image. That works
for upright pages. PaddleX straightens a turned scan before it reads it, so its boxes are in the
**straightened** frame. docdoc throws away how much it turned the page, which leaves the viewer
drawing those boxes over the **unturned** upload.

A spike on 2026-10-21 against the real OCR host showed the problem:

- **Turned 90°.** Text read correctly. The boxes sat on blank paper. The existing warning showed.
- **Turned 180°.** Text read correctly. The boxes sat beside upside-down text, and **no warning
  appeared**, because the page's width and height don't change.
- Tesseract on that host doesn't straighten pages at all and returns garbage for turned scans.
  That is out of scope here; see Non-goals.

The owner hasn't hit this with real documents yet. They chose to fix it before they do.

**Success:** a page PaddleX turned by 90°, 180° or 270° appears **upright** in the viewer, with
boxes on the text, the same way an upright page does.

## Decisions

### The owner's

1. **Show the page straightened,** as the OCR read it, with boxes aligned. Don't show it as
   uploaded.
2. **The result contract mmerp consumes does not change.** No new field and no new value in the
   `Document` JSON. This was decided earlier, when choosing to keep the angle "outside Document".

### The designer's (owner said "theo bạn khuyên")

3. **Capture the angle in the PaddleX adapter** (`src/docdoc/ingest/parsers/paddlex.py`).
   - It already reads `doc_preprocessor_res.angle` per page in `_oriented_size`.
   - It keeps it as one of 0, 90, 180 or 270 per page.
   - A missing or unrecognised value means 0, which is what happens today.
4. **Persist the angles with the cached parse result, not only on the run.**
   - Parsing is content-addressed and cached. A second submission of the same file can reuse the
     stored parse without calling PaddleX, and the angles must survive that.
   - So they live next to the cached parse result, keyed so that whatever finds the `Document`
     also finds its angles. One option is a small sidecar record in the tenant's artifact store
     under the same identity.
   - The exact mechanism is the builder's choice, within the constraints below.
5. **Rotate in the browser.**
   - The console receives the page angles together with the result.
   - The viewer turns the page image with a CSS transform, swapping the container's aspect for 90°
     and 270°.
   - It then draws the boxes in the straightened frame on top.
   - The server neither decodes nor rotates images, and no dependency is added.
6. **The "turned page" warning shows only when the angle is unknown,** meaning runs parsed before
   this change, while the page's aspect still disagrees with the image. When the angle is known, no
   warning shows.

7. **A cache hit with no stored angles means "angle unknown".** This covers a file parsed before
   this change, or a lost angles write.
   - It is not a cache miss: no second OCR call is made, and the warning shows.
   - Re-parsing to heal old entries would cost another billable call, and it would risk a
     conflicting write to the same `document_id`. The store re-raises that conflict rather than
     swallowing it (FR-062).
   - This is the designer's call, raised by the builder check.

## Goals

- Pages PaddleX turned by 90°, 180° or 270° show upright, with aligned boxes. Images and PDF pages
  both work.
- The angles survive a cache hit.
- Old runs keep working: no angle means today's behaviour, warning included.
- Tesseract, `pdf-text`, GCV and Azure results are unaffected. For them the angle is always 0, or
  absent.

## Non-goals

- **Making Tesseract straighten pages.** That is an OCR-host change, enabling Tesseract's
  orientation detection in `packaging/ocr/tesseract`. It is a separate follow-up if the owner wants
  it.
- Exposing the angle to mmerp or on `/v1/runs/{id}/result`.
- Deskewing small tilts. PaddleX's angle is a quarter-turn classification, and so is this.

## Anti-goals

- **Changing the bytes of the stored result, or its identity.** The cached `Document`, its
  `document_id` and `processing_id` must be byte-identical to today's for the same input.
- **Rotating twice.**
  - `pdf-text` already maps boxes through the PDF's own `/Rotate`, recorded as `Page.rotation`.
  - pypdfium2 renders PDF pages already applying `/Rotate`.
  - The new angle is PaddleX's extra turn on top of the page as rendered, and only that may drive
    the CSS turn.
- **Touching the kernel or protected layers** (`kernel/`, `extraction/`, `grounding/`,
  `validation/`), because the `Document` model doesn't change.

## Current state (verified 2026-10-21)

- `paddlex.py` `_oriented_size` (~395–423) reads the angle and swaps width and height for 90 and
  270. `start_page(..., rotation=0)` (~543) is always 0, and the angle is then discarded.
- The worker's OCR path (`runs/worker.py` `_execute_ocr`) calls `parse_only(...)`, which returns
  `(Document, StageOutcome)`, and finishes the run. `StageOutcomeRecord`, the run row and the
  artifact envelope have no free-form field.
- The console has `GET /v1/console/runs/{id}/result`, which returns the `Document` JSON, and
  `GET /v1/console/runs/{id}/pages/{n}`, which returns the page image (PNG/JPEG as stored, PDF
  rendered via pypdfium2).
- The viewer (`ui/src/console/components/ResultViewer.tsx`, `model/result.ts`) draws boxes in an
  SVG `viewBox="0 0 1 1"` over the image. `isTurned` raises the warning when the aspects disagree.
- The spike runs in the demo database (tenant `mmerp`) are still there:
  - `16c5c5fe-e065-477e-a800-308339dd25eb`: PaddleX, turned 90°.
  - `777fb7cf-4731-412e-bf72-f4bf8b9c1b69`: PaddleX, turned 180°.

  Both were parsed before this change, so they have no angles. Re-submitting the same bytes reuses
  that cached parse and stays angle-less (decision 7). To test, generate freshly rotated files.

## Constraints

- **No new Python or npm dependency.**
- **One OCR submit path stays** (`submit_ocr`).
- **Clock and randomness rules** in `docdoc.runs` are unchanged: only `identity.py` reads them.
- **The angle for a page is one of 0, 90, 180 or 270, in degrees clockwise.** The builder must pin
  down PaddleX's sign convention with a real turned scan, so that "upright" is truly upright. The
  spike files can be regenerated by rotating a text image 90° and 180° with Pillow, which is
  dev-only.
- **Console API.** The angles reach the browser on a console route, either the result route or the
  detail route. Their name and shape are the builder's choice and must be recorded in the plan.
  `/v1/*` tenant routes do not change.
- **Visual check is mandatory.** Use Playwright screenshots against the demo server with the real
  OCR worker, for PaddleX at 90°, 180° and 270°, plus an upright page as a control.
- **Guard tests stay green:** route lists, data-model and documented-reference guards, and the
  protected-layers check.

## Left to the builder

- Where and how the angles are persisted next to the cached parse: a sidecar artifact, a store
  record, or something else. It must meet decision 4 and the anti-goals.
- How the adapter hands the angles out without changing `Document`, for example a return value
  alongside it on the PaddleX path.
- The console API field and the UI's rotation mechanics.
- Capturing the angles where every parse goes through, so that extraction runs, which share the
  cache, carry them too. Joining angles across PDF chunks. Keeping the record beside the artifact
  envelope, because `artifacts/envelope.py` and `derivation.py` are protected. Cascading it through
  erasure and retention.
