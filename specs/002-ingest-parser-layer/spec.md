# Feature Specification: Ingest Parser Layer

**Feature Branch**: `002-ingest-parser-layer`

**Created**: 2026-08-17

**Status**: Implemented

**Input**: User description: "Parser layer biến một blob (PDF/ảnh) thành `Document` IR của kernel. Hai
đường: native PDF text path cho PDF có text layer dùng được, và một cloud document-intelligence
provider có geometry cho PDF scan/ảnh. Chọn parser theo capability chứ theo tên provider; quyết định
"text layer có dùng được không" phải explicit và ghi vào ingest provenance."

Milestone 2 of the docdoc MVP — the ingest layer that turns a source file into the canonical
Document IR delivered by Milestone 1, as governed by the constitution (v1.1.1) and ADR-0001,
ADR-0002, and ADR-0003.

## Clarifications

### Session 2026-08-17

- Q: How should a mixed document — some pages carrying a text layer, some scanned — be handled? → A:
  Routing stays a whole-document decision, but the assessment is recorded per page in provenance, so
  a page that yields no tokens under the chosen path is explicitly visible rather than silently
  empty.
- Q: What rule determines the order of canonical text when a parser emits content out of reading
  order — multi-column pages, sidebars, headers and footers? → A: Each parser adapter is responsible
  for emitting tokens in its own documented reading order and declaring that order in provenance.
  The ingest layer validates the result and rejects an invalid one; it never re-orders by geometry
  and performs no layout analysis.
- Q: When several registered parsers satisfy the same capability request, which one is chosen? → A:
  An explicit priority list in configuration decides, defaulting to an order that puts offline
  parsers ahead of ones that call an external service, with the parser id as the final tie-break.
- Q: What retry and timeout policy governs the remote path? → A: At most three attempts, exponential
  backoff with jitter, honoring a wait interval the service asks for, bounded by both a per-attempt
  timeout and an overall deadline for the parse — all configurable, and none of it participating in
  document identity.
- Q: What observability must the ingest layer itself emit? → A: One structured log event per parse,
  carrying identifiers, parser id and version, the text-layer verdict, page count, duration, attempt
  count, and outcome — identifiers and numbers only, never content. Counters, histograms, and tracing
  are deferred to the pipeline milestone.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **developers building on docdoc**: the higher docdoc layers
(transform, extraction, pipeline) and third-party users of the published library. There is no
end-user interface at this milestone. "The system" below means the ingest layer, and "a parser"
means any component that turns a source file into a document, whether it runs locally or calls a
remote service.

### User Story 1 - Turn a text-bearing PDF into a canonical document, offline (Priority: P1)

A developer has a PDF that was produced digitally and already carries a text layer. They hand the
file to docdoc and receive a canonical document whose text, pages, tokens, and geometry come
straight from that text layer — with no credentials, no network call, and no cost.

**Why this priority**: This is the default path for the majority of real documents and the only
path every contributor can run. It is the smallest slice that makes Milestone 1 useful: without it,
a document can only be constructed by hand in a test. It also produces the highest-fidelity
geometry available, which is the fidelity every later grounding guarantee inherits.

**Independent Test**: Point the system at a small text-layer PDF checked into the repository, with
no credentials configured and the network unavailable, and confirm a document comes back whose
text ranges resolve to the pages and bounding boxes of the original.

**Acceptance Scenarios**:

1. **Given** a PDF with a usable text layer, **When** the caller asks the system to parse it,
   **Then** a document is returned whose canonical text is the text the file carries, with one page
   entry per PDF page.
2. **Given** that document, **When** the caller resolves any of its text ranges, **Then** page
   numbers and normalized bounding boxes are returned, without re-reading the file.
3. **Given** a page whose native coordinates use a bottom-left origin or a non-unit page size,
   **When** the document is produced, **Then** every stored coordinate is already normalized to the
   range 0 to 1 with a top-left origin, and no native coordinate survives into the document.
4. **Given** a page that the file rotates for display, **When** the document is produced, **Then**
   geometry describes where content appears on the page as displayed, not in unrotated file space.
