# Feature Specification: Deterministic Grounding

**Feature Branch**: `004-deterministic-grounding`

**Created**: 2026-08-18

**Status**: Implemented

**Input**: User description: "Milestone 4 — grounding: mỗi giá trị đã trích từ Milestone 3 mang một
`claimed_text` do model tự khai; milestone này biến nó thành vị trí thật trong tài liệu — span, page,
bbox — bằng code deterministic của docdoc, exact trước rồi fuzzy, không tìm được thì ungrounded công
khai. Matching chạy trên một match view có version với offset map, `Document.text` giữ nguyên
byte-faithful. Validation là milestone sau."

Milestone 4 of the docdoc MVP — the stage that makes the product's central claim demonstrable end to
end. Milestone 3 shipped deliberately ungrounded: every extracted value carries the verbatim text a
model *claims* it read the value from, and nothing more. This feature resolves that claim to a
position in the source, or states plainly that it could not, as governed by the constitution (v1.2.0)
and ADR-0002, ADR-0003, ADR-0004, ADR-0005, and ADR-0006.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **developers building on docdoc**: the higher docdoc layers
(validation, pipeline, evaluation) and third-party users of the published library. There is no
end-user interface at this milestone. "The system" below means the grounding stage. "A claim" means
the verbatim source text an extracted value carries from Milestone 3; "a location" means a character
range in the document's canonical text together with the page and bounding boxes it occupies.

### User Story 1 - Point at where a value came from (Priority: P1)

A developer has an extraction result saying the invoice total is 1,240.00. They ask docdoc where that
came from and get back a character range in the document's text, the page it sits on, and the
bounding boxes that cover it — computed by docdoc, not asserted by the model.

**Why this priority**: This is the feature, and it is the reason the project exists. Milestones 1 to 3
built a document that can be located, searched, and interpreted; none of them can answer "where did
this number come from?". It is also the smallest slice that stands alone: exact resolution with
everything else honestly ungrounded is already a useful, auditable product.

**Independent Test**: Take a committed document and a committed extraction result whose claims appear
verbatim in the source, run grounding with no network and no credentials, and confirm every value
resolves to the hand-verified character range, page, and boxes.

**Acceptance Scenarios**:

1. **Given** an extraction result and the document it was extracted from, **When** the caller asks the
   system to ground it, **Then** exactly one grounding result is returned carrying one grounding
   outcome per extracted value.
2. **Given** a value whose claim appears in the document, **When** grounding runs, **Then** the value
   carries a character range into the document's canonical text, the page numbers that range occupies,
   and the bounding boxes covering it.
3. **Given** any grounded value, **When** the caller reads its range, **Then** the range addresses the
   document's own canonical text — the text a caller can read, index, and slice — and never an
   internal derived form.
4. **Given** a document produced by a parser that supplied no geometry, **When** a value is grounded,
   **Then** it is still grounded and still carries a range and a page, and the absence of boxes is
   reported as unavailable rather than as an empty result.
5. **Given** a value the model marked absent, **When** grounding runs, **Then** there is nothing to
   resolve, and the value is not reported as ungrounded — an absence the model correctly reported is
   not a grounding failure.
6. **Given** any grounding run, **When** it finishes, **Then** the document it read is unchanged — its
   canonical text, its provenance, and its identity — and the extraction result it read is unchanged.
7. **Given** a claim that appears more than once in the document, **When** grounding runs, **Then** one
   occurrence is chosen by a rule that is the same on every run, and the others are recorded as
   alternatives rather than discarded.
8. **Given** a grounding run, **When** it executes, **Then** it needs no network, no credentials, and
   no provider, and it never re-asks a model anything.

---

### User Story 2 - Find values the page spells differently (Priority: P2)

The same developer's document renders "Invoice" with an *fi* ligature, breaks "1,240.00" across a
hyphenated line, and pads its table cells with non-breaking spaces. The model quoted what a human
reads. Grounding still finds it, and still reports a range into the untouched source.

**Why this priority**: Without it the exact tier fails for purely cosmetic reasons on a large share of
real PDFs, and the grounding rate that Principle IX measures would describe the typesetting rather
than the extraction. It is P2 rather than P1 because P1 delivers a working, honest product on its own;
this raises how much of it lands.

