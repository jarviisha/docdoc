# Quickstart & Validation Guide: Ingest Parser Layer

**Feature**: `002-ingest-parser-layer` | **Date**: 2026-08-17

How to set up, run, and validate this feature. Entity definitions live in
[data-model.md](data-model.md); API semantics live in
[contracts/ingest-api.md](contracts/ingest-api.md). This document does not repeat them.

---

## Prerequisites

- Python >= 3.11 and [`uv`](https://docs.astral.sh/uv/)
- **For everything except V4**: nothing else. No credentials, no network, no database. If any step
  other than V4 needs one of those, this feature has a defect (SC-009).
- **For V4 only**: credentials for the document-intelligence service, in the environment.

## Setup

```bash
uv sync --all-extras      # dev tooling + both parser extras
```

To reproduce what a plain user gets, and confirm the base install stays clean (SC-010):

```bash
uv run --no-project --with docdoc python -c "import docdoc.kernel"   # must succeed
uv run --no-project --with docdoc python -c "import fitz"            # must fail: not a base dependency
```

> **Licence note.** `docdoc[pdf]` pulls in PyMuPDF, which is AGPL-3.0. docdoc itself is Apache-2.0
> and the extra is opt-in — see [research.md R1](research.md). Know this before embedding the extra
> in a closed-source pipeline.

## Run the suites

```bash
uv run pytest tests/unit tests/property tests/contract    # no credentials, no network
uv run pytest -m provider                                 # live service tests; skipped without credentials
uv run mypy --strict src/docdoc
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                                       # layer + provider-containment contracts
```

Expected: the first command passes fully offline. The second reports skips with a stated reason when
credentials are absent — a silent pass would mean the marker is wrong.

---

## Validation scenarios

Each maps to a user story in [spec.md](spec.md). Together they demonstrate the feature end to end.

### V1 — Parse a text-layer PDF offline (US1, P1)

**Goal**: prove the default path produces a locatable document with no network and no credentials.

```bash
uv run python examples/parse_pdf.py tests/fixtures/pdf/digital_invoice.pdf
```

1. `parse()` returns a `Document` whose page count matches the PDF.
2. `doc.find("INV-")` returns at least one `Span`.
3. `doc.locate(span)` returns geometry with `page_index` set and every coordinate inside `0.0..1.0`.

**Pass**: the box lands on the invoice number when drawn over a render of the page.

**Also verify**:

- Running with the network disabled changes nothing.
- `unicode_text.pdf` comes back with no replacement characters, and `find("发票")` spans two
  positions rather than six — offsets are Unicode code points, not bytes.
- Parsing the same file twice yields identical `document_id`, identical text, and identical geometry
  (SC-005, SC-006).
- A blank page appears in `doc.pages` with its dimensions and contributes zero tokens.
- The 90°-rotated fixture yields boxes over the glyphs *as displayed*, not in unrotated file space
  (R8 — this is the test that settles the library's rotation behaviour).
- The two-column fixture yields column one before column two, matching the parser's declared
  `reading_order` (R5).

### V2 — Read the text-layer decision back off a result (US2, P2)

**Goal**: prove the routing decision is explicit, inspectable, and per page.

1. Assess `digital_invoice.pdf` → `text_layer_usable is True`.
2. Assess `scanned_contract.pdf` → `text_layer_usable is False`.
3. Assess `sparse_text_layer.pdf` (a scan carrying a handful of stray characters) → `False`. This is
   the fixture that proves the threshold does real work rather than just separating 0 from 5,000.
4. On any parsed document, read `doc.provenance.text_layer`: rule id, both thresholds, the
   document-level verdict, and one entry per page.

**Pass**: no source file is re-read to answer any of the above.

**Also verify**:

- `parse(..., force="recognition")` on a digital PDF records `overridden=True` and preserves the
  rule's verdict in `overridden_verdict`.
- The mixed fixture (digital pages plus one scanned page) parses natively, and the scanned page's
  per-page verdict shows `text_bearing=False` with its character count — the emptiness is recorded,
  not silent (FR-035).
- Assessing the same bytes twice yields identical character counts (ING-12).

### V3 — Ask for a capability, not a provider (US4, P4)

**Goal**: prove selection is capability-driven and deterministic.

1. Build a registry with two stub parsers of differing capabilities; request one only the second
   satisfies → the second is selected.
2. Register them in the opposite order → the same parser is selected (ING-14).
3. Request a capability neither declares → `ParserCapabilityError` naming the capability and listing
   both candidates with availability.
4. With the `azure` extra absent, inspect `registry.candidates(...)` → the entry is present with
   `available=False` and a reason, not missing (ING-16).

**Pass**: no provider name appears anywhere in the calling code.

### V4 — Parse a scanned document through the service (US3, P3) — credentials required

**Goal**: prove the recognition path produces the same document shape as the native one.

```bash
uv run pytest -m provider tests/integration/test_azure_live.py
```

1. A scanned page returns a `Document` satisfying every kernel invariant.
2. Geometry is normalized to `0..1`, top-left origin — no service coordinate survives.
3. No service type or field name appears in the document.

**Pass**: downstream code cannot tell which path produced the document, except by reading provenance.

**Also verify — offline, via recorded fixtures (no credentials needed)**:

- The recorded-response tests in `tests/unit/test_azure_mapping.py` pin the service-response → IR
  mapping. These are the tests that actually protect the adapter; the live test only proves the wire
  still works (R14). Two responses are recorded: a two-page scan, and an image — the latter because
  "an image yields a single-page document" would otherwise be verified only where credentials exist.
- A table the service cannot fully anchor is reported, not quietly reduced: a cell without a span
  raises rather than leaving a 2x2 table carrying three cells.
- An induced timeout raises `ProviderError(reason="timeout")` after the configured attempt count —
  never a fallback to the native parser (FR-014, ING-21).
- An induced auth failure raises on the **first** attempt with zero retries.
- With no credentials configured at all, requesting the recognition path raises before any byte is
  read or transmitted.

### V5 — Failure, safety, and observability

**Goal**: prove the layer fails loudly and leaks nothing.

- Encrypted PDF → `UnsupportedDocumentError(reason="encrypted")`; a truncated file → `corrupt`; a
  `.pdf` that is really a PNG → parsed as an image, because the bytes decide (ING-1).
- `zero_pages.pdf` → `UnsupportedDocumentError(reason="corrupt")` naming the absence of pages, rather
  than being routed to a recognition parser for a document with nothing to recognize.
- Every refusal raised by a parser names it: an encrypted PDF reports `parser_id="pdf-text"`, and the
  `ingest.parse` event carries the same.
- A file over `max_size_bytes` → rejected before any parse; assert nothing was transmitted.
- Parse the whole fixture set while capturing logs: one `ingest.parse` event per parse, carrying
  identity, parser, verdict, page count, duration, attempts, and outcome — and **zero** occurrences of
  any string from the documents' text (SC-013).
- After the run, including induced failures, no temporary files remain (SC-014).

---

## What "done" looks like

| | Check |
|---|---|
| SC-001 | V1 runs with the network off |
| SC-002 | property suite: every produced box lies within 0..1 for arbitrary page sizes |
| SC-004 | V2 steps 1–3 over the committed sample set, which includes an image |
| SC-003 | V2 step 4 — verdict present for every page |
| SC-005/006 | V1 repeat-run comparison |
| SC-007 | V5 error cases all typed, zero silent empties |
| SC-008 | `lint-imports` passes |
| SC-009 | `pytest tests/unit tests/property tests/contract` green with no credentials |
| SC-010 | the two `--no-project` commands above behave as stated |
| SC-011 | V3 steps 1–2 |
| SC-012 | perf test: 20-page text PDF under 5 s offline |
| SC-013/014 | V5 log and temp-file assertions |
| SC-015 | V1 — `examples/parse_pdf.py` is the single documented example |
| SC-016 | contract test rejects an out-of-order parser |
| SC-017/018 | V4 retry assertions; identity unchanged across transport settings |
