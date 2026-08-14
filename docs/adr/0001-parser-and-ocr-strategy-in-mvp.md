# ADR-0001: Parser and OCR Strategy in the MVP

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(OCR_IN_MVP)` (BLOCKING, Milestone 2)
- **Principles engaged**: IV (Provider-Agnostic Adapters), V (Text-First), XI (MVP Discipline)

## Context

The constitution's MVP stack calls for "one PDF parser, one OCR adapter", while Principle V
insists OCR is a capability rather than the definition of IDP, and the reference design specifies
a native PDF parser plus one geometry-capable cloud provider with no standalone OCR engine.
These are not the same architecture, and Milestone 2 cannot start until it is settled.

A secondary question surfaced during the decision: Principle IV names `OCRProvider` as one of the
internal interfaces, but if no MVP component implements it, Principle XI's rule that every
abstraction must have a concrete present-tense reason to exist is violated.

## Decision

The MVP ships **two `Parser` implementations and no standalone OCR engine**:

1. **Native PDF text parser** (PyMuPDF) — the default path for PDFs with a usable text layer.
   Fast, offline, free, and the parser contributors run by default.
2. **One geometry-capable cloud document-intelligence provider** (Azure Document Intelligence as
   the default choice) — the path for scanned PDFs, images, and mixed documents.

**`OCRProvider` is not introduced in the MVP.** OCR is expressed through `Parser.capabilities`,
not through a separate interface: a document-intelligence provider consumes a blob and returns a
`Document`, which is exactly the `Parser` contract. `OCRProvider` is introduced only when a local
OCR engine is actually implemented and the two contracts demonstrably diverge.

Parser selection remains capability-based (`parser_registry.select(require=...)`), never by
provider name. The text-layer usability decision MUST be explicit and recorded in ingest
provenance, so it is inspectable after the fact.

## Consequences

- Scanned-document support requires credentials for a cloud provider. Contributors without them
  can still run the kernel suite, the property suite, and the full text-PDF path; provider
  integration tests are skipped, per Principle XII.
- The MVP gains real geometry and table extraction on scanned documents, so the golden dataset can
  include scanned and low-quality documents as Principle IX requires.
- The remote adapter exercises the no-SDK-leak rule under realistic conditions (auth, retries,
  partial failures, rate limits) rather than against a purely local library.
- A local OCR engine (Tesseract, PaddleOCR) can be added later as a third `Parser` with no core
  change. If that adapter reveals a genuine contract mismatch, `OCRProvider` is introduced then,
  by amendment.
- Principle IV's interface list is amended accordingly: `Parser`, `LLMClient`, `ArtifactStore` are
  MVP interfaces; `OCRProvider` is deferred.
