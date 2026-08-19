# Changelog

All notable changes to docdoc are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versioning is semantic, with the usual `0.x` caveat: while the major version is zero the public
API may change in any release. `document_id` derivation is versioned separately and independently
(`IDENTITY_SCHEMA_VERSION`), so identities stay readable across API changes.

## [Unreleased]

Milestone 5: deterministic validation. **A located value is now a checked value** — docdoc will
reject an invoice whose stated total does not equal the sum of its lines, and point at the place on
the page the total was read from.

### Added

- **`docdoc.validation`** — a new layer, above `docdoc.grounding` in the `import-linter` layers
  contract, with a forbidden-imports contract of its own. `validate(extraction, grounding, schema)`
  returns one `ValidationResult`: a verdict, one `CheckOutcome` per declared obligation (passed ones
  included), the findings in a total order, counts that reconcile, provenance, and a content-addressed
  artifact id chained from the grounding artifact.
- **The eight constraint keys Milestone 3 declared and never applied are now enforced** — `enum`,
  `const`, `pattern`, `minimum`, `maximum`, `multiple_of`, `min_length`, `max_length`.
  `tests/unit/test_constraint_key_coverage.py` fails the build if a recognised key ever has no
  enforcement path, so the defect cannot recur one key at a time.
- **Cross-field rules, declared as schema data.** `Schema.rules` carries a closed vocabulary of four
  kinds — `sum_equals`, `product_equals`, `comparison`, `conditional_presence` — evaluated by one
  generic engine that never learns what an invoice is. Principle VI forbids a per-document-type code
  path; Principle VII forbids expressing the rule in a prompt.
- **Three verdicts: `valid`, `invalid`, `incomplete`.** The third exists so that a run whose checks
  could not be evaluated cannot report the same word as a run where everything ran and passed. There
  is no boolean anywhere in the result.
- **`pattern_dialect@1`** — docdoc's own linear-time regular-expression subset, with the constructs
  outside it (backreferences, lookaround, named groups, inline flags, lazy quantifiers) rejected when
  the schema loads, naming the construct.
- **Grounding validation.** A value that is present but that nothing in the document supports is
  reported, at a severity the run's `GroundingPolicy` declares (default: a warning).

### Decisions worth reading

