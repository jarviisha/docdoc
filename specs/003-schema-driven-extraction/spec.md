# Feature Specification: Schema-Driven Extraction

**Feature Branch**: `003-schema-driven-extraction`

**Created**: 2026-08-17

**Status**: Draft

**Input**: User description: "Milestone 3 — schema-driven extraction: một schema có version
(`invoice@1`) khai báo cần lấy field nào, engine đưa document + schema cho một LLM adapter
provider-agnostic và trả về extraction result mang typed value kèm verbatim source text. Không có
code path riêng cho từng loại document. Grounding và validation là milestone sau."

Milestone 3 of the docdoc MVP — the extraction layer that turns a canonical Document from
Milestone 2 into structured, schema-conformant values, as governed by the constitution (v1.2.0) and
ADR-0003, ADR-0004, and ADR-0008.

## Clarifications

### Session 2026-08-17

- Q: How should a document whose text exceeds the model's input budget be handled? → A: The
  extraction fails explicitly, as specified, and the kernel's existing `slice` is the documented
  escape hatch: a caller narrows the document themselves and extracts from the narrowed document.
  Windowing and result merging stay deferred. The kernel already carries what a later milestone needs
  to build them — `slice`, `merge`, and the `origin` ranges that survive both — so nothing here
  forecloses windowing; it only refuses to perform an uninstructed cut.
- Q: Should this milestone resolve the exact-match tier of grounding, given that the kernel's
  exact-only `find()` already exists? → A: No. Every grounding field stays unresolved here. ADR-0003
  makes grounding its own stage with its own artifact and its own `grounding_version` in its options
  hash; resolving it inside extraction would collapse two stages and fold a grounding input into the
  extract artifact's identity. Even the exact tier needs the tie-break rule ADR-0005 specifies for a
  claimed text that appears more than once, and inventing a temporary rule here would change results
  under an unchanged `grounding_version` later.
- Q: Are repeating groups such as invoice line items in scope, and how deeply may they nest? → A: In
  scope, bounded to one level: a repeating group may contain scalars and nested groups but not another
  repeating group. A schema that exceeds the bound is rejected when it is registered, with an error
  naming the limit. Unbounded recursion would run through the schema, the requested response shape,
  the conformance check, and the result type at once, and the bound can be raised later without
  breaking anything that was already accepted.
- Q: How are schemas expressed and loaded? → A: As declarative data files, loaded into the registry
  from locations configuration names. A schema's canonical form for hashing is the canonical
  serialization ADR-0002 already defines and the kernel already implements, so `schema_hash` reuses an
  existing rule rather than inventing one. Adding a document type therefore needs no code change,
  which is Principle VI read literally, and matches the treatment prompts already get.
- Q: Should the spec carry a performance criterion, and over what? → A: Yes, over the deterministic
  work only — schema resolution, request construction, response conformance checking, and identity
  computation — measured against the in-repo adapter with the model call excluded. A target spanning
  the model call would make the check depend on a provider's latency rather than on docdoc's own code.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **developers building on docdoc**: the higher docdoc layers
(grounding, validation, pipeline) and third-party users of the published library. There is no
end-user interface at this milestone. "The system" below means the extraction layer, and "a model"
means whatever component answers a structured request about a document, whether it runs locally or
calls a remote service.

### User Story 1 - Ask a document for the fields a schema declares (Priority: P1)

A developer has a canonical document and a schema that says which fields an invoice carries. They
hand both to docdoc and receive one entry per declared field, each carrying a typed value together
with the verbatim source text the model claims it read that value from.

**Why this priority**: This is the feature. Everything else in this milestone exists to make this
call explainable, swappable, or reproducible. It is also the smallest slice that makes Milestones 1
and 2 pay off: until now a document could be located and searched but never interpreted.

**Independent Test**: Register a small schema, run extraction over a committed document using a
deterministic in-repo adapter that returns a fixed response — no credentials, no network — and
confirm every declared field appears in the result with its value and its claimed source text.

**Acceptance Scenarios**:

1. **Given** a document and a registered schema, **When** the caller asks the system to extract,
   **Then** exactly one result is returned carrying one entry per field the schema declares.
2. **Given** a field the document genuinely does not contain, **When** extraction runs, **Then** that
   field is present in the result and explicitly marked absent — not omitted, not defaulted, and not
   filled with an empty value.