5. **Given** the same file parsed twice by the same parser, same version, and same options,
   **When** the two documents are compared, **Then** their text, tokens, geometry, and identity are
   identical.
6. **Given** a PDF page carrying no extractable text, such as a blank page, **When** the document is
   produced, **Then** the page still appears in the document with its dimensions and zero tokens.
7. **Given** a page laid out in two columns, **When** the document is produced, **Then** the text
   follows the parser's declared reading order, and that declaration is recorded in provenance so
   the ordering behind any result is knowable after the fact.

---

### User Story 2 - Have the text-layer decision made explicitly, and recorded (Priority: P2)

A developer processes a mixed pile of documents — some digital, some scanned — and must be able to
answer later, for any single result, whether it came from a native text layer or from recognition,
and why. The system decides this before parsing rather than discovering it halfway through, and
writes the decision into the document's provenance.

**Why this priority**: The constitution makes the text-layer usability decision an explicit,
inspectable one rather than an implicit side effect, and every result produced by this milestone
carries it forever. Getting it wrong silently is the failure mode that forces OCR onto text-bearing
PDFs — destroying native geometry — or, worse, ships a near-empty document from a scan as if it
were a successful parse.

**Independent Test**: Run the assessment over a text-layer PDF and over a scanned PDF, with no
parsers invoked, and confirm each produces the opposite verdict together with the evidence behind
it; then confirm the verdict appears in the provenance of the resulting document.

**Acceptance Scenarios**:

1. **Given** a PDF whose pages carry extractable text, **When** the system assesses it, **Then** the
   verdict is that the text layer is usable, and the assessment reports the evidence that produced
   that verdict.
2. **Given** a PDF whose pages are page-sized images with no extractable text, **When** the system
   assesses it, **Then** the verdict is that the text layer is not usable.
3. **Given** any produced document, **When** a caller inspects its provenance, **Then** it states
   whether a native text layer was used, which parser produced the document, that parser's version,
   and the options it ran with — without re-reading the source file.
4. **Given** a document produced by the recognition path, **When** a caller inspects its provenance,
   **Then** it records that the text layer was not used, and why the assessment reached that
   verdict.
5. **Given** the same file assessed twice, **When** the verdicts are compared, **Then** they are
   identical, and the rule that produced them is identified by a version so a later change to that
   rule is detectable in past results.
6. **Given** a caller who wants to override the assessment and force one path, **When** they say so
   explicitly, **Then** the system honors the override and records both the override and the
   verdict it overrode.
7. **Given** a document where only some pages carry a text layer, **When** it is assessed, **Then**
   provenance records a verdict for every page alongside the document-level verdict that decided
   routing, so a page contributing no tokens is identifiable as expected rather than as a defect.

---

### User Story 3 - Turn a scanned document or an image into the same canonical document (Priority: P3)

A developer has a scanned PDF or a photographed page. They hand it to docdoc and receive a document
of exactly the same shape as the one Story 1 produces — text, pages, tokens, geometry, and, where
the service supplies them, tables — so everything downstream is unaware of which path it came from.

**Why this priority**: It completes the coverage the MVP promises and is what makes scanned
documents usable at all, but it depends on Stories 1 and 2 to define the target shape and the
routing, and it is the only part of this milestone that requires credentials and cost.

**Independent Test**: With credentials configured, send one scanned page to the remote path and
confirm the returned document satisfies the same kernel invariants as a natively parsed one, with
every text range resolving to a page and a bounding box.

**Acceptance Scenarios**:

1. **Given** a scanned PDF and configured credentials, **When** the caller asks the system to parse
   it, **Then** a document is returned that satisfies every kernel construction rule, with tokens in
   ascending, non-overlapping text order.
2. **Given** a service that reports coordinates in its own units and origin, **When** the document is
   produced, **Then** all geometry is normalized to 0-to-1 with a top-left origin, and no
   service-specific type or field name appears anywhere in the document.
3. **Given** an image file rather than a PDF, **When** it is parsed, **Then** a single-page document
   is returned.
4. **Given** a service that supplies tables, **When** the document is produced, **Then** tables are
   retained with their cells traceable to text ranges; **Given** a service that supplies none,
   **Then** their absence is a normal condition, not an error.
