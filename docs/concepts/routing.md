# Routing and corrections

Two halves of one loop. Routing says which results a human should look at;
corrections are what that human says back. Both are **off until configured**, and
neither changes a result.

## What a decision reads

Three signals, all of them docdoc's own measurements:

- **grounding status and score** — whether a value actually appears in the
  document, and how well it matched;
- **validation severity and verdict** — whether the operator's rules passed, and
  whether they could be evaluated at all;
- **schema requiredness** — which fields matter.

## What it does not read

`model_confidence`.

The number is available: the extraction result carries it per value, and reading
it would take one line and would appear to work. It is forbidden by Principle II
and by ADR-0004, and the reason is not stylistic.

A model's self-reported confidence is a number the model chose, about its own
output, with no external referent. A deployment routing on it would find its
review rate move when a vendor shipped a new checkpoint — with no change to any
document, any schema, or any policy. Grounding and validation are measurements
docdoc made and can defend.

`tests/unit/test_routing_ignores_model_confidence.py` varies that field alone
across a fixture set and requires the decision not to move. That test **is** the
requirement: "routing does not read this" is a claim about what does not happen,
and a claim of that shape has no other form.

## Two outcomes

`automatic` and `review`. That is the whole set, and it is closed.

**Not `reject`.** That states what a deployment should *do*, which is the
caller's decision. Whether a doubtful invoice is rejected, held, or paid anyway is
a business question, and encoding it here would turn docdoc's opinion about a
document into an instruction about it.

**Not `retry`.** That would send the result back into the pipeline, which is
forbidden — and the prohibition is what makes routing admissible at all. A
decision that could cause a second run would make the pipeline's output depend on
a policy, and `processing_id` is derived from inputs.

A routing outcome is a **reading** of a result, not a **disposition** of it.

## The policy is data, and it is versioned

```json
{
  "version": "default@1",
  "required_fields": ["total", "invoice_number", "invoice_date"],
  "review_verdicts": ["invalid", "incomplete"],
  "review_on_error_finding": true,
  "review_when_required_ungrounded": true,
  "review_when_required_fuzzy_below": 0.9
}
```

`examples/routing/default.json` is that file.

`version` is recorded on **every** decision the policy produces, and it is
required rather than defaulted. A deployment that edits thresholds without moving
it has made its own audit trail unreadable.

**Editing a policy alters no existing decision.** A decision is a value: it says
what it said and which rules said it, so a deployment that tightened its
thresholds last week can still explain last month's routing. Re-routing is an
explicit act producing a new decision, not a recomputation that silently rewrites
the old one.

`review_when_required_fuzzy_below` is `null` by default, because a threshold
nobody chose is a threshold nobody can defend.

## Where a decision appears

On `GET /v1/runs/{run_id}`, and **absent** rather than null when no policy is
configured:

```json
{ "run_id": "0f8b…", "status": "succeeded", "priority": 0,
  "processing_id": "9ab2…",
  "routing": { "outcome": "review", "policy_version": "default@1",
               "reasons": [ {"field": "total", "signal": "grounding",
                             "observed": "ungrounded"} ] } }
```

`observed` is a **string** and never a score. A decision returning `0.71` would
invite a caller to compare it against another field's, and ADR-0004 records that
grounding scores are not comparable across tiers. What a reviewer needs is
"`total` was ungrounded".

All the reasons are reported, not the first. A reviewer's first question is
*what* is wrong, and one reason of four would send them looking at one field of
several.

**The decision is computed on read, not stored in an artifact.** An artifact
carrying it would make `processing_id` a function of a threshold, so editing a
policy would change the identity of results computed before the edit.

## Corrections

A reviewer who sees a wrong value knows the right one, and that knowledge is
routinely lost — into a spreadsheet, a ticket, or a conversation — while the next
evaluation measures against the same stale labels.

```text
POST /v1/runs/{run_id}/corrections
GET  /v1/runs/{run_id}/corrections
```

The body is the `Correction` model docdoc's evaluation layer already defines,
imported and not redefined. It names the field, both values, where in the
document the right one is, why, who said so, and when.

`annotator` comes from the **body** and is never inferred from the credential.
The person who reviewed a value and the key that submitted it are different
facts, and a deployment where one operations key posts every reviewer's
corrections is the ordinary case.

| Status | Condition |
|---|---|
| `201` | recorded |
| `404` | run unknown, or another tenant's |
| `410` | the run was erased — naming which, because an annotation against something gone is uninterpretable |
| `409` | the run has no result to correct |

## A correction changes nothing

**Not the result, not its artifacts, not its identities.** It sits beside what it
annotates. If recording one edited the artifact it described, the recorded
pipeline output would become a function of who reviewed it, and the whole chain
of derived identities would be describing a run that never happened.

**It moves no metric.** Promotion is a separate, explicit act producing a new
golden set with a new `golden_set_id`, so reports either side of it are visibly
incomparable. A correction that entered a dataset by being recorded would move
every historical number and explain none of them.

**It pins its run.** A run whose result carries a live correction is not swept,
and a correction has its own retention deadline configured independently. Where
the two disagree, the longer wins — a policy that deleted a result somebody
corrected is a data-loss bug that appears only once both features exist.

## What is deliberately absent

No assignment route. No reviewer queue. No work list. No review state. No `PATCH`
on a result. No write path in the viewer.

The correction **model** is permitted; the review **platform** is not, and "a
full review UI" is on the deferred-technology list by name. The absence is the
contract, and `tests/contract/test_no_review_platform.py` asserts it by name
rather than leaving it to be noticed.

A correction cannot be edited or withdrawn either. It is a reviewer's statement
at a moment; withdrawing one is a second correction, which the `POST` already
expresses.

## Limits

- Routing reports an outcome; it takes no action and changes no run state.
  `RunStatus` gains no `needs_review`.
- A decision is computed only for a **succeeded** run, and only when a policy is
  configured.
- `required_fields` empty means the grounding checks find nothing. That is the
  honest behaviour for a policy that was not told what matters — not a claim that
  everything is fine.
- Corrections are stored, read back, and exported for promotion. Promoting them
  is a separate act this feature does not perform.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `DOCDOC_ROUTING_POLICY` | path to the policy document | **unset — no `routing` block at all** |
| `DOCDOC_CORRECTION_RETENTION_DAYS` | how long a correction is kept | unset — as long as its run |

Neither has a flag. A decision records the policy version that produced it, so a
per-invocation override would make two processes disagree about what `default@1`
means.

## See also

- [ADR-0017 — confidence routing policy](../adr/0017-confidence-routing-policy.md)
- [ADR-0004 — confidence semantics](../adr/0004-confidence-semantics.md)
- [retention](retention.md) — what pinning a run means for the sweep
- `examples/routing/default.json`, `examples/corrections/total.json`