3. **Given** any extracted field, **When** the caller inspects it, **Then** it carries the verbatim
   source text the model claims the value came from, unmodified, alongside the typed value.
4. **Given** a schema declaring a repeating group such as invoice line items, **When** extraction
   runs, **Then** each occurrence is returned as its own set of fields, each with its own value and
   claimed source text.
5. **Given** a model response that is not the requested shape, or that omits a declared field,
   **When** extraction runs, **Then** an explicit extraction error names what was wrong; no field is
   coerced, defaulted, or guessed into place.
6. **Given** a model response carrying a field the schema does not declare, **When** the result is
   built, **Then** the undeclared field is discarded and the fact is recorded — it never appears in
   the result.
7. **Given** a value that parses to its declared type but is semantically implausible — a date far in
   the future, a negative total — **When** extraction runs, **Then** it is returned as extracted.
   Judging acceptability is the validation stage's job, not this one's.
8. **Given** a second document type, **When** a developer adds support for it, **Then** they add
   schema and prompt data only, and no document-type-specific code path exists anywhere in the
   engine.

---

### User Story 2 - Depend on a schema version that means something (Priority: P2)

A developer pins their pipeline to `invoice@1`. Their stored results say `invoice@1` forever, and
the number moves only when the contract they depend on actually breaks — not because a colleague
reworded a field description.

**Why this priority**: The result of every extraction carries a schema reference for the rest of its
life, so this must be right on the first result ever stored, not retrofitted. ADR-0008 fixes the
policy; this story is where the policy becomes something a caller can rely on.

**Independent Test**: Register two majors of one schema, extract against each, and confirm each
result names the exact identity used; then edit a description without bumping and confirm the
version holds while the content hash moves.

**Acceptance Scenarios**:

1. **Given** a registered schema, **When** any result is produced from it, **Then** the result names
   the exact schema identity and the schema's content hash.
2. **Given** an extraction request, **When** it names a schema, **Then** it must name a concrete
   version; there is no way to ask for "the newest one" from library code.
3. **Given** two majors of one schema registered at once, **When** either is requested, **Then** it
   resolves independently of the other, and neither shadows the other.
4. **Given** a request naming an unregistered schema or an unregistered version, **When** resolution
   runs, **Then** an explicit error names the requested identity and the versions that do exist; no
   neighbouring version is substituted.
5. **Given** two schemas that differ only in field ordering or formatting, **When** their content
   hashes are compared, **Then** the hashes are equal.
6. **Given** two schemas that differ in anything that can change a result — a field, a type, a
   constraint, a description — **When** their content hashes are compared, **Then** the hashes
   differ.
7. **Given** a registered version whose content hash changes, **When** the automated check runs,
   **Then** the build fails until the change is either published as a new major or acknowledged as
   non-breaking, so no unbumped contract change reaches a consumer silently.
8. **Given** a caller who has never run an extraction, **When** they inspect the registry, **Then**
   they can list every registered identity and read each schema's fields, types, and descriptions.

---

### User Story 3 - Reach a real model without naming it in application code (Priority: P3)

A developer runs extraction against a real model in production. Their application code never names a
provider, a model family, or a model version — swapping any of them is configuration, and the only
observable difference is in provenance.

**Why this priority**: It completes the milestone and is what makes extraction useful outside a
test, but it depends on Stories 1 and 2 to define the request and the contract, and it is the only
part of this milestone that needs credentials and costs money per call.

**Independent Test**: With credentials configured, extract one committed document against one
schema, then repoint configuration at a different model and confirm the same application code runs
and only the recorded provenance changes.

**Acceptance Scenarios**:

1. **Given** configured credentials, **When** extraction runs against a real model, **Then** a
   result of exactly the shape Story 1 produces is returned, and nothing downstream can tell which
   adapter produced it except by reading provenance.
2. **Given** application code that extracts, **When** the configured model or provider changes,
   **Then** no application code changes.
3. **Given** a transient service failure — a timeout, a rate-limit response, a server-side error —
   **When** the call is attempted, **Then** the system retries within the configured attempt limit
   with backoff and, if it still fails, raises an explicit provider error naming the failure. It does
   not fall back to another model and does not return a partial result.
4. **Given** a permanent failure — a rejected credential, a malformed request, a refusal to answer —
   **When** the call is attempted, **Then** it fails on the first attempt with no retry.
5. **Given** no credentials configured, **When** extraction is requested, **Then** an explicit error
   names the unavailable capability, and no document content is transmitted anywhere.
