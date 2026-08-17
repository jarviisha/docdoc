# Changelog

All notable changes to docdoc are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning is semantic, with the usual `0.x` caveat: while the major version is zero the public
API may change in any release. `document_id` derivation is versioned separately and independently
(`IDENTITY_SCHEMA_VERSION`), so identities stay readable across API changes.

## [Unreleased]

Milestone 2: the ingest parser layer. docdoc can now turn a real file into a `Document`.

### Added

- **`docdoc.ingest`** — bytes in, canonical `Document` out. `parse()` detects the media type from
  the byte signature, enforces size and page limits, decides which path the document takes, selects
  a parser by capability, and validates what comes back.
- **Two parsers behind one contract.** `pdf-text` (native, offline, `docdoc[pdf]`) and `azure-di`
  (geometry-capable cloud service, `docdoc[azure]`). A third-party parser satisfying the `Parser`
  protocol is a first-class citizen and is held to the same shared contract test.
- **`text-layer@1`** — the versioned rule deciding whether a native text layer is usable. Recorded
  per page as well as per document, so a page contributing no tokens is an explicit fact rather
  than a silent gap. Overridable with `force=`, which preserves the verdict it overrode.
- **Capability-based selection** — `CapabilityRequest`, `ParserRegistry`, `default_registry()`.
  An explicit priority list decides, defaulting to offline before service-backed, with the parser
  id as the final tie-break. A parser that is installed but unusable stays visible with its reason.
- **Ingest error model** — `IngestError` with `UnsupportedDocumentError`, `ParserCapabilityError`,
  `ParserError`, and `ProviderError`, each carrying a structured `reason`. No provider SDK
  exception escapes an adapter.
- **Bounded transport** — at most three attempts, exponential backoff with jitter, honouring a
  service-supplied wait, bounded by a per-attempt timeout and an overall deadline. Kept in a
  separate type from parse options so it cannot influence document identity.
- **One structured `ingest.parse` event** per parse, carrying identifiers, counts, and timings —
  never document content or credentials.
- **Optional extras** `docdoc[pdf]` and `docdoc[azure]`. The base install remains `pydantic` alone.

### Changed

- **`IngestProvenance` gained two optional fields**, `text_layer` and `reading_order`. Additive and
  defaulting to `None`, so every document Milestone 1 could construct stays valid, and
  `document_id` — which reads only the blob, parser, version, and options hash — is unaffected.
- `import-linter` now enforces `docdoc.ingest` above `docdoc.kernel`, and bars provider SDKs from
  every module except the two adapters.

### Notes

- **PyMuPDF is AGPL-3.0** while docdoc is Apache-2.0. Keeping it behind the opt-in `docdoc[pdf]`
  extra leaves docdoc's own distribution unaffected, but embedding that extra in a closed-source
  pipeline incurs the AGPL obligation. See [ADR-0001](docs/adr/0001-parser-and-ocr-strategy-in-mvp.md).
- Two assumptions in the plan turned out to be wrong and were corrected by measurement rather than
  worked around. PyMuPDF's sorted extraction sorts by vertical position across the page and so
  *interleaves* columns — the adapter therefore uses content-stream order and declares
  `pymupdf-stream@1` rather than claiming a layout reconstruction it does not perform. And word
  coordinates arrive in unrotated page space while the page size is the displayed one, so the
  adapter maps every box through the page rotation matrix first.
- `image/tiff` is detected but not accepted. Multi-page TIFF is common and would need page-splitting
  semantics this milestone puts out of scope; a deployment can opt in and accept that only the
  first page is read.

## [0.1.0] — 2026-08-14

First release. Milestone 1: the kernel and the canonical Document IR.

### Added

- **Value primitives** — `Span` (half-open, Unicode code-point offsets), `BBox` (normalized to
  `0..1`, top-left origin), `Geometry` (page-anchored box), `Token`.
- **Structure** — `Page`, `Block`, `BlockKind`, `Table`, `TableCell`.
- **`Document`** — the immutable canonical IR: text, pages, tokens, blocks, tables, provenance,
  source reference, and identity. Ten construction invariants (DOC-1..DOC-10) are checked once, at
  construction, so an invalid `Document` cannot exist.
- **Operations** — `locate` (text range → page and boxes), `page_for` (text range → pages, working
  without geometry), `find` (exact search), `slice`, and `Document.merge`.
- **Two-level identity** — `blob_id_for`, `options_hash_for`, `document_id_for`, and
  `canonical_json`, per ADR-0002. Inputs are hashed as a named-field JSON object rather than
  concatenated, which removes a collision class where `("pdf", "1.0")` and `("pdf1", ".0")` would
  otherwise produce the same identity.
- **`SpanIndex`** — binary search over sorted arrays, O(log n + k) lookup.
- **Error model** — `DocdocError` → `KernelError` → six specific types, each carrying structured
  attributes rather than only a message.
- **Property suite** — Hypothesis coverage of the round-trip invariant
  `locate(s) == merge(partition(d)).locate(s)`, across randomized documents including Vietnamese
  diacritics, combining marks, and characters outside the BMP.
- **Purity enforcement** — an AST allowlist scan and a runtime audit hook prove the kernel performs
  no file, network, clock, or random access.
- **Layer enforcement** — `import-linter` contracts for the dependency direction and for provider
  SDK isolation.

### Notes on design decisions

Several decisions in this release differ from the original design sketch, each for a stated
reason. They are documented in `specs/001-kernel-document-ir/research.md`:

- `Token` carries no `text` field; its text derives from `document.text[span]`. This removes a
  duplicate-state invariant that could drift, and cuts memory materially at scale.
- `find()` is exact-only and takes no `fuzzy` parameter. Fuzzy matching cannot live in the kernel
  without breaking its dependency rule (ADR-0005).
- Partial geometry within a document is rejected (DOC-8) rather than supported. Allowing it would
  make `locate()` silently lossy — a caller could not tell "no token there" from "geometry
  unavailable here".
- `slice()` drops tokens a cut would truncate. Keeping a clipped token would leave its geometry
  describing glyphs that are no longer present, and a wrong box is worse than a missing one.
- Page numbers are preserved through `slice`, so a slice of page 7 still reports page 7. Page
  indices are therefore strictly ascending rather than contiguous from zero.
- `Document.origin` records which ranges of the original parse a document occupies. Without it,
  `merge` cannot detect overlapping or out-of-order parts, and the rejection rules in the API
  contract are not implementable.

### Not included

Parsers, OCR, LLM adapters, extraction, schemas, grounding, validation, evaluation, persistence,
HTTP API, and CLI. These are Milestones 2 through 7.

[Unreleased]: https://github.com/OWNER/docdoc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/docdoc/releases/tag/v0.1.0
