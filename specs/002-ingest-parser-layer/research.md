# Phase 0 Research: Ingest Parser Layer

**Feature**: `002-ingest-parser-layer` | **Date**: 2026-08-17

Fourteen decisions, each resolving a question the spec deliberately left to planning or that surfaced
while designing against the Milestone 1 kernel. Format: Decision / Rationale / Alternatives.

Where a decision depends on the runtime behaviour of a library that is **not yet installed in this
repository**, that is stated explicitly and the verification is named as an implementation task.
Nothing below is presented as a measured result.

---

## R1 — Native PDF reader: PyMuPDF, shipped as an opt-in extra

**Decision**: PyMuPDF (`pymupdf`), installed only via `docdoc[pdf]`. The base install stays
`pydantic` alone.

**Rationale**: Named directly by the constitution's sanctioned stack and by ADR-0001. It supplies
word-level text with bounding boxes, page dimensions, and rotation in one library, which is exactly
the shape the kernel needs, and it is fast enough that the native path stays the cheap default.

**Licensing consequence that must be documented, not buried**: PyMuPDF is AGPL-3.0 (or a paid
commercial licence); docdoc is Apache-2.0. Keeping it behind an optional extra means docdoc's own
distribution stays Apache-2.0 and the AGPL obligation is only incurred by a user who chooses to
install `docdoc[pdf]`. This MUST be stated in the README and in the extra's documentation, because a
user embedding docdoc in a closed-source pipeline needs to know before, not after.

**Alternatives considered**: `pypdfium2` (Apache-2.0/BSD, permissive, good geometry) and
`pdfplumber`/`pdfminer.six` (MIT, slower, pure Python). Either would remove the licensing footnote.
Both were rejected here because the constitution names PyMuPDF in its sanctioned stack, and
Principle §Governance forbids resolving that silently in code. If the AGPL implication is judged
unacceptable, the correct route is an ADR plus a constitution amendment — **not** a quiet swap in
this milestone.

---

## R2 — Geometry-capable service: Azure Document Intelligence, via its own SDK, confined to one module

**Decision**: Azure Document Intelligence, installed via `docdoc[azure]`, accessed through the
official SDK, and imported in exactly one module: `docdoc/ingest/parsers/azure_di.py`.

**Rationale**: ADR-0001 names it as the default choice. Using the vendor SDK rather than raw HTTP
buys authentication, long-running-operation polling, and error typing that would otherwise be
hand-rolled. Principle IV's rule is that provider types must not *escape* an adapter, not that SDKs
are forbidden — and `import-linter` enforces the containment mechanically (R13).

**Alternatives considered**: calling the REST API directly with `httpx`. Rejected: it trades a
well-tested polling implementation for code docdoc would have to maintain, and it does not reduce
coupling, since the response schema is the coupling, not the transport.

---

## R3 — Text-layer usability rule: `text-layer@1`

**Decision**: A single versioned rule, evaluated per page and then aggregated:

- A page is **text-bearing** when the native reader yields **≥ 100 characters** after discarding
  whitespace, Unicode control characters, and `U+FFFD` replacement characters.
- A document's text layer is **usable** when **≥ 50%** of its pages are text-bearing and at least
  one page is.
- Rule identity `text-layer@1`; both thresholds are configurable; the identity changes to `@2` if
  either default changes.

**Rationale**: 100 characters sits in the wide empty gap between the two populations. Page furniture
on an otherwise scanned page — a stamped page number, a header, a watermark — lands well under it
(≈10–60 characters), while any page of real prose or an invoice body lands far above it. A simple
majority handles the common "scanned cover page on a digital contract" case without per-page routing,
which the spec's Q1 clarification put out of scope. Recording both thresholds and the rule id in
provenance is what makes a later retune detectable in results produced before it (FR-010).

**Verification, not assertion**: these defaults are a starting point chosen from the shape of the
problem, not from measurement. An implementation task validates them against the committed sample set
(one digital PDF, one fully scanned PDF, one with a near-empty text layer, one image) and the plan
expects them to be adjusted if that set disagrees.

**Alternatives considered**: (a) mean characters per page — a single 2,000-character page drags a
20-page scan over any threshold; (b) requiring *every* page to be text-bearing — a single blank page
would route an entire digital document to a paid service; (c) image-coverage ratio per page — more
faithful but needs page rendering, which is far more expensive than the decision warrants.

---

## R4 — Assessment reads the source, and it is a PDF-only question

**Decision**: The assessment runs before parser selection and uses the native reader to pull raw page
text without building any IR. For image inputs it short-circuits: an image has no text layer, so the
verdict is "not usable" with no file inspection at all. If the input is a PDF and the `pdf` extra is
not installed, the assessment raises `ParserCapabilityError` rather than guessing.

