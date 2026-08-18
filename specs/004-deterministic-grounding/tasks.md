---

description: "Task list for 004-deterministic-grounding"
---

# Tasks: Deterministic Grounding

**Input**: Design documents from `/specs/004-deterministic-grounding/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/grounding-api.md](contracts/grounding-api.md), [quickstart.md](quickstart.md)

**Tests**: **NOT optional here.** The template's constitution override names *Grounding* and *Layer
boundaries* explicitly, and this feature is both. Principle XII additionally requires property tests
where invariants are load-bearing, and ADR-0006 names the offset map "the highest-risk component in the
grounding path". Test tasks below are requirements, not suggestions.

**On the golden-set metrics task the template mandates.** The constitution override also requires, for
evaluation-affecting changes — grounding among them — a golden-set task reporting field accuracy,
coverage, missing rate, incorrect rate, and grounding rate. **There is no such task here, and that is a
recorded decision rather than an omission.** The golden dataset does not exist:
`TODO(GOLDEN_DATASET_LICENSING)` is an open constitutional decision gating Milestone 6, and the
constitution's own quality gate 5 makes the evaluation gate advisory until the dataset reaches its target
size. What this milestone owes instead is that the metric become *computable*, which T023 delivers via
`GroundingCounts` and T041 pins via the denominator rule. The first change that can report against a
golden set is Milestone 6's, and the grounding-regression gate becomes blocking there.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to spec.md's prioritised stories
- Exact file paths in every description

## Path Conventions

Single Python project, `src/` layout. New code lands in `src/docdoc/grounding/`; tests in `tests/`.

## A note on where implementation and tests diverge

Three properties the spec assigns to US3 and US4 must be *implemented* in US1, because `ground()` cannot
ship without them: the wrong-document guard (FR-002), the absent-versus-claimless distinction (FR-008 /
FR-009), and the artifact identity (FR-036). Shipping US1 without the first would ship exactly the
confidently-wrong failure this feature exists to prevent. Their **dedicated adversarial tests** stay in
the story that owns them. Each such task says so rather than leaving a reader to notice the split.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, package skeleton, and the fixtures every later phase reads

- [X] T001 Add `rapidfuzz>=3.0` to `[project.dependencies]` in `pyproject.toml`, and correct the comment above it — it currently reads "The kernel's only permitted runtime dependency", which becomes misleading the moment there are two base dependencies. It must say that the *kernel* imports `pydantic` alone while the *base install* now carries `rapidfuzz` for grounding, citing ADR-0005 and the constitution's sanctioned-stack line. Regenerate and commit `uv.lock`
- [X] T002 Create the package skeleton `src/docdoc/grounding/__init__.py` with the module docstring stating what this layer is and what it refuses to do (no network, no credentials, no model call, no judgment about value correctness), mirroring the shape of `src/docdoc/extraction/__init__.py`
- [X] T003 Add `docdoc.grounding` to the layers contract in `pyproject.toml` **above** `docdoc.extraction` (`layers = ["docdoc.grounding", "docdoc.extraction", "docdoc.ingest", "docdoc.kernel"]`) and extend the existing comment explaining that higher layers are added as their milestones land. Must run after T002 — `import-linter` errors on a layer naming a non-existent module
- [X] T003a Add a forbidden-imports contract for `docdoc.grounding` in `pyproject.toml`, listing `httpx`, `requests`, `socket`, `urllib`, `openai`, `anthropic`, `google`, `boto3`, `azure`, `fastapi`, and `sqlalchemy`. This is what turns FR-048 and SC-021 — "no network, no credentials, ever" — from a prose claim into a build failure. It is this milestone's headline property and the only one currently asserted nowhere but in documentation. Must run after T002. Depends on T003
- [X] T004 [P] Build the typesetting fixtures in `tests/fixtures/grounding/`: documents carrying an *fi* ligature, a soft hyphen mid-word, a lowercase word broken by a line-break hyphen, an identifier (`INV-2024-001`) broken by a line-break hyphen, a compound word (`well-known`) broken by a line-break hyphen, non-breaking / narrow / figure spaces, and a combining-mark sequence. Include a builder script so the fixtures are regenerable, following `tests/fixtures/make_fixtures.py`
- [X] T005 [P] Build the adversarial fixture in `tests/fixtures/grounding/`: a highly repetitive document (the 28-character string `'Invoice Total Amount Due    '` repeated 2,000 times) plus the three near-miss claims of research.md R8. This is the input the candidate budget exists for, and it must be committed rather than generated inline so the perf numbers are comparable across runs

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The match view and the offset map. Every user story rests on these, and the offset map is
the component whose failure mode is a plausible wrong answer rather than an exception.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 [P] Implement `GroundingError` in `src/docdoc/grounding/errors.py`, extending the kernel's `DocdocError` root with `document_id`, `extraction_document_id`, and `field_path`. Carries **no** `transient` flag and document why in the docstring: there is no transient failure mode in a deterministic offline computation, unlike `ProviderError` whose flag exists because a network has one (data-model §8, FR-044)
- [X] T007 [P] Implement `GroundingOptions` in `src/docdoc/grounding/options.py` with `threshold: float = 0.90` and `candidate_budget: int = 1_500`. Docstring must state that `threshold` changes which candidates are *generated* and not merely which are accepted, so it cannot be applied to a stored result after the fact, and must carry the budget's derivation — `500 ms ÷ 20 values ÷ 72 starts per ms`, rounded down for CI headroom — so nobody later "rounds it up to a nicer number" and silently unbinds SC-020 (data-model §6, GRD-19, GRD-19a)
- [X] T008 Implement `OffsetMap` and `Segment` in `src/docdoc/grounding/offsets.py` as a segment list with `bisect` lookup, not a dense per-character array (research.md R9). Must provide view→source position mapping and view-range→source-range mapping that maps each boundary **outward** to the containing source region (GRD-7, GRD-8, GRD-9, research.md R10)
- [X] T009 Implement `MatchView` in `src/docdoc/grounding/view.py`: NFKC → soft-hyphen removal → de-hyphenation → whitespace collapsing, building the text and the `OffsetMap` in one pass. Write the docstring to record that ligature expansion and non-breaking-space folding are **effects of NFKC** and deliberately not separate steps, so a later reader does not "fix" the missing rules (GRD-2, GRD-4, research.md R5). Depends on T008
- [X] T010 Implement the de-hyphenation rule inside `src/docdoc/grounding/view.py`: remove the hyphen only when the character before it and the first non-whitespace character after the line break are both lowercase; otherwise drop the line break and keep the hyphen. Docstring must carry the three measured cases that decided it and name the residual `well-known` → `wellknown` loss at exactly 0.900 (GRD-3, research.md R7). Depends on T009
- [X] T011 Add `document_id`, `version = "v1"`, and `view_id = sha256(document_id, version)` to `MatchView` in `src/docdoc/grounding/view.py`, reusing the kernel's existing `canonical_json` rather than inventing a second convention (GRD-6, FR-020). Depends on T009
- [X] T012 [P] Write the offset-map property tests in `tests/property/test_offset_map_properties.py` (Hypothesis): every view position maps to exactly one source position; the mapping is monotonic non-decreasing; a round trip is the identity where both boundaries survive and a **containing** range otherwise; a round trip never narrows or moves a range. These are the strongest tests outside the kernel, per ADR-0006, because an incorrect map returns a grounded-looking value pointing at the wrong place and nothing else in this repository would catch it (research.md R15)
- [X] T013 [P] Write the match-view unit tests in `tests/unit/test_match_view.py` over the T004 fixtures: assert NFKC performs ligature expansion and non-breaking / narrow / figure-space folding, assert it does **not** touch soft hyphens, and assert the combining-mark case shrinks the text — the three measurements that justify four rules producing six effects (research.md R5)
- [X] T014 [P] Write the de-hyphenation tests in `tests/unit/test_dehyphenation.py`: `INV-2024-001` survives a line break intact, `am-ount` is joined, and `well-known` joins to `wellknown` scoring exactly 0.900. Assert the 0.900 explicitly with a comment that this test is the tripwire for any future threshold increase (research.md R7)
- [X] T015 Write `tests/unit/test_grounding_boundaries.py` asserting the layer direction mechanically: `docdoc.grounding` may import `docdoc.extraction`, and `docdoc.extraction` must not import `docdoc.grounding`. This is the check that justifies the new package existing at all rather than living inside `extraction` (research.md R1, Principle X)

**Checkpoint**: The match view and offset map are correct and property-tested. User story work can begin.

---

## Phase 3: User Story 1 - Point at where a value came from (Priority: P1) 🎯 MVP

**Goal**: An extracted value resolves to a character range, a page, and a bounding box in the source
document — exact tier only. Everything that does not match verbatim is honestly ungrounded.

**Independent Test**: Ground a committed extraction result over a committed PDF with no network and no
credentials; every value whose claim appears in the document lands on its hand-verified range, page, and
boxes, and `document.text[span.start:span.end]` reads back as the claim.

**This phase is a shippable product on its own.** Exact-only grounding with honest ungrounding is
useful and auditable; US2 raises how much of it lands.

### Tests for User Story 1

> Write these first and confirm they fail before implementing.

- [X] T016 [P] [US1] Write `tests/integration/test_ground_real_pdf.py::test_points_at_source` over the Milestone 2 PDF fixtures and the Milestone 3 `echo` adapter: every value with a verbatim claim carries a span, pages, and boxes, and the span reads back as the claim
- [X] T017 [P] [US1] Write `tests/unit/test_exact_tier.py`: a claim present once resolves `exact` with score exactly `1.0`; a claim present several times resolves to the earliest occurrence with the others as alternatives, all scoring `1.0` (GRD-13)
- [X] T018 [P] [US1] Write `tests/unit/test_geometry_unavailable.py`: a document from a parser that supplied no geometry still grounds, still carries a span and pages, and reports `geometry is None` — never `()`. Assert both cases distinctly, because collapsing "unavailable" into "nothing there" is the bug FR-006 exists to prevent (GRD-17)
- [X] T019 [P] [US1] Write `tests/unit/test_immutability.py`: the document, its canonical text, its provenance, and the extraction result are all byte-identical before and after grounding, asserted by comparison rather than by trusting that nothing wrote to them (FR-007)
- [X] T020 [P] [US1] Write `tests/unit/test_wrong_document.py`: grounding an extraction result against a different document raises `GroundingError` naming both identities, **including** when the other document happens to contain the claims. That last case is the point — it is the failure that most looks like success (FR-002)

### Implementation for User Story 1

- [X] T021 [P] [US1] Implement `GroundingStatus` in `src/docdoc/grounding/result.py` as a three-member string enum, with a docstring stating that `exact` means verbatim *modulo documented, versioned cosmetic folding* — not byte-identical in the raw source. Put it in the docstring rather than only in the design docs, because a docstring travels where a design document does not (GRD-1)
- [X] T022 [P] [US1] Implement `Alternative` and `GroundingOutcome` in `src/docdoc/grounding/result.py`: frozen, `extra="forbid"`, with `span is None` if and only if `status == ungrounded`, `geometry` distinguishing `None` from `()`, and `truncated` present from the start so US2 does not have to widen the type (GRD-14 … GRD-18)
- [X] T023 [US1] Implement `GroundingCounts`, `GroundingProvenance`, and `GroundingResult` in `src/docdoc/grounding/result.py`, with `counts` carrying `not_applicable` separately so the grounding-rate denominator lives in the data rather than being reinvented by each consumer (data-model §7, FR-035). Depends on T022
- [X] T024 [P] [US1] Implement `src/docdoc/grounding/identity.py`: `GROUNDER_ID`, `GROUNDER_VERSION`, `GROUNDING_VERSION = "v1"`, the options hash folding `grounding_version` / `match_view_version` / `GroundingOptions` and nothing else, and `grounding_artifact_id_for()` chaining from the extraction artifact id per ADR-0003. Reuse the kernel's `canonical_json` and `options_hash_for` (GRD-20). *Implemented here because `GroundingResult` carries `artifact_id`; US4 tests what moves it*
- [X] T025 [US1] Implement the exact tier in `src/docdoc/grounding/match.py`: fold the claim through the same transformations as the view (FR-018 — folding only the document would leave an NBSP-bearing claim unable to match), find all occurrences in the view with a `str.find` loop, map each back to a source range, and assign score `1.0` **structurally** rather than by running a scorer, so the two tiers' scores cannot become the same quantity (research.md R13). Assert in the accompanying test that the claim itself is never altered, trimmed, re-cased, or re-encoded — the folding is applied to a copy for comparison, and the stored claim stays byte-identical to what the model returned (FR-011). Depends on T009, T022
- [X] T026 [US1] Implement pages and geometry resolution in `src/docdoc/grounding/match.py` using the kernel's existing `Document.page_for()` and `Document.locate()`, absorbing `CapabilityError` into `geometry=None` rather than letting it escape a perfectly well-grounded value (research.md R11, GRD-17). Depends on T025
- [X] T027 [US1] Implement `ground()` in `src/docdoc/grounding/ground.py`: the wrong-document guard first (FR-002), then derive the match view **once** (FR-019), then one outcome per value carrying a claim, then counts, provenance, and artifact id. A value the model reported absent gets **no outcome** and is counted `not_applicable`; a value present with no claim or an empty claim is `ungrounded` and counted as such — these are different facts and the most likely thing to implement wrong (data-model §5, FR-008, FR-009, FR-026). Depends on T023, T024, T026
- [X] T028 [US1] Implement `src/docdoc/grounding/observe.py`: one `grounding.ground` structured event per run carrying document identity, extraction artifact id, grounding artifact id, both versions, per-outcome counts, truncation count, and duration — and **zero** document text, claim text, extracted values, or view text. The view is a new class of derived content that would leak document text if logged, which makes this rule stricter than Milestone 3's (FR-046, FR-047). Depends on T027
- [X] T029 [US1] Export the public surface from `src/docdoc/grounding/__init__.py` exactly as `contracts/grounding-api.md` §1–§7 describes: `ground`, `GroundingOptions`, `GroundingError`, `GroundingResult`, `GroundingOutcome`, `GroundingStatus`, `Alternative`. Depends on T027

**Checkpoint**: Exact-tier grounding works end to end, offline. US1 is independently shippable.

---

## Phase 4: User Story 2 - Find values the page spells differently (Priority: P2)

**Goal**: Claims that do not match even after cosmetic folding — a transposed digit, a dropped word — are
resolved by approximate matching within the threshold, with runners-up recorded.

**Independent Test**: Ground claims carrying one to three edits against a committed document; each
resolves `fuzzy` with its similarity score, claims below the threshold stay `ungrounded` with no range,
and the adversarial fixture stays within the candidate budget with `truncated` set.

### Tests for User Story 2

- [X] T030 [P] [US2] Write `tests/unit/test_candidates.py`: assert `k = floor((1 - t) · m / t)` matches the self-referential definition for claim lengths 1–59; assert `k = 0` for claims of nine characters or fewer, so the filter correctly degenerates to exact search; assert the completeness property — a window at or above threshold is always in the candidate set (GRD-10, GRD-11, research.md R3, R4)
- [X] T031 [P] [US2] Write `tests/unit/test_fuzzy_tier.py`: a claim one edit away resolves `fuzzy` with the measured score; a claim whose best candidate falls below the threshold is `ungrounded` with `span=None` and `score=None` and **no exception raised** (FR-045); a fabricated claim appearing nowhere is likewise ungrounded rather than attached to the nearest range (FR-023)
- [X] T032 [P] [US2] Write `tests/unit/test_tiebreak.py`: the total order resolves highest score → earliest start → shortest range, and yields exactly one winner when score, start, **and** length all tie. Construct that all-three-tie case explicitly; it is the one a naive `max()` gets away with until it does not (FR-024, GRD-12)
- [X] T033 [P] [US2] Write the candidate-budget tests in `tests/unit/test_candidates.py` against the T005 adversarial fixture: reaching the budget sets `truncated` on the outcome, still resolves from the candidates examined, and increments `counts.truncated`. Assert the run does **not** raise — an over-budget value has good candidates in hand and refusing to report the best of them serves nobody (research.md R8)

- [X] T033a [P] [US2] Write `tests/integration/test_match_view_lift.py`: resolve the T004 typesetting fixtures twice — once against the match view, once against raw `Document.text` — and assert the share of claims reaching the **exact** tier is strictly higher with the view, emitting the two rates and the delta so the increase is *reported* rather than merely asserted. This is the only evidence ADR-0006 was worth its scope; without it the match view's justification rests on an argument nobody re-measures (SC-007)

### Implementation for User Story 2

- [X] T034 [US2] Implement the pigeonhole candidate filter in `src/docdoc/grounding/candidates.py`: derive `k` from the threshold and claim length, split the claim into `k + 1` disjoint blocks, locate every block occurrence with a `str.find` loop, and emit implied window starts over `±k` with lengths `m - k … m + k`. The docstring must carry the completeness argument — `k` edits damage at most `k` of `k + 1` disjoint blocks — because it is what lets FR-028's determinism claim cover recall and not only ordering (GRD-10, GRD-11)
- [X] T035 [US2] Enforce the candidate budget in `src/docdoc/grounding/candidates.py`: sort candidate starts, take the first `candidate_budget`, and report whether truncation occurred. Sorting before truncating is what keeps the truncated set deterministic; truncating a set would make it depend on the hash seed (research.md R8, R14). Depends on T034
- [X] T036 [US2] Implement the fuzzy tier in `src/docdoc/grounding/match.py` using `rapidfuzz`'s `Levenshtein.normalized_similarity(..., score_cutoff=threshold)` as a **pure scorer only**. Do not use `fuzz.partial_ratio_alignment` or `process.cdist`; the docstring must say why, because both look like obvious improvements to a later reader: the first returns one alignment where alternatives need several and its tie behaviour is an undocumented internal that could move every result under an unchanged `grounding_version`, the second requires numpy (research.md R2). Depends on T034
- [X] T037 [US2] Implement the total tie-break and alternatives selection in `src/docdoc/grounding/match.py`: sort candidates by (score descending, start ascending, length ascending), take the winner, and retain up to five runners-up at or above the threshold. Sort candidate starts before scoring so the alternatives list does not depend on set iteration order (FR-024, FR-025, GRD-12, research.md R14). Depends on T036
- [X] T038 [US2] Wire the fuzzy tier into `src/docdoc/grounding/ground.py` behind the exact tier, so fuzzy runs only when no exact match exists, and propagate `truncated` to the outcome and to `counts` (FR-021, FR-023). Depends on T027, T037
- [X] T038a [P] [US2] Write `tests/unit/test_repeating_group_uniqueness.py`: three entries with identical claims against text containing two occurrences resolve to the two distinct ranges in entry order, with the third `ungrounded`; and two **distinct** field paths reading one shared range both resolve to it, proving the uniqueness rule does not fire outside a repeating group. Assert the alternatives of one entry may still name the range another entry won (FR-029, SC-012, GRD-13a)
- [X] T038b [US2] Implement group-scoped assignment in `src/docdoc/grounding/ground.py`: group values by (repeating group path, field name), resolve in entry index order, and exclude ranges already taken within that group. The exclusion must apply to the **winner only** — an alternative may still name a range another entry won, because alternatives record what was there rather than what was assigned. Scope the exclusion to the group; applying it globally would force a date read as both issue date and due date to invent a second location (GRD-13a, research.md R16). Depends on T027, T037

**Checkpoint**: Both tiers work. US1 and US2 are independently testable.

---

## Phase 5: User Story 3 - Never be misled by a grounded-looking value (Priority: P3)

**Goal**: Every honesty property holds mechanically — ungrounded is distinguishable everywhere, scores
are not comparable across tiers, the model's self-report changes nothing, and no view position escapes.

**Independent Test**: Ground a committed set containing exact, fuzzy, and unresolvable values; each is
mechanically distinguishable in the result, in the counts, and in the log event, and altering
`model_confidence` changes no outcome.

- [X] T039 [P] [US3] Write `tests/unit/test_model_confidence_inert.py`: ground one committed set twice with `model_confidence` altered between runs and assert the outcomes, scores, spans, alternatives, and artifact id are all identical. This is SC-017 and it is the mechanical form of ADR-0004's rule that an untrusted signal routes nothing. In the same file assert that `calibrated_confidence` and `calibrator_version` are still unset after grounding — they belong to the same family of extraction-layer fields this stage must leave alone, and ADR-0004 reserves them for a versioned calibrator that does not exist (FR-032, FR-033)
- [X] T040 [P] [US3] Write `tests/unit/test_no_view_offsets_escape.py`: over a document where the view and the source differ in length, assert every span in every outcome and every alternative is a valid range in `Document.text`, and that no error message or log field carries a view position. This is the automated form of SC-004 and it guards ADR-0006's headline failure (FR-016). Also assert the **view itself never escapes**: no public export of `docdoc.grounding` returns the folded text, and no result field carries it. FR-013's "never handed to consumers" is otherwise checked nowhere — the offset and logging assertions above cover positions and log lines, not the API surface (FR-013)
- [X] T041 [P] [US3] Write `tests/unit/test_absent_and_claimless.py` covering the three-row table of data-model §5: a model-reported absence produces **no outcome** and counts `not_applicable`; a present value with `claimed_text=None` is `ungrounded`; a present value with `claimed_text=""` is `ungrounded`. Assert the rate denominator directly, because collapsing rows one and two would make the grounding rate depend on how many fields a schema declares (FR-008, FR-009, FR-026)
- [X] T042 [P] [US3] Write `tests/unit/test_grounding_observe.py`: the event carries every field FR-047 names, and a content-leak assertion sweeps the captured log output for document text, claim text, extracted values, and view text (SC-019)
- [X] T043 [US3] Add the score-semantics documentation to `src/docdoc/grounding/result.py`: `GroundingOutcome.score`'s field description must state that exact and fuzzy scores are not comparable and that nothing in docdoc ranks values across tiers by score. It goes in the field description rather than a comment because FR-031 requires it *wherever the score is exposed*, and the generated schema is one of those places — the same reasoning that put the untrusted label on `model_confidence` in Milestone 3 (FR-031, ADR-0004)
- [X] T043a [P] [US3] Write `tests/unit/test_no_validation_judgment.py`: a value whose claim resolves but whose extracted value disagrees with the text at that range — `value=1240.00` against a claim resolving to text reading `1,420.00` — produces a normal grounded outcome with no finding, no warning, and no status change. Grounding answers *where*, not *whether*; the disagreement is a Milestone 5 finding. plan.md gate 8 calls this temptation real and named, and this test is what makes the boundary mechanical rather than a matter of discipline (FR-010, Principle VII)
- [X] T044 [US3] Verify and, where missing, add the ungrounded-distinguishability assertions across `tests/unit/test_fuzzy_tier.py` and `tests/unit/test_grounding_observe.py`: no representation exists — result, counts, or log event — in which an ungrounded value reads as located (FR-034)

**Checkpoint**: The honesty properties are mechanical rather than aspirational.

---

## Phase 6: User Story 4 - Reproduce and explain a grounding six months later (Priority: P4)

**Goal**: Identical inputs give identical results on any machine, and every result carries what is needed
to explain it after the algorithm, the view, and the threshold have all moved.

**Independent Test**: Ground the same inputs twice under two `PYTHONHASHSEED` values and confirm
byte-identical winners, scores, and alternatives; change the threshold and confirm the artifact id moves.

- [X] T045 [P] [US4] Write `tests/property/test_tiebreak_properties.py` (Hypothesis): for any generated candidate set, exactly one winner is produced and the alternatives list is stable. Must pass under two `PYTHONHASHSEED` values — a single-seeded suite passes this by luck, which is exactly how a non-deterministic alternatives list reaches a release (FR-028, research.md R14)
- [X] T046 [P] [US4] Write `tests/unit/test_grounding_identity.py`: changing `grounding_version`, `match_view_version`, the threshold, or the candidate budget each moves the artifact id; grounding the same extraction twice with the same options leaves it equal and the outcomes identical; and the id chains from the extraction artifact id per ADR-0003 (GRD-20, FR-036, FR-039)
- [X] T047 [P] [US4] Write `tests/unit/test_grounding_identity.py::test_reground_is_independent`: re-grounding produces a new result with its own provenance and does not mutate, overwrite, or reinterpret the prior one, asserted on frozen instances (FR-041)
- [X] T047a [P] [US4] Write `tests/unit/test_provenance_recording.py`: every field of `GroundingProvenance` is populated on every result and none is empty, a placeholder, or a default standing in for a value that was never recorded — asserted field by field rather than by checking the object exists. Milestone 3 shipped this exact test for extraction and it is the reason a missing provenance field would be caught there; dropping the pattern here would leave SC-013 resting on the type system, which cannot tell a recorded empty string from an unrecorded one (FR-038, SC-013)
- [X] T048 [US4] Add the two-hash-seed run to CI in `.github/workflows/ci.yml` so determinism is checked on every push rather than only when someone remembers to vary the seed locally. Without this, T045's guarantee is only as good as a developer's habit (research.md R14)
- [X] T049 [US4] Add the version-bump guard in `tests/unit/test_grounding_identity.py`: a checked-in snapshot of `GROUNDING_VERSION`, `MATCH_VIEW_VERSION`, the default threshold, and a hash over the transformation rule set, so that changing the candidate generator, the scorer, the tie-break, the slack derivation, or the default threshold fails the build until the version moves. ADR-0005 requires the bump and no system detects a semantic change on its own — the snapshot is what converts a silent algorithm change into a failed build (FR-027, SC-015)

**Checkpoint**: All four stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T050 [P] Write `examples/ground_invoice.py`: parse a committed PDF, extract with the `echo` adapter, ground, and print each value with its page and box. Must run with no credentials and no network, and must supply its own minimal schema rather than reading `schemas/` — that directory is not in the wheel, as Milestone 3 recorded (SC-022, Principle XII's "at least one example")
- [X] T051 [P] Write `docs/concepts/grounding.md`: the three states, what `exact` does and does not mean after ADR-0006, why the two scores are not comparable, why an absent field is not ungrounded, and the match view's role — including that it is never exposed as `Document.text`. Principle XII forbids shipping a feature without documentation
- [X] T052 [P] Update `README.md` to state that grounding is implemented, replacing any text that describes it as forthcoming, and update `docs/concepts/extraction.md` where it says grounding is deferred to a later milestone
- [X] T053 Extend `tests/unit/test_plan_tree_is_current.py` to cover `docdoc.grounding` as well as `docdoc.extraction`, so this plan's test tree is checked by the same mechanism rather than by a second hand-maintained list. The meta-test category it defines gains no new members here
- [X] T054 [P] Extend `tests/integration/test_examples_run.py` to execute `examples/ground_invoice.py`, so the example is run rather than read
- [X] T055 Write `tests/perf/test_grounding_perf.py` (marked `perf`) covering the plan's table: match-view construction once per run, exact tier over 20 values, fuzzy tier over 20 values on ordinary text, and the adversarial row from the T005 fixture. Assert the match view is built **once**, not per value — that is the row that would move if FR-019 regressed. Assert the adversarial fixture stays inside SC-020's 500 ms **with** the default budget and would exceed it without one, so the test proves the budget is doing the work rather than merely being present
- [X] T056 Run every scenario in [quickstart.md](quickstart.md) end to end and correct any drift between the document and the shipped behaviour. The commands there are user-facing and are the first thing a contributor runs
- [X] T057 Add a `CHANGELOG.md` entry recording the new base dependency, the new layer, the three ADR refinements (grounding's package placement, the derived slack, the containment form of the round-trip invariant), and the two known gaps carried forward — dash folding and the razor-thin `well-known` case
- [X] T058 Draft the clarifying amendment to `docs/adr/0005-fuzzy-grounding-specification.md` recording that grounding lives in its own package and layer rather than `extraction/grounding.py`, and that the candidate slack is derived from the threshold rather than chosen. Per the constitution's precedence rule, a decision that departs from an ADR must be raised and recorded, not resolved silently in code (research.md R1, R4)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T003 must follow T002, and T003a must follow T003 — `import-linter` errors on a contract naming a module that does not exist
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story**
- **US1 (Phase 3)**: depends on Foundational. No dependency on other stories
- **US2 (Phase 4)**: depends on Foundational and on T027 (`ground()`) from US1 — the fuzzy tier is wired in behind the exact tier
- **US3 (Phase 5)**: depends on US1. Independent of US2, except T044, which reads better once both tiers exist
- **US4 (Phase 6)**: depends on US1 for identity and on US2 for the alternatives-stability property
- **Polish (Phase 7)**: depends on all desired stories

### Within Each Story

- Tests are written first and confirmed failing
- `result.py` types before `match.py`, `match.py` before `ground.py`, `ground.py` before `observe.py`
- `offsets.py` before `view.py` — the view builds the map as it folds
- `candidates.py` before the fuzzy tier in `match.py`

### Parallel Opportunities

- T004 and T005 (fixtures) run alongside each other and alongside T001–T003
- T006 and T007 (errors, options) are independent of T008–T011 and of each other
- T012, T013, T014 (the Phase 2 test files) are three different files with no shared state
- All five US1 test tasks (T016–T020) are parallel
- T021, T022, T024 are parallel; T023 depends on T022
- All six US2 test tasks (T030–T033, T033a, T038a) are parallel
- All five US3 test tasks (T039–T042, T043a) are parallel
- T045, T046, T047, T047a are parallel
- T045, T046, T047 are parallel
- T050, T051, T052, T054 are parallel

---

## Parallel Example: User Story 1

```bash
# The five US1 test files, written together and confirmed failing:
Task: "Integration test in tests/integration/test_ground_real_pdf.py"
Task: "Exact-tier unit tests in tests/unit/test_exact_tier.py"
Task: "Geometry-unavailable tests in tests/unit/test_geometry_unavailable.py"
Task: "Immutability tests in tests/unit/test_immutability.py"
Task: "Wrong-document refusal tests in tests/unit/test_wrong_document.py"

