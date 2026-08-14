# Architecture Decision Records

Decisions that bind implementation. Each ADR resolves a question the
[constitution](../../.specify/memory/constitution.md) raised but deliberately left open, so that
it would be decided explicitly rather than settled by whichever code landed first.

| ADR | Title | Status | Resolves |
|-----|-------|--------|----------|
| [0001](0001-parser-and-ocr-strategy-in-mvp.md) | Parser and OCR strategy in the MVP | Accepted | `OCR_IN_MVP` |
| [0002](0002-blob-and-document-identity.md) | Blob identity and document identity are separate | Accepted | `DOCUMENT_IDENTITY` |
| [0003](0003-content-addressed-artifact-chain.md) | Per-stage content-addressed artifact chain | Accepted | `PROCESSING_CACHE_KEY` |
| [0004](0004-confidence-semantics.md) | Confidence is never a single blended number | Accepted | `CONFIDENCE_SEMANTICS` |
| [0005](0005-fuzzy-grounding-specification.md) | Fuzzy grounding lives outside the kernel and is fully pinned | Accepted | `FUZZY_GROUNDING_SPEC` |
| [0006](0006-comparison-time-match-view.md) | Normalization happens in a comparison-time match view | Accepted | `NORMALIZATION_VS_GROUNDING` |

## Conventions

- Filenames: `NNNN-kebab-case-title.md`, numbered sequentially and never reused.
- Status: `Proposed` → `Accepted` → `Superseded by ADR-NNNN`. Accepted ADRs are not edited in
  place once implementation depends on them; they are superseded by a new ADR.
- Every ADR states the principles it engages and the concrete consequences it accepts.
- An ADR may not override a constitutional principle. If a decision requires that, amend the
  constitution in the same change.