6. **Given** a service that accepts the request and never answers, **When** the per-attempt timeout
   or the overall deadline passes, **Then** the extraction is abandoned with an explicit error naming
   which bound was exceeded.
7. **Given** a document whose text exceeds the configured input budget, **When** extraction is
   requested, **Then** an explicit error names the document, the budget, and the actual size. The
   document is not truncated, no pages are dropped, and no result is produced from a prefix.
8. **Given** a base installation, **When** its dependencies are inspected, **Then** no provider SDK
   is present, and no provider type appears outside its own adapter.

---

### User Story 4 - Explain any extraction after the fact (Priority: P4)

Six months later, a developer is asked why a stored result says the total was 1,240.00. They can
reconstruct exactly which document, schema version, prompt, model, and settings produced it, and can
tell whether re-running today would use the same inputs.

**Why this priority**: Without it, an extraction result is an assertion with no evidentiary value —
the thing the product exists to avoid. It is last because it records what Stories 1 to 3 do, and can
be tested against any of them.

**Independent Test**: Extract twice against a deterministic adapter with one input changed each time
and confirm the recorded artifact identity changes for every result-affecting input and holds for
every input that cannot change a result.

**Acceptance Scenarios**:

1. **Given** any result, **When** a caller inspects its provenance, **Then** it records the document
   identity, the schema identity and hash, the prompt hash, the model identity and version, the
   decoding settings, the adapter identity and version, the extractor version, and the token usage
   the call consumed.
2. **Given** two extractions differing in any result-affecting input — document, schema, schema
   content, prompt, model, model version, or a decoding setting — **When** their artifact identities
   are compared, **Then** the identities differ.
3. **Given** two extractions differing only in retry, timeout, or deadline settings, **When** their
   artifact identities are compared, **Then** the identities are equal, because neither can change
   the content of a successful result.
4. **Given** the same document already parsed, **When** only the schema changes, **Then** the
   document's own identity is unchanged and is reused, so nothing about the parse is redone.
5. **Given** a document re-extracted under a newer schema version, **When** both results are read,
   **Then** both exist with their own provenance and the older result is unchanged.
6. **Given** any extraction, successful or failed, **When** the log output is inspected, **Then** one
   structured event carries the identities, model, adapter, token usage, duration, attempt count, and
   outcome — and zero document text, extracted values, prompt content, or credentials appear anywhere
   in the logs.
7. **Given** two runs against a real model with identical inputs, **When** the results are compared,
   **Then** they may legitimately differ, and both are fully explainable. The system does not claim
   byte-identical repeatability across model calls.

### Edge Cases

- A model that returns valid structure but every field absent, for a document that plainly contains
  the values: an extraction that found nothing is a legitimate, recorded result, not an error.
- A model that returns a value whose claimed source text does not appear in the document at all — a
  plausible-looking fabrication. Extraction records the claim verbatim and does not attempt to
  verify it; detecting it is precisely what grounding exists for in the next milestone.
- A model that returns a value but no claimed source text, or claimed source text but no value.
- A model that returns the same field twice, or returns a scalar where a repeating group was asked
  for and vice versa.
- Two equally plausible candidates in one document — an invoice total appearing in a summary box and
  again in a footer.
- A document with zero tokens, and a document whose text is whitespace only.
- A schema declaring zero fields, and a schema declaring a repeating group with zero occurrences
  found.
- A schema nesting a repeating group inside another repeating group: rejected when it is registered,
  not when it is first extracted against.
- A schema whose field description is long enough to dominate the request, or which contradicts the
  field's declared type.
- Text containing ligatures, combining marks, characters outside the Basic Multilingual Plane, or
  right-to-left scripts: claimed source text is preserved byte-for-byte, so the next milestone can
  still locate it.
- A document whose text exceeds the input budget by one character, and one that exceeds it by an
  order of magnitude.
- A repeating group large enough that the response, rather than the request, exceeds the model's
  output budget: an explicit error naming the bound, never a silently shortened list.
- A model that stops mid-response, returning a structurally incomplete answer.
- A model that refuses to answer on content grounds: a permanent failure, reported as such, not
  retried.
- A service that answers successfully with an empty body.
- Credentials that expire between attempts of one extraction.
- A service that asks for a wait longer than the remaining overall deadline: the extraction fails on
  the deadline rather than sleeping past it.