**Independent Test**: Run grounding over committed documents that contain ligatures, soft hyphens,
line-broken hyphenation, non-breaking spaces, and irregular whitespace, and confirm claims that a
reader would call verbatim resolve, with ranges that address the original source text unchanged.

**Acceptance Scenarios**:

1. **Given** a claim that differs from the source only by a ligature, a soft hyphen, a hyphen at a line
   break, a non-breaking space, or whitespace run length, **When** grounding runs, **Then** it resolves,
   and the range returned addresses the original source text with its ligatures and hyphens intact.
2. **Given** the document's canonical text, **When** it is read before and after any grounding run,
   **Then** it is byte-identical — no normalization, no line joining, no hyphen removal, and no
   whitespace collapsing has been applied to it.
3. **Given** a claim that does not match even after cosmetic differences are set aside — a transposed
   digit, a dropped word, a re-spaced amount — **When** grounding runs, **Then** an approximate match
   above the configured similarity threshold resolves it and the value is marked as an approximate
   match with its similarity score.
4. **Given** a claim whose best candidate falls below the threshold, **When** grounding runs, **Then**
   the value is explicitly ungrounded with no range, no score, and no box — not attached to the nearest
   thing available.
5. **Given** several candidates above the threshold, **When** grounding runs, **Then** one wins by a
   rule that always produces a single answer, and the runners-up are recorded up to a documented limit.
6. **Given** the same claim and the same document, **When** grounding runs repeatedly and on different
   machines, **Then** the winning range, the score, and the list of runners-up are identical every time.

---

### User Story 3 - Never be misled by a grounded-looking value (Priority: P3)

A developer builds an approval step on top of docdoc. They need certainty that a value presented as
located really was located, that an approximate match is never quietly presented as an exact one, and
that nothing the model said about its own certainty has crept into the decision.

**Why this priority**: A wrong location is worse than no location — it is an audit trail that points
confidently at the wrong place. This story is what separates grounding from a plausible guess. It is
P3 because it constrains the behaviour Stories 1 and 2 produce rather than adding new reach.

**Independent Test**: Ground a committed set containing values that resolve exactly, values that
resolve approximately, and values that cannot resolve at all, then confirm each of the three is
mechanically distinguishable in the result, in the summary counts, and in the emitted log event.

**Acceptance Scenarios**:

1. **Given** any grounded value, **When** a consumer inspects it, **Then** whether it resolved exactly,
   resolved approximately, or did not resolve is readable mechanically, without parsing prose and
   without inferring it from the presence of a range.
2. **Given** an ungrounded value, **When** it passes through any layer of the system, **Then** it stays
   distinguishable from a grounded one; there is no representation in which it can be mistaken for
   located.
3. **Given** an exactly resolved value and an approximately resolved one, **When** their scores are
   read, **Then** the difference in what the two scores mean is documented wherever the score is
   exposed, and no part of the system ranks one against the other.
4. **Given** a value carrying the model's own self-reported confidence, **When** grounding runs, **Then**
   that number is passed through untouched, influences no decision this feature makes, and is still
   labelled untrusted.
5. **Given** an extraction result that was produced from a different document, **When** a caller asks to
   ground it against this one, **Then** the request is refused with an explicit error naming both
   identities; ranges from one parse are never resolved against another.
6. **Given** a value that is present but carries no claim at all, **When** grounding runs, **Then** it is
   ungrounded — a value with no stated evidence is not located, and this is a normal recorded outcome
   rather than an error.
7. **Given** a claim that resolves to a range, **When** grounding finishes, **Then** the system makes no
   judgment about whether the value follows from the text at that range; deciding that is the
   validation stage's job.
8. **Given** a grounding run over a whole result, **When** it finishes, **Then** the counts of exactly
   resolved, approximately resolved, and unresolved values are readable from the result, so the
   grounding rate can be computed without running anything again.

---

### User Story 4 - Reproduce and explain a grounding six months later (Priority: P4)

A stored result says a total was found at a particular place on page 3. A developer needs to know
whether re-running today would look in the same way, and what would have to change for the answer to
move.

**Why this priority**: A location whose derivation cannot be reconstructed has the same evidentiary
value as no location. It is last because it records what Stories 1 to 3 do and can be tested against
any of them.

**Independent Test**: Ground the same inputs twice and confirm the recorded identity is unchanged; then
change the similarity threshold and confirm it moves, and confirm that changing something which cannot
affect an outcome leaves it alone.

