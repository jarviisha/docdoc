# Public API Contract: Grounding Layer

The surface `docdoc.grounding` exposes. Pre-1.0 this may change;
`TODO(PRE_1_0_VERSIONING)` in the constitution governs what stability is promised.

No module behind this contract imports anything that can open a connection, read a credential, or name
a provider. That is not an incidental property — it is what makes every guarantee below testable by
anyone who clones the repository (FR-048), and an `import-linter` contract turns it into a build
failure rather than a promise.

## 1. Entry point

```python
from docdoc.grounding import GroundingOptions, ground

result = ground(
    document,                      # docdoc.kernel.Document — read, never modified
    extraction,                    # docdoc.extraction.ExtractionResult — read, never modified
    options=GroundingOptions(),    # optional — threshold and candidate budget
)
```

Returns exactly one `GroundingResult`, or raises (§6). Never a partially grounded result.

`ground` is synchronous, in-process, and pure with respect to its inputs. Calling it twice with the same
arguments returns equal results — this is a guarantee, not an expectation, and it is the one Milestone 3
explicitly could not make.

The document must be the one the extraction came from. Passing another raises rather than resolving
against it (§6).

## 2. Outcomes

```python
outcome = result.outcomes["total"]

outcome.status        # 'exact' | 'fuzzy' | 'ungrounded'
outcome.score         # 1.0 | 0.90..1.0 | None
outcome.span          # Span(1204, 1218) — a range into document.text — or None
outcome.pages         # (2,)
outcome.geometry      # (Geometry(...), ...) | None
outcome.alternatives  # up to 5 runners-up
outcome.truncated     # True if the candidate budget was reached
```

Reading the located text back is the whole point, and it works with plain indexing because the range
addresses the document's own text:

```python
document.text[outcome.span.start:outcome.span.end]
```

Three shapes that are deliberately distinguishable and are the ones most likely to be conflated:

```python
outcome.geometry is None   # the parser supplied no geometry — unavailable
outcome.geometry == ()     # geometry exists; this range covers no tokens
"total" not in result.outcomes   # the model reported the field absent — nothing to ground
```

The third is not an ungrounded value. A field the model correctly reported as absent produces no outcome
and is counted under `not_applicable`, so it cannot depress the grounding rate.

## 3. Status and score semantics

| `status` | `score` | Meaning |
|---|---|---|
| `exact` | `1.0` | Found verbatim in the match view |
| `fuzzy` | the similarity, `>= threshold` | Found within the threshold |
| `ungrounded` | `None` | Nothing cleared the threshold, or there was nothing to search for |

**`exact` means verbatim modulo documented cosmetic folding**, not byte-identical in the raw source. A
value whose source spells `Invoice` with an *fi* ligature resolves as `exact`, and the range returned
points at the ligature.

**The two scores are not comparable.** An exact `1.0` is assigned by definition; a fuzzy score is a
measurement. Ranking values across tiers by score is meaningless and no part of docdoc does it
(ADR-0004).

`model_confidence` on the extracted value is untouched by this layer. It influences no outcome here, and
it remains untrusted.

## 4. Alternatives

```python
for alt in outcome.alternatives:
    alt.span, alt.score
```

Runners-up at or above the threshold, up to five, in the same order the winner was chosen by. When a
claim matched exactly in several places — an invoice total in a summary box and again in a footer — the
other exact occurrences appear here, all with score `1.0`.

An alternative carries no pages and no geometry; resolve them from its `span` if you need them:

```python
document.page_for(alt.span)
document.locate(alt.span)
```

Ambiguity has no dedicated status. A tie resolves to one winner with the runners-up here, which keeps the
three-state model closed (ADR-0005).

### Repeating groups with identical claims

Two line items both claiming `Widget` resolve to **different** ranges, in entry order — the first entry
takes the first occurrence, the second takes the next. Where the text occurs fewer times than the entries
claiming it, the surplus entries are `ungrounded`, because there is no further place they could have come
from.

The rule is scoped to one repeating group at one field path. Two **distinct** fields that legitimately
read the same text — an invoice date serving as both issue date and due date — both resolve to that one
shared range; uniqueness never fires across field paths.

An alternative may still name a range another entry won. Alternatives record what was there, not what was
assigned.

## 5. Counts, provenance, and identity

```python
result.counts.exact, result.counts.fuzzy, result.counts.ungrounded
result.counts.not_applicable    # values the model reported absent
result.counts.truncated         # values whose candidate budget was reached

result.provenance.grounding_version    # 'v1'
result.provenance.match_view_version   # 'v1'
result.provenance.options.threshold    # 0.90
result.provenance.extraction_artifact_id
result.artifact_id                     # 'sha256:…'
```

The grounding rate is computable from `counts` without walking the outcomes and without re-running
anything (FR-035).

The artifact id follows ADR-0003's chain from the extraction artifact. It moves when
`grounding_version`, `match_view_version`, the threshold, or the candidate budget move, and not
otherwise.

## 6. Errors

```python
from docdoc.grounding import GroundingError
```

`GroundingError` is raised when:

- the extraction result did not come from this document — the error names both identities and no
  location is produced;
- the offset map's invariants fail at runtime, which is a defensive check rather than an expected
  condition.

It is **not** raised when a value cannot be located. That is an `ungrounded` outcome, and it is the
answer this stage exists to be able to give.

Grounding errors are never retried. There is no transient failure mode in a deterministic offline
computation, and the type carries no `transient` flag for that reason.

## 7. Options

```python
GroundingOptions(
    threshold=0.90,         # similarity required for the fuzzy tier
    candidate_budget=1_500, # maximum candidate positions scored per value
)
```

Both participate in artifact identity, because both can change an outcome. `threshold` in particular
changes which candidates are *generated*, not merely which are accepted — so it is not a post-filter and
cannot be applied to a stored result after the fact.

The `0.90` default is an initial estimate pinned by ADR-0005, not a measured optimum. It is to be tuned
against the golden set at Milestone 6, and that tuning will bump `grounding_version`.

Reaching `candidate_budget` does not fail the value: it is resolved from the candidates examined and
`truncated` is set on the outcome and recorded in the log event. The default is derived from the
performance criterion rather than chosen — on ordinary text the filter produces roughly 17 candidate
positions, so the cap sits about 100× above the normal case and fires only on pathological input.

## 8. What this layer will not do

- It will not tell you whether the value is *right* — only where its claim resolves. Whether the number
  at that range supports the extracted value is Milestone 5's question (Principle VII).
- It will not re-ask a model anything, under any circumstance.
- It will not modify the document, its text, its provenance, or the extraction result.
- It will not expose the match view. The folded text is never returned, never logged, and never
  presented as `Document.text`.
- It will not report a value as grounded that it did not locate.
