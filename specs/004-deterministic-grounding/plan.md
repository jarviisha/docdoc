# Implementation Plan: Deterministic Grounding

**Branch**: `004-deterministic-grounding` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-deterministic-grounding/spec.md`

## Summary

Build docdoc's grounding stage: the code that takes the verbatim claim Milestone 3 recorded for each
extracted value and resolves it to a character range, a page, and a bounding box in the source document —
or states plainly that it could not.

Four pieces of machinery. A **match view** that folds away the typesetting artifacts which make a correct
value look wrong (ligatures, soft hyphens, line-break hyphenation, exotic spaces), paired with an
**offset map** that carries every position back to the untouched source. A **candidate filter** with a
completeness proof, so "we did not find it" means it is not there rather than that we did not look. A
**total tie-break** that makes the winner independent of iteration order, platform, and library version.
And a **provenance record** that pins the algorithm version, the view version, and the threshold into the
stage's artifact identity, so a result produced today can be explained after all three have moved.

The stage is deliberately narrow. It answers *where*, and nothing else. It does not ask whether the value
follows from the text at that range, whether it is plausible, or whether it satisfies a constraint —
Principle VII gives all three to Milestone 5. It does not re-ask the model anything, which is what makes
it the first stage since the kernel with no probabilistic edge at all: no network, no credentials, no
provider, and therefore no test that a contributor has to skip.

**There is no kernel change at this milestone.** `page_for` and `locate` already do what FR-005 and
FR-006 need (research.md R11).

The one thing to know before reading further: `rapidfuzz` is used as a **scorer only**. Every choice the
algorithm makes — which candidates exist, which wins, how ties resolve, what lands in `alternatives` — is
docdoc's own code, because ADR-0005 requires the tie-break to be ours and versioned, and a library's
undocumented internal cannot carry a `grounding_version` (research.md R2).

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestones 1–3)

**Primary Dependencies**: Base install gains its **second** runtime dependency: `rapidfuzz>=3.0`
alongside `pydantic`. Sanctioned explicitly by the constitution's stack line ("`rapidfuzz` in the
extraction layer for grounding") and by ADR-0005 ("`rapidfuzz` enters the base install for anyone using
extraction. It is not a provider SDK"). It is a small pure-wheel package with no transitive dependencies.
No new optional extra. Stdlib used: `unicodedata` (NFKC), `bisect` (offset map lookup), `hashlib`
(view identity), `re` (repeating-group slot keys), `enum` (`GroundingStatus`), `time` (a monotonic clock
read only for the log event's duration, never on the deterministic path), `logging`, `typing`.

The kernel's dependency rule is untouched: `rapidfuzz` was already in the kernel's `import-linter`
forbidden list before this milestone, and `tests/unit/test_kernel_purity.py` must keep passing unedited.
The `pyproject.toml` comment reading "The kernel's only permitted runtime dependency" is updated in this
change to say what it now means — the *kernel* imports `pydantic` alone; the *base install* carries two.

**Storage**: N/A. `Document` + `ExtractionResult` in, `GroundingResult` out. The match view is derived
per run and discarded; its identity is computed and exposed but nothing is persisted or cached
(research.md R9, and the same deferral Milestone 3 made).

**Testing**: `pytest` + `hypothesis`, `mypy --strict`, `ruff`, `import-linter`. Three tiers, **all
offline** (research.md R15): unit and integration over committed documents; property tests over the
offset map and the tie-break; a `perf`-marked tier for SC-020. This feature adds **no provider tier and
no test that skips** — the first milestone since the kernel of which that is true. The repository's suite
still reports 11 skips, all of them Milestone 2's and Milestone 3's live provider tests.

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows. Unlike Milestone 3,
**everything** here must behave identically on all three: there is no model call to excuse a difference.

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals** — enforced by `tests/perf/test_grounding_perf.py` (marked `perf`). Exploratory
measurements from research.md, best-of-N on a contributor laptop:

| Operation | Target | Measured | Basis |
|---|---|---|---|
| Match view + offset map, 50k-character document | < 200 ms | **48.5 ms** | Once per run, not per value (FR-019) |
| Exact tier, 20 values, 50k view | < 100 ms | **49.9 ms** (≈ the view build; matching is a `str.find` loop) | SC-020 first clause |
| Fuzzy tier, 20 values, 50k view, ordinary text | < 500 ms | **52.1 ms** | SC-020 second clause |
| Fuzzy tier, 20 values, adversarial repetitive document | < 500 ms | **328 ms** at the 1,500 budget; **1541 ms** unbounded | research.md R8 — the case the budget exists for |
| Offset-map lookup | negligible | `bisect` over a segment list | Once per resolved value, not per character |

**Measured after implementation, and one number moved the reading.** The match view dominates: at 48.5 ms
for a 50k document it is roughly the whole cost of an ordinary grounding, and the two matching tiers add
1.4 ms and 3.6 ms on top of it. That is the right shape — folding is linear in the document and runs
once, while matching is a `str.find` loop or a bounded scan per value — but it means the ordinary-text
headroom against SC-020 is about **9.6×**, not the 35× the pre-implementation estimate implied. The
estimate had measured matching alone and not counted the view.

The adversarial row is where the budget earns itself: **328 ms bounded against 1541 ms unbounded**, a
4.7× reduction and the difference between meeting SC-020 and missing it by 3×. It clears the 500 ms bound
with 1.5× headroom, which is deliberately the tightest margin in the table — the budget was *derived*
from that bound (GRD-19a), so a comfortable margin there would mean the default was set too low and was
truncating more than it needed to. The cost is a `truncated` flag on the affected values, never a silent
short answer.

Targets sit far above measurements for the reason Milestones 2 and 3 recorded: a perf test that trips on
machine noise gets disabled, and a disabled test protects nothing. What these catch is a match view
rebuilt per value instead of per run — the first row is the one that would move — or a candidate filter
that lost its length bound and went quadratic.

**Milestone 3 shipped a live perf risk that this milestone inherits.** The kernel's whole-document slice
measured 279 ms best-of-5 against a 300 ms budget — 1.07× headroom — and the `perf:` CI job may be red on
a slower runner. Nothing here changes it, and this plan does not adopt it. Recording it so that a red
`perf:` job on this branch is diagnosed rather than assumed to be this feature's doing.

**Constraints**: The entire feature must run with no credentials and no network (FR-048) — not merely
most of it. No clock and no randomness on the grounding path (FR-004, Principle III), which extends to
set iteration order: candidate starts are sorted before scoring and the suite runs under two
`PYTHONHASHSEED` values (research.md R14). No view position may escape into a result, a log, or an error
message (FR-016). Document text, claim text, extracted values, and view text never reach logs (FR-046) —
note this is a **stronger** rule than Milestone 3's, because the view is a new class of derived content
that would leak document text if logged.

**Scale/Scope**: Documents to the extraction layer's input budget; extraction results to a few hundred
values. Eleven modules in one new package, no kernel change, no new fixture format — the committed
PDFs from Milestone 2 and the `echo` adapter from Milestone 3 supply the inputs, plus a small set of
hand-built documents carrying the typesetting artifacts R5 and R7 exist for.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.2.0. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — no kernel change (research.md R11: `page_for` and `locate` already suffice). `rapidfuzz` was already in the kernel's forbidden-imports contract before this milestone; `tests/unit/test_kernel_purity.py` must keep passing **unedited**, which is the check that the new base dependency did not reach the kernel |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every result records document identity, the extraction artifact grounded, `grounding_version`, `match_view_version`, the threshold, and the grounder's id and version (data-model §7). Re-grounding produces a new result; nothing is overwritten (FR-041). The document and the extraction result are read and never modified (FR-007) |
| 3 | **Grounding integrity (II)** | **PASS — this gate is the feature.** Grounding is computed by docdoc from the document and the claim alone; no model contributes to, confirms, or vetoes an outcome (FR-004). `model_confidence` is passed through untouched and routes nothing, tested by grounding one set twice with that value altered (SC-017). Ungrounded stays machine-distinguishable at every layer (FR-034). The three-state model is closed — ambiguity goes to `alternatives`, not to a fourth status (FR-003) |
| 4 | **Determinism (III)** | **PASS** — no clock, no randomness, no network, no provider state anywhere in this feature. The tie-break is total by construction (FR-024), so the winner is order-independent; candidate starts are nonetheless sorted before scoring so the *alternatives* order is too, and the suite runs under two hash seeds (research.md R14). `rapidfuzz` is used only as a pure scorer, so no library internal can move a result under an unchanged `grounding_version` (research.md R2) |
| 5 | **Provider isolation (IV)** | **PASS** — no provider SDK is involved at all. `rapidfuzz` is a scorer, not a provider: no credentials, no network, no service. The base install still pulls zero provider SDKs (SC-021's install check) |
| 6 | **Text-first (V)** | **N/A** — no parsing and no recognition. This feature consumes whatever `Document` ingest produced and cannot re-route it |
| 7 | **Schema-driven (VI)** | **PASS** — grounding is schema-agnostic by construction: it reads claims and produces ranges, and never looks at a field's name, type, or document type. No document-type-specific path is possible here, which the existing SC-014 check continues to enforce across the package |
| 8 | **Validation separation (VII)** | **PASS** — FR-010 forbids this stage from judging whether the value follows from the text at the resolved range. Grounding answers *where*; Milestone 5 answers *whether*. The temptation this gate guards against is real and named in the spec: a value whose claim resolves but whose number disagrees with the text is a **validation** finding, and reporting it here would put a semantic rule in the wrong stage |
| 9 | **No silent fallback (VIII)** | **PASS** — grounding an extraction result against a document it did not come from is refused with both identities named (FR-002), never silently resolved. A value that cannot be resolved is reported ungrounded, never attached to the nearest available range (FR-023). The candidate budget truncates **loudly**: recorded on the value and in the log event (research.md R8) |
| 10 | **Measurability (IX)** | **PASS** — the result carries per-outcome counts so the grounding rate is computable without re-running (FR-035). Absent fields are excluded from the denominator (FR-008), so a correctly reported absence cannot flatter or depress the metric. The *rate itself* is Milestone 6's question and no target is claimed here — this milestone makes it measurable |
| 11 | **Layer direction (X)** | **PASS with a refinement recorded** — `docdoc.grounding` is added to the layers contract **above** `docdoc.extraction`. Principle X's chain does not name grounding; ADR-0003's chain does, unambiguously, and is finer-grained and consistent with it. See design decision 1 |
| 12 | **MVP discipline (XI)** | **PASS** — nothing from the Deferred Technology list. No cache, no persistence, no vector store, no embedding matcher, no EditMap over `Document.text`. The candidate slack is *derived* rather than introduced as a tunable (research.md R4), and separate ligature and NBSP rules were **not** written because NFKC already performs them (research.md R5) — two abstractions removed by measurement rather than added by anticipation |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or kernel operation semantics change, so the Milestone 1 property suite applies unchanged and must stay green. New property tests cover the offset map and the tie-break (research.md R15), which ADR-0006 names as warranting "the strongest tests outside the kernel itself" |
| 14 | **Open decisions** | **PASS** — the two BLOCKING decisions gating this milestone, `FUZZY_GROUNDING_SPEC` and `NORMALIZATION_VS_GROUNDING`, were resolved by ADR-0005 and ADR-0006 on 2026-08-14 and are **followed, not reinterpreted**. Three points where the ADRs underdetermine an implementation are surfaced as design decisions below rather than settled silently in code, per the constitution's precedence rule. `TODO(GOLDEN_DATASET_LICENSING)` gates Milestone 6 and `TODO(PRE_1_0_VERSIONING)` gates first release; neither is touched |

### Design decisions that refine the spec and the ADRs

Recorded so reviewers see them here rather than discovering them in code. None is a constitution
violation; three depart from an ADR's literal text and say so.

1. **Grounding is its own package and its own layer, not `extraction/grounding.py`.** ADR-0005 names
   that path. Its binding decision, per its own title, is that fuzzy matching lives *outside the kernel*;
   the module path is the illustration. Three things make the illustration wrong now: the dependency
   direction on `extraction` becomes unenforceable when both are one layer, ADR-0003 already treats
   grounding as a separate stage with its own processor identity, and it is seven concerns rather than
   one function (research.md R1). **Recommended follow-up**: fold this into ADR-0005 as a clarifying
   amendment if accepted at review.

2. **The candidate slack is derived from the threshold, not chosen.** The spec's checklist carried
   "choose and justify the slack" into this phase as an open constant. It is not open:
   `k = floor((1 - t) · m / t)` falls out of the scorer's own definition, verified exactly for claim
   lengths 1–59. A larger slack generates only provably-below-threshold candidates; a smaller one breaks
   the completeness proof. Measured impact of getting this wrong: **1373 ms versus 53 ms** for one value
   (research.md R4). ADR-0005's "± a bounded slack" is satisfied by a bound it did not have to name.

3. **Two of ADR-0006's six transformations are performed by NFKC and are not implemented separately.**
   Measured: NFKC expands ligatures and folds non-breaking, narrow, and figure spaces. Writing a separate
   ligature table after NFKC has already expanded them would be dead code a reader would reasonably
   trust. Soft-hyphen removal is *not* covered by NFKC — verified — so it stays explicit. Four rules
   produce all six effects (research.md R5).

4. **De-hyphenation joins only lowercase-to-lowercase, and the residual case is razor-thin.** Both
   obvious rules are measurably wrong: always de-hyphenating scores `INV-2024-001` at 0.833 against its
   own document, never de-hyphenating scores `amount` at 0.857 — both below threshold, neither rescued by
   the fuzzy tier. The case-based rule separates identifiers from justified line breaks without a
   dictionary. It leaves one loss: a genuine compound word broken at a line end scores **exactly 0.900**,
   clearing the threshold by nothing. **This constrains Milestone 6**: raising the threshold above 0.90
   breaks that case, so the tuning must measure it deliberately (research.md R7).

5. **Dash folding is a known gap that this milestone does not close.** NFKC maps U+2011 to U+2010 but
   neither to ASCII `-`, so a document typeset with U+2010 and a model quoting ASCII will miss the exact
   tier and fall to fuzzy. Fixing it means adding a seventh transformation to a version ADR-0006 pinned,
   which is the "resolve it silently in code" the constitution forbids. Recorded as a `v2` candidate to
   be decided with Milestone 6's measurements (research.md R6).

6. **`rapidfuzz` is a scorer and nothing more.** `fuzz.partial_ratio_alignment` would do candidate
   location in C++ at 0.22 ms over a 50k view, and is rejected: it returns one alignment where
   `alternatives` needs several, and its behaviour on ties is an undocumented internal that could move
   every result while `grounding_version` stayed at `v1`. `process.cdist` is rejected because it requires
   numpy, which is not in the sanctioned stack (verified: `ModuleNotFoundError`). The consequence is that
   candidate generation is docdoc's own code, which is the point (research.md R2, R3).

7. **The round-trip invariant is stated as containment, not identity.** ADR-0006 asserts "round-tripping
   a source span through the view and back is the identity". Taken literally that is unsatisfiable for a
   range whose boundary falls inside a character the view deletes — a soft hyphen has no view position to
   return from. FR-017 and R10 state it as identity where both boundaries survive and containment
   otherwise, and fix the direction: a round trip may **widen** a range, never narrow or move it. Widening
   yields a slightly large box; narrowing yields a box that omits part of the value, which is the
   confidently-wrong failure ADR-0006 itself warns is this component's signature.

8. **The candidate budget truncates loudly.** An adversarial document produces 139 ms for a single value,
   so an unbounded scan is a denial-of-service surface in a library that will run behind an API. The
   budget caps candidate starts, still resolves from what was examined, and records the truncation on the
   value and in the log event. A silent cap would be worst precisely here, because the case is rare
   enough that nobody would notice it. **Its default is derived, not chosen**: 1,500 starts per value,
   from SC-020's 500 ms across 20 values at the measured 72 starts/ms. An earlier draft said 20,000,
   which at that rate is ~278 ms for one value and would never have fired on the very input it exists
   for — a backstop sized so it can never trip (research.md R8, GRD-19a).

9. **Identical claims in a repeating group get group-scoped uniqueness.** Resolving each value
   independently makes two line items both claiming `Widget` land on the same range, because the
   tie-break picks the earliest for both — every line item pointing at the first one's box, a wrong audit
   trail that looks well-formed. Assignment is greedy in entry order, with each entry excluding ranges
   earlier entries took. **Scoped to one repeating group at one field path, never global**: an invoice
   date read as both issue date and due date must resolve to the one range it occupies, and global
   uniqueness would force the second to invent a location. This was found by `/speckit-analyze` — FR-029
   was in the spec and in no other artifact, and the algorithm as designed could not satisfy it
   (research.md R16).

## Project Structure

### Documentation (this feature)

```text
specs/004-deterministic-grounding/
├── plan.md              # This file
├── spec.md              # Feature specification (48 FR, 22 SC)
├── research.md          # Phase 0 output — 15 resolved decisions
├── data-model.md        # Phase 1 output — entities, GRD-1…GRD-20, error model
├── quickstart.md        # Phase 1 output — setup and 5 validation scenarios
├── contracts/
│   └── grounding-api.md   # Phase 1 output — public API contract
├── checklists/
│   └── requirements.md    # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

