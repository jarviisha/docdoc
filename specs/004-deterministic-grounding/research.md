# Phase 0 Research: Deterministic Grounding

**Feature**: `004-deterministic-grounding` | **Date**: 2026-08-18 | **Plan**: [plan.md](plan.md)

Every decision below was reached by running the code, not by recalling how these libraries behave. The
measurements are reproducible with `uv run pytest -m perf` once the feature lands; the exploratory
numbers quoted here came from the probes recorded in each entry.

Sixteen decisions. R3, R4, R7, and R16 are the ones that would change the feature's behaviour if
reversed. R16 was added during implementation, when `/speckit-analyze` found FR-029 had a requirement
and no design.

---

## R1 — Where the grounding code lives

**Decision**: A new top-level package, `src/docdoc/grounding/`, added to the `import-linter` layers
contract **above** `docdoc.extraction`.

**Rationale**: ADR-0005 says fuzzy matching lives in `extraction/grounding.py`. That sentence sits under
the heading "**Placement**" in an ADR titled *Fuzzy Grounding Lives Outside the Kernel*, and the decision
it records is the one the title states: the kernel may not host a fuzzy matcher without breaking its
`pydantic`-only rule. The specific module path is the illustration, not the ruling.

Three facts make the illustration the wrong shape now:

1. **Grounding consumes extraction's output.** It reads an `ExtractionResult`. Inside
   `docdoc.extraction`, that dependency is invisible to the layers contract — `import-linter` cannot
   express "grounding may import extraction but not the reverse" when both are the same layer. As its
   own layer, the direction is machine-checked, which Principle X requires ("layer discipline is
   machine-checked").
2. **ADR-0003 already names it a separate stage** in the chain `parse → extraction → grounding →
   validation`, with its own processor id, version, and options hash. A stage with its own artifact
   identity that lives inside another stage's package will be mistaken for part of it.
3. **It is not one module.** The match view, the offset map, the candidate filter, the scorer, the
   identity derivation, the error type, and the log event are seven concerns. `extraction/grounding.py`
   was written when grounding looked like one function call.

Principle X's chain (`API → Pipeline → Extraction → Transform → Ingest → Kernel`) does not name
grounding. It also does not name it as belonging to any listed layer. ADR-0003's chain is finer-grained
and consistent with it, and is what the layers contract will encode.

**Alternatives considered**: `extraction/grounding/` as a sub-package — rejected because it leaves the
dependency direction unenforceable and puts a distinct ADR-0003 stage inside another one.
`transform/grounding/` — ADR-0006 says "the match view lives in the transform/grounding layer", but
`transform` sits *below* extraction in Principle X's order while grounding must sit above it, so the
match view and the resolver would be split across two packages on opposite sides of the layer that
separates them. Rejected under Principle XI: two packages where one has a present-tense reason to exist.

**Consequence to record**: this is a plan that departs from an accepted ADR's literal text. It is carried
into plan.md's design-decisions section so a reviewer sees it there rather than in a diff, and it should
be folded into ADR-0005 as a clarifying amendment if accepted.

---

## R2 — What `rapidfuzz` is used for, and what it is not used for

**Decision**: `rapidfuzz` is used for exactly one thing — `Levenshtein.normalized_similarity(a, b,
score_cutoff=t)`, a pure function from two strings to a number. Candidate generation, winner selection,
tie-breaking, and alternative ordering are all docdoc's own code.

**Rationale**: ADR-0005 requires the tie-break rule to be ours, total, and versioned. Two `rapidfuzz`
APIs looked like they could do more of the work, and both were rejected on inspection:

- **`fuzz.partial_ratio_alignment`** returns the single best alignment of a needle inside a haystack,
  in C++, in 0.22 ms over a 50,000-character view. It is tempting and it is wrong for this feature. It
  returns *one* alignment, so it cannot populate the alternatives list ADR-0005 requires. Worse, when
  several alignments tie, which one it returns is an undocumented internal. A probe over a view
  containing three identical occurrences returned the earliest — which happens to agree with our
  tie-break — but nothing in the library promises that, and a minor-version change that altered it would
  silently change every grounding result while `grounding_version` stayed at `v1`. That is precisely the
  failure ADR-0005's versioning rule exists to prevent.
- **`process.cdist`** would score every window in one C++ call. It raises `ModuleNotFoundError: No
  module named 'numpy'` — it requires numpy, which is not in the constitution's sanctioned stack and is
  not a dependency this feature can justify adding.

Using the library as a scorer only keeps the determinism claim testable against docdoc's own code.

**Alternatives considered**: implementing normalized Levenshtein in pure Python — rejected, it is
100–1000× slower and it is the one part of this feature where a compiled implementation carries no
design risk, because a distance function has no choices to make. `difflib` from the standard library —
rejected: `SequenceMatcher` computes a similarity that is not Levenshtein, is not the measure ADR-0005
pinned, and carries a documented "autojunk" heuristic that makes its result depend on the input's
character frequencies.

---

## R3 — Candidate generation: a pigeonhole filter with a proof, not a heuristic

**Decision**: Generate candidates by the classic pigeonhole (partition) filter. For a claim of length `m`
and threshold `t`, let `k` be the maximum edit count that can still clear the threshold. Split the claim
into `k + 1` disjoint blocks. Find every exact occurrence of every block in the match view with a
`str.find` loop. Each occurrence of the block at claim-offset `off` landing at view position `p` implies
a window starting within `[p - off - k, p - off + k]`. Score windows of length `m - k … m + k` at each
implied start with R2's scorer, keeping those at or above the threshold.

**Rationale**: the filter has a **proof of completeness**, which a heuristic prefilter would not. If the
edit distance between the claim and a window is at most `k`, then `k` edits can damage at most `k` of the
`k + 1` disjoint blocks, so at least one block survives verbatim inside that window. Therefore every
window that could clear the threshold is generated. No true match can be missed, which is what lets
FR-028's determinism claim and SC-003's correctness claim be made about recall and not only about
ordering.

It also needs no dependency beyond the scorer, and its inner loop is `str.find`, which is C.

Measured on a 50,000-character view with a 30-character claim carrying two edits: **0.69 ms**, 17
candidate starts, 3 windows at or above threshold, winner at the true position.

**Implementation added one branch this entry did not anticipate.** The argument needs `k + 1` *non-empty*
blocks, and they do not exist once `k` reaches the claim's length — which happens around `t = 0.5` and
below. There the filter falls back to scanning every position, bounded by the same candidate budget. The
first implementation instead degraded to a single block containing the whole claim, which silently
required a verbatim match at exactly the thresholds where the caller had asked for the opposite. It
shipped that way for one commit and was caught by a test written to check something else entirely.

**Alternatives considered**: scanning every offset in the view (~50,000 Python-level scorer calls per
value) — rejected on cost as the *default* path, and adopted as the low-threshold fallback above, where
the filter has nothing to filter on. An n-gram index over the view — rejected under Principle XI: it is a second
data structure with its own build cost, and the pigeonhole filter already gives completeness at a cost
this feature can afford. `partial_ratio_alignment` as a locator followed by local rescoring — rejected
per R2: recall would then depend on a `rapidfuzz` internal, which is the property this design most needs
to own.

---

## R4 — The candidate slack is derived from the threshold, not chosen

**Decision**: There is no independent slack constant. `k = floor((1 - t) · m / t)`, and `k` is
simultaneously the block count minus one, the start-position slack, and the window-length slack.

**Rationale**: The spec's checklist carried "choose and justify the slack" into this phase as an open
number. Working it out showed it is not free. `normalized_similarity` is `1 - distance / max(m, |w|)`,
so a window clears threshold `t` only if `distance ≤ (1 - t) · max(m, |w|)`. Since `|w| ≤ m + k`, this is
self-referential; solving it gives the closed form above. Verified against the self-referential
definition for every `m` from 1 to 59, exactly.

A window whose length differs from `m` by more than `k` cannot clear the threshold, because the length
difference alone is already that many edits. A start displaced by more than `k` cannot either. So a slack
larger than `k` generates only candidates that are provably below threshold, and a slack smaller than `k`
breaks R3's completeness proof. The number is determined.

**This is not a micro-optimisation.** An early probe used an independent `slack = 8`, which scores
`(2·8+1)² = 289` windows per block occurrence against the derived version's `(2k+1)²`. On the adversarial
document of R8, that difference is **1373 ms versus 53 ms** for a single value — a 26× gap that is the
difference between meeting SC-020 and missing it by an order of magnitude.

| Claim length `m` | 5 | 10 | 16 | 24 | 30 | 50 |
|---|---|---|---|---|---|---|
| `k` at `t = 0.90` | 0 | 1 | 1 | 2 | 3 | 5 |

`k = 0` for claims of 9 characters or fewer, meaning a short claim must match exactly. That is correct
rather than a degenerate case: at a 0.90 threshold a single edit in a 9-character string scores 0.889 and
does not clear it. The filter degenerating to exact search is the filter agreeing with the threshold.

**Consequence**: changing the threshold changes `k` and therefore changes the candidate set, not merely
the acceptance test. This is why FR-042 puts the threshold in the options hash and why changing its
default requires a `grounding_version` bump — the tuning at Milestone 6 will change which candidates are
generated, not just which survive.

---

## R5 — NFKC already performs two of ADR-0006's six transformations

**Decision**: Implement the match view as NFKC first, then soft-hyphen removal, then de-hyphenation, then
whitespace collapsing. Ligature expansion and non-breaking-space folding are **not** implemented
separately; NFKC performs them.

**Rationale**: measured, not assumed:

| Input | NFKC output | Note |
|---|---|---|
| `'ﬁ'` (U+FB01) | `'fi'` | ligature expansion, 1 → 2 chars |
| `'ﬃ'` (U+FB03) | `'ffi'` | 1 → 3 chars |
| `'a\xa0b'` (NBSP) | `'a b'` | NBSP folding |
| `'a b'` (narrow NBSP) | `'a b'` | folded too |
| `'a b'` (figure space) | `'a b'` | folded too |
| `'in\xadvoice'` (soft hyphen) | `'in\xadvoice'` | **unchanged — needs explicit removal** |
| `'é'` (combining acute) | `'é'` | 2 → **1** char |

Writing a separate ligature table after NFKC has already expanded them would be dead code that a reader
would reasonably trust. Writing a separate NBSP rule would be the same. ADR-0006's list of six describes
the *effects* the view must have, and four rules produce all six.

The soft-hyphen row is why the list cannot be reduced to NFKC alone. The combining-mark row is why the
offset map cannot be arithmetic: NFKC both grows and **shrinks** text, so a naive "track a running delta"
implementation is wrong in both directions.

**Alternatives considered**: NFC instead of NFKC — rejected, NFC does not expand ligatures, which is the
transformation ADR-0006 was principally written for. Implementing all six rules explicitly and skipping
NFKC — rejected: it would miss the long tail NFKC covers (full-width forms, circled digits, narrow and
figure spaces) that a hand-written table would not enumerate.

---

## R6 — Dash folding is a known gap, and this feature does not close it

**Decision**: The match view does **not** fold dash variants to ASCII hyphen-minus. Claims differing from
the source only in dash codepoint fall to the fuzzy tier.

**Rationale**: measured — NFKC maps U+2011 (non-breaking hyphen) to U+2010 (hyphen), but **not** to
U+002D (hyphen-minus), and leaves U+2010 alone. So a document typeset with U+2010 and a model quoting
ASCII `-` will not match at the exact tier.

Adding a dash-folding rule would very likely raise the grounding rate. It is not done here because
ADR-0006 pins the transformation list for `match_view_version = "v1"` and states that altering it
requires a version bump. Adding a seventh transformation inside the implementation of `v1` would be
exactly the "resolve it silently in code" the constitution's precedence rule forbids.

**Recorded as**: a candidate for `match_view_version = "v2"`, to be decided with the Milestone 6
measurements that can show what it is worth. The fuzzy tier covers it in the meantime, at the cost of the
affected values reporting `fuzzy` rather than `exact`.

---

## R7 — De-hyphenation: join only lowercase-to-lowercase

**Decision**: Remove a hyphen at a line break **only** when the character immediately before it and the
first non-whitespace character after the break are both lowercase letters. Otherwise remove the line
break and keep the hyphen.

**Rationale**: this is the one transformation where both obvious answers are measurably wrong, so the
measurement decided it. Similarities at the 0.90 threshold:

| Case | Claim | View after the naive rule | Similarity | Clears 0.90? |
|---|---|---|---|---|
| Justified line break in a word | `amount` | `am-ount` *(never de-hyphenated)* | **0.857** | ✗ |
| Identifier broken at a line end | `INV-2024-001` | `INV2024001` *(always de-hyphenated)* | **0.833** | ✗ |
| Compound word, chosen rule | `well-known` | `wellknown` | **0.900** | ✓, barely |

Always de-hyphenating destroys identifiers, which are exactly what an IDP engine extracts — invoice
numbers, purchase-order references, tax ids. Never de-hyphenating destroys ordinary words broken by
justification. Neither is acceptable, and neither is rescued by the fuzzy tier, because both land below
threshold.

The case-based rule separates them without a dictionary, a language model, or any locale knowledge:
typesetting breaks lowercase words mid-word, while identifiers carry uppercase letters or digits around
their hyphens. It is deterministic, cheap, and explainable in one sentence.

**The residual loss is genuine and is recorded rather than hidden.** A real compound word broken at a
line end — `well-known` — is joined to `wellknown` and then scores exactly **0.900** against the claim.
It clears the threshold by nothing at all. Two consequences follow, and both belong in the Milestone 6
tuning brief: raising the threshold above 0.90 breaks this case, and the case is common enough in prose
that the tuning must measure it deliberately rather than discover it.

**Alternatives considered**: a dictionary check on the joined word — rejected: it is a data dependency,
it is language-specific, and it makes the match view's output depend on a word list that would itself
need versioning. Emitting both readings — impossible: the view is one string with one offset map.

---

## R8 — The adversarial case is real, and the candidate budget is explicit

**Decision**: Bound the number of candidate starts scored per value at a configurable maximum, defaulting
to **1,500** — a value derived from SC-020 and the measured scoring rate, not picked. On truncation, the
value is still resolved from the candidates examined, and the truncation is recorded on the outcome and
in the log event.

**Rationale**: R3's filter is complete but its cost scales with how often the claim's blocks occur.
Measured on a deliberately adversarial document — the 28-character string `'Invoice Total Amount Due    '`
repeated 2,000 times, 56,000 characters — with claims that do **not** match exactly:

| Claim | `k` | Candidate starts | Time |
|---|---|---|---|
| `'Totxl'` | 0 | 0 | 3 ms |
| `'Total Amount Dux'` | 1 | 6,000 | 53 ms |
| `'Xnvoice Total Amount Due'` | 2 | 9,998 | **139 ms** |

At 139 ms per value, twenty such values take 2.8 seconds — 5.6× over SC-020's 500 ms budget. The budget
is the backstop for that shape of input.

**Two things make this less alarming than the numbers suggest, and both are worth stating.** First, the
exact tier short-circuits it: a claim whose blocks are that common is usually a claim that appears
verbatim, and the exact tier resolves it in 4.4 ms even with 2,000 occurrences. The adversarial case
requires a claim that is simultaneously near-miss and near-ubiquitous. Second, the document is
synthetic; on the repository's real parsed invoice, twenty fuzzy-tier values cost **0.68 ms in total**.

The budget therefore exists for a case this feature expects never to hit in practice, which is exactly
when a silent cap would be most dangerous — it would never be noticed. Hence FR-035's counts and FR-047's
event both carry the truncation flag, and a truncated grounding says so on the value.

**Alternatives considered**: no budget — rejected, an unbounded scan on a pathological document is a
denial-of-service surface in a library that will eventually run behind an API. A budget that raises an
error — rejected: an over-budget value has candidates in hand and refusing to report the best of them
serves nobody. Sampling candidates — rejected: it makes recall depend on a sampling rule, and a
deterministic prefix of a sorted list is both simpler and explainable.

**Sizing the budget is the whole point of it.** At the measured 72 candidate starts per ms, SC-020's
500 ms across 20 values allows ~1,800 starts each; 1,500 leaves headroom for a slower runner. On ordinary
text the filter produces ~17 candidate starts, so the cap sits roughly 100× above the normal case and
fires only on pathological input — which is the shape a backstop should have. A budget large enough never
to fire is not a backstop.

An earlier draft of this entry said "default 20,000" without deriving it. At the measured rate that is
~278 ms for a **single** value: one value could consume 56% of SC-020's budget for twenty, and the
adversarial case that motivated the cap — 9,998 starts — would never have reached it. The number was
wrong in the direction that is hardest to notice, because nothing would ever have tripped it.

---

## R9 — The offset map is a segment list, not a per-character array

**Decision**: Represent the offset map as an ordered tuple of segments, each mapping a run of view
positions to a run of source positions, with `bisect` for lookup. Not a dense array of one source offset
per view character.

**Rationale**: the transformations are identity over long runs — a page of ordinary text produces one
segment. A dense array costs one Python integer per character (roughly 8 bytes of pointer plus the object
for anything above the small-int cache) for a document where a handful of segments carry the same
information. Lookup is `O(log s)` in the segment count rather than `O(1)`, which is immaterial: it runs
once per resolved value and per alternative, not once per character.

The segment form is also what makes FR-015's invariants inspectable. "Monotonic non-decreasing" is a
property of a short list a test can read and a human can print, rather than a property asserted over
50,000 array entries.

**Alternatives considered**: a dense array — rejected on memory for the benefit of a lookup that is not
on any hot path. Recomputing positions arithmetically from a running delta — rejected as incorrect: R5
measured NFKC both growing and shrinking text, so a single delta cannot describe it.

**Implementation found one thing this entry got wrong, and it mattered.** A segment needs an explicit
`identity` flag; whether the view copied a run through character for character cannot be inferred from
`length == source_length`. The counterexample is a ligature carrying a combining mark — `ﬁ` + U+0301 folds
to `fí`, two view characters from two source characters, equal lengths and *not* a character-wise
mapping. Mapping a position inside it by addition landed on the combining mark alone and returned a range
that had lost half the match. Hypothesis found it; no review pass had.

The flag also made the segment list real. Coalescing consecutive untouched characters into one run is
what this entry describes, and the first implementation emitted one segment per character regardless — so
200 characters of ordinary text produced 200 segments rather than the 1 it does now. The design was
described here and not built until the bug forced the restructure.

---

## R10 — Mapping a view range back to a source range

**Decision**: Map the view range's start and end independently through the segment list, and take the
**smallest source range containing both mapped boundaries**. Where a boundary falls inside a region the
view deleted or merged, it maps to the boundary of the containing source region — outward, never inward.

**Rationale**: FR-017 requires that a round trip may widen a range but never narrow or move it. Widening
is safe: a slightly larger source range still contains the matched text, and the geometry derived from it
is a slightly larger set of token boxes. Narrowing is not: it would produce a range that excludes part of
what actually matched, and a bounding box that omits part of the value — a confidently wrong answer of
exactly the kind ADR-0006 warns is this component's characteristic failure.

**"Outward" has a second half that implementation supplied.** A position at a rewritten segment's
*boundary* is not inside it, and treating the two the same over-widens: a claim ending where a collapsed
whitespace run begins came back with the newline attached. Widening must be *to contain what matched*,
not past it. So an end position maps to the segment's far edge only when it falls strictly inside;
at `offset == 0` both directions map to the near edge.

The asymmetry is the whole rule, and it is why FR-017 states the invariant as "identity where boundaries
survive, containment otherwise" rather than as the unqualified identity ADR-0006's text implies. A range
whose boundary sits inside a soft hyphen the view deleted has no exact source boundary to return to; the
containing one is the only correct answer.

---

## R11 — Pages and geometry come from the kernel, unchanged

**Decision**: Resolve pages with the existing `Document.page_for(span)` and geometry with the existing
`Document.locate(span)`. No kernel change at this milestone.

**Rationale**: both already do exactly what FR-005 and FR-006 need, and their existing semantics are the
ones the spec's assumptions describe. `page_for` works from text position alone, so it succeeds on
documents whose parser supplied no geometry — which is what lets FR-006 keep such a value grounded.
`locate` raises `CapabilityError` rather than returning an empty tuple when a parser supplied no
geometry, which is precisely FR-006's "unavailable must not read as nothing is there"; grounding catches
that error and records unavailability rather than letting it escape.

`locate` returns one box per **intersecting** token with no sub-token interpolation. A resolved range
that starts mid-token therefore yields a box covering the whole token. The spec records this as an
assumption; the reason it is right is the kernel's own: interpolating a partial box from a character
offset assumes uniform glyph advance, which is false for proportional fonts and every complex script.

**Consequence**: a grounded value's box may be wider than the value. That is documented, not fixed.

---

## R12 — Alternatives carry ranges and scores, and resolve geometry on demand

**Decision**: An alternative stores its source range and its score. Its pages and boxes are computed when
asked for, not when the alternative is created.

**Rationale**: alternatives exist so ambiguity is visible. Most are never inspected — they matter when a
human is investigating one disputed value. Resolving geometry for five alternatives per value, for every
value, would multiply the kernel geometry work by six to serve a case that arises for a small fraction of
values. The range is what makes an alternative meaningful and it is what is stored.

---

## R13 — Threshold representation and the exact tier's score

**Decision**: The threshold is a `float` defaulting to `0.90`, carried on a `GroundingOptions` type, and
folded into the options hash by the kernel's existing `canonical_json`. An exact resolution scores
exactly `1.0` and is not produced by running the scorer.

**Rationale**: `canonical_json` already fixes float formatting across platforms — CPython's shortest
round-tripping repr for IEEE-754 doubles — so the threshold hashes identically everywhere without a new
convention, which is the same reuse ADR-0008 chose for `schema_hash`.

The exact tier assigning `1.0` by definition rather than by measurement matters for a reason worth
recording: the scorer would also return `1.0` for an exact match, so the two agree today. But ADR-0004
says the two tiers' scores are not comparable, and deriving the exact tier's score from the fuzzy tier's
scorer would quietly make them the same quantity. Assigning it structurally keeps the tiers independent,
so a future change to the fuzzy scorer cannot move an exact score.

---

## R14 — Determinism has no clock, no randomness, and no set iteration

**Decision**: The grounding path introduces no clock, no randomness, and no network. Sets are used for
candidate-start collection but are **sorted** before any scoring, and nothing downstream iterates a set
or a dict whose order could vary.

**Rationale**: `PYTHONHASHSEED` randomises string hashing per process, so iterating a set of candidate
starts directly would produce a different scoring order per run. That would not change the winner —
the tie-break is total, so the winner is order-independent by construction — but it would change the
order of equal-scoring alternatives, and FR-028 requires the alternatives list itself to be identical
across runs. Sorting before scoring makes the tie-break's totality something the implementation relies
on rather than something it needs.

A test runs the suite under two different `PYTHONHASHSEED` values, because this is a failure that a
single-seeded test suite passes by luck.

---

## R15 — Test tiers, and what the property tests must cover

**Decision**: Three tiers, all offline. Unit and integration tests over committed documents; property
tests (Hypothesis) over the offset map and the tie-break; a `perf`-marked tier for SC-020.

Property coverage is where this feature's risk actually is, so it is enumerated rather than left to
judgment:

| Property | Why it is the one that matters |
|---|---|
| Every view position maps to exactly one source position | FR-015; a gap or a duplicate is a wrong box |
| The map is monotonic non-decreasing | FR-015; a non-monotonic map produces ranges that run backwards |
| Round trip is identity where boundaries survive, containment otherwise | FR-017, R10; the narrowing case is the dangerous one |
| Every returned range is valid in the source text | FR-016; a view position escaping is the headline bug of ADR-0006 |
| The tie-break yields exactly one winner for any candidate set | FR-024; must hold when score, start, **and** length all tie |
| Identical inputs yield identical winner, score, and alternatives | FR-028; run under two hash seeds (R14) |
| A claim present verbatim always resolves at the exact tier | the pigeonhole filter must never lose an exact match |

**Rationale**: Principle XII mandates property tests for the kernel, and this feature changes no kernel
code — so that suite applies unchanged and must stay green. But ADR-0006 names the offset map "the
highest-risk component in the grounding path" and says it "warrants the strongest tests outside the
kernel itself". These are those tests. The reason is worth restating in the plan: an incorrect offset map
does not crash and does not return nothing. It returns a grounded-looking value pointing at the wrong
place, which no other test in this repository would catch.

**No provider tier exists.** Nothing in this feature can reach a network, so there is no credentialed
test class and nothing of *this feature's* to skip. That is FR-048 and SC-021, and it is the first
milestone since the kernel of which it is true. The repository's suite still reports 11 skips — Milestone
2's Azure and Milestone 3's Gemini live tests — so "the whole suite runs for every contributor" would be
an overclaim; what runs for everyone is every test this milestone added.

---

## R16 — Identical claims inside a repeating group

**Decision**: Uniqueness is enforced within one repeating group at one field path, by greedy assignment
in entry order. Each entry's winner excludes ranges already taken by earlier entries in the same group.

**Rationale**: resolving each value independently makes two line items both claiming `Widget` resolve to
the same range, because the tie-break picks the earliest occurrence for both. Every line item would then
point at the first one's box — a wrong audit trail that looks completely well-formed, which is the
failure class this feature exists to prevent.

The scope is the whole decision. **Global** uniqueness would be actively wrong: an invoice date read as
both issue date and due date must resolve to the one range it occupies, and forcing the second to move
elsewhere would invent a location. Uniqueness is a property of repetition, not of grounding.

Greedy in entry order rather than optimal assignment: entry order and document order agree for every
table a parser produces in reading order, the rule is explainable in one sentence, and an optimal
assignment (Hungarian) would make one value's range depend on another value's candidate set — which is
far harder to explain when someone disputes a single field.

Exclusion applies to the **winner only**. An alternative may still name a range another entry won,
because alternatives record what was there rather than what was assigned; filtering them would hide the
ambiguity the list exists to surface.

**Consequence to accept**: where the model returns entries out of document order, the assignment is
wrong but deterministic — entry 0 takes the first occurrence regardless. This is recorded rather than
solved; detecting it needs a document-order signal the extraction layer does not currently carry.

**This entry exists because `/speckit-analyze` found the requirement had no design at all.** FR-029 was
written into the spec and then appeared in no other artifact, and the algorithm as designed provably
could not satisfy it. Recorded here rather than quietly fixed in code, per the constitution's precedence
rule for decisions that were never actually made.

**Alternatives considered**: global span uniqueness — rejected, it breaks legitimate sharing. Leaving it
unsolved — rejected, it makes repeating-group grounding visibly wrong on the most common document type
docdoc targets.
