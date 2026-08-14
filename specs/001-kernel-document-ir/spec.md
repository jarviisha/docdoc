# Feature Specification: Kernel and Canonical Document IR

**Feature Branch**: `001-kernel-document-ir`

**Created**: 2026-08-14

**Status**: Draft

**Input**: Milestone 1 of the docdoc MVP — the canonical Document Intermediate Representation and
its deterministic operations, as governed by the project constitution (v1.1.0) and ADR-0002 and
ADR-0005.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **developers building on docdoc**: the higher docdoc layers
(ingest, transform, extraction, pipeline) and third-party users of the published library. There
is no end-user interface at this milestone. "The system" below means the document kernel.

### User Story 1 - Represent a document without losing where anything came from (Priority: P1)

A developer holds a parsed document and needs to know, for any range of its text, exactly which
page it sits on and where on that page it appears. They construct a document from text, pages,
tokens and blocks, then ask for the physical location of a text range and receive page numbers
and bounding boxes.

**Why this priority**: This is the reason the project exists. Every capability above it —
grounding, evaluation, human review, audit — is impossible if a text range cannot be resolved
back to a physical location. It is also the smallest slice that delivers standalone value: a
usable, traceable document representation with no parser, no model, and no infrastructure.

**Independent Test**: Construct a document by hand in a test, with no files and no network, ask
for the location of a known text range, and confirm the returned page and bounding box match the
token that produced it.

**Acceptance Scenarios**:

1. **Given** a document whose text contains "INV-001" covered by tokens on page 1, **When** the
   caller requests the location of that text range, **Then** the system returns the page number
   and the normalized bounding box of the covering tokens.
2. **Given** a text range spanning tokens on two different pages, **When** the caller requests its
   location, **Then** the system returns one geometry entry per page, ordered by page.
3. **Given** a text range whose bounds fall outside the document text, **When** the caller
   requests its location, **Then** the system raises an explicit error naming the offending
   bounds, and does not clamp, truncate, or return an empty result.
4. **Given** a document produced by a source that supplied no geometry, **When** the caller
   requests any location, **Then** the system raises an explicit capability error stating that
   geometry is unavailable, rather than returning empty geometry.
5. **Given** any document, **When** the caller attempts to modify it in place, **Then** the
   attempt fails; operations that change a document return a new document.

---

### User Story 2 - Cut a document apart and put it back together without losing provenance (Priority: P2)

A developer needs to work with part of a document — a single page, a section, a window around a
candidate value — and later reassemble parts into a whole. Cutting and reassembling must never
change where a piece of text physically came from.

**Why this priority**: This is the invariant the constitution designates as foundational. It is
second only because it presupposes Story 1. Downstream chunking, windowed extraction, and
page-parallel processing all depend on it, and a defect here silently corrupts every grounded
value produced later rather than failing loudly.

**Independent Test**: Take a document, cut a range out of it, reassemble the pieces, then confirm
that a text range resolves to the same physical location before and after the round trip.

**Acceptance Scenarios**:

1. **Given** a document and a text range, **When** the caller cuts that range out, **Then** the
   resulting document's text equals the original text over that range, and its tokens keep their
   page association and geometry.
2. **Given** several parts cut from one document, **When** the caller reassembles them, **Then**
   text positions are rebased so that every token still resolves to its original physical
   location.
3. **Given** any text range in an original document, **When** that range is carried through a cut
   and a reassembly, **Then** its resolved physical location is identical to the location
   resolved from the original document.
4. **Given** a text range that begins on one page and ends on the next, **When** it is cut,
   **Then** the resulting document retains both pages and the tokens keep their original page
   association.
5. **Given** an empty text range, **When** it is cut, **Then** the operation succeeds and produces
   an empty document that still carries its source reference and provenance.
6. **Given** parts that do not all originate from the same source document, **When**
   reassembly is attempted, **Then** the system raises an explicit error rather than producing a
   document with mixed provenance.

---

### User Story 3 - Tell two parses of the same file apart (Priority: P3)

A developer processes the same file twice — once with a fast local reader, once with a
higher-fidelity one — and needs the two results to be distinguishable, because their text
positions are not interchangeable. They also need to recognize when two uploads are the same
file.

**Why this priority**: This blocks the parser milestone. Without it, two parses of one file share
an identity while carrying incompatible text positions, so a location from one parse can be
silently applied to the other — a defect that produces confidently wrong results rather than
errors.

**Independent Test**: Derive identities for the same bytes under two different parse
configurations and confirm the source identity matches while the document identities differ.

**Acceptance Scenarios**:

1. **Given** the same file bytes presented twice, **When** source identity is derived, **Then**
   both derivations produce the same identity.
2. **Given** one file processed by two different sources, **When** document identities are
   derived, **Then** the two identities differ while the source identity is shared.
3. **Given** one file processed twice by the same source, same version, and same options,
   **When** document identities are derived, **Then** they are identical.
4. **Given** two option sets with the same content but different key ordering, **When** document
   identities are derived, **Then** the identities are identical.