- A schema registered twice under one identity, and two schemas whose identities differ only in
  case.
- A registered schema that cannot be loaded at all: it is a broken file, not a version, and must be
  rejected at registration rather than at first use.
- The process failing or being interrupted mid-call, after cost has been incurred but before a
  result exists.

## Requirements *(mandatory)*

### Functional Requirements

**The extraction contract**

- **FR-001**: The system MUST accept a canonical document together with a schema identity and MUST
  produce exactly one extraction result or raise an explicit error. It MUST NOT return a partial
  result.
- **FR-002**: The result MUST carry one entry per field the schema declares, including fields the
  document does not contain, so that an absent field is an explicitly recorded outcome rather than a
  gap in the output.
- **FR-003**: Every extracted field MUST carry the **verbatim source text** the model claims the
  value came from, byte-faithful and unmodified, alongside the typed value. A result that carries
  only an interpreted value cannot be grounded afterwards, so this is a requirement of this feature
  and not of the next one.
- **FR-004**: The system MUST support fields that are scalars, nested groups, and repeating groups. A
  schema that cannot express a repeating group cannot express an invoice, so this is in scope at this
  milestone.
- **FR-048**: Repetition MUST be bounded to one level: a repeating group may contain scalars and
  nested groups, and MUST NOT contain another repeating group. A schema that exceeds the bound MUST be
  rejected when it is registered, with an error naming the limit and the offending field. Raising the
  bound later MUST NOT invalidate a schema that was already accepted.
- **FR-005**: An absent field MUST NOT be an error, MUST NOT be filled with a default, a guess, or an
  empty value, and MUST be distinguishable from a field the model returned as empty.
- **FR-006**: Extraction MUST NOT enforce the schema's field constraints. It checks only that the
  response has the requested shape and that each value parses to its declared type. Whether a
  parseable value is *acceptable* is the validation stage's question, per Principle VII.
- **FR-007**: A model response that is not the requested shape, or that omits a declared field, MUST
  raise an explicit extraction error naming what was wrong. Re-requesting from the model is
  permitted; silently coercing, truncating, or defaulting a response into shape is not.
- **FR-008**: A value returned for a field the schema does not declare MUST be discarded and the
  occurrence recorded. It MUST NOT be merged into the result.
- **FR-009**: Extraction MUST NOT modify the document it read, its canonical text, or its provenance.
- **FR-010**: The engine MUST contain no document-type-specific code path. Supporting a new document
  type MUST require new schema and prompt data only, and this MUST be enforced automatically rather
  than by convention.
- **FR-011**: The response shape the model is asked for MUST be derived from the schema, so that
  conformance can be checked mechanically rather than interpreted from prose.

**Schema identity, versioning, and the registry**

- **FR-012**: Every schema MUST have a stable identity of the form `name@version`, and every result
  MUST reference the exact identity used.
- **FR-013**: Every schema MUST have a content hash derived from a canonical form of the schema. That
  canonical form MUST be the canonical serialization ADR-0002 already defines, not a second convention
  invented for schemas. Schemas differing only in field order or formatting MUST hash identically;
  schemas differing in anything that can change a result — a field, a type, a constraint, a
  description — MUST hash differently.
- **FR-049**: Schemas MUST be expressed as declarative data, not as code, and the registry MUST load
  them from locations that configuration names. Adding or changing a document type MUST NOT require a
  code change.
- **FR-050**: A schema file that is malformed, that declares an unknown type, that names the same
  field twice, or whose identity does not match a registered-identity form MUST be rejected at load
  time with an error naming the file and the defect. Loading MUST NOT partially register a schema.
- **FR-014**: An extraction request MUST name a concrete `name@version`. The library core MUST NOT
  resolve an implicit or newest version. An outer edge MAY offer such a convenience, but MUST resolve
  it to a concrete version before extracting and MUST record the resolved version in the result.
- **FR-015**: Multiple majors of one schema MUST be resolvable concurrently, and neither MUST shadow
  the other.
- **FR-016**: A request naming an unregistered schema or version MUST raise an explicit error naming
  the requested identity and the versions that do exist. No neighbouring version may be substituted.
- **FR-017**: The version-bump rules of ADR-0008 MUST be enforced by an automated check that fails
  the build when a registered version's content hash moves, so that an unbumped contract change
  cannot reach a consumer silently. Classification of the change remains a human review obligation;
  the check exists to guarantee the classification is made.