**Rationale**: The verdict must be cheap, deterministic, and reproducible from bytes alone (FR-009,
FR-010). Extracting page text is far cheaper than a full parse and touches no network. Guessing when
the reader is absent would be exactly the silent fallback Principle VIII forbids.

**Alternatives considered**: assessing from a completed native parse — rejected, because a document
that fails the rule would have paid the full parse cost first, and the verdict is meant to *precede*
routing.

---

## R5 — Reading order is the adapter's declared property

**Decision**: Every parser exposes a `reading_order` identifier recorded in provenance. The native
adapter declares `pymupdf-layout@1` and requests the library's layout-ordered word extraction; the
Azure adapter declares `azure-di-service@1` and preserves the service's own ordering. The ingest
layer validates ascending, non-overlapping token order and rejects violations — it never sorts.

**Rationale**: Directly implements the spec's Q2 clarification (FR-036, FR-037). Reading order is the
one place where provider-specific knowledge legitimately lives, and pushing it into the adapter keeps
a layout engine — which would be heuristic, hard to test, and squarely on Principle XI's rejected
list — out of the core.

**Unverified, and named as a task**: which extraction mode PyMuPDF's sorted output actually produces
on a two-column page has not been checked in this repository, because the library is not installed
yet. The implementation task is a test over a committed two-column fixture asserting that column one
is emitted before column two; if the library's sort does not deliver that, the declared identifier
changes to describe what it *does* deliver rather than what was hoped for.

---

## R6 — Canonical text is assembled from tokens, and the tokens index into it

**Decision**: The adapter builds `Document.text` by concatenating word tokens — a single space
between words on a line, `\n` at line ends, `\n` between pages — and every token's span points into
that assembled string. Page spans partition the text.

**Rationale**: The kernel requires tokens to be ascending, non-overlapping, and inside the text, and
requires each page's span to cover its tokens. Taking the library's page-level text blob as canonical
and then locating word boxes inside it by searching would be fragile and occasionally wrong. Building
the text from the tokens makes the correspondence exact by construction rather than by search.
FR-007's "byte-faithful to what the parser emitted" is satisfied: this *is* what the parser emitted;
no normalization, dehyphenation, or whitespace collapsing is applied on top.

**Consequence worth stating**: `Document.text` is therefore not byte-identical to what a PDF viewer's
copy-paste produces. It is a faithful serialization of the parser's tokens, which is the thing spans
must agree with.

**Alternatives considered**: library page text as canonical with a search-based token mapping —
rejected as fragile; character-level tokens — rejected as an enormous size increase for no present
grounding benefit.

---

## R7 — Geometry normalization, with a bounded tolerance and a loud failure beyond it

**Decision**: Divide by the displayed page width and height. Coordinates that fall outside `0..1` by
**≤ 1% of the page dimension** are clamped to the boundary; anything further out raises
`GeometryError` naming the page and the offending box.

**Rationale**: Glyph boxes sitting a hair outside the MediaBox are ordinary rendering slop, and
failing a whole document over a fraction of a point would be useless. A box 30% off the page is not
slop — it is a coordinate-system or rotation bug, and that is precisely the class of error this
project must not absorb silently. The tolerance draws the line between the two.

**Alternatives considered**: clamping everything (hides real bugs — a wrong box is worse than a
missing one, per the M1 precedent); rejecting everything outside `0..1` (fails on ordinary documents).

---

## R8 — Rotation is resolved to displayed space

**Decision**: Geometry describes the page as displayed. The adapter applies the page's rotation
transform before normalizing, and `Page.rotation` records the rotation that was applied.

**Rationale**: FR-006. A caller drawing a box on a rendered page must get a box over the right
glyphs; unrotated file-space coordinates would be silently wrong on exactly the scanned-and-rotated
documents this milestone exists to support.

**Unverified, and named as a task**: whether the chosen extraction call already returns rotated
coordinates, or whether the rotation matrix must be applied by the adapter, is library behaviour that
has not been checked here. The implementation task is a test over a committed 90°-rotated fixture.

---

## R9 — File type comes from the bytes, not from the caller

**Decision**: A small stdlib signature table (`%PDF-`, JPEG, PNG, TIFF magic bytes) decides the type.
TIFF is *detected* even though it is not *accepted*, so a TIFF is rejected as an unsupported type
rather than as an unrecognizable file — a much more useful error, and the difference costs four bytes
of comparison.
The caller's declared type is recorded but never trusted; a mismatch is resolved in favour of the
content, and an unrecognized signature raises `UnsupportedDocumentError`.

**Rationale**: Handing PDF bytes labelled `image/png` to an image path is a failure mode worth
designing out, and the check costs a few bytes of comparison. A signature table avoids adding
`python-magic` (a libmagic binding) to satisfy something a dozen lines of stdlib handles.

**Alternatives considered**: trusting the declared type (rejected: unsafe); `python-magic` (rejected:
a native dependency for a trivial need — Principle XI).

---

