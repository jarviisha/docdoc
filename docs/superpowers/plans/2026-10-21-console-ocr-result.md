# Console OCR Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A document submitted from the console runs ahead of that tenant's other waiting OCR runs.
The run detail then refreshes on its own until the run finishes, and shows the page image beside
the recognised text, with a box over each recognised piece of text.

**Architecture:**
- **Priority:** console OCR submissions are given `Priority.URGENT`, threaded into the shared
  `submit_ocr`. `/v1/ocr` keeps `ordinary`.
- **Page images:** a new console route serves page *n* of a run's source document as an image. An
  image document is returned as it is; a PDF page is rendered with `pypdfium2` and encoded to PNG by
  a small stdlib encoder.
- **UI:** the run detail polls while the run is `queued` or `running`. When an OCR run has
  succeeded, it loads the `Document` and the page images and shows a split viewer: page image with
  an SVG box overlay on the left, recognised text on the right, and a two-way hover highlight.

**Tech Stack:** Python 3 / FastAPI / pypdfium2 (already in the `ocr` extra); React 19 + TypeScript
+ Astryx. No new dependency.

**Spec:** `docs/superpowers/specs/2026-10-20-console-upgrade-design.md`, decision 3 (result view
B). This plan replaces "Plan 3", reordered by the owner on 2026-10-21, with this deviation:

- **The rotation angle is not persisted in this plan.**
  - PaddleX's `doc_preprocessor_res.angle` is used and then dropped inside the adapter
    (`ingest/parsers/paddlex.py` `_oriented_size`). There is no field outside `Document` to keep it
    in. `StageOutcomeRecord`, the run row and the artifact envelope are all fixed-shape.
  - Persisting it means changing the parser's return path, which needs its own design.
  - What this plan does instead: when a page's `Document` aspect (width > height or not) disagrees
    with the rendered image's aspect, the viewer says "This page was turned by the OCR host; boxes
    may not line up." and still draws the boxes. The Tesseract kit never turns pages, so its
    results always line up.
  - The deviation is recorded for the owner to accept at plan review.

## Execution shape

| Lane | Tasks | Worktree |
|---|---|---|
| Backend | 1 | its own |
| Frontend | 2 → 3 | its own |

Then Task 4 integrates and checks the result against **real OCR**. The lanes' files are disjoint:
backend touches `src/` and `tests/`; frontend touches `ui/` and `docs/concepts/console.md`.

**Databases:**
- Tests use `DOCDOC_TEST_DATABASE_URL=postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc`.
- The demo server on :8765 and the OCR worker use `docdoc_demo`. **Never point tests at
  `docdoc_demo`.**
- **Never start, stop or create a container.** Never touch the :8765 server or the worker; the
  controller owns both.
- The OCR endpoints file `/tmp/docdoc-ocr-endpoints.json` holds a secret. Never print it, copy it,
  or commit it.

## API contract (both lanes build to this)

- **`POST /v1/console/tenants/{tenant_id}/ocr`** is unchanged, except that the created run's
  `priority` is `"urgent"`. `POST /v1/ocr` stays `"ordinary"`.
- **`GET /v1/console/runs/{run_id}/pages/{page}`**, where `page` is a 0-based integer.
  - **200**: the image bytes.
    - The source is PNG or JPEG and `page == 0`: the stored bytes, with their own
      `Content-Type` (`image/png` or `image/jpeg`).
    - The source is PDF and `0 <= page < page_count`: page `page` rendered to PNG, with the
      longer edge at most **2000 px** (scale = min(2000 / max(w_pt, h_pt), 200/72)), and
      `Content-Type: image/png`.
    - Every 200 carries `Cache-Control: private, max-age=300`.
  - **404**: `_unknown_run` for an unknown run, a page out of range, or a missing blob.
  - **409** `{"error":"no_page_images","detail":"…"}` for an extraction run. Page images are
    served for OCR runs only.
  - **503** `{"error":{"class":"PageRenderingUnavailable","message":"rendering PDF pages needs the docdoc[ocr] extra (pypdfium2)"}}`
    when `pypdfium2` cannot be imported and the source is a PDF.
  - Session-gated like every console GET. The handler is a plain `def`, because rendering
    blocks.