**Acceptance Scenarios**:

1. **Given** any grounding result, **When** its provenance is read, **Then** it records the document
   identity, the extraction it grounded, the version of the matching algorithm, the version of the
   derived comparison form, the similarity threshold used, and the grounder's own identity and version.
2. **Given** two grounding runs differing in the algorithm version, the comparison-form version, or the
   threshold, **When** their identities are compared, **Then** the identities differ.
3. **Given** two grounding runs over the same extraction with all of those unchanged, **When** their
   identities are compared, **Then** the identities are equal, and the outcomes are identical.
4. **Given** an extraction that is re-grounded, **When** both results are read, **Then** both exist with
   their own provenance and the earlier one is unmodified.
5. **Given** a change to the matching algorithm, the candidate generator, the scorer, the tie-break rule,
   or the default threshold, **When** it is proposed, **Then** it requires an algorithm version bump, and
   an automated check fails the build if the version did not move.
6. **Given** any grounding run, successful or refused, **When** the log output is inspected, **Then** one
   structured event carries the identities, the versions, the per-outcome counts, the duration, and the
   result — and zero document text, claim text, extracted values, or derived comparison text appear
   anywhere in the logs.

### Edge Cases

- A claim that is the empty string, or is only whitespace: nothing to search for, so an explicitly
  ungrounded value rather than a match at every position or an error.
- A claim longer than the document's entire text, and a claim exactly as long as it.
- A claim that appears hundreds of times — a currency symbol, a repeated table header — where the
  runners-up list is far shorter than the candidate set.
- Two candidates with identical scores, identical start positions, and identical lengths: the tie-break
  must still produce one answer rather than depending on iteration order.
- A claim that resolves across a page boundary, and one that resolves inside a table cell.
- A claim whose winning range begins or ends inside a character the comparison form removed — a soft
  hyphen, a collapsed whitespace run — where the range returned must still be a real range in the
  source.
- A claim that resolves to a range covering no tokens at all, because the parser tokenized around it.
- A value whose claim resolves but whose page cannot be determined, and a document whose pages do not
  cover the whole text.
- Text containing combining marks, characters outside the Basic Multilingual Plane, right-to-left
  scripts, or mixed scripts within one claim, where a naive character count and a real position
  disagree.
- A document whose text is empty, and one that is whitespace only.
- An extraction result with zero values, and one where every value is absent.
- A repeating group where two occurrences carry byte-identical claims — two line items with the same
  description — which must resolve to different places rather than both to the first.
- A repeating group with three entries whose claims are identical but where the text occurs only twice:
  two resolve and the third is ungrounded, because there is no third place for it to have come from.
- A repeating group whose entries the model returned out of document order, so entry order and
  occurrence order disagree.
- A claim that a model fabricated entirely and that appears nowhere: the ungrounded outcome this
  feature exists to produce, and never an error.
- A claim that appears in the document only inside a header or footer repeated on every page.
- A document large enough that scanning it once per value is the dominant cost.
- An extraction result grounded against a document that parsed from the same bytes with different
  parser options — same source file, different parse, different identity.
- The similarity threshold configured to 0.0 or to 1.0, both of which are legal and both of which
  change every outcome.

## Requirements *(mandatory)*

### Functional Requirements

**The grounding contract**

- **FR-001**: The system MUST accept an extraction result together with the document it was extracted
  from, and MUST produce exactly one grounding result or raise an explicit error. It MUST NOT return a
  partially grounded result.
- **FR-002**: The system MUST refuse to ground an extraction result against any document other than the
  one it was extracted from, with an explicit error naming both identities. Ranges anchor to a specific
  parse under ADR-0002, so resolving a claim against a different parse would produce a location that is
  syntactically valid and semantically wrong.
- **FR-003**: Every extracted value that carries a claim MUST receive exactly one of three outcomes:
  resolved exactly, resolved approximately, or ungrounded. No fourth state may be introduced; ambiguity
  is expressed through alternatives, not through a new status.
- **FR-004**: Grounding MUST be computed by docdoc from the document and the claim alone. No model may
  contribute to, confirm, or veto a grounding outcome, and no grounding input may originate from a
  model's self-assessment.
