# Phase 1 Data Model: Deterministic Grounding

**Feature**: `004-deterministic-grounding` | **Date**: 2026-08-18 | **Plan**: [plan.md](plan.md)

Every type below traces to a spec requirement. Invariants are numbered `GRD-n` and are what the test
suite binds to; the plan's test tree maps each file to the invariants it holds.

Types are `pydantic` models, frozen and `extra="forbid"`, matching the convention Milestones 1–3 set.
`MatchView` and `OffsetMap` are the exception and are plain classes — see §2.

---

## 1. `GroundingStatus`

The closed vocabulary of outcomes. A string enum with exactly three members:

| Value | Meaning |
|---|---|
| `exact` | The claim was found verbatim in the match view |
| `fuzzy` | The claim was found within the similarity threshold |
| `ungrounded` | No candidate cleared the threshold, or there was nothing to search for |

- **GRD-1**: The vocabulary is closed at three. Ambiguity is expressed through `alternatives`, never
  through a fourth member (FR-003). Adding one is a constitutional amendment, not an implementation
  detail — ADR-0005 says so explicitly.

Note what `exact` does **not** mean. After ADR-0006 it means "found verbatim modulo documented, versioned
cosmetic folding" — not byte-identical in the raw source. The distinction is carried in the enum's own
docstring rather than only here, because a reader who assumes the stronger meaning would be misled and a
docstring travels where a design document does not.

---

## 2. `MatchView` and `OffsetMap`

The derived comparison form of a document's text, and the map that carries positions back.

```text
MatchView
    text          str        the folded text, for comparison only
    offsets       OffsetMap
    version       str        "v1"
    document_id   str
    view_id       str        sha256 over (document_id, version)
```

```text
OffsetMap
    segments      tuple[Segment, ...]      ordered, non-overlapping
Segment
    view_start    int
    source_start  int
    length        int        view characters this segment covers
    source_length int        source characters it consumed
    identity      bool       whether the view copied it through character for character
```

Plain classes rather than `pydantic` models: `MatchView.text` can be tens of thousands of characters and
these are constructed once per run in a hot path, so validation on construction buys nothing and costs
measurably. They are also never serialised — FR-013 forbids exposing the view at all.

- **GRD-2**: The transformations, in order, are NFKC → soft-hyphen removal → de-hyphenation → whitespace
  collapsing (FR-014, research.md R5). Ligature expansion and non-breaking-space folding are effects of
  NFKC and are **not** separate steps.
- **GRD-3**: De-hyphenation removes the hyphen only when the character before it and the first
  non-whitespace character after the line break are both lowercase letters; otherwise the line break is
  removed and the hyphen kept (research.md R7).
- **GRD-4**: Whitespace collapsing reduces every run of whitespace to a single space (U+0020).
- **GRD-5**: The view is derived once per grounding run and reused across every value (FR-019).
- **GRD-6**: `document_id` is recorded on the view, and `view_id` is `sha256` over `document_id` and
  `version` (FR-020).
- **GRD-7**: Segments are ordered by `view_start`, non-overlapping, and cover every view position — the
  map is **total** in the view→source direction (FR-015).
- **GRD-8**: The mapping is monotonic non-decreasing: for view positions `a <= b`, the mapped source
  positions satisfy `map(a) <= map(b)` (FR-015).
- **GRD-9**: Mapping a view range to a source range maps each boundary **outward** to the containing
  source region. A round trip returns the original range where both boundaries survive the
  transformations, and a containing range otherwise. It may widen a range; it must never narrow or move
  one (FR-017, research.md R10).

- **GRD-9a**: `identity` is **recorded, never inferred**. A position inside a non-identity segment has no
  source position of its own and MUST map outward; a position at a segment *boundary* maps to the near
  edge in both directions, because widening must contain what matched rather than reach past it.

GRD-9 is the invariant this feature most needs to hold. Its failure mode is not an exception — it is a
grounded-looking value pointing at the wrong place, which ADR-0006 identifies as the highest risk in the
grounding path.

**GRD-9a exists because both of its clauses were bugs first, and Hypothesis found both.** Inferring
identity from `length == source_length` is wrong for a ligature carrying a combining mark: `ﬁ` + U+0301
folds to `fí`, two view characters from two source characters, equal lengths and emphatically not a
character-wise mapping — mapping by addition landed on the combining mark alone and returned a range that
had lost half the match. And treating a boundary position like an interior one over-widened in the other
direction: a claim ending where a collapsed whitespace run begins came back with the newline attached.
Both are narrowing or misplacing failures of exactly the kind this module exists to prevent, and neither
raised anything.