Single-project Python library. Only the paths below are created or touched; sibling packages
(`transform/`, `pipeline/`, `api/`) arrive in later milestones.

```text
pyproject.toml               # + rapidfuzz base dep, + grounding in the layers contract,
                             #   + the corrected comment on what "base install" now means
uv.lock                      # regenerated and committed

src/docdoc/
├── kernel/                  # UNCHANGED at this milestone (research.md R11)
├── ingest/                  # UNCHANGED
├── extraction/              # UNCHANGED — read from, never modified
└── grounding/               # NEW LAYER, above extraction (research.md R1)
    ├── __init__.py          # the public surface in contracts/grounding-api.md
    ├── errors.py            # GroundingError — the constitution's named type, finally used
    ├── view.py              # MatchView: NFKC, soft hyphen, de-hyphenation, whitespace (R5, R7)
    ├── offsets.py           # OffsetMap: segment list + bisect; view→source, outward (R9, R10)
    ├── candidates.py        # the pigeonhole filter and the derived k (R3, R4)
    ├── match.py             # exact tier, fuzzy tier, the total tie-break, alternatives (R2)
    ├── options.py           # GroundingOptions — threshold and candidate budget
    ├── identity.py          # GROUNDER_ID, options hash, grounding artifact id (ADR-0003)
    ├── result.py            # GroundingOutcome, GroundingResult, GroundingProvenance, counts
    ├── observe.py           # the single `grounding.ground` structured event
    └── ground.py            # ground() — the entry point that composes the above

tests/
├── unit/
│   ├── test_match_view.py             # GRD-1…GRD-5: the four rules, and NFKC doing two of six
│   ├── test_dehyphenation.py          # GRD-6: the case rule, incl. the 0.900 residual (R7)
│   ├── test_candidates.py             # GRD-10, GRD-11: derived k, completeness, the budget
│   ├── test_repeating_group_uniqueness.py  # GRD-13a: group-scoped assignment (FR-029)
│   ├── test_exact_tier.py             # GRD-12: exact wins, other occurrences become alternatives
│   ├── test_fuzzy_tier.py             # GRD-13, GRD-14: threshold boundary, score semantics
│   ├── test_tiebreak.py               # GRD-15: total order, incl. the all-three-tie case
│   ├── test_absent_and_claimless.py   # GRD-16: FR-008 vs FR-009 — the two are not the same
│   ├── test_grounding_edge_cases.py   # the spec's enumerated boundaries (Phase 8)
│   ├── test_no_validation_judgment.py # FR-010: answers *where*, never *whether*
│   ├── test_wrong_document.py         # GRD-17: FR-002, refusal naming both identities
│   ├── test_grounding_identity.py     # GRD-18…GRD-20: what moves the artifact id and what must not
│   ├── test_geometry_unavailable.py   # FR-006: grounded without boxes, CapabilityError absorbed
│   ├── test_model_confidence_inert.py # SC-017: alter it, outcomes identical
│   ├── test_no_view_offsets_escape.py # FR-016: SC-004's automated check over results, logs, errors
│   ├── test_grounding_observe.py      # FR-047 event schema + content-leak assertion (SC-019)
│   ├── test_grounding_boundaries.py   # layer direction: grounding may import extraction, not vice versa
│   ├── test_immutability.py           # FR-007: document, text, and extraction result unchanged
│   └── test_plan_tree_is_current.py   # EXTENDED — the Milestone 3 check now covers this tree too
├── property/
│   ├── test_offset_map_properties.py  # GRD-7…GRD-9 + the four map invariants (R15) —
│   │                                  #   the highest-risk component in the feature
│   └── test_tiebreak_properties.py    # exactly one winner for any candidate set, any hash seed
├── integration/
│   ├── test_ground_real_pdf.py        # end to end over the Milestone 2 fixtures
│   ├── test_match_view_lift.py        # SC-007: the exact-tier lift the view earns, reported
│   └── test_examples_run.py           # EXTENDED — the new example is executed, not read
├── perf/
│   └── test_grounding_perf.py         # marked `perf`, SC-020, incl. the adversarial row
└── fixtures/
    └── grounding/                     # documents carrying ligatures, soft hyphens, line-break
                                       #   hyphenation, exotic spaces, and a repetitive adversarial one

examples/
└── ground_invoice.py        # SC-022: extracted value → page and box, no credentials
```