## R10 — Ingest error types, and the two different "capability" errors

**Decision**: Add `IngestError(DocdocError)` with the constitution's named subtypes: `ParserError`,
`UnsupportedDocumentError`, `ParserCapabilityError`, `ProviderError`. Limits are not a new type:
`UnsupportedDocumentError` carries `reason ∈ {mime_type, size_limit, page_limit, encrypted, corrupt}`.
Timeouts and deadlines are `ProviderError` with `reason ∈ {timeout, deadline, rate_limit, auth,
transport, service}`.

**Rationale**: The constitution fixes the error vocabulary; inventing `DocumentTooLargeError`
alongside it would fragment a list that is meant to be stable. A structured `reason` keeps the
distinction machine-readable without growing the type set.

**Terminology hazard, resolved explicitly**: the kernel already exports `CapabilityError`, and it
means something different. `kernel.CapabilityError` = "this document cannot answer that question,
because its parser supplied no geometry". `ingest.ParserCapabilityError` = "no available parser can
satisfy this request". Both are documented side by side in the contract so the pair is never confused.

---

## R11 — Registry, priority, and the offline-first default

**Decision**: Explicit registration into a `ParserRegistry`; a default factory registers whichever
adapters are importable and marks the rest unavailable with a reason. Priority is an ordered tuple of
parser ids, defaulting to `("pdf-text", "azure-di")` — offline before service-backed — with
`parser_id` as the final tie-break. No plugin entry points.

**Rationale**: Implements the spec's Q3 clarification (FR-016). An explicit, readable list is
inspectable without running anything; entry-point discovery would make the selected parser depend on
what happens to be installed in the environment, which is the opposite of that. FR-018 requires an
unusable parser to be reported with its reason rather than silently omitted, which is why
unavailability is a recorded state and not an absence from the registry.

**Alternatives considered**: `importlib.metadata` entry points (rejected: Principle XI — no present
need, and it weakens inspectability); scoring parsers by declared capability overlap (rejected:
implicit and surprising).

---

## R12 — Retry, timeout, and deadline, and why none of it touches identity

**Decision**: The remote adapter makes at most 3 attempts with exponential backoff plus jitter,
honours a service-supplied wait interval when it is shorter than the remaining budget, bounds each
attempt with a timeout, and bounds the whole parse with a monotonic deadline. These live in a
`TransportSettings` object that is **not** part of `ParseOptions` and therefore never reaches
`options_hash`. The SDK's own retry policy is configured to docdoc's values rather than left at its
defaults, so retries happen in one place.

**Rationale**: Implements the spec's Q4 clarification (FR-038, FR-039). Splitting transport settings
from parse options is what makes FR-039 true by construction: a value that cannot change a successful
result must not be able to change that result's identity. Two retry layers stacked — SDK and adapter —
would silently multiply the attempt count past the documented bound.

**Alternatives considered**: putting timeouts in `ParseOptions` (rejected: pollutes identity, so the
same document parsed with a longer timeout would get a different `document_id`); disabling SDK retries
entirely and re-implementing polling (rejected: more code, same behaviour).

---

## R13 — Boundary enforcement extended, not re-invented

**Decision**: Add `docdoc.ingest` above `docdoc.kernel` in the `import-linter` layers contract, and a
forbidden contract naming `pymupdf`/`fitz`/`azure`/`httpx` as forbidden from every ingest module
except `docdoc.ingest.parsers.*`. The kernel's existing purity test is untouched and must keep passing.

**Rationale**: Principle X requires the boundary to be machine-checked, and SC-008 makes it a build
failure. Extending the existing contracts costs nothing and keeps one mechanism rather than two.

---

## R14 — Provider tests run offline by default, live tests are opt-in

**Decision**: Three tiers. (1) Unit and property tests — no credentials, no network. (2) Adapter
tests against **recorded, scrubbed service responses** committed as fixtures — these run everywhere
and are what actually pin the response-to-IR mapping. (3) Live integration tests marked `provider`,
skipped with a stated reason when credentials are absent.

**Rationale**: SC-009 requires a contributor without credentials to run the whole suite bar the
service-backed tests, and FR-034 requires the split. Recorded responses are the tier that makes the
Azure adapter genuinely testable rather than nominally covered — without them, "skipped without
credentials" would mean the mapping is never exercised in CI at all.

**Scrubbing is a requirement, not a nicety**: recorded fixtures must contain no real document content
and no account identifiers, and are generated from synthetic documents.

---

## Parser versioning (applies to R1, R2, R12)

`parser_version` is the adapter's own version **plus** the underlying library or service API version —
`1.0.0+pymupdf-1.24.9`, `1.0.0+azure-di-2024-11-30`. This is required by FR-020 and ADR-0003: a
library upgrade that changes extraction output changes the document's identity, which is the entire
point of a content-addressed chain. A bare adapter version would let two materially different parses
collide.