- **FR-018**: A schema MUST be inspectable without running an extraction: a caller can list every
  registered identity and read each schema's fields, types, and descriptions.
- **FR-019**: A schema that cannot be loaded MUST be rejected when it is registered, not when it is
  first used.
- **FR-020**: Prompts MUST be data keyed to a schema identity, never code, and their content MUST
  carry a hash that is recorded in every result they produced.

**The model adapter**

- **FR-021**: Callers MUST NOT name a provider, model family, or model version anywhere in
  application code. Which model answers a request is configuration.
- **FR-022**: The adapter contract MUST be satisfiable by more than one provider. The MVP integrates
  exactly one, and adding a second MUST NOT require a change to the contract.
- **FR-023**: No provider SDK type, exception, or field name may appear outside its own adapter, and
  this MUST be enforced automatically rather than by convention.
- **FR-024**: The base installation MUST NOT require any provider SDK. Each adapter's dependencies
  ship as an optional extra.
- **FR-025**: Retries MUST be limited to transient network and service failures — timeouts,
  connection failures, rate-limit responses, and server-side errors. Rejected credentials, malformed
  requests, content refusals, and every schema, grounding, or validation error MUST fail on the first
  attempt.
- **FR-026**: The system MUST make at most a configurable number of attempts, defaulting to three,
  spaced by exponential backoff with jitter, and MUST honour a wait interval the service asks for in
  preference to its own backoff. Every attempt MUST be bounded by a timeout and the whole extraction
  by an overall deadline; exceeding either MUST raise an explicit error naming which bound was
  exceeded.
- **FR-027**: Retry, timeout, and deadline settings MUST NOT participate in extraction artifact
  identity, since they cannot change the content of a successful result. They MUST be observable in
  logs so that a slow or retried extraction is diagnosable after the fact.
- **FR-028**: When the configured model is unavailable — missing credentials, missing optional
  dependency — the system MUST raise an explicit error naming the unavailable capability and the
  reason, and MUST NOT be silently omitted from consideration.
- **FR-029**: The system MUST NOT fall back to a different model, provider, or schema version when
  the configured one fails. A different one may be used only because a caller asked for it.
- **FR-030**: When a document's text exceeds the configured input budget, or a response would exceed
  the configured output budget, the system MUST raise an explicit error naming the document, the
  bound, and the actual size. It MUST NOT truncate the document, drop pages, extract from a prefix,
  or shorten a repeating group.
- **FR-046**: The error raised by FR-030 MUST name narrowing the document as the supported way
  forward, and the documentation MUST show it. A caller narrows a document with the kernel's existing
  `slice`, extracts from the result, and receives a result whose provenance names the narrowed
  document — so the record states exactly what was read rather than implying the whole document was.
  The extraction layer itself MUST NOT choose a cut on the caller's behalf.

**Confidence and the grounding boundary**

- **FR-031**: Where a model self-reports confidence, it MUST be stored verbatim in a field documented
  as untrusted, MUST NOT be blended with any other signal, and MUST NOT influence any routing or
  acceptance decision, per ADR-0004.
- **FR-032**: The grounding fields ADR-0004 requires MUST be present in the result shape and MUST be
  left unresolved by this feature. Extraction MUST NOT set a grounding status it did not compute, and
  MUST NOT report a value as grounded.
- **FR-047**: Extraction MUST NOT resolve any tier of grounding, including the exact tier the kernel's
  existing search could satisfy cheaply. Grounding is a separate stage under ADR-0003 with its own
  artifact and its own version inputs, and no grounding input — `grounding_version`,
  `match_view_version`, or a match threshold — may appear in the extract stage's options hash.

**Provenance, identity, and reproducibility**

- **FR-033**: Every result MUST record the document identity, the schema identity and content hash,
  the prompt hash, the model identity and version, the decoding settings, the adapter identity and
  version, the extractor version, and the token usage the call consumed.
- **FR-034**: Every result MUST carry a content-addressed artifact identity derived per ADR-0003 from
  the document's artifact identity together with the extractor's identity, version, and options hash.
  The options hash MUST fold the schema identity, the schema content hash, the prompt hash, the model
  identity and version, the decoding settings, and the requested response shape.
- **FR-035**: Any change to a result-affecting input MUST change the artifact identity; a change to
  retry, timeout, or deadline settings MUST NOT.
