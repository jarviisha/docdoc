# Grounding

Grounding answers one question: **where did this value come from?**

Extraction records the text a model *claims* it read a value from. Grounding
decides — by itself, from the document and the claim alone — whether that claim
resolves to a real place. A model never gets a vote on whether its own output is
grounded (Principle II).

```python
from docdoc.grounding import ground

result = ground(document, extraction)

outcome = result.outcomes["total"]
outcome.status    # 'exact' | 'fuzzy' | 'ungrounded'
outcome.span      # a range into document.text
outcome.pages     # (2,)
outcome.geometry  # the boxes covering it
```

`document.text[outcome.span.start:outcome.span.end]` reads the value back out of
the untouched source.

## Three states, and no fourth

| Status | Meaning | Score |
|---|---|---|
| `exact` | Found verbatim in the folded view | `1.0` |
| `fuzzy` | Found within the similarity threshold | the measurement |
| `ungrounded` | Nothing cleared the threshold, or there was nothing to search for | `None` |

The vocabulary is closed. Ambiguity is expressed through `alternatives` — the
runners-up, up to five — not through a new status. Adding a fourth state is a
constitutional amendment, not an implementation detail (ADR-0005).

### What `exact` does *not* mean

It does **not** mean byte-identical in the source. It means *found verbatim
modulo documented, versioned cosmetic folding*. A supplier whose name is typeset
`Ofﬁce` with a single U+FB01 glyph resolves `exact` against a model that quoted
`Office`, and the range returned points at the ligature.

That is the whole point of the match view below, and a reader who assumes the
stronger meaning would be misled.

### The two scores are not comparable

An `exact` score is `1.0` by definition. A `fuzzy` score is a measurement. Ranking
values against each other by this number is meaningless, and nothing in docdoc
does it (ADR-0004).

`model_confidence` on the extracted value is a different thing again: the model's
own self-report, **untrusted**, passed through untouched, and influencing nothing.

## The match view

Matching does not run against `Document.text`, and it does not run against a
normalized `Document.text` either. It runs against a derived, versioned **match
view** — and every range it returns is mapped back to the source (ADR-0006).

`Document.text` stays byte-faithful. The view is never exposed, never persisted
as canonical text, and never handed to a consumer.

Four rules produce the view (`match_view_version = "v1"`):

1. **NFKC** — which also expands ligatures (`ﬁ` → `fi`) and folds non-breaking,
   narrow, and figure spaces to a plain space. Those are not separate rules
   because NFKC already performs them.
2. **Soft-hyphen removal** — NFKC does *not* touch U+00AD, so this is explicit.
3. **De-hyphenation across line breaks** — see below.
4. **Whitespace collapsing** — any run becomes one space.

It is worth what it costs: on the committed typesetting fixtures, plain substring
matching resolves **0 of 7** claims at the exact tier and the folded view resolves
**6 of 7**.

### De-hyphenation joins only lowercase to lowercase

Both obvious rules are measurably wrong:

| Rule | Case | Similarity | Clears 0.90? |
|---|---|---|---|
| Never de-hyphenate | `amount` vs `am-ount` | 0.857 | ✗ |
| Always de-hyphenate | `INV-2024-001` vs `INV2024001` | 0.833 | ✗ |

Neither failure is rescued by the fuzzy tier — both fall below the threshold, so
the value is lost outright. So the hyphen is removed only when the character
before it and the first non-whitespace character after the break are both
lowercase. Typesetting breaks lowercase words mid-word; identifiers carry
uppercase letters or digits around their hyphens.

One case is still lost: a genuine compound word broken at a line end.
`well-known` joins to `wellknown` and scores **exactly 0.900** — clearing the
threshold by nothing at all. Raising the threshold above 0.90 breaks it, which is
a constraint on any future tuning rather than a bug.

## The algorithm

Pinned as `grounding_version = "v1"` (ADR-0005):

1. Fold both the document and the claim.
2. **Exact tier**: every occurrence, `str.find`. Any hit → `exact`, score `1.0`.
3. **Fuzzy tier**, only if there is no exact hit: candidate windows scored with
   normalized Levenshtein, keeping those at or above the threshold (default
   `0.90`).
4. **Tie-break**, total and in order: highest score → earliest start → shortest
   range. Because the ordering is total, exactly one winner exists for any
   candidate set and no result depends on iteration order or platform.
5. Up to five runners-up are recorded in `alternatives`.
6. Winning view offsets are mapped back, so returned ranges are always
   source-text ranges.

The candidate filter has a **completeness proof**: if a window is within `k`
edits of the claim, and the claim is split into `k + 1` disjoint blocks, then at
least one block survives verbatim in that window. Searching for every block
therefore finds every window that could clear the threshold. This is what lets
`ungrounded` mean *it is not there* rather than *we did not look hard enough*.

`k` is derived, not chosen: `k = floor((1 - threshold) · m / threshold)`.

## The two absences, which are different

| Extraction said | Grounding says | In the rate? |
|---|---|---|
| the field is absent | **no outcome at all**, counted `not_applicable` | no |
| present, no claim | `ungrounded` | yes |
| present, empty claim | `ungrounded` | yes |

A correctly reported absence is not a grounding failure. Counting it as one would
make the grounding rate depend on how many fields a schema declares, so adding an
optional field nobody fills in would appear to degrade quality.

```python
result.counts.grounding_rate   # (exact + fuzzy) / (exact + fuzzy + ungrounded)
```

`None`, not `0.0`, when there was nothing to ground.

## Repeating groups

Two line items both claiming `Widget` resolve to **different** ranges, in entry
order. Where the text occurs fewer times than the entries claiming it, the
surplus entries are `ungrounded`.

The rule is scoped to one repeating-group slot and is never global: an invoice
date read as both issue date and due date must resolve to the one range it
occupies.

## Geometry, and its absence

`geometry is None` means the parser supplied none — **unavailable**. An empty
tuple means geometry exists and the range covers no tokens. A value from a parser
with no geometry is still fully grounded, and still carries its range and page.

Boxes are token-granular. A range starting mid-token yields the whole token's
box, because deriving a partial box from a character offset assumes uniform glyph
advance and is false for proportional fonts and every complex script. Slightly
too wide is coarse; interpolated is wrong.

## Reproducibility

```python
result.provenance.grounding_version    # 'v1'
result.provenance.match_view_version   # 'v1'
result.provenance.options.threshold    # 0.90
result.artifact_id                     # chained from the extraction artifact
```

The artifact id follows ADR-0003's chain and moves when `grounding_version`,
`match_view_version`, the threshold, or the candidate budget move — and not
otherwise.

`0.90` is an initial estimate pinned by ADR-0005, not a measured optimum. Tuning
it against a golden set is Milestone 6's work, and that tuning bumps
`grounding_version`.

## What grounding will not do

- **Judge whether the value is right.** A value of `1240.00` whose claim resolves
  to text reading `1,420.00` grounds normally. The disagreement is a *validation*
  finding — Milestone 5's (Principle VII).
- **Re-ask a model anything.** No module in this layer imports anything that can
  open a connection, enforced by an `import-linter` contract. (Importing it does
  pull `socket` into `sys.modules`, exactly as importing `docdoc.kernel` alone
  does — pydantic reaches `email.utils` while building models. The claim is about
  docdoc's own code, which is the part this project controls.)
- **Modify anything it reads.**
- **Report a value as grounded that it did not locate.**

## See also

- [`extraction.md`](extraction.md) — where the claims come from
- ADR-0004 — why confidence is never one blended number
- ADR-0005 — the pinned algorithm, threshold, and tie-break
- ADR-0006 — the match view and its offset map