- **`GET /v1/console/runs/{run_id}/result`** is unchanged. It returns the `Document` JSON:
  - `text` is the canonical string.
  - `pages[]` holds `{index, span: [s, e], width, height, rotation}`.
  - `tokens[]` holds positional arrays `[[s, e], [page_index, [x0, y0, x1, y1]] | null, confidence | null]`.
    The bbox is normalised 0..1 with a top-left origin.
  - A token's text is `text[s:e]`, sliced by **Unicode code point**, not UTF-16 unit.

## Global Constraints

- **No new Python or npm dependency.** PNG encoding uses `zlib` and `struct` from the standard
  library. Pillow stays dev-only.
- **No change to `Document` or the kernel.** `kernel/`, `extraction/`, `grounding/` and
  `validation/` stay untouched (`tests/contract/test_protected_layers_untouched.py`).
- **The one OCR submit path stays.** Priority is a parameter of `submit_ocr`, not a second copy.
- **Clock and randomness.** Nothing under `src/docdoc/runs/` other than `identity.py` reads a
  clock or a random source.
- **Forbidden words** under `ui/src/console`: `queue` (`queued` is fine), `capacity`, `balance`,
  `assign`, `workload`, `approve`, `reject`, `escalate`, `disposition`. Components decide nothing.
  Use Astryx components only, with no raw `<input>`/`<select>`.
- **Guard lists:** `CONSOLE_ADDED` in `tests/contract/test_console_adds_one_read.py`, `ROUTES` in
  `test_http_ui_endpoints.py`, `test_jobs_routes_unchanged.py` (paths containing `runs`),
  `test_no_review_platform.py`, and `test_documented_api_references_resolve.py` (add this plan to
  `CONFIG_DOCUMENTS` if it names `DOCDOC_*` settings).