- **FR-036**: The extractor MUST expose a stable identity and version, and MUST change its version
  whenever its output changes for unchanged inputs.
- **FR-037**: The system MUST NOT claim byte-identical results across repeated model calls, because
  the model is a probabilistic edge under Principle III. It MUST instead record everything needed to
  explain any single result.
- **FR-038**: Re-extraction MUST produce a new result with its own provenance and MUST NOT mutate,
  overwrite, or reinterpret a prior result.

**Safety, observability, and failure**

- **FR-039**: Document text, extracted values, claimed source text, prompt content, and credentials
  MUST NOT be written to logs. Logs may carry identifiers, hashes, counts, timings, and token counts
  only.
- **FR-040**: Every extraction, successful or failed, MUST emit one structured log event carrying the
  document identity, the schema identity, the artifact identity where one was produced, the model and
  adapter identities and versions, the token usage, the duration, the attempt count, and the outcome.
- **FR-041**: No document content may be transmitted to an external service until the request has
  been fully validated — schema resolved, credentials present, input budget satisfied — so that a
  request which was always going to fail never sends the document anywhere.
- **FR-042**: All failures MUST surface as docdoc's own typed, provider-neutral errors carrying enough
  detail to identify the document, the schema, and the responsible adapter. No provider exception may
  cross the layer boundary.
- **FR-043**: A failed extraction MUST leave no partial result and MUST NOT mutate any result that
  already exists.

**Contributor reach**

- **FR-044**: Every part of the extraction path except the model call itself MUST be runnable with no
  credentials and no network access, against a deterministic in-repo adapter that returns fixed
  responses.
- **FR-045**: Tests that require a real provider MUST be separable from the unit and property suites
  and MUST NOT be required in order to run them.

### Key Entities

- **Schema**: A versioned declaration of the fields a document type carries — identity
  (`name@version`), content hash, and field specifications. Authored as declarative data, never as
  code. The only place document-type knowledge lives.
- **FieldSpec**: One declared field — name, declared type, cardinality (scalar, group, or repeating
  group), constraints, and the description that tells a model what to look for. Repetition is bounded
  to one level. Constraints are declared here and enforced by the validation stage, not by extraction.
- **SchemaRegistry**: The set of schemas known to a running system, keyed by identity, holding
  multiple majors of one name concurrently. Loads schema data from locations configuration names,
  rejects a schema that cannot be loaded, and resolves only concrete versions.
- **PromptTemplate**: The instruction data keyed to a schema identity, carrying a hash that is
  recorded in every result it produced. Data, never code.
- **ModelAdapter**: Anything that answers a structured request about a document. Carries a stable
  identity and version, declares the model it reached and that model's version, and reports token
  usage. The remote provider and the deterministic in-repo adapter are two instances of one contract.
- **DecodingOptions**: The model settings a call ran with — the ones that can change a result.
  Part of extraction identity, unlike retry, timeout, and deadline settings.
- **ExtractionRequest**: A document, a concrete schema identity, and the options a call runs with —
  what a caller hands to the extraction layer.
- **ExtractedValue**: One field's outcome — the typed value or an explicit absence, the verbatim
  claimed source text, the untrusted model-reported confidence, and the grounding fields this feature
  leaves unresolved.
- **ExtractionResult**: One entry per declared field, plus the extraction's provenance and its
  content-addressed artifact identity.
- **ExtractionProvenance**: Document identity, schema identity and hash, prompt hash, model and
  adapter identities and versions, decoding settings, extractor version, and token usage.
- **Document** *(existing, from Milestone 1)*: The canonical IR this feature reads and never modifies.
- **IngestProvenance** *(existing, from Milestone 1)*: How the document was produced. Its identity is
  the input to this stage's artifact identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A committed document is extracted against a registered schema with no credentials, no
  network access, no database, and no object storage, using the deterministic adapter.
- **SC-002**: 100% of declared fields appear in every result, including absent ones; zero declared
  fields are silently omitted, and zero undeclared fields appear.
- **SC-003**: 100% of extracted values carry verbatim claimed source text preserved byte-for-byte;
  zero carry a normalized, trimmed, or re-cased substitute.
- **SC-004**: 100% of results name the exact schema identity and content hash used, readable without
  re-running the extraction.
- **SC-005**: Two schemas differing only in field order or formatting hash identically in 100% of
  cases; two schemas differing in any field, type, constraint, or description hash differently in
  100% of cases.
