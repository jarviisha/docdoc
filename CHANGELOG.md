# Changelog

All notable changes to docdoc are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning is semantic, with the usual `0.x` caveat: while the major version is zero the public
API may change in any release. `document_id` derivation is versioned separately and independently
(`IDENTITY_SCHEMA_VERSION`), so identities stay readable across API changes.

## [Unreleased]

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
