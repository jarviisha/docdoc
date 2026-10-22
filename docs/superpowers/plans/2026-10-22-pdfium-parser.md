# Native PDF parsing on PDFium — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PyMuPDF (AGPL) with `pypdfium2` (Apache-2.0/BSD-3) in the native `pdf-text` parser, and in every PDF generator, so PyMuPDF leaves the project entirely and the default Docker image parses digital PDFs.

**Architecture:** `pdf_text.py` reads characters from PDFium's text page and groups them into words and lines itself. It resets the page rotation to 0 before reading, which keeps content-stream order, and maps each box to displayed space with a pure function. One re-entrant lock in `docdoc.ingest` serializes every PDFium call in the process. Fixture and dataset PDFs are written with `reportlab` (BSD, dev-only).

**Tech Stack:** Python 3.11+, `pypdfium2>=5.0` (currently 5.14.0, PDFium 156.0.8076.0), `reportlab` (dev), pytest + xdist, import-linter.

**Spec:** `docs/superpowers/specs/2026-10-22-pdfium-parser-design.md`

## Global Constraints

- No AGPL or other copyleft dependency may remain reachable from the runtime, the image, or `uv.lock`, including "optionally". At the end, `grep -n pymupdf uv.lock` prints nothing.
- Breaking changes are allowed (pre-1.0, ADR-0011). Identities, `parser_version`, `reading_order` and committed fixture bytes may change, with no migration or shim.
- The golden set's `grounding_rate` must stay **1.0000 (30/30)**. Baseline with PyMuPDF: field_accuracy 0.9286 (26/28), coverage 0.9600 (24/25), missing 0.0400, incorrect 0.0400.
- Words must match PyMuPDF's word sequence on every fixture, and normalized boxes must be within **0.002** of it, measured before PyMuPDF is removed.
- Historical docs under `specs/` and `docs/superpowers/plans/` (other than this plan) are records and are not edited.
- Run tests as `AGENTS.md` says: `uv run pytest -n0 <files>` while iterating, `make test` before each commit that touches more than one test file.
- **Updating a test pin is allowed only for identity, version, or bytes changes.** That covers hashes, `parser_version` strings, `reading_order` names, blob ids, and snapshot identity fields. A changed word, word order, box beyond 0.002, text-layer verdict, or error type is a behaviour change: stop and report it instead of editing the test.

## Review Focus