- **SC-006**: Zero extraction requests in docdoc's own code, tests, and documented examples resolve a
  schema version implicitly; 100% name a concrete `name@version`.
- **SC-007**: A registered version whose content hash moves without an acknowledged classification
  fails the build in 100% of cases.
- **SC-008**: Two majors of one schema are registered and extracted against in the same process, and
  100% of the resulting artifact identities differ.
- **SC-009**: Any change to document, schema, schema content, prompt, model, model version, or a
  decoding setting changes the artifact identity in 100% of cases; a change to retry, timeout, or
  deadline settings changes it in 0% of cases.
- **SC-010**: Changing only the schema reuses the existing parse in 100% of cases; zero re-parses
  and zero repeated ingest-provider calls are triggered by an extraction-only change.
- **SC-011**: 100% of results record document identity, schema identity and hash, prompt hash, model
  identity and version, decoding settings, adapter identity and version, extractor version, and token
  usage.
- **SC-012**: 100% of failures — unregistered schema, malformed schema file, schema exceeding the
  one-level repetition bound, malformed model response, missing credential, over-budget document,
  service failure, exceeded deadline — surface as a typed docdoc error naming the document, the
  schema, and the responsible adapter; zero produce a partial result, a partially registered schema, a
  silently truncated document, a shortened repeating group, or an automatic switch to another model.
  The over-budget error names narrowing the document as the way forward in 100% of cases.
- **SC-013**: Zero provider SDK types appear outside their own adapter, verified by an automated
  boundary check that fails the build; and installing the base package pulls in zero provider SDKs.
- **SC-014**: Zero document-type-specific code paths exist in the engine, verified by an automated
  check; a second document type is added in the test suite by adding schema and prompt data only,
  with zero engine changes.
- **SC-015**: Zero occurrences of document text, extracted values, claimed source text, prompt
  content, or credentials in log output, verified by a test over the logs produced while extracting
  the sample set; and 100% of extractions in that run — successes and failures alike — emit one
  structured event carrying identities, model, adapter, token usage, duration, attempts, and outcome.
- **SC-016**: Zero bytes of document content are transmitted for a request that fails schema
  resolution, credential availability, or the input budget check, verified against a transport that
  records every call attempt.
- **SC-017**: Zero extractions exceed the configured overall deadline, and zero make more attempts
  than the configured limit; 100% of retried failures belong to the transient classes, and permanent
  failures are retried zero times.
- **SC-018**: 100% of extracted values leave the grounding fields unresolved; zero report a grounding
  status this milestone did not compute.
- **SC-019**: A contributor with no credentials runs 100% of the unit and property suites and the
  entire extraction path except the model call itself; only provider-backed tests are skipped, and
  each skip states its reason.
- **SC-020**: A new contributor extracts fields from a real document by following a single documented
  example, without reading the implementation.
- **SC-021**: The deterministic work of one extraction — resolving the schema, constructing the
  request, checking the response's conformance, and computing the artifact identity — completes in
  under 100 ms for a 20-page document against a 20-field schema, measured against the in-repo adapter
  with the model call excluded. Model latency is a property of the provider and is recorded per
  extraction rather than bounded here.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a candidate
for `/speckit-clarify` if it proves wrong in review.

- **Extraction returns claimed text, not spans.** The model is asked for the verbatim source text
  behind each value; resolving that text to positions and geometry in the document is the grounding
  milestone's job. Extraction therefore never reports a value as located, and never verifies that the
  claimed text exists in the document — not even by exact search, which the kernel could already do.
  The reason is structural rather than a matter of effort: ADR-0003 makes grounding its own stage with
  its own artifact and its own version inputs, so resolving it here would fold a grounding input into
  the extract artifact's identity and collapse two stages into one. The consequence to accept is that
  when this milestone ships, every extracted value is still ungrounded, so the product's central claim
  is not demonstrable end to end until Milestone 4.
- **Which schema applies is the caller's decision.** Document classification — inferring that a file
  is an invoice — is out of scope. The caller names the schema.