- **FR-005**: Every grounded value MUST carry a character range into the document's canonical text, the
  page numbers that range occupies, and the bounding boxes covering it where the parser supplied
  geometry.
- **FR-006**: Where the producing parser supplied no geometry, the value MUST still be grounded and MUST
  still carry its range and pages, and the unavailability of boxes MUST be reported as unavailable —
  never as an empty set of boxes, which a caller could read as "nothing is there".
- **FR-007**: Grounding MUST NOT modify the document, its canonical text, its provenance, its identity,
  or the extraction result it read. It produces a new result.
- **FR-008**: A value the model reported as absent has no claim to resolve. It MUST NOT be reported as
  ungrounded and MUST be excluded from the denominator of any grounding rate, so that a correctly
  reported absence cannot depress a quality metric.
- **FR-009**: A value that is present but carries no claim MUST be ungrounded. It has no stated evidence,
  and this is a normal recorded outcome, not an error.
- **FR-010**: Grounding MUST NOT judge whether the extracted value follows from the text at the resolved
  range, whether the value is plausible, or whether it satisfies any schema constraint. That is the
  validation stage's question under Principle VII.
- **FR-011**: Grounding MUST NOT alter, trim, re-case, or re-encode the claim it was given. The only
  transformations permitted are the versioned ones of the comparison form, applied for comparison only.

**The comparison form and its offset map**

> **On the two names.** This specification says *comparison form* because it describes the thing by what
> it is for. ADR-0006, and every design and implementation artifact downstream of this one, calls the same
> thing the **match view** — that is its canonical name. They denote one concept, and a reader mapping
> FR-012 … FR-020 onto the implementation should expect to find them under the second name.

- **FR-012**: Matching MUST run against a derived comparison form of the document, never against the raw
  canonical text alone and never against a normalized canonical text.
- **FR-013**: The document's canonical text MUST remain byte-faithful source text. The comparison form
  MUST NOT be exposed as a document, MUST NOT be persisted as canonical text, and MUST NOT be handed to
  consumers as the document's text.
- **FR-014**: The transformations the comparison form applies MUST be pinned under a version identifier
  and MUST comprise, per ADR-0006: compatibility normalization, ligature expansion, soft-hyphen removal,
  de-hyphenation across line breaks, non-breaking-space folding, and whitespace collapsing. Adding,
  removing, or altering any transformation REQUIRES a version bump.
- **FR-015**: The comparison form MUST carry an explicit offset map, because the transformations change
  length and a position therefore cannot be computed arithmetically. Every position in the comparison
  form MUST map to exactly one position in the source text, and the mapping MUST be monotonic
  non-decreasing.
- **FR-016**: Every range this feature returns — for a winning match and for every alternative alike —
  MUST be a range into the source text. No position in the comparison form may escape into a result, a
  log, an error message, or a public interface.
- **FR-017**: Mapping a source range into the comparison form and back MUST return the original range
  whenever both of its boundaries survive the transformations. Where a boundary falls inside a region the
  transformations removed or merged, the mapped range MUST be the smallest source range containing the
  original, so a round trip may widen a range but MUST NEVER narrow one or move it.
- **FR-018**: The same transformations MUST be applied to the claim before it is compared, so that both
  sides of every comparison are in the same form. Folding only the document would leave a claim
  containing a non-breaking space unable to match a document from which that space was folded away.
- **FR-019**: The comparison form MUST be derived once per document per grounding run and reused across
  every value, so that cost scales with the document rather than with the product of document size and
  value count.
- **FR-020**: The comparison form MUST have an identity derived from the document identity and the
  comparison-form version, so that two runs can establish they compared against the same thing.

**The matching algorithm**

- **FR-021**: The system MUST attempt an exact match first. Any exact hit MUST yield the "resolved
  exactly" outcome with a score of 1.0.
- **FR-022**: Where no exact match exists, the system MUST generate candidate ranges sized to the claim's
  length within a pinned, documented slack, and MUST score each on a scale of 0.0 to 1.0 with a pinned
  similarity measure. Where the configured threshold is low enough that the slack reaches the claim's own
  length, the sizing rule no longer bounds anything and the system MUST consider every position rather
  than narrow its search — a lower threshold asks for a broader search, never a narrower one.
