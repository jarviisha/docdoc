# Caching & Identity Requirements Quality Checklist: Milestone 7

**Purpose**: Unit-test the *requirements* governing artifact reuse and identity — are they complete,
unambiguous, mutually consistent, and objectively verifiable — before `/speckit-tasks` turns them
into work.
**Created**: 2026-08-22
**Feature**: [spec.md](../spec.md) · [research.md](../research.md) · [contracts/pipeline-api.md](../contracts/pipeline-api.md)

**Depth**: formal gate. This must pass before tasks are generated.

**Status**: **complete, 2026-08-22.** 36 items, all resolved. 8 new requirements (FR-058…FR-065),
18 requirements or criteria amended, 4 assumptions added, 1 terminology table added. Resolutions are
recorded inline below.

**Why this domain**: reuse is the milestone's only genuinely new risk. Every other area has a safety
net that fires without anyone reading prose — `import-linter` for boundaries, contract tests for the
two interfaces, the existing `observe.py` prohibitions for leakage. A caching rule that is merely
*vague* produces a wrong result that is stored, returned, and indistinguishable from a right one.

---

## Requirement Completeness

- [x] CHK001 Are requirements defined for what happens when the same `artifact_id` is written twice with *different* payloads — the observable symptom of a processor whose output moved without a version bump? [Gap, Spec §FR-011]
      → **Added FR-062.** Same content is a no-op; different content raises, naming both, and never overwrites. This is the one place the system can detect the failure ADR-0003 hands to human review.
- [x] CHK002 Are requirements defined for concurrent writes of the same artifact by two processes? The scenario appears in Edge Cases but no FR states the outcome. [Gap, Spec §Edge Cases]
      → **FR-062, second clause.** Concurrent writes of identical content both succeed; no lock, lease, or coordinator. Atomic replacement of an immutable content-addressed entry is what makes the race benign.
- [x] CHK003 Is the degraded mode — store root unwritable, absent, or full — stated as a *requirement*, or does it exist only in research R5 and the contract? [Gap, Research §R5, Contract §6]
      → **Added FR-063.** It was in research and the contract only; now a requirement.
- [x] CHK004 Is there a requirement that each stage's `options_hash` fold **exactly** the inputs ADR-0003's table names, including its Milestone 5 amendment? Reuse correctness rests entirely on this and no FR states it. [Gap, ADR-0003]
      → **Added FR-058**, including the obligation to assert the folded set per stage by test. Under-folding causes stale reuse; over-folding destroys reuse. Neither is visible in any output.
- [x] CHK005 Is there a requirement that wall-clock durations, `request_id`, and other correlation-only values MUST NOT enter any identity? The contract asserts it; the spec does not. [Gap, Contract §1]
      → **Added FR-060**, framed on the separation `ingest.options` already makes between what a parse produces and how it talks to a service.
- [x] CHK006 Are requirements defined for verifying a suspect cache — recomputing a stage and comparing against the stored artifact — given that CHK001's failure mode is undetectable by design? [Gap, Spec §FR-019]
      → **Added FR-064**: a mode that executes every stage and still writes, so FR-062 fires on results that would otherwise have been read back. Surfaced in the CLI as `--verify-cache`.
- [x] CHK007 Does FR-044 cover **blobs** as well as artifacts? It reasons about artifacts containing extracted values; a blob is the entire source document, which is strictly more sensitive. [Completeness, Spec §FR-044, §FR-021]
      → **FR-044 rewritten** to name both, and to require both be created readable only by the owning account. The blob was the bigger exposure and the one the original wording missed.
- [x] CHK008 Is the default location of the store root specified, or only constrained negatively ("not shared or world-readable")? [Gap, Spec §FR-044, Research §Open questions]
      → **FR-017 now says there is no default and the store is off unless configured.** This closes the research open question by removing it: a store exists because an operator asked for one and said where.
- [x] CHK009 Are requirements defined for what a run reports when a stage was reused but the store write of a *later* stage failed? [Gap, Coverage]
      → **FR-063, second clause.** A write failure never fails a run whose stages succeeded; the stage reports `EXECUTED`.

## Requirement Clarity

- [x] CHK010 Is "incompatible" defined for `artifact_format_version` — does adding an optional field to a stored model make prior artifacts incompatible? [Ambiguity, Research §R6, Spec §FR-015]
      → **Defined in FR-015**: a field removed, renamed, or retyped, or a new field whose absence is not answerable from what was stored. Adding a field with a never-load-bearing default is compatible and must not move the version.
- [x] CHK011 Does FR-015 say whether an incompatible artifact is a **miss** or an **error**? Research R5 answers it; the requirement itself is silent, and the two readings differ by whether an upgrade breaks every run. [Ambiguity, Spec §FR-015, Research §R5]
      → **FR-015 now says miss, not error**, with the reason attached.