- **One window per extraction, and narrowing is the caller's decision.** The whole document's text is
  presented in a single request. When it does not fit the configured budget, the extraction fails
  explicitly rather than being windowed, chunked, or truncated, and the caller narrows the document
  with the kernel's existing `slice` before extracting again. Windowed extraction remains a real
  requirement for long documents, but it needs a chunk-boundary and result-merging design — which
  field wins when two windows disagree, how a repeating group is concatenated — that would roughly
  double this milestone's scope, and a wrong merge rule produces a result that looks complete.
  Deferring it costs nothing structurally: Milestone 1 already built `slice`, `merge`, and the
  `origin` ranges that survive both, precisely so that windowed extraction stays possible later.
  Because `slice` preserves original page numbers and geometry byte-identically, a value extracted
  from a narrowed document can still be grounded back to its true page, so the escape hatch costs no
  provenance.
- **Constraints are declared in the schema and enforced by the validator.** This feature stores them
  as part of the schema — and therefore in its content hash, per ADR-0008 — but checks only shape and
  type parseability. Milestone 5 enforces them.
- **A deterministic in-repo adapter is part of the deliverable, not a test fixture.** Without it, no
  contributor could run the extraction suite and every test would cost money and vary run to run. It
  satisfies the same adapter contract as the real one.
- **Model self-reported confidence is requested and stored, never used.** Consistent with ADR-0004,
  it is recorded as an untrusted field so a later calibrator can be fitted against it, and it
  influences nothing in this milestone.
- **Decoding settings default to the most repeatable configuration the adapter offers**, and are
  recorded either way. This reduces variance between runs but is not a determinism guarantee, and no
  requirement here depends on one.
- **The artifact identity is computed, not stored.** This feature derives the extraction artifact id
  per ADR-0003 and exposes it, but persists nothing and caches nothing. The artifact store is the
  pipeline milestone's concern.
- **Extraction is synchronous and in-process.** No queue, no worker, no background execution and no
  batching across documents at this milestone.
- **Token usage is reported when the adapter can report it**, and its absence is a normal condition
  for an adapter that has no notion of tokens, not an error.
- **One document, one schema, one result per call.** Extracting several schemas from one document is
  several calls; the results are independent and separately identified.

## Dependencies

- **Milestone 1 (`001-kernel-document-ir`)** — the canonical Document IR and its identity model. This
  feature reads documents and never modifies them, and inherits their identity as the input to its own
  artifact identity.
- **Milestone 2 (`002-ingest-parser-layer`)** — supplies real documents to extract from, and the
  provider-agnostic adapter pattern this feature repeats for models. Its existing typed errors,
  including the provider-error concept it already defines, MUST be reused rather than duplicated
  under a second incompatible name.
- **Constitution v1.2.0** — Principles III, IV, VI, VII, VIII, X, and XII bind this feature directly.
- **ADR-0003** — fixes the per-stage artifact chain that FR-034 and FR-035 express, including which
  inputs the extract stage folds into its options hash.
- **ADR-0004** — fixes the separation of trusted grounding fields from untrusted model-reported
  confidence, and forbids a blended score.
- **ADR-0008** — fixes schema identity, the version-bump rules, concurrent majors, and the absence of
  implicit version resolution in the core.
- **ADR-0005 and ADR-0006** — not implemented here, but they constrain this feature: grounding will
  resolve the claimed source text FR-003 records, so that text must survive byte-faithfully or the
  next milestone cannot do its job.
- **One LLM provider** — an external dependency for the real path only. Its unavailability MUST NOT
  affect the deterministic path or any test that does not require it.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Grounding of any kind: exact matching, approximate matching, the comparison-time match view, span
  resolution, and any verification that a claimed value appears in the document.
- Validation of any kind: field-level rules, cross-field rules such as `sum(line_items) == total`,
  and any judgment about whether an extracted value is acceptable.
- Confidence calibration, and any blended or derived confidence score.
- Document classification — deciding which schema applies to a document.
- Windowed, chunked, or paginated extraction, and merging results across windows.
- A second model adapter, a local or self-hosted model, model fine-tuning, and prompt
  auto-optimization.
- Artifact storage, caching, result persistence, and any database.
- Evaluation, golden datasets, accuracy measurement, and human correction — Milestone 6.
- Queues, workers, background execution, batching across documents, and any HTTP interface or
  command-line tool.
- Tooling that *generates* or edits schema data — authoring UIs, generators, inference of a schema
  from example documents. Hand-authored schema data is in scope; producing it for the author is not.
- Repeating groups nested inside repeating groups, schema migration of stored results, and any
  deprecation lifecycle for a retired schema version.
- Metrics counters, latency histograms, and distributed tracing — deferred to the pipeline milestone;
  this feature emits structured log events only.