- **Backend gates:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/docdoc`,
  `uv run lint-imports`, and the full suite once at the end:
  `uv run pytest tests/unit tests/property tests/contract tests/integration -m "not perf" -q -o addopts=""`
  with the test database.
- **Frontend gates,** from `ui/`:
  `npm run typecheck && npm test && npm run lint:boundaries && npm run build`.
- **Visual check is mandatory for UI tasks.** Use Playwright screenshots, run with
  `cd /tmp && uvx --with playwright python <script>`, and read the PNGs.

## Review Focus

1. **Vietnamese text and code points.** Token spans are code-point offsets. Slicing a JS string by
   UTF-16 index breaks on characters outside the BMP, and on combining sequences if they are ever
   split.
   Test: Task 2, `test_token_text_is_sliced_by_code_point`, using a string with an emoji and
   Vietnamese diacritics before the token.
2. **A run that fails while the detail is open.** Polling stops, the failure banner appears, and no
   result or page request is made.
   Test: Task 2, `test_detail_polls_only_while_active`. Task 4 covers it visually.
3. **A multi-page PDF.** The pager moves through the pages, and each page's boxes come only from
   tokens whose `page_index` matches.
   Test: Task 2, `test_lines_are_grouped_by_page`.
4. **A page whose Document aspect disagrees with the image** (turned by PaddleX). The warning
   shows, and boxes are still drawn.
   Test: Task 2, `test_turned_page_is_flagged`.
5. **A token with no geometry** (a parser without geometry). The text list still shows it, with no
   box and no error.
   Test: Task 2, `test_tokens_without_geometry_have_no_box`.

---

### Task 1 (backend lane): console priority and the page-image route

**Files:**
- Modify: `src/docdoc/api/ocr.py`.
  - `submit_ocr(..., priority: Priority = Priority.ORDINARY)` passes `priority=int(priority)` into
    `_RunSpec`.
  - `/v1/ocr` leaves it unset.
- Modify: `src/docdoc/api/console.py`.
  - The console OCR route passes `Priority.URGENT`.
  - Add `GET /runs/{run_id}/pages/{page}`.
- Create: `src/docdoc/api/pages.py`, with these functions:
  - `encode_png(width: int, height: int, stride: int, channels: int, buffer: bytes, *, bgr: bool) -> bytes`.
    It handles RGB and RGBA, converts BGR(A) to RGB(A) when `bgr`, drops row padding, uses filter
    type 0, and compresses with zlib at level 6.
  - `render_pdf_page(data: bytes, page: int, *, max_edge: int = 2000) -> bytes`.
    - It raises `IndexError` when `page` is out of range.
    - It imports `pypdfium2` lazily, and raises `RuntimeError("pypdfium2")` when that import fails.
    - It renders and returns the PNG from `encode_png`, taking the bitmap's
      `.buffer`/`.width`/`.height`/`.stride`/`.n_channels`/`.mode`.
  - `page_image(data: bytes, page: int) -> tuple[bytes, str]`.
    - It dispatches on `detect_media_type`: PNG and JPEG return `(data, media_type)` for page 0
      and raise `IndexError` otherwise; PDF calls `render_pdf_page`.
    - Any other type raises `ValueError`.
- Modify: the guard lists.
- Test: `tests/unit/test_pages.py` (new), `tests/contract/test_console_pages.py` (new), and an
  extension of `tests/contract/test_console_submit_ocr.py`.

**Interfaces:** the produced names are exactly as above. The route maps exceptions as follows:

| Exception | Answer |
|---|---|
| `IndexError` | 404 |
| `RuntimeError("pypdfium2")` | the 503 from the contract |
| `ValueError` | 404 |
| unknown run, or a missing blob | 404 |
| extraction run | 409 |

- [ ] **Step 1: Write the failing tests**

```python
def test_encode_png_round_trips_with_pillow():          # dev-only Pillow decodes encode_png output of a 3x2 RGB and a 2x2 RGBA buffer with padding (stride > w*ch) back to the same pixels
def test_encode_png_swaps_bgr():                         # bgr=True buffer of pure blue (0,0,255 in BGR order = b'\xff\x00\x00') decodes to RGB (0,0,255)
def test_render_pdf_page_caps_the_long_edge():           # a generated 2-page PDF (make it with pypdfium2's PdfDocument.new + new_page(612,792)) → PNG decoded size max(w,h) <= 2000; page=2 → IndexError
def test_page_image_passes_png_through():                # PNG bytes, page 0 → (same bytes, "image/png"); page 1 → IndexError
def test_console_ocr_runs_are_urgent(signed_in, ...):    # console submit → body["priority"] == "urgent"; POST /v1/ocr (Bearer) → "ordinary"
def test_console_page_of_a_png_run(...)                  # seeded OCR run over a stored PNG blob → 200 image/png, same bytes, Cache-Control private
def test_console_page_of_a_pdf_run(...)                  # PDF blob → page 0 200 image/png; page 5 → 404
def test_console_page_of_an_extraction_run_is_409(...)
def test_console_page_refuses_a_bearer_key(...)
```

- [ ] **Step 2: Run them and confirm they fail.**
  Run: `uv run pytest tests/unit/test_pages.py tests/contract/test_console_pages.py -q -o addopts=""`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates and the full suite once.** Expected: green.
- [ ] **Step 5: Commit** with the message `feat(api): urgent console OCR and page images for OCR runs`.

---

### Task 2 (frontend lane): result model, binary transport, detail polling decision

**Files:**
- Create: `ui/src/console/model/result.ts`, `ui/test/console/result.test.ts`
- Modify: `ui/src/transport.ts`. Add
  `sendForBlob(plan: RequestPlan): Promise<{ ok: boolean; status: number; blob: Blob | null }>`.
  It sends the same console header and returns `blob` only when `ok`. The model boundary still
  holds, because this is the only fetch site.
- Modify: `ui/src/console/model/client.ts`. Add the intent
  `{ type: "run-page"; runId: string; page: number }` → `GET /v1/console/runs/{id}/pages/{page}`,
  and update `ALLOWED`.
- Modify: `ui/src/console/model/runs.ts`. Add `detailPolls(run: RunRecord | null): boolean`. It is
  true while `status` is `queued` or `running`, and false otherwise or when `run` is null.

**Interfaces:**
- `interface ResultLine { text: string; bbox: [number, number, number, number] | null; confidence: number | null; index: number }`
  - `index` is the token's position in `tokens[]`, used to pair hover across the two panes.
- `interface ResultPage { index: number; width: number; height: number; lines: ResultLine[] }`
- `toResultPages(document: unknown): ResultPage[]`
  - It reads `text`, `pages` and `tokens` as in the contract, and slices by code point:
    `Array.from(text)`, sliced, then joined.
  - Each token goes to the page in its `geometry[0]`. A token with `null` geometry goes to the page
    whose `span` contains the token's start.
  - Within each page, lines keep their token order.
  - A body without `text`/`pages`/`tokens` arrays throws.
- `isTurned(page: ResultPage, imageWidth: number, imageHeight: number): boolean`
  - True when `(page.width > page.height) !== (imageWidth > imageHeight)`.
  - Square cases, with a difference under 2 %, are false.
- `confidenceLabel(c: number | null): string` gives `"—"` for null and `"94 %"` for 0.94, rounded.
- `TURNED_NOTICE = "This page was turned by the OCR host; boxes may not line up."`

- [ ] **Step 1: Write the failing tests**

```ts
test("token text is sliced by code point", () => {
  const doc = { text: "😀 Hóa đơn: 1.250.000", pages: [{ index: 0, span: [0, 20], width: 100, height: 50, rotation: 0 }],
    tokens: [[[2, 9], [0, [0.1, 0.1, 0.5, 0.2]], 0.9], [[10, 20], [0, [0.1, 0.3, 0.9, 0.4]], null]] };
  const [p] = toResultPages(doc);
  assert.equal(p.lines[0].text, "Hóa đơn");
  assert.equal(p.lines[1].text, " 1.250.000".trim() === p.lines[1].text.trim() ? p.lines[1].text : "x"); // and assert exact slice
});
test("lines are grouped by page", () => { /* two pages, tokens with page_index 0/1 → each page gets only its own lines, order kept */ });
test("tokens without geometry have no box", () => { /* geometry null → bbox null, placed by span */ });
test("turned page is flagged", () => {
  const page = { index: 0, width: 800, height: 1100, lines: [] };
  assert.equal(isTurned(page, 1100, 800), true); assert.equal(isTurned(page, 800, 1100), false); assert.equal(isTurned({ ...page, width: 1000, height: 1010 }, 1010, 1000), false);
});
test("detail polls only while active", () => {
  assert.equal(detailPolls(null), false);
  for (const s of ["queued", "running"]) assert.equal(detailPolls({ status: s } as RunRecord), true);
  for (const s of ["succeeded", "failed", "cancelled"]) assert.equal(detailPolls({ status: s } as RunRecord), false);
});
test("run-page addresses the console page route", () => {
  assert.deepEqual(requestFor({ type: "run-page", runId: "r1", page: 2 }), { method: "GET", path: "/v1/console/runs/r1/pages/2", query: {}, hasBody: false });
});
test("confidence label", () => { assert.equal(confidenceLabel(null), "—"); assert.equal(confidenceLabel(0.936), "94 %"); });
```

  Write the code-point test with exact expected strings. The placeholder line above only shows
  intent; replace it with `assert.equal(p.lines[1].text, "1.250.000")`, adjusting the span so it
  points exactly at that substring.
- [ ] **Step 2: Run them and confirm they fail.** Run: `cd ui && npm test`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the frontend gates.** Expected: green.
- [ ] **Step 5: Commit** with the message `feat(ui): result model, binary transport, detail polling`.

---

### Task 3 (frontend lane): live run detail and the result viewer

**Files:**
- Create: `ui/src/console/components/ResultViewer.tsx`
- Modify: `ui/src/console/components/RunArea.tsx`, in `RunDetail`.
- Modify: `docs/concepts/console.md`. Add a short "Result view" section.

**Interfaces:**
- Consumes: Task 2's exports and the existing `usePolling`, `nextPoll` and `onVisible`.
- **RunDetail**
  - While `detailPolls(run)` holds, it re-reads `run-detail` every 5 s, with no overlap, paused
    while hidden, through `usePolling`.
  - When a re-read shows the run `succeeded` and its kind is OCR, it loads `run-result` once and
    renders `<ResultViewer>`.
  - Download result stays.
  - When a run fails, the existing failure banner shows, and no result is requested.
- **ResultViewer**
  - **Props:** `runId`, `pages: ResultPage[]`, and `request` for page images.
  - **Fetching images:** it fetches each page image with `sendForBlob({type: "run-page", …})` when
    that page is first shown, turns it into a blob URL with `URL.createObjectURL`, and revokes all
    URLs on unmount.
  - **Layout,** two columns:
    - **Left:** the page image, max width about 55 % of the content area, scaled to fit. Over it
      sits an absolutely positioned `<svg viewBox="0 0 1 1" preserveAspectRatio="none">` holding
      one `<rect>` per line with a bbox, drawn as a thin outline.
    - **Right:** a scrollable list of lines (text, plus `confidenceLabel` muted).
  - **Hover:** hovering a line highlights its rect (filled, tinted), and hovering a rect highlights
    the line and scrolls it into view.
  - **Pager:** "Page n of N", with Previous and Next buttons.
  - **Turned pages:** when `isTurned(page, img.naturalWidth, img.naturalHeight)`, it shows
    `TURNED_NOTICE` in a warning `Banner` above the image.
  - **Failures:** an image fetch failure shows a banner in place of the image, and the text list
    still renders.
  - Use Astryx `Card`, `Banner`, `Button` and `Text`. A small amount of inline style is fine for the
    positioned overlay.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Run the frontend gates.** Expected: green.
- [ ] **Step 3: Visual check.**
  - Run your own server from this worktree on port 8766, against `docdoc_demo`, read-only use.
    Use `DOCDOC_CONSOLE_ROOT` pointing at your built `ui/dist`, and give it the same
    `DOCDOC_STORE_ROOT=/tmp/docdoc-console-store` so it can read the existing blobs and results.
  - The demo database already has succeeded OCR runs. One is run `ba26af17-41d0-482c-b46d-17fc641a8703`,
    tenant `mmerp`, an image with two Vietnamese lines.
  - This lane has no page route, so the image will fail to load. Screenshot the viewer with the
    image-failure banner and the text list, then judge the layout.
  - Real images are checked in Task 4. Stop your 8766 server when done.
- [ ] **Step 4: Commit** with the message `feat(ui): live run detail and OCR result viewer`.

---

### Task 4 (after merging both lanes): integration against real OCR

- [ ] **Step 1: Merge the lanes.** Run the backend gates, the full suite and the frontend gates.
- [ ] **Step 2: Restart the demo server on the merged code.** The controller does this; the OCR
  worker keeps running.
- [ ] **Step 3: Drive the real flow with Playwright,** reading every screenshot.
  - Sign in. Submit a generated PNG with three lines of Vietnamese text (render it with Pillow),
    using tenant `mmerp` and parser `tesseract`.
  - It lands on the detail page. Watch it go from queued to succeeded **without clicking
    anything**.
  - The viewer appears. Hover a line and screenshot the highlight; hover a box and screenshot
    again.
  - Check that the boxes sit over the text.
  - Repeat with parser `paddle`, and with a 2-page PDF (generate it with pypdfium2), stepping
    through both pages.
  - Confirm the run's `priority` is `urgent` over HTTP.
- [ ] **Step 4: Commit any fixes** with the message `fix(console): <what>`.