- **FR-023**: A best candidate scoring at or above the configured threshold MUST yield the "resolved
  approximately" outcome carrying that score. A best candidate below the threshold MUST yield the
  ungrounded outcome with no range and no score.
- **FR-024**: The winner MUST be selected by a **total** ordering applied in this order: highest score,
  then earliest start position, then shortest range. Because the ordering is total, a single winner
  exists for any candidate set, and no outcome may depend on iteration order, hash order, or platform.
- **FR-025**: Up to a documented limit of runners-up at or above the threshold MUST be recorded as
  alternatives, ordered by the same rule. Where a claim matched exactly in several places, the remaining
  exact occurrences MUST be recorded as alternatives rather than discarded.
- **FR-026**: An empty or whitespace-only claim MUST NOT be searched for and MUST yield the ungrounded
  outcome. It would otherwise match everywhere, which would mean nothing.
- **FR-027**: The candidate generator, the slack, the similarity measure, the threshold's default value,
  and the tie-break rule MUST all be pinned under a single algorithm version. Changing any of them
  REQUIRES a version bump, enforced by an automated check rather than by review discipline alone.
- **FR-028**: For a fixed claim, document, comparison-form version, algorithm version, and threshold, the
  winning range, the score, and the ordered list of alternatives MUST be identical on every run and on
  every platform.
- **FR-029**: Where several occurrences of one repeating group carry identical claims, each occurrence
  MUST resolve to a different source range, in entry order, rather than all collapsing onto the first.
  Uniqueness is scoped to one repeating group at one field path and MUST NOT be applied globally: two
  different fields that legitimately read the same text — an invoice date serving as both issue date and
  due date — MUST both resolve to that shared range. Where a claim occurs fewer times than the entries
  claiming it, the surplus entries MUST be ungrounded rather than assigned a range already taken.

**Score semantics and honest reporting**

- **FR-030**: The score MUST be 1.0 for an exact resolution, the measured similarity for an approximate
  one, and absent for an ungrounded value. It MUST NOT be blended with any other signal.
- **FR-031**: Scores from the exact and approximate tiers MUST NOT be compared with one another, and this
  MUST be documented wherever the score is exposed. Nothing in the system may rank values across tiers by
  score.
- **FR-032**: The model's self-reported confidence MUST be passed through untouched, MUST NOT influence
  any grounding decision, and MUST remain labelled untrusted wherever it is exposed, per ADR-0004.
- **FR-033**: The reserved calibrated-confidence and calibrator-version fields MUST remain unset. No
  blended or derived score may be produced by this feature.
- **FR-034**: An ungrounded value MUST remain machine-distinguishable from a grounded one at every layer
  that carries it. Emitting an ungrounded value in a form indistinguishable from a grounded one is a
  constitutional violation, not a presentation choice.
- **FR-035**: The grounding result MUST expose the count of values in each outcome, so that the grounding
  rate Principle IX requires can be computed without re-running anything.

**Identity, provenance, and reproducibility**

- **FR-036**: Grounding MUST be its own stage in the ADR-0003 chain, with its artifact identity derived
  from the extraction artifact's identity together with the grounder's identity, version, and options
  hash.
- **FR-037**: The options hash MUST fold the algorithm version, the comparison-form version, and the
  similarity threshold, and MUST NOT fold anything that cannot change an outcome.
- **FR-038**: Every grounding result MUST record the document identity, the extraction artifact it
  grounded, the algorithm version, the comparison-form version, the threshold used, and the grounder's
  identity and version.
- **FR-039**: Any change to a result-affecting input MUST change the artifact identity; a change to a
  setting that cannot affect an outcome MUST NOT.
- **FR-040**: The grounder MUST expose a stable identity and version and MUST change its version whenever
  its output changes for unchanged inputs.
- **FR-041**: Re-grounding MUST produce a new result with its own provenance and MUST NOT mutate,
  overwrite, or reinterpret a prior result.
- **FR-042**: The similarity threshold MUST be a documented, configurable option with a default of 0.90.
  Changing the value on a call changes the artifact identity; changing the **default** requires an
  algorithm version bump, because it silently changes every result produced without an explicit setting.

**Errors, safety, and observability**

- **FR-043**: All failures MUST surface as docdoc's own typed, provider-neutral grounding errors carrying
  enough detail to identify the document, the extraction, and the value at fault.