5. **Given** a transient service failure such as a timeout or a rate-limit response, **When** the
   parse is attempted, **Then** the system retries up to the configured attempt limit with backoff,
   and, if it still fails, raises an explicit provider error that names the failure — it does not
   silently fall back to another parser and does not return a partial document.
6. **Given** a permanent service failure such as a rejected credential or an unsupported file,
   **When** the parse is attempted, **Then** the failure is raised immediately without retrying.
7. **Given** no credentials are configured at all, **When** the recognition path is requested,
   **Then** the system raises an explicit error stating that the capability is unavailable, before
   any file is read or transmitted.
8. **Given** a service that accepts the request but never responds, **When** the per-attempt timeout
   or the overall deadline passes, **Then** the parse is abandoned with an explicit error naming
   which bound was exceeded, rather than blocking indefinitely.

---

### User Story 4 - Ask for a capability, never for a provider (Priority: P4)

A developer needs a parser that can supply geometry for a scanned file. They express that as a
requirement — "I need geometry, and this is the file type" — and the system picks a parser that
satisfies it. They never name a provider in their own code, so swapping the provider changes
configuration, not application code.

**Why this priority**: This is the boundary that keeps docdoc from becoming a vendor wrapper. It is
last because the first three stories can each be demonstrated with a directly constructed parser,
but no later milestone should be written against a provider name, so it must exist before extraction
work begins.

**Independent Test**: Register two parsers with different declared capabilities in a test, request a
capability only one of them satisfies, and confirm the right one is chosen — then request a
capability neither satisfies and confirm the failure names the capability, not a provider.

**Acceptance Scenarios**:

1. **Given** several registered parsers, **When** the caller requests a set of required capabilities
   for a given file type, **Then** the system returns a parser that declares all of them.
2. **Given** more than one parser satisfies a request, **When** selection runs, **Then** the
   configured priority order decides, an offline parser is preferred over a service-backed one under
   the default order, and repeated selection returns the same parser regardless of the order the
   parsers were registered in.
3. **Given** no registered parser satisfies the request, **When** selection runs, **Then** an explicit
   error is raised naming the required capability and what was actually available — no parser is
   substituted and no degraded result is produced.
4. **Given** a parser whose declared capabilities do not match what it actually produced — for
   example it declares geometry but returns tokens without it — **When** the document is
   constructed, **Then** the mismatch is rejected as an error rather than stored.
5. **Given** an application that has parsed documents through the system, **When** the underlying
   provider is swapped in configuration, **Then** no application code changes and the only
   observable difference is in provenance and identity.

### Edge Cases

- A file whose declared type disagrees with its actual content, such as an image renamed to `.pdf`:
  the actual content decides, and an unsupported type is an explicit error.
- A multi-page image container such as TIFF: rejected as unsupported rather than silently reduced to
  its first page.
- A password-protected or otherwise encrypted PDF: an explicit error, never a silently empty
  document.
- A structurally corrupt or truncated file, and a zero-byte file.
- A PDF with zero pages.
- A document where only *some* pages carry a text layer — the common "scanned page appended to a
  digital contract" case. Routing is decided for the whole document, so the minority pages yield no
  tokens; the per-page verdict in provenance is what makes that visible instead of silent.
- A PDF whose text layer exists but is unusable in practice: a handful of stray characters, or an
  invisible text layer produced by a poor recognition pass.
- A deployment with the recognition path installed but no native reader: an unforced parse of a PDF
  fails explicitly, because the text-layer question cannot be answered; a forced one succeeds and
  records that the rule never ran.
- A file exceeding the configured size limit, or a page count exceeding the configured limit:
  rejected before any parsing or transmission.
- Text containing ligatures, combining marks, characters outside the Basic Multilingual Plane, or
  right-to-left scripts: positions and geometry stay consistent with the kernel's rules.
- A parser that emits overlapping or out-of-order regions: rejected with an explicit error naming
  the parser and the offending region. Repairing the order is the adapter's responsibility, never
  the ingest layer's — an invalid token set never reaches the kernel, and is never silently sorted
  into a plausible-looking one.
- A page laid out in columns, or carrying a header, footer, or sidebar: the resulting text order is
  whatever the parser's declared reading order produces, and differs legitimately between parsers.