**Existing files this feature edits rather than creates.** The tree above lists what is new. Seven
existing files are also touched and are deliberately not shown as tree entries, because listing them
would imply this milestone owns them: `README.md` and `docs/concepts/extraction.md` (text describing
grounding as forthcoming), `docs/concepts/grounding.md` (new, but in an existing directory),
`docs/adr/0005-fuzzy-grounding-specification.md` (the clarifying amendment of design decision 1),
`CHANGELOG.md`, and `.github/workflows/ci.yml` (the two-hash-seed run). Named here so T053's plan-tree
check does not read them as drift.

**`tests/fixtures/make_fixtures.py` is deliberately not among them**, though an earlier revision of this
list and T004 both said otherwise. That script exists to regenerate *binary* fixtures — PDFs and a PNG —
so a reviewer can see what a committed blob contains without opening it. The grounding fixtures are
Python string constants: a ligature, a soft hyphen, a line-break hyphen. There is nothing to regenerate
and nothing hidden from review, so a builder for them would be ceremony. Recorded rather than quietly
dropped, because a plan naming a file it never touched is the same defect as a test tree naming a file
that does not exist.

**On enumerating test files here.** Milestone 3's plan recorded that this list went stale three times and
added `tests/unit/test_plan_tree_is_current.py` to check it mechanically. That check is *extended* rather
than duplicated: it already derives the rule from "test files importing the package under test", so it
covers `docdoc.grounding` once the package name is added to its scope. The meta-test category it defines
— tests that check a property of the repository and therefore cannot import the package — gains no new
members here.