- **FR-044**: Grounding errors MUST NOT be retried, per the constitution's error model. There is no
  transient failure mode in a deterministic, offline computation.
- **FR-045**: An ungrounded value MUST NOT raise an error, MUST NOT fail the run, and MUST NOT be treated
  as a defect in the extraction it came from. It is the outcome this stage exists to be able to state.
- **FR-046**: Document text, claim text, extracted values, and comparison-form text MUST NOT be written to
  logs. Logs may carry identifiers, hashes, versions, counts, scores, and timings only.
- **FR-047**: Every grounding run, successful or refused, MUST emit one structured event carrying the
  document identity, the extraction artifact identity, the grounding artifact identity where one was
  produced, the algorithm and comparison-form versions, the per-outcome counts, the duration, and the
  outcome.
- **FR-048**: Grounding MUST require no network access, no credentials, and no provider, and MUST NOT
  contact a model for any reason. Every part of this feature MUST be runnable by a contributor with
  neither credentials nor connectivity.

### Key Entities

- **MatchView**: The derived, versioned comparison form of a document's text — the transformations of
  ADR-0006 applied for matching only. Carries its own version and an identity derived from the document
  it was built from. Never a document, never persisted as canonical text, never exposed to consumers.
- **OffsetMap**: The explicit correspondence between positions in the match view and positions in the
  source text. Monotonic and total in the view-to-source direction. The highest-risk component in this
  feature: an incorrect map produces confidently wrong boxes rather than visible failures.
- **GroundingOutcome**: One value's result — the status (exact, approximate, ungrounded), the score, the
  source range where one was found, the pages it occupies, and the boxes covering it where geometry
  exists.
- **Alternative**: A runner-up range at or above the threshold, with its score, retained so that ambiguity
  is visible rather than silently resolved. Bounded to a documented count.
- **GroundingOptions**: The settings a run used that can change its outcome — principally the similarity
  threshold. Part of the stage's identity, unlike anything that cannot change an outcome.
- **GroundingResult**: One outcome per extracted value, the per-outcome counts, the stage's provenance,
  and its content-addressed artifact identity.
- **GroundingProvenance**: Document identity, the extraction artifact grounded, algorithm version,
  comparison-form version, threshold, and the grounder's identity and version.
- **Grounder**: The processor for this stage in the ADR-0003 chain, with a stable identity and a version
  that moves whenever output moves for fixed inputs.
- **Document** *(existing, Milestone 1)*: The canonical IR this feature reads and never modifies. Supplies
  the canonical text, the exact search, the page mapping, and the geometry lookup this feature builds on.
- **ExtractedValue** *(existing, Milestone 3)*: Supplies the claim to resolve and the grounding fields
  Milestone 3 deliberately left unresolved. This feature is what resolves them.
- **ExtractionResult** *(existing, Milestone 3)*: The input artifact of this stage, and the source of the
  document identity this feature checks against.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A committed document and a committed extraction result are grounded with no credentials, no
  network access, no database, and no object storage.
- **SC-002**: 100% of values carrying a claim receive exactly one of the three outcomes; zero are left
  unresolved, zero carry a fourth status, and zero are silently omitted from the result.
- **SC-003**: On a committed sample set with hand-verified expected locations, 100% of values reported as
  grounded point at the correct character range; zero point at a different occurrence, a neighbouring
  range, or a position derived from the comparison form.
- **SC-004**: 100% of returned ranges are valid ranges in the document's canonical text; zero comparison-form
  positions appear in any result, log, or error message.
- **SC-005**: The document's canonical text is byte-identical before and after grounding in 100% of runs,
  and 100% of ranges resolve against text that still contains its original ligatures, soft hyphens, and
  whitespace.
- **SC-006**: Mapping a source range through the comparison form and back returns the original range in
  100% of cases where both boundaries survive the transformations, and returns a containing range in 100%
  of the remaining cases; zero round trips narrow a range or move it.
- **SC-007**: On a committed set of documents containing ligatures, soft hyphens, line-broken hyphenation,
  and non-breaking spaces, the share of claims resolving at the exact tier is measurably higher with the
  comparison form than without it, and the increase is reported rather than asserted.
- **SC-008**: Repeating grounding of identical inputs produces byte-identical winning ranges, scores, and
  alternative lists in 100% of runs and across every supported platform; zero outcomes vary with
  iteration order or hash order.