- [x] CHK012 Is "or a subset of it" in FR-019 quantified? The contract offers exactly one subset — by stage — while the requirement implies an open set. [Clarity, Spec §FR-019, Contract §5]
      → **FR-019 fixed at two subsets**: all of it, or one stage. No query language.
- [x] CHK013 Is "bounded" quantified for the match-view cache, or is the bound left to implementation? An unbounded cache over a 48-document sweep is a memory profile nobody specified. [Clarity, Research §R11, Spec §FR-020]
      → **FR-020 now requires a maximum entry count with LRU eviction**, configurable, default documented. Research R11 updated to match.
- [x] CHK014 Does FR-012's phrase "from its recorded inputs" hold for the *first* run of a document, where nothing has been recorded yet? [Ambiguity, Spec §FR-012]
      → **Reworded**: "the inputs ADR-0003 defines for it — which are known before the stage runs, not read back from a previous run."
- [x] CHK015 Is "invalidate" in FR-013 defined against an append-only store that deletes nothing — does it mean "not reused", or "removed"? [Ambiguity, Spec §FR-013, §FR-011]
      → **FR-013 reframed**: a changed input gives the stage a *different identity*, so invalidation is a consequence of a new id rather than an act performed on an old one. Nothing is deleted or marked stale.
- [x] CHK016 Is the vocabulary consistent across documents: "artifact identity", "artifact id", `processing_id`, "job id", "terminal artifact id"? Four names appear for what is sometimes one value. [Clarity, Spec §FR-007, Contract §3]
      → **Added a terminology table** to the spec fixing all four, including that job id is not a separate value.
- [x] CHK017 Is it stated who owns the obligation to bump `processor_version`, and what evidence a reviewer checks it against? ADR-0003 assigns it to review; no requirement here restates it. [Clarity, ADR-0003, Spec §FR-002]
      → **FR-002 gains a sub-clause**: the obligation stays with review, and now has a symptom (FR-062) and a way to provoke it (FR-064). A reviewer of any change to a stage's output must state whether the version moved.

## Requirement Consistency

- [x] CHK018 Do FR-023 (explain **any** identity the system produced) and FR-017 (the store is optional) conflict? A run with no store produces identities whose envelopes were never written, and `derivation()` reads envelopes. [Conflict, Spec §FR-023, §FR-017]
      → **Real conflict; FR-023 narrowed** to identities held in a store, with the no-store case required to say so plainly rather than reconstruct a derivation. Contract §7 updated.
- [x] CHK019 Does SC-002's "byte-identical" conflict with `StageOutcome.duration_ms`, which necessarily differs between two runs? The quickstart works around it by deleting the outcomes block — a workaround the criterion does not authorise. [Conflict, Spec §SC-002, Data model §StageOutcome]
      → **Real conflict; SC-002 rewritten** to name the two fields permitted to differ. A criterion that is false as written gets satisfied by deleting the inconvenient field, which is precisely what the quickstart had started doing.
- [x] CHK020 Are the store's miss-versus-raise rules stated identically in spec, research R5, and contract §6, or does one of the three carry a variant a reader could act on? [Consistency, Spec §FR-014, §FR-015]
      → **Reconciled.** All three now carry the same table, extended with the three FR-062/FR-063 rows and cross-referenced by FR number.
- [x] CHK021 Is FR-020's match-view cache consistent with FR-012's per-stage reuse — when the grounding artifact is reused, is the view cache reached at all? [Consistency, Spec §FR-020, §FR-012]
      → **FR-020 now states it**: when the grounding artifact is reused the view is never built, so the cache serves only the case artifact reuse does not — several extractions grounding against one document in one process.
- [x] CHK022 Do the two `Stage` enums — the pipeline's and `docdoc.evaluation`'s — have a stated relationship, or is the duplication only explained in the data model? [Consistency, Data model §Stage]
      → **Closed as intentionally design-level.** The data model explains why they stay separate: merging them moves `prediction_set_id` and invalidates the committed public tier for no gain. Not promoted to a requirement, because it constrains implementation rather than behaviour.
- [x] CHK023 Does FR-013's partial-reuse rule agree with ADR-0008 about which schema edits move which identity, or does it restate it in weaker words? [Consistency, Spec §FR-013, ADR-0008]
      → **FR-013 now defers explicitly** and forbids restating ADR-0008 in weaker words.

## Acceptance Criteria Quality

- [x] CHK024 Can FR-022 — "MUST NOT be designed in a way that makes adding garbage collection a breaking change" — be objectively verified, or is it an unfalsifiable aspiration? [Measurability, Spec §FR-022]
      → **It could not.** FR-022 rewritten to one checkable obligation: every artifact records its stage and its input identity, so reachability from a set of roots is computable by walking the store alone. That is all a collector needs.