---

## 3. `Candidate`

An internal, transient scoring record. Never leaves the matching module and never appears in a result.

```text
Candidate
    view_start  int
    view_end    int
    score       float    0.0 .. 1.0
```

- **GRD-10**: For a claim of length `m` and threshold `t`, the candidate set is generated by the
  pigeonhole filter over `k + 1` disjoint blocks, where `k = floor((1 - t) · m / t)`. Window starts range
  over `±k` of each block occurrence's implied start; window lengths range over `m - k … m + k`
  (research.md R3, R4).
- **GRD-11**: The filter is **complete**: if a window's similarity to the claim is at or above `t`, that
  window is in the candidate set. No true match can be missed. This is provable rather than empirical —
  `k` edits can damage at most `k` of `k + 1` disjoint blocks, so one survives verbatim.
- **GRD-11a**: The pigeonhole argument requires `k + 1` **non-empty** blocks, which do not exist when the
  edit budget reaches the claim's length — around `t = 0.5` and below. There the filter MUST fall back to
  scanning every position rather than degrade, so completeness holds at **every** threshold. Returning a
  single block containing the whole claim looks like a reasonable degradation and silently requires a
  *verbatim* match, which is the opposite of what lowering the threshold asks for. Cost is bounded by
  `candidate_budget` exactly as on the filtered path.

`k = 0` for claims of nine characters or fewer, which degenerates the filter to exact search. That is the
filter agreeing with the threshold, not a special case: a single edit in a nine-character string scores
0.889 and does not clear 0.90.

GRD-11a was a shipped bug before it was an invariant. The first implementation degraded rather than
falling back, so at low thresholds "ungrounded" quietly meant "did not match verbatim". A test written to
check something else — that the threshold is not a post-filter — found it.

---

## 4. `Alternative`

A runner-up, retained so ambiguity is visible rather than silently resolved.

```text
Alternative
    span    Span      source-text range
    score   float
```

- **GRD-12**: At most five alternatives are retained, ordered by the same total rule as the winner
  (FR-025, ADR-0005).
- **GRD-13**: Where the claim matched exactly in several places, the remaining exact occurrences are the
  alternatives. They are not discarded, and they are not treated as a lesser tier — all carry score 1.0.
- **GRD-13a**: Within one repeating group at one field path, no two entries resolve to the same source
  range. Assignment is greedy in entry index order; an entry with no remaining occurrence is
  `ungrounded`. The constraint is scoped to the group — two distinct field paths may and must share a
  range when they legitimately read the same text (FR-029, research.md R16). Exclusion applies to the
  winner only; an alternative may still name a range another entry won.

Pages and boxes are **not** stored on an alternative and are resolved on demand from `span` (research.md
R12). An alternative's `span` is what makes it meaningful; most are never inspected.

---

## 5. `GroundingOutcome`

One value's result. This is what fills the fields Milestone 3 left unresolved.

```text
GroundingOutcome
    field_path      str
    status          GroundingStatus
    score           float | None
    span            Span | None
    pages           tuple[int, ...]
    geometry        tuple[Geometry, ...] | None
    alternatives    tuple[Alternative, ...]
    truncated       bool
```

- **GRD-14**: `score` is exactly `1.0` for `exact`, the measured similarity for `fuzzy`, and `None` for
  `ungrounded` (FR-030). The exact tier's `1.0` is assigned structurally, **not** produced by running the
  scorer — the two tiers' scores are incomparable under ADR-0004, and deriving one from the other's
  scorer would quietly make them the same quantity (research.md R13).
- **GRD-15**: `span` is `None` if and only if `status` is `ungrounded`. There is no representation of a
  grounded value without a range, and none of an ungrounded value with one (FR-023, FR-034).
- **GRD-16**: `span` is always a range into `Document.text`. No view position may appear here, in
  `alternatives`, in a log, or in an error message (FR-016).
- **GRD-17**: `geometry` is `None` when the producing parser supplied no geometry, and an empty tuple
  when geometry exists but the range covers no tokens. The two are different facts and must not collapse:
  `None` means unavailable, `()` means nothing there (FR-006).
- **GRD-18**: `truncated` is `True` when the candidate budget was reached. The outcome is still resolved
  from the candidates examined; the flag is how the cap stops being silent (research.md R8).

### The two absences, which are not the same thing

The distinction that most invites a bug:

| Milestone 3 input | Grounding outcome | In the rate denominator? |
|---|---|---|
| `present=False` (the model reported the field absent) | **No outcome is produced at all** | No (FR-008) |
| `present=True`, `claimed_text=None` | `ungrounded` | Yes (FR-009) |
| `present=True`, `claimed_text=""` | `ungrounded` | Yes (FR-026) |