**Structure Decision**: `src/` layout unchanged. `grounding/` is one flat package with one module per
concern, mirroring `extraction/`'s shape so a reader who has understood that layer already knows where to
look. There is no `adapters/` sub-package and there will not be one: this layer has no provider to adapt,
which is the structural expression of the fact that grounding is deterministic all the way down.

The split between `view.py`, `offsets.py`, `candidates.py`, and `match.py` is not decomposition for its
own sake. Each is separately property-testable, and ADR-0006 requires the offset map specifically to
carry the strongest tests outside the kernel — which is far easier to honour when it is a type with its
own module than when it is a local variable inside a matching function.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No gate fails. Four items are recorded not as violations but because a reviewer would reasonably ask why
they exist.

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| A new top-level package and a new entry in the layers contract | ADR-0003 makes grounding a distinct stage with its own processor identity and options hash, and Principle X requires layer discipline to be machine-checked rather than conventional | Putting it in `docdoc.extraction` as ADR-0005's text literally says. Rejected: `import-linter` cannot express "grounding imports extraction but not the reverse" when both are one layer, so the direction would rest on review discipline — which is the thing Principle X specifically says must be mechanical (research.md R1) |
| A second runtime dependency in the base install | The fuzzy tier needs a real edit-distance implementation, and a pure-Python one is 100–1000× slower on the one component where a compiled implementation carries no design risk — a distance function makes no choices | Pure-Python Levenshtein, or `difflib`. `difflib` is rejected on correctness, not speed: `SequenceMatcher` computes a different similarity than the one ADR-0005 pinned, and its "autojunk" heuristic makes the result depend on the input's character frequencies (research.md R2) |
| A candidate budget that truncates rather than failing | A pathological document costs 139 ms for a single value, so an unbounded scan is a denial-of-service surface in a library that will eventually run behind an API | No budget, or a budget that raises. No budget is rejected on the measurement. Raising is rejected because an over-budget value has good candidates in hand, and refusing to report the best of them serves nobody — truncating and *saying so* on the value and in the log gives the caller both the answer and the caveat (research.md R8) |
| Property tests concentrated on the offset map rather than spread evenly | ADR-0006 names it "the highest-risk component in the grounding path" and warns that an incorrect map produces "confidently wrong bounding boxes — grounded-looking values pointing at the wrong place" | Uniform test coverage across the package. Rejected: every other component in this feature fails loudly. The offset map is the only one whose failure mode is a plausible, well-formed, wrong answer, and no other test in this repository would catch it |