- [x] CHK025 Is SC-003's "every stage from extraction onward is executed exactly once" measurable without an instrument the spec never requires — a per-stage execution counter? [Measurability, Spec §SC-003, §FR-047]
      → **SC-003 now names FR-047's counters** as the instrument, and rules out inferring from timings.
- [x] CHK026 Does SC-006 ("terminal identity recomputable from the inputs the run recorded") state *which* recorded fields must suffice, so the criterion can fail rather than be argued? [Measurability, Spec §SC-006]
      → **SC-006 now names them**: `RunProvenance` plus the per-stage processor identities, versions, and options hashes — and nothing else. A recomputation needing anything more fails the criterion rather than the test.
- [x] CHK027 Is SC-005's "returned as reusable results in 0% of attempts" paired with a defined population of attempts, or is the denominator implicit? [Measurability, Spec §SC-005]
      → **SC-005 now defines the fixture**: one corrupt artifact, one under an incompatible format version, and one conflicting write per stage, with the expected outcome stated for each.
- [x] CHK028 Are the reuse criteria stated as counts rather than timings throughout, so that a slow machine cannot fail them and a fast one cannot pass them falsely? [Acceptance Criteria, Spec §SC-002, §SC-003]
      → **Held already**, and now explicitly in SC-003. No timing threshold appears anywhere in the criteria.

## Scenario & Edge Case Coverage

- [x] CHK029 Are requirements defined for a store shared between two docdoc versions running concurrently, beyond the format-version rule? [Coverage, Spec §Edge Cases]
      → **Added as an assumption**: safe when artifact-format and processor versions agree, which is what those versions are for — and FR-062 is what catches the case where the assumption was wrong.
- [x] CHK030 Are recovery requirements defined — after `ArtifactError` on a corrupt artifact, what is the supported path back to a correct result? FR-019's clear is the presumed answer but is not connected to it. [Recovery, Gap, Spec §FR-014, §FR-019]
      → **FR-019 now names it**: cleared deliberately by a human and recomputed, never overwritten by the run that found the fault.
- [x] CHK031 Are requirements defined for a golden-set evaluation run *with* the store enabled, where a stale artifact would silently change a published metric? SC-015 requires that run; no requirement governs its reproducibility. [Coverage, Spec §SC-015]
      → **Added as an assumption**: the recorder runs with no store by default, so a committed prediction set is always the product of full execution. Chosen over adding a field to `DocumentPrediction`, which would move `prediction_set_id` and break SC-014's byte-identical requirement.
- [x] CHK032 Is the parse-stage lookup point — after routing, before the parser — stated in a requirement, or only in research R2 and the contract? It is the design's load-bearing decision and gate 6 depends on it. [Gap, Research §R2, Contract §3]
      → **Added FR-061**, including that a cached document must not arrive carrying a routing decision this run did not make.
- [x] CHK033 Are requirements defined for reuse when the *adapter* is unavailable — a cached extraction exists, but no credentials are configured? [Coverage, Gap]
      → **Added FR-059**, generalised: computing an identity must never need credentials or a network; only executing may. A fully-reused run therefore succeeds with no credentials at all.
- [x] CHK034 Is the assumption that `content_id_for` and `canonical_json` are stable across docdoc versions stated? Every stored artifact's integrity check depends on it, and `IDENTITY_SCHEMA_VERSION` exists precisely because that derivation can change. [Assumption, Research §R4]
      → **Added as an assumption**, naming `IDENTITY_SCHEMA_VERSION` as the kernel's existing mechanism for the event, and FR-015's format version as what absorbs it.
- [x] CHK035 Is the dependency on ADR-0003's *proposed* Milestone 5 amendment recorded as a dependency on something not yet accepted? The validate stage's `options_hash` inputs come from an amendment still marked proposed. [Assumption, ADR-0003 §Amendment]
      → **Added FR-065.** The amendment must be accepted or superseded in this milestone. FR-058 requires folding exactly what it names, so the design depends on a decision the record says was never made — the implicit resolution the constitution's precedence rule forbids.
- [x] CHK036 Is the assumption that artifacts are small enough to hold in memory and serialise whole stated anywhere, given that a parsed `Document` carries every token and its geometry? [Assumption, Gap]
      → **Added as an assumption**, with FR-039's size limits named as what keeps it true.

---

## Notes

- Every item is closed. Two closed as decisions rather than edits (CHK022 design-level, CHK028
  already held); the other 34 produced a change to `spec.md`, and eight of those propagated to
  `research.md`, `data-model.md`, `contracts/pipeline-api.md`, or `contracts/cli.md`.
- The three flagged in advance as most likely to need a spec edit — CHK001, CHK018, CHK019 — all
  did, and two of them were genuine contradictions rather than omissions.
- Requirement numbering is append-only: FR-058…FR-065 sit beside the requirements they refine, so
  every pre-existing number still points at the same sentence.