- A parser that supplies geometry for only some tokens: rejected, per the kernel's all-or-nothing
  geometry rule.
- A remote service that returns success with an empty result for a non-empty page.
- A remote call interrupted midway, or credentials that expire during a multi-page parse.
- A service that accepts a request and never responds, or that responds slowly enough that the
  overall deadline expires mid-retry.
- A service that asks for a wait longer than the remaining overall deadline: the parse fails on the
  deadline rather than sleeping past it.
- The same file submitted twice: source identity is recognized as identical without re-reading the
  bytes twice.
- Temporary files written during parsing when the process fails or is interrupted.

## Requirements *(mandatory)*

### Functional Requirements

**Parsing contract**

- **FR-001**: The system MUST turn a source file into a canonical document that satisfies every rule
  the kernel enforces, so that a document from any parser is indistinguishable in shape from a
  document from any other.
- **FR-002**: Every parser MUST accept the source file's bytes together with its declared type, and
  MUST produce exactly one document or raise an explicit error. It MUST NOT return a partial
  document.
- **FR-003**: Parsers MUST declare the capabilities they can supply — at minimum whether they supply
  text, geometry, tables, and handwriting recognition — and that declaration MUST be recorded in the
  produced document.
- **FR-004**: A parser MUST NOT declare a capability it does not supply for the document it just
  produced; a mismatch MUST be detected and raised rather than stored.
- **FR-005**: Geometry from any parser MUST be converted to the kernel's normalized coordinates —
  0 to 1, top-left origin, one page per geometry entry — inside the parser adapter. No source-native
  coordinate system may reach the document.
- **FR-006**: Page rotation and page dimensions MUST be resolved so that geometry describes the page
  as displayed.
- **FR-007**: Canonical text MUST remain byte-faithful to what the parser emitted. The ingest layer
  MUST NOT normalize whitespace, join lines, remove hyphenation, or linearize tables into the
  canonical text.
- **FR-008**: The system MUST support, at minimum, PDF, JPEG, and PNG, and MUST raise an explicit
  unsupported-document error for anything else. Multi-page image containers such as TIFF are out of
  scope at this milestone, because reading one would require the page splitting this feature defers.
- **FR-036**: Each parser MUST emit its tokens in a reading order it documents and declares, and that
  declaration MUST be recorded in provenance so a caller can tell which ordering rule produced a
  given document.
- **FR-037**: The ingest layer MUST validate that a parser's output is in ascending, non-overlapping
  text order and MUST raise an explicit error when it is not. It MUST NOT re-order tokens by
  geometry, infer columns, or perform any other layout analysis to repair the order.

**Path selection and the text-layer decision**

- **FR-009**: The system MUST decide whether a document's native text layer is usable **before**
  choosing a parser, using a single documented, deterministic rule.
- **FR-010**: That decision MUST be reproducible: the same bytes MUST always yield the same verdict,
  and the rule MUST carry a version so that a later change to it is detectable in results produced
  earlier.
- **FR-011**: The decision, its verdict, the evidence behind it, and the rule version MUST be
  recorded in the produced document's provenance, and MUST be readable without re-reading the source
  file. The verdict MUST be recorded **per page as well as for the document as a whole**, so that a
  page which contributes no tokens under the chosen path is explicitly identifiable as such.
- **FR-035**: A page that the assessment judged text-less MUST be distinguishable from a page that
  the parser simply failed to produce tokens for. An empty page is only a normal condition when the
  recorded per-page verdict accounts for it.
- **FR-012**: A caller MUST be able to override the decision and force a specific path; when they do,
  both the override and the verdict it overrode MUST be recorded. When the assessment cannot run at
  all — the native reader is absent — an explicit override MUST still be honoured, and the absence of
  a verdict MUST itself be recorded. This is the only supported way to parse a PDF in a deployment
  that installs the recognition path alone.
- **FR-013**: A document whose text layer is judged usable MUST be routed to the native text path;
  one judged unusable MUST be routed to a parser that declares recognition-backed geometry. Routing
  is decided once for the whole document; per-page verdicts inform provenance, not routing.
