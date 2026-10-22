# ADR-0025: Native PDF Parsing on PDFium

- **Status**: Accepted
- **Date**: 2026-10-22
- **Supersedes**: [ADR-0001](0001-parser-and-ocr-strategy-in-mvp.md)'s choice of PyMuPDF as the
  library behind the native PDF text path. ADR-0001's parser and OCR strategy is otherwise unchanged.
- **Relates to**: [ADR-0007](0007-apache-2-license.md) (Apache-2.0), [ADR-0011](0011-pre-1.0-versioning.md)
  (pre-1.0 identities), [ADR-0021](0021-caller-named-parsers-and-ocr-endpoints.md) (the PaddleX
  adapter, which already used `pypdfium2`)
- **Design**: `docs/superpowers/specs/2026-10-22-pdfium-parser-design.md`

## Context

docdoc is an Apache-2.0 service that other people self-host from its Docker image. Its native PDF
text path (`pdf-text`) ran on PyMuPDF, which is AGPL-3.0. To avoid passing that obligation to
everyone who runs the image, the parser sat behind the opt-in `pdf` extra and the default image left
it out. As a result, a fresh default deployment could not read even a digital PDF without a cloud
credential.

`pypdfium2` (Apache-2.0 / BSD-3, bindings to Chrome's PDFium, BSD-3) was already a dependency: the
PaddleX adapter splits PDFs with it, and the console and the Tesseract shim render pages with it.

## Decision

`pdf-text` parses with `pypdfium2`. PyMuPDF leaves the project entirely: runtime, extras, `uv.lock`,
and the fixture and golden-set generators, which now write PDFs with `reportlab` (BSD, dev-only).
The `pdf` extra is `pypdfium2` and is in the default image.

- **Words from characters.** PDFium returns characters with boxes, not words. Whitespace and
  non-text characters (Unicode `Cc`, `Cf`, `Cs`, `Co`, `Cn`) end a word, and a newline also ends
  the line.
- **Reading order** is declared as `pdfium-stream@1`: content-stream order. On a rotated page,
  PDFium's text page reorders the text, so the rotation is reset to 0 in memory before the text is
  read. Boxes are then mapped to the page as displayed by `displayed_box`.
- **One process-wide lock.** PDFium is not thread-safe, and the API parses, renders console pages,
  and splits PaddleX PDFs from its threadpool. `docdoc.ingest.pdfium_lock.PDFIUM_LOCK` is held by
  every caller.
- **Encrypted versus corrupt.** PDFium leaves its last error in place after some failed loads. A
  password error is believed only when the file declares `/Encrypt`.
- **The visible page is the frame.** Boxes are relative to the crop box clipped to the media box,
  which is what PDFium renders for the console. Text outside it (printer's marks, slug, text parked
  off the page) is dropped, as PyMuPDF did, instead of failing the document.
- **Line-end hyphens are kept.** PDFium replaces a hyphen that ends a line with U+FFFE and drops the
  break after it. The parser restores both.
- `parser_version` is `2.0.0+pypdfium2-<version>`.

## Evidence

The comparison and the scores below were measured before PyMuPDF was removed.

On every PDF in `tests/fixtures/pdf` and `datasets/mvp/documents` (11 files), PDFium and PyMuPDF
produced the same word count and the same word sequence, including on the 90° rotated page. The
largest normalized box difference was 0.0017, about 1 pt on A4.

Golden set (`datasets/mvp`, public tier):

| | field_accuracy | coverage | missing | incorrect | grounding_rate |
|---|---|---|---|---|---|
| PyMuPDF (baseline) | 0.9286 | 0.9600 | 0.0400 | 0.0400 | 1.0000 (30/30) |
| PDFium, same documents | 0.9286 | 0.9600 | 0.0400 | 0.0400 | 1.0000 (30/30) |
| PDFium, documents regenerated with reportlab | 0.9286 | 0.9600 | 0.0400 | 0.0400 | 1.0000 (30/30) |

## Consequences

- Document and artifact identities change, because `parser_version` and `reading_order` are part of
  them. docdoc is pre-1.0 (ADR-0011), so there is no migration and no compatibility shim.
- The default image reads digital PDFs with no credential. Scans and images still need a cloud
  parser or an OCR endpoint.
- PDFium calls in one API process run one at a time. The worker runs one run per process and is
  unaffected.
- A zero-page PDF is refused while it is being opened ("could not be opened as a PDF") rather than
  after its pages are counted. The reason is still `corrupt`.