# Then the independent US1 types:
Task: "GroundingStatus in src/docdoc/grounding/result.py"
Task: "Alternative and GroundingOutcome in src/docdoc/grounding/result.py"
Task: "Identity derivation in src/docdoc/grounding/identity.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (blocks everything) → 3. Phase 3 US1
4. **Stop and validate**: quickstart Scenarios 1 and 5 pass; the whole suite runs with no credentials

At this point docdoc grounds every value whose claim appears verbatim and honestly ungrounds the rest.
That is a coherent product and the first time the project's central claim is demonstrable end to end.

### Incremental Delivery

1. Setup + Foundational → the match view and offset map are correct and property-tested
2. + US1 → **MVP**: exact grounding to span, page, and box
3. + US2 → recall rises; approximate matches and alternatives appear
4. + US3 → the honesty properties become mechanical
5. + US4 → determinism and identity are checked in CI

### A caution on partial delivery

`grounding_version = "v1"` designates the **complete** algorithm of ADR-0005 — both tiers, the tie-break,
and the alternatives limit. Stopping after US1 is a valid development checkpoint but is **not** a shippable
`v1`: results produced by exact-only grounding and results produced by the full algorithm would carry the
same version while being produced by different algorithms, which is exactly the confusion the version
exists to prevent. If exact-only grounding is ever released, it needs its own version identifier.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task
- Commit after each task or logical group
- The four highest-risk tasks are **T008/T009** (the offset map — a wrong map returns a plausible wrong
  answer), **T034** (the completeness argument — if it is wrong, "not found" stops meaning "not there"),
  **T038b** (group-scoped assignment — scoped too widely it invents locations for legitimately shared
  ranges, scoped away entirely it collapses every line item onto one box), and **T049** (the version
  guard — without it every other guarantee can silently drift)