- **FR-014**: The system MUST NOT fall back from one parser to another automatically. When the chosen
  path fails, the failure MUST surface; a different path may only be taken because a caller asked for
  it.

**Capability-based selection**

- **FR-015**: Callers MUST be able to request a parser by required capabilities and source type, and
  MUST NOT be required to name a provider anywhere in application code.
- **FR-016**: Selection among several satisfying parsers MUST follow an explicit priority order that
  is part of configuration and readable without running the system. The default order MUST place
  parsers that run offline ahead of parsers that call an external service. Where priority does not
  separate two candidates, the parser id MUST break the tie, so selection is fully deterministic and
  never depends on registration order, dictionary iteration, or process state.
- **FR-017**: When no available parser satisfies a request, the system MUST raise an explicit error
  naming the required capability and the availability of each candidate. It MUST NOT substitute a
  parser that satisfies only part of the request.
- **FR-018**: A parser that is installed but unusable — missing credentials, missing optional
  dependency — MUST be reported as unavailable with the reason, and MUST NOT be silently omitted from
  consideration.

**Provenance, identity, and reproducibility**

- **FR-019**: Every produced document MUST record which parser produced it, that parser's version,
  the options it ran with, the capabilities it declared, and the text-layer verdict.
- **FR-020**: A parser MUST expose a stable identity and a version, and MUST change its version
  whenever its output changes for unchanged inputs.
- **FR-021**: Source identity MUST be derived from the file bytes alone, and document identity from
  source identity together with the parser's identity, version, and options — so two parses of the
  same file are always distinguishable.
- **FR-022**: Options MUST be reduced to identity in a canonical form that is stable across
  processes, platforms, and key ordering.
- **FR-023**: The native text path MUST be deterministic: identical bytes and options MUST produce
  byte-identical documents on every run and platform. The remote path MUST record everything needed
  to explain a result even though the service itself may not be deterministic.
- **FR-024**: A document MUST NOT carry the source file's bytes; it MUST reference them by identity.

**Failure, boundaries, and safety**

- **FR-025**: All failures MUST surface as docdoc's own typed, provider-neutral errors, carrying
  enough detail to identify the offending file and the responsible parser. No provider exception may
  cross the layer boundary.
- **FR-026**: No provider or file-format library type may appear in the produced document or in any
  layer other than the parser's own adapter, and this MUST be enforced automatically rather than by
  convention.
- **FR-027**: Retries MUST be limited to transient network and service failures — timeouts,
  connection failures, rate-limit responses, and server-side errors — and MUST NOT be applied to
  unsupported documents, rejected credentials, or malformed input, which MUST fail on the first
  attempt.
- **FR-038**: The remote path MUST make at most three attempts, spaced by exponential backoff with
  jitter, and MUST honor a wait interval the service asks for in preference to its own backoff. Every
  attempt MUST be bounded by a timeout, and the whole parse MUST be bounded by an overall deadline;
  exceeding either MUST raise an explicit error rather than waiting indefinitely. The attempt limit,
  backoff, timeout, and deadline MUST all be configurable.
- **FR-039**: Retry, timeout, and deadline settings MUST NOT participate in document identity, since
  they cannot change the content of a successful result. They MUST be observable in logs so a slow or
  retried parse is diagnosable after the fact.
- **FR-028**: The system MUST enforce configured limits on file size, page count, and accepted file
  types, and MUST reject an over-limit file before parsing or transmitting it.
- **FR-029**: Document contents, extracted text, and credentials MUST NOT be written to logs. Logs
  may carry identifiers, hashes, counts, and timings only.
- **FR-040**: Every parse, successful or failed, MUST emit one structured log event carrying the
  source identity, the document identity where one was produced, the parser id and version, the
  text-layer verdict, the page count, the duration, the number of attempts, and the outcome. This is
  what makes "why did this document take the recognition path?" answerable in a running system
  without re-parsing the file.
- **FR-030**: Any temporary file created during parsing MUST be removed, including when the parse
  fails or is interrupted.
- **FR-031**: A parse failure MUST leave no partially constructed document and MUST NOT corrupt or
  mutate any document that already exists.

**Packaging and reach**