- **docdoc ships a regular-expression engine, and that needed an argument.** CPython's `re` takes
  1,183 ms on `^(a+)+$` against 24 characters and doubles per character, so an ordinary 40-character
  field value runs for days; a timeout would make a verdict depend on machine speed, which no artifact
  id could describe. `google-re2` was measured (6.08 µs typical, 499 µs on a 10,000-character
  adversarial input, against this engine's 6.11 µs and 9.8 ms) and **declined** — with RE2 the dialect
  would be whatever binary happened to be installed, and its version would have to enter
  `options_hash`, so two machines could produce different verdicts. The risk is contained by a
  Hypothesis test that makes `re.fullmatch` the oracle for *what* matches; this engine exists only for
  *how long it may take*.
- **The grounding policy is folded into `options_hash`, beyond ADR-0003's literal Validate row.** The
  row predates the policy and the policy changes verdicts, so the ADR's own rule about omitted inputs
  settles it. Raised as a clarifying amendment rather than resolved silently.
- **`Schema.rules` is hashed only when non-empty.** Every schema hash committed at Milestone 3 is
  unchanged, and `tests/unit/test_schema_snapshot.py` passes **unedited** — introducing rules
  invalidates no stored extraction artifact.
- **An obligation exists only where it applies.** No constraint check is declared for a value the
  model reported absent. Declaring one and marking it not-evaluated would make `incomplete` the
  verdict of nearly every real document, and the state would stop carrying information. This refines
  data-model VAL-17, which had listed `value_absent` as a not-evaluated reason.
- **`number` is lossy by declaration.** Milestone 3 parses a `number` field to a Python `float`, so
  validation reads it through `Decimal(str(v))` — never `Decimal(v)` — and the documentation says
  plainly that `decimal` is the type for money. A guarantee the type system contradicts would be worse
  than the honest sentence.

### Changed

- `Schema` gains `rules`; `FieldSpec` constraints are now checked against their declared type **at
  load** (FR-025), so a numeric bound on a boolean is an authoring error rather than a check that
  silently never runs. Milestone 3's `test_schema_hash` property strategy was updated to draw types
  and constraints together, since an ill-matched pairing is no longer constructible.
- `tests/unit/test_plan_tree_is_current.py` now attributes a test file to the **highest** layer it
  imports, and accepts any plan listing it — Milestone 5 added tests of schema-layer behaviour that
  belong to its own plan rather than to Milestone 3's.

### Measured

- Validating 444 checks (200 line items, 20 rules) takes **4.4 ms** against SC-020's 50 ms bound.
- The adversarial pattern `(a+)+` against 10,000 characters: **9.8 ms**, where CPython's `re` does not
  finish.

---

**Milestone 4: deterministic grounding** — also unreleased, and kept here in full because nothing has
shipped between the two.

Milestone 4: deterministic grounding. **docdoc's central claim is now demonstrable end to end** — an
extracted value can be taken to the character range, page, and bounding box it came from.

### Added

- **`docdoc.grounding`** — a new layer, above `docdoc.extraction` in the `import-linter` layers
  contract. `ground(document, extraction)` returns one outcome per value that carried a claim:
  `exact`, `fuzzy`, or `ungrounded`, with a source-text range, pages, boxes, and up to five
  alternatives.
- **The match view** (`match_view_version = "v1"`) — a derived, versioned folding of the document's
  text for comparison only. `Document.text` stays byte-faithful and the view is never exposed.
  Measured worth: on the committed typesetting fixtures, plain substring matching resolves **0 of 7**
  claims at the exact tier and the folded view resolves **6 of 7**.
- **A candidate filter with a completeness proof** — pigeonhole partitioning, so `ungrounded` means
  *not there* rather than *not looked for*. Verified against brute force in the test suite.
- **`grounding_version = "v1"`**, a version-guard snapshot that fails the build if the candidate
  generator, scorer, tie-break, slack derivation, or default threshold move without a bump, and a
  grounding artifact id chained from the extraction artifact per ADR-0003.
- **`GroundingError`** — the constitution named it; this is its first implementation. It carries no
  `transient` flag, because a deterministic offline computation has no transient failures.
- `docs/concepts/grounding.md` and `examples/ground_invoice.py`, both runnable with no credentials.

### Changed

- **`rapidfuzz>=3.0` joins the base install**, making it the second base dependency. Sanctioned by
  ADR-0005 and the constitution's stack line. The *kernel* still imports `pydantic` alone, and
  `tests/unit/test_kernel_purity.py` enforces that unchanged. The `pyproject.toml` comment that read
  "the kernel's only permitted runtime dependency" now says what it actually means.
- The plan-tree meta-test is parameterised over layers rather than hard-coded to extraction.

### Notes on three decisions that depart from an ADR's literal text

- **Grounding is its own package and layer**, not `extraction/grounding.py` as ADR-0005's text says.
  That ADR's binding decision, per its title, is that fuzzy matching lives *outside the kernel*.
  Inside one package the dependency direction is unenforceable. See the ADR's amendment note.
- **The candidate slack is derived, not chosen**: `k = floor((1 - t) · m / t)`, verified for claim
  lengths 1–59. An independent constant measured **1373 ms against 53 ms** for one value.
- **The round-trip invariant is containment, not identity.** ADR-0006 states it as an identity, which
  is unsatisfiable for a range whose boundary falls inside a character the view deletes.

### Known gaps, recorded rather than hidden

- **Dash folding is absent.** NFKC maps U+2011 to U+2010 but neither to ASCII `-`, so a document
  typeset with U+2010 and a model quoting ASCII misses the exact tier. Adding it would be a seventh
  transformation inside a version ADR-0006 pinned. A `v2` candidate for Milestone 6.
- **A compound word broken at a line end scores exactly 0.900** — clearing the threshold by nothing.
  Raising the default threshold above 0.90 breaks it. This constrains the Milestone 6 tuning.
- **No golden-set metrics task.** The dataset does not exist (`TODO(GOLDEN_DATASET_LICENSING)` gates
  Milestone 6). This milestone makes the grounding rate *computable*; it sets no target for it.

---

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
