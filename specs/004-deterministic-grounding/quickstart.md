# Quickstart & Validation: Deterministic Grounding

**Feature**: `004-deterministic-grounding` | **Date**: 2026-08-18 | **Plan**: [plan.md](plan.md)

How to run this feature and how to convince yourself it works. Types and semantics are in
[data-model.md](data-model.md); the public surface is in [contracts/grounding-api.md](contracts/grounding-api.md).

**No credentials and no network are needed for anything on this page** — including the end-to-end
scenario. Grounding has no provider tier at all, so none of *its* tests skip for want of one, and that
is FR-048 rather than a convenience.

## Setup

```bash
uv sync --all-extras                # see the note below on why --all-extras
uv run pytest                       # 1351 passed, 11 skipped
```

`rapidfuzz` arrives with the base install; there is no grounding extra to enable.

**Why `--all-extras` and not `--extra dev --extra pdf`.** Grounding itself needs neither `pdf` nor
`google` — `pdf` only supplies the parser for the committed PDF fixtures. But the *repository's* suite
carries Milestone 3's adapter tests, which import `google-genai`, and syncing without that extra
produces **44 failures** that have nothing to do with this feature. Measured both ways.

**The 11 skips are not grounding's.** They are Milestone 2's Azure and Milestone 3's Gemini *live*
tests, which need credentials and cost money; each states its reason. Every test this milestone added
runs for every contributor.

## The 30-second version

```bash
uv run python examples/ground_invoice.py
```

Builds a typeset document — one carrying an `fi` ligature and a reference broken across a line by a
hyphen — extracts against `invoice@1` with the deterministic `echo` adapter, grounds the result, and
prints each value with the page and box it was found at. Every value resolves at the **exact** tier
despite the model having quoted what a human reads rather than what the bytes say.

---

## Scenario 1 — A value points at a place in the document (US1, SC-001, SC-003)

**Validates**: FR-001, FR-005, FR-007.

```bash
uv run pytest tests/integration/test_ground_real_pdf.py -k PointsAtSource -v
```

**Expected**: every value whose claim appears in the document carries a `span`, a page, and boxes;
`document.text[span.start:span.end]` reads back as the claim; and the document's text is byte-identical
before and after.

The last clause is the one to watch. It is asserted by comparing the text, not by trusting that nothing
wrote to it.

## Scenario 2 — Typesetting does not defeat a correct value (US2, SC-005, SC-007)

**Validates**: FR-012 … FR-018, GRD-2, GRD-3.

```bash
uv run pytest tests/unit/test_match_view.py tests/unit/test_dehyphenation.py -v
```

**Expected**: claims differing from the source only by a ligature, a soft hyphen, a line-break hyphen, a
non-breaking space, or whitespace run length resolve at the **exact** tier, and the ranges returned point
into source text that still contains all of it.

Two results here are worth reading rather than just running:

- The suite asserts that NFKC performs ligature expansion and non-breaking-space folding, and that it
  does **not** touch soft hyphens. That is why there are four transformation rules and not six
  (research.md R5).
- `test_dehyphenation.py` pins the three cases that decided the rule: `INV-2024-001` must survive a line
  break intact, `am-ount` must be joined, and `well-known` scores exactly **0.900** — clearing the
  threshold by nothing at all. If a future change raises the threshold, that test is the one that fails,
  and it is meant to (research.md R7).

## Scenario 3 — The unfindable is reported as unfound (US3, SC-010, SC-016)

**Validates**: FR-023, FR-026, FR-034, FR-045, and the two absences of data-model §5.

```bash
uv run pytest tests/unit/test_fuzzy_tier.py tests/unit/test_absent_and_claimless.py -v
```

**Expected**: a fabricated claim that appears nowhere yields `status='ungrounded'`, `span=None`,
`score=None` — and **no exception**. A value the model reported absent produces no outcome at all and is
counted under `not_applicable`. A value present with no claim is `ungrounded` and counted as such.

The distinction between the last two is the one most likely to be implemented wrong. A correctly reported
absence must not depress the grounding rate; a value asserted without evidence must.

## Scenario 4 — The same inputs give the same answer, anywhere (US4, SC-008)

**Validates**: FR-024, FR-028, and research.md R14.

```bash
PYTHONHASHSEED=0 uv run pytest tests/property/ -v
PYTHONHASHSEED=12345 uv run pytest tests/property/ -v
```

**Expected**: identical winners, scores, and alternative lists under both seeds, and the four offset-map
invariants holding over generated inputs.

Running it twice under different seeds is the point. Set iteration order varies with the hash seed, so a
single-seeded run can pass by luck — which is exactly how a non-deterministic alternatives list would
reach a release.

## Scenario 5 — An extraction cannot be grounded against the wrong document (SC-018)

**Validates**: FR-002.

```bash
uv run pytest tests/unit/test_wrong_document.py -v
```

**Expected**: `GroundingError` naming both document identities. No location is produced, not even for
values whose claims happen to appear in the other document.

That last clause is why this check exists. Ranges anchor to a specific parse under ADR-0002, so grounding
against a different parse of the same file would return ranges that are syntactically valid and
semantically wrong — the failure that looks most like success.

---

## Performance

```bash
uv run pytest -m perf -v
```

Targets and measurements are in [plan.md](plan.md). Two rows to understand before trusting the rest.

The **match view dominates** an ordinary grounding: 48.5 ms to fold a 50k-character document against
49.9 ms for the whole exact-tier run over twenty values. That is the right shape — folding is linear in
the document and happens once — and it means the headroom against SC-020 is about 9.6×, not the 35× the
pre-implementation estimate suggested.

The **adversarial row is where the budget earns itself**: 328 ms with the default candidate budget
against 1541 ms without one. It clears the 500 ms bound with 1.5× headroom, deliberately the tightest
margin in the table, because the budget was *derived* from that bound. A truncated value says so on
`outcome.truncated` rather than quietly returning less.

If `perf` is red on this branch, check the kernel's whole-document slice row first. Milestone 3 shipped
it at 1.07× headroom against its budget and recorded that a slower CI runner may push it over; that is
inherited here and is not this feature's doing.

## Reading a result by hand

```python
result = ground(document, extraction)

print(result.counts)                    # exact / fuzzy / ungrounded / not_applicable / truncated
print(result.provenance.grounding_version, result.provenance.match_view_version)
print(result.artifact_id)

for path, o in sorted(result.outcomes.items()):
    where = f"p{o.pages[0]} {o.span}" if o.span else "—"
    print(f"{path:24} {o.status.value:11} {o.score!s:6} {where}")
```

The grounding rate is `(exact + fuzzy) / (exact + fuzzy + ungrounded)`. `not_applicable` is deliberately
outside the denominator, and `counts` carries it separately so that convention lives in the data rather
than being reinvented by each consumer.
