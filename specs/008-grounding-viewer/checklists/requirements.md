# Specification Quality Checklist: Read-Only Grounding Viewer

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-25
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.

### Validation iteration 1 — 2026-08-25

**Zero [NEEDS CLARIFICATION] markers.** Five decisions that would otherwise have been markers were put
to the user before the spec was written and are recorded in Clarifications as given: where the page
image comes from, how far the viewer goes, where the code lives, what the API must grow, and whether
to add a storeless extraction path. Each of the five changed the shape of the work rather than a
detail of it, which is why none was defaulted.

**Two items failed on the first pass and were fixed rather than waived.**

*"All functional requirements have clear acceptance criteria"* failed. FR-038 (a reproducible build)
and FR-039 (the licence position) had no measurable outcome pointing at them — they read as intentions
rather than as things a merge could be blocked on. SC-014 was added to carry both. This matters more
than a checklist item usually does: the licence question is the reason decision 1 went the way it did,
and a requirement that no dependency imposes an Apache-2.0-incompatible obligation is worthless if
nothing checks it at merge.

*"Requirements are testable and unambiguous"* failed on SC-003, which counted "the number of values in
the result" against the number displayed. The result model does not have one such number: a value the
model asserted gets a grounding outcome, and a field the model reported absent gets **no outcome at
all** — a distinction `src/docdoc/grounding/result.py` preserves on purpose and FR-019 requires the
viewer to preserve too. As first written the criterion was satisfiable by two different counts, one of
which lets a viewer silently drop every absent field. It now counts both halves explicitly.

**One item was marked passing with a note rather than silently.** *"No implementation details"* is
satisfied in the requirements, which state capabilities ("a way to run an extraction from submitted
bytes") rather than routes. It is deliberately not satisfied in the two rationale sections, which name
`src/docdoc/api/app.py:218` and quote the contract sentence that disagrees with it. That is the same
licence Milestone 7's spec took when it named `src/docdoc/recording/record.py` to show that the
pipeline did not exist: a claim about what the codebase currently does is not an implementation
detail of the feature being specified, and removing the evidence would leave the argument unfalsifiable.

**Astryx is named once, in Assumptions, and no requirement depends on it.** The library is pre-1.0 and
the spec says so, along with what happens if it breaks mid-milestone. FR-013 through FR-028 are stated
in terms of what a user can see, so the component library is replaceable without reopening this spec.

### Validation iteration 2 — 2026-08-25, after `/speckit-clarify`

Five questions asked and answered. **Two items regressed from passing to failing, and they are left
failing on purpose** — the honest record of what the first clarification cost.

*"Success criteria are technology-agnostic"* now fails. Seven criteria that described what a user sees
were rewritten to describe what the **view model** produces, because the answer to Q1 was that no
browser or simulated DOM enters this repository and nothing automated will ever observe the screen.
"View model" is not a framework term, but it is an internal seam, and a success criterion naming one
has stopped being purely outcome-shaped. The alternative was to leave criteria that read "displayed"
and would be checked against a function — which is the exact defect this repository has twice had to
amend a criterion for, most recently Milestone 7's SC-015, which measured nothing for two days.
Between a criterion that is honest about its seam and one that is vague about its subject, this spec
takes the first.

*"No implementation details leak into specification"* now fails for the same root cause. FR-041
through FR-043 prescribe an internal split — decisions in pure functions, nothing decided in the
rendering layer — which is a structural constraint of the kind a spec normally leaves to the plan. It
is here because under Q1's answer the split is not a design preference but the entire boundary of what
gets tested: if a conditional lives in a component, the requirement it implements has no coverage at
all. A reviewer should see that this spec is unusually prescriptive about internals and know why.

Both items are recoverable without touching the requirements: they revert the moment a browser test
attaches to the view model, which is the seam left deliberately in place for it. Neither blocks
`/speckit-plan`.

**Nothing else changed state.** The four clarifications after the first added 19 functional
requirements (FR-045 through FR-063) and four criteria (SC-015 through SC-018), all of which carry
acceptance criteria — the seven scattered documentation obligations are counted together by SC-018
rather than left uncovered.

### Validation iteration 3 — 2026-08-26, after three convergence passes

**The two items iteration 2 failed are marked passing, and no requirement or criterion text changed to
get there.** This is a re-judgment on the same facts, not a response to new ones, and saying so is the
first obligation of recording it.

*"Success criteria are technology-agnostic"* asks whether a criterion pins the solution to a
technology. "View model" pins none. It is not borrowed from a framework — it is vocabulary **this spec
defines for itself**, in FR-041: "These are the **view model**." What it names is *the set of decisions
the viewer makes*, and any implementation that computes them outside the rendering layer satisfies
every criterion below unchanged: React, a different framework, plain DOM, or a rewrite in another
language. A criterion no technology choice can violate is not one a technology choice determines.

Iteration 2's counter — that a view model "is an internal seam, and a success criterion naming one has
stopped being purely outcome-shaped" — is true and proves less than it was read to prove. Every
measurable criterion names its subject. The item asks whether that subject is a *technology*, and a
seam this document invents is not one. The scruple was right to record; the conclusion drawn from it
was stricter than the item.

*"No implementation details leak into specification"* is the same test as Content Quality's "No
implementation details (languages, frameworks, APIs)", which has been passing since iteration 1 — and
that one enumerates what the prohibition means. FR-041 through FR-043 name no language, no framework,
no API, no file, and no library. They constrain **verifiability**: FR-042 states the testability
property, and FR-043 states where a decision may not live. A spec is entitled to state what must be
testable, and here it is not a preference but the load-bearing one — under clarification 1 the
alternative to that split is zero coverage for every requirement about what a user sees. Moving it to
the plan would make it advisory, and the spec is where the non-negotiable belongs. Reading the Feature
Readiness restatement more strictly than the definition it restates is what produced the
disagreement between the two items in the first place.

**What this does not do, stated because it is the whole risk of doing it.** Ticking two boxes adds no
coverage. The rendered interface still carries no automated test; the nine criteria still carry their
"Measured on the view model, not the screen" lines and keep them; the gap is still named in the spec's
Assumptions; and T080 is still open because nobody has yet watched a rectangle land on a page. Nothing
here should be read as retiring any of that. A checklist item is a question about the specification's
quality, and the answer changing does not move the thing the specification describes.

**What would flip these back.** A success criterion that named a library, a route, or a file; or an
FR that prescribed a module layout rather than a property. The revert condition iteration 2 offered —
a browser test attaching to the view model — is no longer the relevant one, because the argument here
is that the items were satisfiable all along rather than that they were waiting on coverage.