5. **Given** a change to the source's version, **When** the document identity is derived, **Then**
   it differs from the identity derived before the change.

---

### User Story 4 - Find exactly where a piece of text occurs (Priority: P4)

A developer has a quoted piece of text and needs every place it occurs in the document, as text
ranges that can then be resolved to physical locations.

**Why this priority**: This is the entry point the grounding work builds on, but grounding itself
is a later milestone. Exact search is valuable on its own and is deliberately the only search
this feature provides.

**Independent Test**: Search a constructed document for a string that occurs more than once and
confirm all occurrences are returned in document order.

**Acceptance Scenarios**:

1. **Given** a document containing a string three times, **When** the caller searches for it,
   **Then** three text ranges are returned, ordered from earliest to latest.
2. **Given** a document not containing the string, **When** the caller searches, **Then** an
   empty result is returned — this is a normal outcome, not an error.
3. **Given** overlapping potential matches, **When** the caller searches, **Then** non-overlapping
   matches are returned using a consistent, documented rule, and repeated searches return
   identical results.
4. **Given** an empty search string, **When** the caller searches, **Then** the system raises an
   explicit error rather than returning every position.

### Edge Cases

- A document with no pages, no tokens, or empty text: construction succeeds and operations behave
  predictably rather than raising unexpected errors.
- A text range of zero length: resolving its location returns an empty result; cutting it produces
  an empty document.
- A text range exactly at a page boundary, and one exactly at the end of the document text.
- Text containing characters outside the Basic Multilingual Plane, combining marks, or
  right-to-left scripts: positions stay consistent and reversible.
- Tokens supplied out of order, overlapping, or extending beyond the document text: rejected at
  construction with an explicit error, so an invalid document cannot exist.
- A page with zero tokens, for example a blank scanned page.
- Reassembling zero parts, or exactly one part.
- Cutting a range that covers the entire document: the result is equivalent to the original.
- A source that supplies text but no geometry, or geometry for only some tokens.
- A source failure partway through construction: no partially built document is returned.

## Requirements *(mandatory)*

### Functional Requirements

**Representation**

- **FR-001**: The system MUST represent a document as canonical text plus pages, tokens, blocks,
  tables where available, ingestion provenance, a reference to the source file, and its own
  identity.
- **FR-002**: Documents MUST be immutable. Any operation that changes a document MUST return a new
  document and leave the original unchanged.
- **FR-003**: A document MUST NOT contain the original file bytes; it MUST reference them.
- **FR-004**: Text ranges MUST be half-open, starting at or before they end, and MUST fall within
  the document text. Positions MUST be counted in characters, consistently for all text.
- **FR-005**: Geometry MUST be expressed in coordinates normalized to the range 0 to 1 with the
  origin at the top-left of the page, independent of any source's native coordinate system, and
  MUST be associated with exactly one page.
- **FR-006**: Every token MUST be traceable to a text range, a page, and geometry where geometry
  is available.
- **FR-007**: The system MUST reject at construction any document whose tokens fall outside its
  text, overlap one another, or are not in ascending order, so that an invalid document cannot be
  constructed.

**Operations**

- **FR-008**: The system MUST resolve any valid text range to the physical locations it covers,
  ordered by page and then by position within the page.
- **FR-009**: The system MUST return an empty result when resolving a zero-length range, and MUST
  raise an explicit error when resolving a range outside the document text.
- **FR-010**: The system MUST produce a new document from any valid text range, preserving the
  text over that range together with token page association and geometry.
- **FR-011**: The system MUST reassemble parts into a single document, rebasing text positions so
  that every token still resolves to its original physical location.
- **FR-012**: For any text range, resolving its location in the original document MUST equal
  resolving the corresponding range after that document has been cut and reassembled.
- **FR-013**: Reassembly MUST preserve each part's provenance, and MUST raise an explicit error if
  the parts do not share the same source and the same producing configuration.
- **FR-014**: The system MUST return all non-overlapping exact occurrences of a search string, in
  document order, deterministically. Approximate or fuzzy search MUST NOT be provided at this
  layer.

**Identity**

- **FR-015**: Source identity MUST be derived solely from the original file bytes, so identical
  bytes always yield identical source identity.
- **FR-016**: Document identity MUST be derived from source identity together with the identity,
  version, and options of whatever produced the document, so that any difference in those inputs
  yields a different document identity.
- **FR-017**: All text ranges and geometry MUST be interpreted relative to a document identity,
  never relative to source identity alone.
- **FR-018**: Options MUST be reduced to identity using a canonical form that is stable across
  processes, platforms, and key ordering, so that equivalent options always yield equal identity.

**Boundaries and failure**

- **FR-019**: Kernel operations MUST be deterministic: identical inputs MUST produce identical
  outputs, on every run and every platform.
- **FR-020**: The kernel MUST NOT read or write files, access the network, read the clock, or use
  randomness.
- **FR-021**: The kernel MUST NOT depend on any higher docdoc layer, on any external provider, or
  on any transport, storage, or interface technology.