A correctly reported absence is not a grounding failure and must not depress the metric. A value asserted
without evidence is a grounding failure and must. Collapsing the two would make the grounding rate depend
on how many fields a schema happens to declare.

---

## 6. `GroundingOptions`

The settings that can change an outcome, and therefore participate in identity.

```text
GroundingOptions
    threshold          float = 0.90
    candidate_budget   int   = 1_500
```

- **GRD-19**: `threshold` is folded into the options hash. Changing it on a call changes the artifact
  identity; changing its **default** requires a `grounding_version` bump, because the default silently
  changes every result produced without an explicit setting (FR-042).
- **GRD-19a**: The default `candidate_budget` is **derived from SC-020, not chosen**:
  `500 ms ÷ 20 values ÷ 72 candidate starts per ms ≈ 1,800`, rounded down to 1,500 for headroom on a CI
  runner slower than the machine that produced the measurement. This is what makes SC-020 hold by
  construction rather than by hope. An earlier draft used 20,000, which at the measured rate is ~278 ms
  for a *single* value — one value could consume 56% of the budget for twenty, and the adversarial case
  that motivated the cap (9,998 starts) would never have reached it. A backstop that never fires on the
  input it exists for is decoration.

`threshold` changes which candidates are *generated*, not only which are accepted — `k` derives from it
(GRD-10). This is why it cannot be treated as a post-filter.

`candidate_budget` is folded too, because truncation can change an outcome. This is the one place where
the "settings that cannot change a result stay out of identity" rule cuts the other way from Milestone
3's transport settings, and the reason is that a budget is not a timeout: reaching it changes the answer.

---

## 7. `GroundingProvenance` and `GroundingResult`

```text
GroundingProvenance
    document_id             str
    extraction_artifact_id  str
    grounding_version       str    "v1"
    match_view_version      str    "v1"
    options                 GroundingOptions
    grounder_id             str
    grounder_version        str
```

```text
GroundingResult
    outcomes        dict[str, GroundingOutcome]     keyed by field path
    counts          GroundingCounts
    provenance      GroundingProvenance
    artifact_id     str
```

```text
GroundingCounts
    exact         int
    fuzzy         int
    ungrounded    int
    not_applicable int      values the model reported absent
    truncated     int
```

- **GRD-20**: `artifact_id = sha256(extraction_artifact_id + grounder_id + grounder_version +
  options_hash)`, per ADR-0003. The options hash folds `grounding_version`, `match_view_version`, and
  `GroundingOptions` — and nothing else (FR-036, FR-037).

`counts` exists so the grounding rate is computable without walking the outcomes (FR-035). It carries
`not_applicable` separately precisely so the denominator question of §5 has one answer in the data rather
than a convention each consumer reinvents.

---

## 8. Error model

One new type, extending the kernel's `DocdocError` root the way Milestones 2 and 3 did.

```text
GroundingError(DocdocError)
    document_id             str | None
    extraction_document_id  str | None
    field_path              str | None
```

`GroundingError` is named in the constitution's error model and has had no implementation until now.

- Raised when an extraction result is grounded against a document it did not come from, naming both
  identities (FR-002).
- Raised when the offset map's invariants are violated at runtime — a defensive check, because a broken
  map must fail loudly rather than return a plausible wrong range.
- **Never** raised for an ungrounded value (FR-045). That is an outcome, not an error.
- **Never** retried (FR-044). There is no transient failure mode in a deterministic offline computation,
  which is why this error type carries no `transient` flag — unlike `ProviderError`, whose flag exists
  because a network has one.

`CapabilityError` from the kernel is **absorbed**, not propagated: `locate()` raises it when a parser
supplied no geometry, and grounding turns that into `geometry=None` (GRD-17) rather than failing a value
that is perfectly well grounded without a box.

---

## 9. What this feature does not add

Recorded because their absence is a decision:

- No `GroundedValue` type wrapping `ExtractedValue`. The outcome is keyed by field path and joined by the
  caller. Wrapping would mean copying every extracted value to attach a range, which mutates nothing but
  duplicates everything, and would make `ExtractionResult` and its grounded twin two things a consumer
  has to keep in step.
- No calibrated confidence and no calibrator version. ADR-0004 reserves them; they stay `None` (FR-033).
- No annotation or correction type. A wrong location is a real thing to record and Milestone 6 owns it.
- No cache, no store, no persisted view. The identity is computed and exposed; storing it is the pipeline
  milestone's concern (research.md R9).
