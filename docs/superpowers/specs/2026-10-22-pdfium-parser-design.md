# Native PDF parsing on PDFium instead of PyMuPDF

Date: 2026-10-22. Project A of three in the move to a server-side service; B (evaluation as a
service feature) and C (retiring the installable-library surface) follow and are out of scope here.

## Why

docdoc is an open-source, Apache-2.0 service that other people self-host from its Docker image.
Its native PDF text path (`pdf-text`) runs on PyMuPDF, which is AGPL-3.0. To avoid passing that
obligation to everyone who runs the image, the parser sits behind the opt-in `pdf` extra and the
default image leaves it out (`packaging/docker/Dockerfile`, ADR-0001). The result is that **a fresh
default deployment cannot read even a digital PDF without a cloud credential.**

The owner wants PDF handling to be permissively licensed from end to end, so that the default image
reads PDFs out of the box and nobody running docdoc has a licence question to answer.

`pypdfium2` (Apache-2.0 / BSD-3, bindings to Chrome's PDFium) is already a dependency, in the `ocr`
extra, where the PaddleX adapter uses it to split PDFs and the Tesseract shim uses it to render
pages. A throwaway spike (below) showed it reproduces PyMuPDF's words almost exactly.

## Goals

- `pdf-text` parses with `pypdfium2`. PyMuPDF is gone from runtime code, from `pyproject.toml`
  extras, from `uv.lock`, and from the default Docker image's concerns.
- The default Docker image includes the native PDF path and parses a digital PDF with no
  credential configured.
- Every AGPL caveat about the PDF path (README, Dockerfile comment, `pyproject.toml` comment, ADRs,
  constitution, concept docs) is removed or rewritten to say the path is permissively licensed.
- The behaviour the current parser guarantees still holds: word-level text with geometry, boxes
  "as displayed" on rotated pages (R8), declared reading order, two-column order, Unicode offsets
  in code points, blank pages present with zero tokens, `text-layer@1` routing verdicts, encrypted
  and zero-page files refused with the same errors, deterministic output across repeated runs.

## Non-goals

- Retiring the CLI, the base install, the library API, or the other extras (project C).
- Evaluation over HTTP or in the console (project B).
- Changing the OCR, Azure, GCV, or PaddleX paths beyond what sharing `pypdfium2` requires.
- Better reading order than today. Matching today's behaviour is the bar.

## Anti-goals

- Any AGPL or other copyleft dependency remaining reachable from the runtime or the image,
  including "optionally".
- A silent quality drop: the swap must not be merged on faith; the evidence below goes in the PR.

## Constraints and decisions already made

- **Breaking changes are allowed.** docdoc is pre-1.0 (ADR-0011) and no deployment holds data that
  needs preserving. `parser_version`, `reading_order`, document identities and artifact identities
  will change; no migration and no compatibility shim for identities produced by PyMuPDF.
- **Acceptance evidence**, in the PR:
  1. Golden set (`datasets/mvp`) regenerated with the new parser and scored with `docdoc eval`.
     Baseline with PyMuPDF, re-verified 2026-10-22 by regenerating predictions:
     field_accuracy 0.9286 (26/28), coverage 0.9600 (24/25), missing 0.0400, incorrect 0.0400,
     **grounding_rate 1.0000 (30/30)**. grounding_rate is the parser-sensitive number and must stay
     30/30. The two field errors are authored on purpose (labels disagree with the echo answers)
     and should not move.
  2. A parser-to-parser comparison table over every PDF in `tests/fixtures/pdf` and
     `datasets/mvp/documents`: word count, whether the text sequence is identical, and the maximum
     normalized box delta.
- **Order of measurement** *(builder-check call)*. The evidence isolates the parser by measuring on
  the PDFs committed today, which PyMuPDF wrote. Score and compare those first, with PyMuPDF still
  installed. Only then rewrite the generators, regenerate, and re-score. The PR reports both runs:
  the parser swap on unchanged documents, and the regenerated set. The comparison script stays a
  throwaway outside the repo; only its output table goes in the PR, so no PyMuPDF import is
  committed.
- **Spike result** (2026-10-22, unrotated page space, boxes normalized to 0–1): on all 11 PDFs the
  word count matched, the text sequence matched on 10, and the maximum box delta was 0.0017
  (≈1 pt on A4, from PDFium's loose char boxes versus PyMuPDF's font boxes). The exception is
  `rotated_90.pdf`: same words, same boxes, **different order**. PyMuPDF keeps content-stream
  order; PDFium's text page reorders it. `scanned_contract.pdf` has no text layer in either.
  `encrypted.pdf` and `zero_pages.pdf` were not compared (they exercise refusals).

## What changes

- `src/docdoc/ingest/parsers/pdf_text.py`: rewritten on `pypdfium2`. PDFium returns per-character
  boxes, not words, so the parser groups characters into words and lines itself. It also maps
  boxes through the page rotation itself. `page_text_lengths` (the input to `text-layer@1`) reads
  the text page instead.
- `version` becomes `"<adapter>+pdfium-<pypdfium2 version>"` (or the PDFium build, if that is the
  more honest identity of the thing that produced the text). `reading_order` becomes a new
  declared name, such as `pdfium-stream@1`, rather than reusing `pymupdf-stream@1`.
- **One process-wide PDFium lock** *(builder-check call)*. PDFium is not thread-safe.
  `src/docdoc/api/pages.py` already serializes its own renders with a module-level `_PDFIUM` lock,
  but the API also runs parses in a threadpool (`api/documents.py`, `api/ocr.py`), and PaddleX's
  PDF splitting takes no lock. Once `pdf-text` is on PDFium, all of these share one library. Move
  the lock below the API layer, into ingest, where `pdf_text`, the PaddleX splitter, and the page
  renderer can all import it. This is in scope because it is a correctness requirement of the
  swap. As a result, PDFium calls inside one API process run one at a time. The worker runs one
  run per process and is unaffected. If that shows in the perf budgets, report it in the PR.
- `pyproject.toml`: the `pdf` extra is `pypdfium2` instead of `pymupdf`. The import-linter
  contracts that forbid or route `pymupdf`/`fitz` now name `pypdfium2`, so the layer rule still
  holds: only the parsers (and the OCR adapters that already use it) may import it.
- `packaging/docker/Dockerfile`: `pdf` joins the default `DOCDOC_EXTRAS`. The AGPL comment and the
  "default build can parse nothing on its own" paragraph go.
- Fixture and dataset generators (`tests/fixtures/make_fixtures.py`,
  `datasets/mvp/make_dataset.py`) currently *write* PDFs with PyMuPDF, so they must be rewritten
  too, or PyMuPDF stays in the dev environment. Regenerating changes the committed PDFs' bytes,
  and therefore their blob hashes, the manifest, snapshots, and identity-pinning tests. That is
  accepted.
- Tests that name PyMuPDF or pin its identities, versions, or snapshots are updated. There are
  about 45 test files with matches (`grep -rli pymupdf tests`). The CI `base-install` step "the
  absence check can actually fail" installs `pymupdf` to prove detection works; it should use
  whatever the base-install check now looks for.
- Docs: a new ADR records the switch and supersedes the PDF-parser part of ADR-0001. ADR-0007
  (licence), the constitution, `docs/concepts/ingest.md`, `docs/concepts/runs.md`, `README.md`,
  `examples/`, and `CHANGELOG.md` are updated wherever they describe PyMuPDF or the AGPL caveat.
  Historical milestone docs under `specs/` and `docs/superpowers/plans/` are records, not
  descriptions, and stay as written.

## Left to the builder

- **How characters become words and lines.** Split on whitespace and PDFium's generated
  characters (`\r`, `\n`, synthetic spaces), start a new line on PDFium line breaks or a large
  vertical jump, and use loose or tight char boxes. Judge it by the spike's bar: same word
  sequence as PyMuPDF on the fixtures.
- **Rotated-page order.** Recover content-stream order, for example by walking text page objects
  instead of the text page, or by ordering PDFium's output. Either way the order must be
  deterministic, must match what `reading_order` declares, and must satisfy whatever the rotated
  fixture's tests assert. If a test asserts PyMuPDF's exact order and PDFium's order is equally
  defensible "as displayed", changing the test is acceptable when the PR explains why.
- **Generator rewrite.** Write the fixture and dataset PDFs with `pypdfium2`'s page-object API or
  with a permissively licensed writer such as `reportlab` (BSD) as a dev-only dependency. A
  rasterized "scanned" fixture can be rendered with `pypdfium2` + Pillow (Pillow is already a dev
  dependency).
- **`text-layer@1` thresholds.** If PDFium's character counts differ from PyMuPDF's in a way that
  flips a routing verdict on any fixture, either keep the rule and adjust the counting, or version
  the rule (`text-layer@2`). The verdicts on the committed sample set must stay correct (SC-004).
- **Error mapping.** Map `pypdfium2`'s exceptions for encrypted, corrupt, and zero-page files onto
  the existing `UnsupportedDocumentError` / `ParserError` reasons, so callers see no difference.
- **Performance.** The `perf` budgets for ingest (`tests/perf/test_ingest_perf.py`) must still pass.
  If PDFium is slower, measure it and say so in the PR rather than raising a budget silently.