- **FR-022**: A requested capability that is unavailable MUST produce an explicit error naming the
  capability and its availability. The system MUST NOT substitute an empty or default result.
- **FR-023**: All failures MUST surface as the kernel's own error types, carrying enough detail to
  identify the offending input.
- **FR-024**: A failure during construction MUST NOT produce a partially built or corrupted
  document.

### Key Entities

- **Span**: A half-open range of positions within document text. The unit that connects extracted
  values back to the source.
- **BBox / Geometry**: A normalized rectangle on a specific page, describing where something
  physically appears.
- **Token**: The smallest addressable unit of text, carrying its text range, page, geometry, and
  the source's own confidence in it where provided.
- **Page**: One physical page, with its index and dimensions; the frame of reference for geometry.
- **Block**: A grouping of tokens representing a structural region such as a paragraph or heading.
- **Table**: Structured rows and cells, retained when the producing source provides them, with
  cells traceable to text ranges.
- **Document**: The canonical root: text, pages, tokens, blocks, tables, provenance, source
  reference, and identity. Immutable.
- **BlobRef**: A reference to the original file, carrying source identity without carrying bytes.
- **IngestProvenance**: The record of what produced this document — which source, which version,
  which options, and whether a native text layer was used.
- **SpanIndex**: The lookup that makes resolving a text range to its covering tokens efficient.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of text ranges covered by tokens, a caller can obtain page and bounding box
  in a single call.
- **SC-002**: The cut-and-reassemble location invariant (FR-012) holds across at least 10,000
  automatically generated scenarios covering page boundaries, empty ranges, adjacent ranges, and
  multi-page ranges, with zero failures.
- **SC-003**: Identical file bytes yield identical source identity in 100% of cases; identical
  bytes with identical producing configuration yield identical document identity in 100% of cases;
  any change to that configuration yields a different identity in 100% of cases.
- **SC-004**: Equivalent option sets differing only in key ordering yield identical identity in
  100% of cases.
- **SC-005**: Every kernel operation is repeatable: running the full test suite twice produces
  identical results, with zero dependence on file access, network, clock, or randomness — verified
  by a test that fails if any occurs.
- **SC-006**: 100% statement coverage of the four core operations (resolve location, cut,
  reassemble, exact search).
- **SC-007**: A developer can construct a document and resolve a text range to a physical location
  with no database, no object storage, no credentials, and no network access.
- **SC-008**: 100% of invalid requests — out-of-range positions, unavailable capabilities,
  mismatched reassembly, empty search strings — produce an explicit error; zero produce a silent
  empty or default result.
- **SC-009**: Constructing an invalid document is impossible: 100% of malformed token sets are
  rejected at construction.
- **SC-010**: A new contributor can build a document and resolve a location by following a single
  documented example, without reading the implementation.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a
candidate for `/speckit-clarify` if it proves wrong in review.

- **Text positions are counted in characters**, not bytes, so that positions are stable regardless
  of how text is encoded for storage or transport.
- **Reassembly is restricted to parts of the same document.** Combining parts originating from
  different source files into one compound document is out of scope for this milestone; it is
  rejected with an explicit error. This is the conservative reading of MVP discipline, and can be
  relaxed later without changing the operation's contract.
- **Overlapping tokens are invalid.** Some sources emit overlapping regions; normalizing them is
  the producing layer's responsibility, not the kernel's.
- **Exact search returns non-overlapping matches**, scanning left to right and resuming after each
  match, which is the conventional and least surprising rule.
- **Tables are optional.** Their absence is a normal condition and is not an error; a request for
  table-derived information from a document without tables follows the capability-error rule.
- **Page dimensions are supplied by whatever produces the document.** The kernel does not infer
  page size and does not convert between coordinate systems; it only stores already-normalized
  geometry.
- **Confidence values from a producing source are stored verbatim and are not interpreted** at
  this layer. This mirrors the treatment of model-reported confidence decided in ADR-0004.
- **No text normalization occurs here.** Canonical text stays byte-faithful to what the producing
  source emitted; normalization for matching purposes belongs to a later milestone.

## Dependencies

- **Constitution v1.1.0** — Principles I, III, VIII, X, and XII bind this feature directly.
- **ADR-0002** — fixes the two-level identity model that FR-015 through FR-018 express.
- **ADR-0005** — fixes exact-only search at this layer, with approximate search deferred to the
  extraction layer.
- **No runtime dependency on any other docdoc component.** This feature sits at the bottom of the
  dependency order and depends on nothing above it.

## Out of Scope

Explicitly deferred to later milestones, and MUST NOT appear in this feature:

- Any file-format reader, text-layer detection, or optical recognition.
- Approximate or fuzzy matching of any kind.
- Normalization of canonical text, and the position map that a matching view would require.
- Extraction, schemas, validation, evaluation, annotations.
- Persistence, artifact storage, caching, interfaces, and command-line tooling.
- Chunking and windowed views over a document.