- **FR-032**: The base installation MUST NOT require any provider SDK or file-format library. Each
  parser's dependencies ship as an optional extra.
- **FR-033**: The full native text path — including the text-layer decision, capability selection,
  and every kernel guarantee — MUST be runnable with no credentials and no network access.
- **FR-034**: Tests that require a remote service MUST be separable from the unit and property suites
  and MUST NOT be required in order to run them.

### Key Entities

- **Parser**: Anything that turns a source file into a document. Carries a stable identity, a
  version, a declaration of what it can supply, and a declared reading order. The native reader and
  the remote service are two instances of the same contract.
- **ParserCapabilities**: What a parser can supply — text, geometry, tables, handwriting — and the
  source types it accepts. The vocabulary callers use instead of provider names.
- **ParserRegistry**: The set of parsers known to a running system, their configured priority order,
  and the deterministic rule that picks one from a capability request. Also knows which of them are
  currently unavailable, and why.
- **TextLayerAssessment**: The verdict on whether a file's native text layer is usable, the evidence
  behind it, the version of the rule that produced it, and whether a caller overrode it. Carries both
  a document-level verdict — the one that decides routing — and a verdict per page, so a text-less
  page inside an otherwise digital document is a recorded fact rather than an absence.
- **ParseOptions**: The knobs a parse ran with. Part of document identity, so two parses differing
  only in options are distinguishable.
- **ParseRequest**: A source file's bytes and declared type, plus required capabilities and options —
  what a caller hands to the ingest layer.
- **IngestProvenance** *(existing, from Milestone 1)*: The record carried by every document of which
  parser produced it, with which version, options, capabilities, and text-layer verdict. This feature
  is what populates it for real files.
- **BlobRef** *(existing, from Milestone 1)*: The reference to the source file — identity, type, and
  size, never bytes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A text-layer PDF is turned into a document, and any of its text ranges resolved to a
  page and a bounding box, with no credentials, no network access, no database, and no object
  storage.
- **SC-002**: For 100% of tokens produced by any parser, geometry falls within 0 to 1 on both axes
  with a top-left origin and names exactly one page; zero coordinates fall outside that range.
- **SC-003**: 100% of produced documents carry the text-layer verdict, the parser identity, the
  parser version, the options, and the declared capabilities, readable without re-reading the source
  file; and the verdict is present for 100% of pages, not only for the document as a whole.
- **SC-004**: The text-layer verdict is identical across repeated assessments of the same bytes in
  100% of cases, and the verdict is correct on 100% of the committed sample set — which MUST include
  at least one digital PDF, one fully scanned PDF, one PDF carrying a near-empty text layer, and one
  image.
- **SC-005**: The native path produces byte-identical documents across repeated runs and across
  platforms in 100% of cases.
- **SC-006**: Identical bytes with an identical parser, version, and options yield identical document
  identity in 100% of cases; any change to parser, version, or options yields a different identity in
  100% of cases.
- **SC-007**: 100% of failures — unsupported type, encrypted file, over-limit file, missing
  capability, missing credential, service failure — surface as a typed docdoc error naming the file
  and the responsible parser; zero produce a silent empty document, a partial document, or an
  automatic switch to a different parser.
- **SC-008**: Zero provider or file-format types appear outside their own adapter, verified by an
  automated boundary check that fails the build.
- **SC-009**: A contributor with no credentials runs 100% of the unit and property suites and the
  entire native text path; only service-backed tests are skipped, and the skip states its reason.
- **SC-010**: Installing the base package pulls in zero provider SDKs and zero file-format libraries.
- **SC-011**: 100% of parser selections in docdoc's own code and documented examples are expressed as
  capability requirements; zero name a provider. Selection returns the same parser in 100% of
  repeated runs, including when the parsers are registered in a different order.
- **SC-012**: A 20-page text-layer PDF is turned into a document in under 5 seconds on a contributor
  laptop with no network access.
- **SC-013**: Zero occurrences of document text, extracted values, or credentials in log output,
  verified by a test over the logs produced while parsing the sample set; and 100% of parses in that
  run — successes and failures alike — emit a structured event carrying identity, parser, verdict,
  page count, duration, attempts, and outcome.