- **SC-009**: For every candidate set that contains a tie in score, a tie in start position, and a tie in
  length, exactly one winner is produced in 100% of cases.
- **SC-010**: 100% of claims whose best candidate falls below the threshold are reported ungrounded; zero
  are attached to the nearest available range.
- **SC-011**: Values the model reported absent contribute 0% to the ungrounded count and 0% to the
  grounding-rate denominator.
- **SC-012**: Occurrences of one repeating group carrying identical claims resolve to distinct ranges in
  100% of cases, in entry order; and two distinct fields legitimately reading one shared range both
  resolve to it in 100% of cases — the uniqueness rule fires zero times outside a repeating group.
- **SC-013**: 100% of grounding results record the document identity, the extraction artifact grounded,
  the algorithm version, the comparison-form version, the threshold used, and the grounder identity and
  version, readable without re-running the grounding.
- **SC-014**: Changing the algorithm version, the comparison-form version, or the threshold changes the
  artifact identity in 100% of cases; changing anything that cannot affect an outcome changes it in 0% of
  cases.
- **SC-015**: A change to the candidate generator, the similarity measure, the tie-break rule, the slack,
  or the default threshold without an algorithm version bump fails the build in 100% of cases.
- **SC-016**: 100% of ungrounded values are mechanically distinguishable from grounded ones in the result,
  in the summary counts, and in the emitted log event; zero representations exist in which the two are
  indistinguishable.
- **SC-017**: The model's self-reported confidence influences 0% of grounding outcomes, verified by
  grounding one committed set twice with that value altered and confirming the outcomes are identical.
- **SC-018**: An extraction result grounded against a document it did not come from is refused in 100% of
  cases with an error naming both identities; zero produce a location.
- **SC-019**: Zero occurrences of document text, claim text, extracted values, or comparison-form text
  appear in log output, verified over the logs produced while grounding the sample set; and 100% of runs
  emit one structured event carrying identities, versions, per-outcome counts, duration, and outcome.
- **SC-020**: Grounding a 20-page document against a 20-value extraction result completes in under 100 ms
  when every claim resolves at the exact tier, and in under 500 ms when every claim falls through to the
  approximate tier, with the comparison form derived once per run. The second bound holds for **any**
  input, including a pathological one, because the per-value candidate budget is derived from it; a value
  that reaches the budget is resolved from the candidates examined and reports that it was truncated.
- **SC-021**: A contributor with no credentials and no network access runs 100% of this feature's tests
  and 100% of its documented examples; zero are skipped for want of a provider.
- **SC-022**: A new contributor takes an extracted value to a page and bounding box by following a single
  documented example, without reading the implementation.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a candidate for
`/speckit-clarify` if it proves wrong in review.

- **Grounding grounds an extraction result, not arbitrary text.** The input is one extraction result and
  the document it came from, because ADR-0003 makes the extraction artifact this stage's input. A
  general "find this string" facility already exists in the kernel and is not what this feature exposes.
- **One contiguous source range per value.** The algorithm selects a single winning range; runners-up go
  to alternatives. A value assembled from disjoint places in the document — a total stated once in a
  table and again in words — is out of scope, and the alternatives list is not a substitute for it. A
  claim that the comparison form de-hyphenated still resolves to one contiguous source range, because the
  removed characters lie inside it.
- **The 0.90 threshold is an estimate, not a measured optimum.** ADR-0005 says so explicitly. This feature
  builds the machinery and makes the threshold configurable and identity-bearing; tuning it against a
  golden set is Milestone 6's work, and the tuning will bump the algorithm version.
- **The comparison form is derived in-process and not persisted.** ADR-0006 describes it as a
  content-addressed artifact, and this feature computes and exposes that identity — but persisting it is
  the artifact store's job, which Milestone 3 already deferred to the pipeline milestone. Deriving it once
  per run satisfies every requirement here.
- **Boxes are token-granular and may be wider than the value.** The kernel resolves a range to the boxes
  of the tokens it intersects, with no sub-token interpolation, because deriving a partial box from a
  character offset assumes uniform glyph advance and is wrong for proportional fonts and complex scripts.
  A box that is slightly too wide is coarse; a box derived by interpolation is wrong.