1. **Concurrent PDFium use in one API process.** A `pdf-text` parse in the threadpool can overlap a console page render and a PaddleX split, and PDFium is not thread-safe. Every PDFium call site must hold `PDFIUM_LOCK`. Task 1 pins the shared lock object; Task 2 pins that the parser takes it.
2. **Pages rotated 180° or 270°.** There is only a 90° fixture. `displayed_box` gets a numeric test for all four rotations (Task 2).
3. **A crop box not at the origin.** Boxes must be relative to the crop box, not the media box. `displayed_box` is tested with an offset crop box (Task 2).
4. **Control and generated characters inside the text run** (`\r`, `\n`, PDFium's synthetic spaces, `\x02`, `￾`). These must split words and never appear inside one. Task 2 tests the grouping function on a synthetic character stream.
5. **A rotation value PDFium reports that is not a multiple of 90.** This must raise `ParserError(reason="internal")` and never produce boxes outside [0, 1]. Task 2 tests it.

---

### Task 1: One process-wide PDFium lock in the ingest layer

**Files:**
- Create: `src/docdoc/ingest/pdfium_lock.py`
- Modify: `src/docdoc/api/pages.py` (lines ~22–23 and the `with _PDFIUM:` at ~73)
- Modify: `src/docdoc/ingest/parsers/paddlex.py` (`_pdf_chunks`, ~995–1050)
- Test: `tests/unit/test_pdfium_lock.py`

**Interfaces:**
- Produces: `docdoc.ingest.pdfium_lock.PDFIUM_LOCK: Final[threading.RLock]`. Every PDFium call holds it, from opening a document to closing it. It is re-entrant so that a helper which takes it can be called from code that already holds it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pdfium_lock.py
import threading

from docdoc.api import pages
from docdoc.ingest.pdfium_lock import PDFIUM_LOCK


def test_the_lock_is_reentrant() -> None:
    assert isinstance(PDFIUM_LOCK, type(threading.RLock()))


def test_the_page_renderer_shares_the_ingest_lock() -> None:
    assert pages._PDFIUM is PDFIUM_LOCK
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest -n0 tests/unit/test_pdfium_lock.py`
Expected: FAIL, `ModuleNotFoundError: docdoc.ingest.pdfium_lock`.

- [ ] **Step 3: Implement**

Create `pdfium_lock.py` with only the stdlib `threading` import and a docstring. The docstring says PDFium is not thread-safe, and that this lock sits in ingest because ingest must not import api. In `api/pages.py`, write `from docdoc.ingest.pdfium_lock import PDFIUM_LOCK as _PDFIUM` in place of the local `threading.Lock()`, and keep the existing `ponytail:` comment. In `paddlex._pdf_chunks`, wrap everything from `pdfium.PdfDocument(source.data)` up to the last chunk save in `with PDFIUM_LOCK:`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -n0 tests/unit/test_pdfium_lock.py tests/unit/test_pages.py tests/unit/test_paddlex_parser.py tests/contract/test_console_pages.py && uv run lint-imports`
Expected: all pass; import-linter `Contracts: N kept, 0 broken`.

- [ ] **Step 5: Commit**

```bash
git add src/docdoc/ingest/pdfium_lock.py src/docdoc/api/pages.py src/docdoc/ingest/parsers/paddlex.py tests/unit/test_pdfium_lock.py
git commit -m "refactor(ingest): one process-wide PDFium lock shared by renderer and PaddleX"
```

---

### Task 2: `pdf-text` on pypdfium2, measured against PyMuPDF on today's files

PyMuPDF stays installed during this task, for the comparison only. Committed PDFs are not regenerated here.

**Files:**
- Modify: `src/docdoc/ingest/parsers/pdf_text.py` (full rewrite of the library-facing parts; keep `PARSER_ID`, the class name, `parse()`'s signature, the provenance construction and the error types)
- Modify: `pyproject.toml`: `pdf = ["pypdfium2>=5.0"]` (line 45), the comment above it, and import-linter: in the ingest contract's `ignore_imports`, replace the two `pdf_text -> pymupdf/fitz` lines with `"docdoc.ingest.parsers.pdf_text -> pypdfium2"`. Leave `pymupdf`/`fitz` in every `forbidden_modules` list as a guard.
- Modify: `tests/unit/test_pdf_text_parser.py`
- Test: `tests/unit/test_pdf_text_geometry.py` (new; pure functions, no PDF)

**Interfaces:**
- Consumes: `PDFIUM_LOCK` (Task 1).
- Produces (module-level in `pdf_text.py`):
  - `displayed_box(box: tuple[float, float, float, float], *, crop: tuple[float, float, float, float], rotation: int) -> tuple[float, float, float, float]`. `box` and `crop` are PDF user space `(left, bottom, right, top)`. Returns `(x0, y0, x1, y1)` in points, with the origin at the top left of the page **as displayed**. Raises `ValueError` if `rotation not in (0, 90, 180, 270)`.
  - `displayed_size(crop: tuple[float, float, float, float], rotation: int) -> tuple[float, float]`: `(W, H)` for 0/180 and `(H, W)` for 90/270.
  - `group_words(chars: Iterable[tuple[str, tuple[float, float, float, float]]]) -> list[list[tuple[str, tuple[float, float, float, float]]]]`. Takes `(character, box)` in text-page order and returns lines of `(word, union box)`. A character that is `""`, `str.isspace()`, or Unicode category `Cc`/`Cf`/`Cs`/`Co`/`Cn` (which covers `\x02` and `￾`) ends the current word. A `"\n"` also ends the current line. Empty lines are dropped.
  - `PdfTextParser.version == f"2.0.0+pypdfium2-{pypdfium2.version.PYPDFIUM_INFO}"` (for example `"2.0.0+pypdfium2-5.14.0"`).
  - `PdfTextParser.reading_order == "pdfium-stream@1"`
  - `page_text_lengths(source) -> tuple[str, ...]`: unchanged signature.

- [ ] **Step 1: Write the failing geometry and grouping tests**

```python
# tests/unit/test_pdf_text_geometry.py
import pytest
pytest.importorskip("pypdfium2")
from docdoc.ingest.parsers.pdf_text import displayed_box, displayed_size, group_words

CROP = (10.0, 20.0, 110.0, 220.0)          # W=100, H=200, offset from origin
BOX = (20.0, 200.0, 30.0, 210.0)           # unrotated top-left: x 10..20, y 10..20

@pytest.mark.parametrize(("rotation", "expected"), [
    (0, (10.0, 10.0, 20.0, 20.0)),
    (90, (180.0, 10.0, 190.0, 20.0)),
    (180, (80.0, 180.0, 90.0, 190.0)),
    (270, (10.0, 80.0, 20.0, 90.0)),
])
def test_a_box_lands_where_the_page_displays_it(rotation, expected):
    assert displayed_box(BOX, crop=CROP, rotation=rotation) == pytest.approx(expected)

def test_quarter_turns_swap_the_displayed_size():
    assert displayed_size(CROP, 0) == (100.0, 200.0)
    assert displayed_size(CROP, 90) == (200.0, 100.0)

def test_an_odd_rotation_is_refused():
    with pytest.raises(ValueError):
        displayed_box(BOX, crop=CROP, rotation=45)

def test_generated_and_control_characters_split_words():
    b = (0.0, 0.0, 1.0, 1.0)
    chars = [("A", b), ("B", b), (" ", b), ("C", b), ("\x02", b), ("D", b),
             ("\r", b), ("\n", b), ("E", b), ("￾", b)]
    lines = group_words(chars)
    assert [[w for w, _ in line] for line in lines] == [["AB", "C", "D"], ["E"]]

def test_a_word_box_is_the_union_of_its_characters():
    (line,) = group_words([("A", (0, 0, 1, 2)), ("B", (1, -1, 3, 1))])
    assert line == [("AB", (0, -1, 3, 2))]
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest -n0 tests/unit/test_pdf_text_geometry.py`
Expected: FAIL, `ImportError: cannot import name 'displayed_box'`.

- [ ] **Step 3: Implement the parser on pypdfium2**

`displayed_box`: convert to unrotated top-left coordinates `x0u = left - cl`, `x1u = right - cl`, `y0u = ct - top`, `y1u = ct - bottom`, with `W = cr - cl` and `H = ct - cb`, then apply:

```text
0:   (x0u,     y0u,     x1u,     y1u)
90:  (H - y1u, x0u,     H - y0u, x1u)
180: (W - x1u, H - y1u, W - x0u, H - y0u)
270: (y0u,     W - x1u, y1u,     W - x0u)
```

Parsing (all inside `with PDFIUM_LOCK:`, from open to close):
1. Open with `pypdfium2.PdfDocument(source.data)`. On `PdfiumError`, `err_code == pypdfium2.raw.FPDF_ERR_PASSWORD` maps to `UnsupportedDocumentError(reason="encrypted")` and anything else to `reason="corrupt"`. Keep the messages the current `_open` uses. `paddlex._pdf_chunks` has the same mapping.
2. For each page: `rotation = page.get_rotation()` and `crop = page.get_cropbox()`. Then `builder.start_page(width, height=displayed_size(crop, rotation), rotation=rotation)`. Then call `page.set_rotation(0)`. Reading with the rotation reset keeps content-stream order on rotated pages; with the rotation in place, PDFium reorders them. This was measured on `rotated_90.pdf`.
3. Build `textpage = page.get_textpage()`. For `i in range(textpage.count_chars())`, take `(textpage.get_text_range(i, 1), textpage.get_charbox(i, loose=True))`. Pass these to `group_words`, map each word box through `displayed_box(..., rotation=rotation)`, normalize it with the existing `_box` error handling (`normalize_bbox`, where `GeometryError` becomes `ParserError`), and call `builder.add_line`.
4. A `ValueError` from `displayed_box` becomes `ParserError(reason="internal", detail="rotation")`.
5. `page_text_lengths` returns `textpage.get_text_range()` per page, under the lock.

Update the module docstring: licence (now Apache/BSD, no extra caveat), reading order (`pdfium-stream@1`: content-stream order, with the rotation reset to 0 before reading), and rotation (mapped by `displayed_box`).

- [ ] **Step 4: Update `tests/unit/test_pdf_text_parser.py`**

- `importorskip("pymupdf")` becomes `importorskip("pypdfium2")`. Drop `import pymupdf`.
- `test_the_declared_order_names_what_is_actually_delivered` now asserts `"pdfium-stream@1"`.
- `test_sorted_extraction_would_interleave_them` pinned PyMuPDF's `sort=True` behaviour. Delete it, and say in the commit message that the property it guarded ("this adapter claims no layout reconstruction") is still pinned by `test_columns_are_not_interleaved`.
- Add `test_parsing_holds_the_pdfium_lock`. Monkeypatch `docdoc.ingest.parsers.pdf_text.PDFIUM_LOCK` with a recorder whose `__enter__` counts calls, parse `digital_invoice.pdf`, and assert the count is at least 1.

- [ ] **Step 5: Run the parser tests**

Run: `uv run pytest -n0 tests/unit/test_pdf_text_geometry.py tests/unit/test_pdf_text_parser.py tests/unit/test_parse_native.py tests/unit/test_assess_text_layer.py tests/unit/test_unicode_text.py tests/integration/test_ground_real_pdf.py`
Expected: all pass. If a text-layer verdict changes, stop and report it (Global Constraints).

- [ ] **Step 6: Measurement 1, with the parser swapped and today's documents**

Use the throwaway script `/tmp/pdfspike/compare.py` from the spike, or recreate it outside the repo. Change its PDFium side to call `docdoc.ingest.parsers.pdf_text.PdfTextParser().parse(...)` and compare token text and boxes against `pymupdf` words mapped through `page.rotation_matrix`, normalized by `page.rect`. Run it over `tests/fixtures/pdf` and `datasets/mvp/documents`.
Expected: same word count and sequence on every file, including `rotated_90.pdf`, and a max box delta ≤ 0.002. Save the table for the PR.

Then:

```bash
uv run --extra pdf python datasets/mvp/make_dataset.py    # documents still written by PyMuPDF; predictions now parsed by pdfium
uv run docdoc eval datasets/mvp/manifest.json --predictions datasets/mvp/predictions --no-store
```

Expected: grounding_rate 1.0000 (30/30). The other four metrics are unchanged from the baseline. Save the output for the PR, then `git checkout -- datasets`. The regenerated dataset is committed in Task 3.

- [ ] **Step 7: Full suite, then fix pins only**

Run: `make test`. For each failure, apply the Global Constraints rule: update identity, version, or bytes pins (for example `tests/unit/test_identity_recompute.py`, `tests/integration/test_upgrade_compatibility.py`, `tests/fixtures/snapshots/`, and tests asserting `"pymupdf"` in a version string); stop and report anything else. Then run `make lint`.
Expected: `make test` passes, `make lint` is clean.

- [ ] **Step 8: Commit**

```bash
git add -A src tests pyproject.toml uv.lock
git commit -m "feat(ingest): pdf-text parses with pypdfium2 instead of PyMuPDF"
```

---

### Task 3: Generators on reportlab; PyMuPDF leaves the project

**Files:**
- Modify: `pyproject.toml`: add `"reportlab>=4.0"` to the `dev` extra, with a one-line comment that it is used only by the fixture and dataset generators. `uv lock`.
- Modify: `tests/fixtures/make_fixtures.py`, `datasets/mvp/make_dataset.py` (~line 281)
- Regenerate: `tests/fixtures/pdf/*.pdf`, `tests/fixtures/image/*` if they are produced by the same script, `datasets/mvp/{documents,predictions,manifest.json}`
- Modify: `.github/workflows/ci.yml` `base-install` job: the absence check and "the absence check can actually fail" both use `pypdfium2` instead of `pymupdf`. Update the error text to "pypdfium2 is installed by the base package; it must be an extra".
- Modify: `packaging/docker/Dockerfile`: `ARG DOCDOC_EXTRAS=api,postgres,s3,google,azure,gcv,ocr,pdf`. Replace the AGPL paragraph and the "default build can parse nothing on its own" paragraph with one line saying the native PDF path is in the default image and is permissively licensed. Keep the `ocr` paragraph.

**Interfaces:**
- Consumes: the Task 2 parser, to regenerate predictions.

- [ ] **Step 1: Rewrite the generators with reportlab**

Rules that keep the fixtures meaning the same thing:
- `canvas.Canvas(path, pagesize=(WIDTH, HEIGHT), invariant=1)`. `invariant=1` makes the output byte-deterministic, so regeneration is reproducible.
- Text lines go through `drawString(x, HEIGHT - y, line)` with the same `x`, `y`, line spacing and `fontsize=11` as today. reportlab's origin is at the bottom left, PyMuPDF's at the top left.
- CJK (`"china-s"` in `UNICODE_LINES`): use `pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))` and set that font. `"helv"` becomes `"Helvetica"`. The spike confirmed that PDFium extracts `发票` and `numéro` exactly.
- Rotation: `canvas.setPageRotation(90)` before `showPage()`.
- Encryption: `pdfencrypt.StandardEncryption("user-secret", "owner-secret", strength=128)`. 256 needs `pyaes`; 128 is enough, since the test only needs a password-protected file. Confirm PDFium refuses it with `FPDF_ERR_PASSWORD`.
- Rasterized "scanned" pages: render the text page with `pypdfium2` (`page.render(scale=110/72).to_pil()`), then place the PNG on a fresh reportlab page with `drawImage` covering the full page.
- `mixed_pages`: one canvas, with the text page first and then the image pages, in today's order.
- `zero_pages` is already hand-written bytes. Leave it as is.
- Update the generator docstrings that say PyMuPDF.

- [ ] **Step 2: Regenerate and verify**

```bash
uv run python tests/fixtures/make_fixtures.py
uv run python datasets/mvp/make_dataset.py
uv run python tests/fixtures/make_fixtures.py && git status --short tests/fixtures   # second run: no changes (determinism)
uv run docdoc eval datasets/mvp/manifest.json --predictions datasets/mvp/predictions --no-store
```

Expected: the second generator run leaves no diff. Eval gives grounding_rate 1.0000 (30/30) with the other metrics at baseline (measurement 2; save it for the PR).

- [ ] **Step 3: Remove PyMuPDF from the lock and check nothing imports it**

```bash
uv lock && uv sync --all-extras
grep -n pymupdf uv.lock                                  # expect: no output
grep -rn "import pymupdf\|import fitz" src tests datasets packaging   # expect: no output
```

- [ ] **Step 4: Full suite, then fix pins only**

Run `make test` and `make lint`, applying the same pin rule as Task 2 Step 7. Fixture blob hashes and snapshots will move, and that is expected. Text-based expectations in tests that read the fixtures (for example `find("ACME")` page and box assertions) must still hold. If one fails, the generator changed the document's layout: fix the generator, not the test.

- [ ] **Step 5: Commit**

```bash
git add -A pyproject.toml uv.lock tests datasets .github/workflows/ci.yml packaging/docker/Dockerfile
git commit -m "build: PDF fixtures and golden set written with reportlab; PyMuPDF removed"
```

---

### Task 4: Docs say what is true now

**Files:**
- Create: `docs/adr/0025-native-pdf-parsing-on-pdfium.md` (status Accepted, date 2026-10-22). It supersedes ADR-0001's choice of PyMuPDF for the native path; ADR-0001's OCR strategy is unaffected. Context: an OSS service image, the AGPL obligation, the default image unable to parse. Decision: pypdfium2. Consequences: identities change (pre-1.0), the `pdf` extra is now permissive and in the default image, one process-wide lock, reportlab for generators. Include the measurement 1 and 2 numbers.
- Modify: `docs/adr/0001-parser-and-ocr-strategy-in-mvp.md`: add `- **Superseded in part by**: [ADR-0025](0025-native-pdf-parsing-on-pdfium.md) (the native PDF library)` to the header. Leave the body alone.
- Modify: `docs/adr/README.md` (index row), `docs/adr/0007-apache-2-license.md`, `docs/constitution.md`, `docs/concepts/ingest.md`, `docs/concepts/runs.md`, `README.md`, `examples/*.py`, `examples/serve_api.md`, `CHANGELOG.md` (Unreleased: "pdf-text parses with pypdfium2; PyMuPDF and its AGPL caveat are gone; the default image reads digital PDFs"). Also the docstrings and comments that name PyMuPDF in `src/docdoc/ingest/parsers/__init__.py`, `src/docdoc/ingest/parser.py`, `src/docdoc/kernel/provenance.py`, `src/docdoc/extraction/{extract,errors}.py`, `src/docdoc/cli/__init__.py`, `src/docdoc/cli/commands/worker.py`, `src/docdoc/runs/worker.py`, `src/docdoc/pipeline/runner.py`, `src/docdoc/recording/record.py`, and `packaging/docker/compose.yml`.

- [ ] **Step 1: Find every remaining mention**

Run: `grep -rlni "pymupdf\|fitz\|agpl" --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=specs --exclude-dir=dist . | grep -v "docs/superpowers/plans/"`
Expected: the list above, plus `ui/LICENSES.md` and `ui/scripts/check-licenses.mjs`. Those two are the npm licence allowlist, which rejects AGPL packages and should keep doing so, so leave them alone.

- [ ] **Step 2: Rewrite each mention**

Each mention either becomes accurate (pypdfium2, Apache/BSD, `pdfium-stream@1`, `"2.0.0+pypdfium2-…"`) or is deleted if it only existed to explain the AGPL caveat. Examples showing a version string use the new format. README's install list says `docdoc[pdf]` is the native PDF path with no licence warning. Project C rewrites the install section itself; this task does not restructure it.

- [ ] **Step 3: Verify**

Run: the Step 1 grep again (expect only `ui/LICENSES.md`, `ui/scripts/check-licenses.mjs` and `CHANGELOG.md`'s historical entries), then `make lint && make test`. The docs-consistency tests (`test_documented_api_references_resolve.py`, `test_plan_tree_is_current.py`, `test_convergence_gaps.py`) are included and must pass.

- [ ] **Step 4: Commit**

```bash
git add -A docs README.md CHANGELOG.md examples src packaging
git commit -m "docs: ADR-0025 and every PyMuPDF mention updated for the PDFium parser"
```

---

### Task 5: Prove it end to end and open the PR

- [ ] **Step 1: The default image parses a digital PDF with no credential**

```bash
docker build -t docdoc:pdfium -f packaging/docker/Dockerfile .
docker run --rm docdoc:pdfium python -c "from docdoc.ingest.registry import default_registry; print([(e.id, e.available, e.unavailable_reason) for e in default_registry().candidates_all() if e.id == 'pdf-text'])"
```

Expected: `[('pdf-text', True, None)]`.

- [ ] **Step 2: Perf budgets**

Run: `uv run pytest -m perf -q -n0`
Expected: pass. If an ingest budget fails, report the measured number in the PR. Do not raise the budget in this branch.

- [ ] **Step 3: Open the PR**

Follow `AGENTS.md`: squash merge, so the body describes all the work. Use the `mattpocock-skills:pr` template. Evidence must include the measurement 1 comparison table, measurement 1 and 2 eval outputs against the baseline, `grep -n pymupdf uv.lock` empty, the image check, and the perf result.