- **SC-014**: Zero temporary files remain after the sample set is parsed, including after induced
  failures.
- **SC-015**: A new contributor parses a real file and resolves a value's location by following a
  single documented example, without reading the implementation.
- **SC-016**: 100% of produced documents record the reading order their parser declared, and 100% of
  out-of-order or overlapping parser output is rejected with an explicit error; zero token sets are
  silently re-ordered by the ingest layer.
- **SC-017**: Zero remote parses exceed the configured overall deadline, and zero make more attempts
  than the configured limit. 100% of retried failures belong to the transient classes; permanent
  failures are retried zero times.
- **SC-018**: Two parses that differ only in retry, timeout, or deadline settings produce the same
  document identity in 100% of cases.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a candidate
for `/speckit-clarify` if it proves wrong in review.

- **Routing is a whole-document decision; the assessment is recorded per page.** A document goes
  entirely to one path, because per-page routing would multiply the identity and provenance model by
  the number of pages and the MVP has no evidence yet that mixed documents are common enough to
  justify it. But the verdict is computed and stored for every page, so a mostly-digital document
  with one scanned page is parsed natively and that page's emptiness is an explicitly recorded
  outcome rather than a silent gap. Merging a natively parsed page with a recognized page into one
  document remains deferred.
- **The usability rule is a documented threshold over extractable text per page**, expressed as a
  minimum quantity of meaningful extractable characters on a minimum proportion of pages, with both
  numbers configurable and carrying a rule version. The exact defaults are a tuning decision to be
  fixed during planning against the committed sample set; the requirement here is that the rule is
  single, deterministic, versioned, and recorded.
- **Assessment reads the file, not the parse.** The verdict is reached from a cheap inspection of the
  source, before a parser runs, so a rejected path costs nothing.
- **One remote service is integrated, and it is optional.** The MVP integrates a single
  geometry-capable document-intelligence service; a second one, and any local recognition engine, are
  out of scope but MUST NOT require a contract change to add later.
- **Reading order belongs to the parser adapter, not to the ingest layer.** Two parsers may
  legitimately produce different text order for the same file; that is already reflected in their
  differing document identities. The ingest layer validates and rejects, it does not repair.
- **Reading a source file is synchronous and in-process.** No queue, no worker, and no background
  execution at this milestone.
- **Bytes are supplied by the caller.** How a file arrives — upload, filesystem, object store — is an
  outer-layer concern; this feature takes bytes and a declared type.
- **Confidence reported by a parser is stored verbatim and not interpreted**, consistent with the
  treatment of model-reported confidence in ADR-0004.
- **Caching of parse results is out of scope here.** This feature produces the identities that a
  cache will later key on, but stores nothing.

## Dependencies

- **Milestone 1 (`001-kernel-document-ir`)** — the canonical Document IR, its construction rules, its
  identity model, and its error types. This feature produces those documents and cannot loosen any
  rule they enforce.
- **Constitution v1.1.1** — Principles IV, V, VIII, X, and XII bind this feature directly.
- **ADR-0001** — fixes the two-parser, no-standalone-OCR strategy and the capability-based selection
  rule this feature implements.
- **ADR-0002** — fixes the identity model that FR-021 and FR-022 express.
- **ADR-0003** — fixes the parser identity and version discipline that FR-020 requires, because a
  parser's version is an input to every downstream artifact identity.
- **A geometry-capable document-intelligence service** — an external dependency for the recognition
  path only. Its unavailability MUST NOT affect the native path.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Any local or self-hosted recognition engine, and any second remote provider.
- Per-page routing, and merging natively parsed pages with recognized pages into one document.
- Extraction, schemas, prompts, model calls, validation, evaluation, and annotations.
- Grounding of any kind, including the match view and approximate matching.
- Normalization of canonical text.
- Artifact storage, caching, and result persistence.
- Queues, workers, background execution, and any HTTP interface or command-line tool.
- Metrics counters, latency histograms, and distributed tracing — deferred to the pipeline milestone;
  this feature emits structured log events only.
- Document classification, page splitting, and page-orientation correction.
- Layout analysis of any kind: column detection, reading-order reconstruction, and header/footer
  identification.