- **Pages come from the text, boxes come from geometry.** Pages tile the canonical text exactly, so a
  grounded value always has a page even when the parser supplied no geometry. This is why FR-006 can
  insist a value stay grounded without boxes.
- **An exact match means exact in the comparison form.** After ADR-0006, "resolved exactly" means found
  verbatim modulo documented, versioned, cosmetic folding — not byte-identical in the raw source. The
  distinction is documented wherever the status is exposed, because a reader who assumes the stronger
  meaning would be misled.
- **The three outcomes are the whole vocabulary.** Ambiguity, low token coverage, and a match inside a
  repeated header are all reported through the range, the score, and the alternatives — not through new
  statuses. If evaluation later shows this hides real ambiguity, adding a state is a constitutional
  amendment, not an implementation detail.
- **Grounding is synchronous, in-process, and offline.** No queue, no worker, no batching across
  documents, and no network of any kind. It is deterministic code over data already in memory.
- **The stage computes its artifact identity but stores nothing.** Consistent with Milestone 3.
- **Grounding never re-asks the model.** A claim that does not resolve is reported as not resolving. Asking
  the model to try again would make the outcome depend on a probabilistic edge, which Principle III
  forbids for this stage.
- **Routing is not built here.** Principle IX requires that confidence eventually drive automatic-versus-review
  routing. This feature produces the trusted signal that routing will read; it makes no routing decision
  and defines no policy.

## Dependencies

- **Milestone 1 (`001-kernel-document-ir`)** — supplies the canonical text, the half-open range model, the
  exact-only search, the page mapping that works without geometry, and the range-to-geometry resolution
  this feature builds on. Its identity model is why FR-002 exists.
- **Milestone 2 (`002-ingest-parser-layer`)** — supplies real documents with real geometry, and the
  parsers whose typesetting artifacts are the reason ADR-0006 exists.
- **Milestone 3 (`003-schema-driven-extraction`)** — supplies the extraction result, the byte-faithful
  claims this feature resolves, and the unresolved grounding fields it fills. Its typed errors and its
  provenance conventions MUST be reused rather than duplicated under a second incompatible name.
- **Constitution v1.2.0** — Principles I, II, III, VIII, IX, X, XI, and XII bind this feature directly.
  Principle II is the one it exists to satisfy.
- **ADR-0002** — fixes the two-level identity that makes FR-002's refusal necessary, and the canonical
  serialization the options hash reuses.
- **ADR-0003** — fixes grounding as its own stage, its position in the chain, and exactly which inputs its
  options hash folds.
- **ADR-0004** — fixes the separation of the trusted grounding fields this feature computes from the
  untrusted model self-report it must not read.
- **ADR-0005** — fixes the algorithm, the threshold, the tie-break, the alternatives limit, and the
  requirement that the whole thing carry a version.
- **ADR-0006** — fixes the comparison form, its transformations, its offset map, and the rule that returned
  ranges are always source-text ranges.
- **An approximate string-matching dependency in the extraction layer** — a small, pure-wheel library, not
  a provider SDK. It enters the base install for anyone using extraction, which ADR-0005 accepted
  explicitly.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Validation of any kind: field-level rules, cross-field rules, and any judgment about whether a value at
  a resolved location is acceptable — Milestone 5.
- Confidence calibration, any blended or derived score, and any automatic-versus-review routing policy.
- Tuning the similarity threshold, the golden dataset, grounding-rate targets, accuracy measurement, and
  regression evaluation — Milestone 6. This feature makes the rate computable; it does not set a target
  for it.
- The full EditMap that would normalize the canonical text itself for all consumers. Only the
  matching-scoped comparison form is in scope.
- Disjoint or multi-range grounding of a single value, and grounding a value to a region rather than to a
  range.
- Semantic, embedding-based, or model-assisted matching of any kind; asking a model to confirm, re-quote,
  or re-locate anything.
- Table-structure-aware or reading-order-aware matching beyond what the parser already recorded.
- Persistence, caching, and storage of the comparison form or of grounding results, and any database.
- Human correction of a wrong location, correction storage, and any review interface — the annotation model
  is Milestone 6's.
- Queues, workers, background execution, batching across documents, and any HTTP interface or command-line
  tool.
- Metrics counters, latency histograms, and distributed tracing — this feature emits structured log events
  only.
- Re-extraction, re-parsing, or any change to how a document or an extraction was produced.
