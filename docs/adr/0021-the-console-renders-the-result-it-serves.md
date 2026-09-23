# ADR-0021: The Console Renders a Run's Result, and Derives Nothing From It

- **Status**: Proposed
- **Date**: 2026-09-21
- **Supersedes on acceptance**: `specs/011` FR-022a (the "link and nothing more" half), FR-037, and
  the first half of SC-016. FR-038 (nothing logged), FR-035 (no signing secret), and ADR-0019 §2's
  five nouns are untouched.
- **Relates to**: [ADR-0019](0019-operations-console-boundary.md) (the console's boundary, and the
  five nouns this does not move), [ADR-0020](0020-retire-the-grounding-viewer.md) (whose third
  rejected alternative is the shape of this decision), [ADR-0004](0004-confidence-semantics.md)
  (confidence is never one number, and this is a new place it is exposed),
  [ADR-0010](0010-artifact-store-and-job-model.md) (the result representation being rendered)
- **Principles engaged**: IX (evaluation and human correction are product features), XI (MVP
  discipline), XII (boundaries enforced by tests rather than convention)

## Context

`specs/011`'s clarification session asked *"How much of a run's result does the run detail show?"*
and answered **none of it**, on an argument worth quoting because this ADR has to answer it:

> `GET /v1/runs/{run_id}` already holds this line — *"One result representation, reachable one
> way"* — and a console that rendered values would be the second one. It also keeps FR-037
> absolute, which is what makes SC-016 a single check rather than a list of exceptions.

Two claims, and only the first is about results. The second is about *checkability*, and it has
since been contradicted by the guard's own contract. `specs/011/contracts/console-guards.md` §3
records, under **What rule set 2 cannot catch**:

> SC-016's word *derived*. A figure computed from a result and then rendered under an innocent name
> — a field count called `total`, a grounding rate called `score` — passes this scan.

So SC-016 has two halves and only one of them was ever machine-checked. *"0% of console views
display extracted values"* is a word list. *"0 figures shown anywhere in the console are derived
from a result's contents"* is checked by nobody, and has been since the guard was written. The
"single check with no exception list" also already carries an exception list: `client.ts:175` holds
`console-boundary-exempt: link only`.

Meanwhile the operator is left where ADR-0020 put them. The grounding viewer is gone; its recorded
cost was *"a stored run's grounding cannot be inspected in a browser"*, and `specs/011`'s Out of
Scope had already called opening a stored run *"the most-wanted thing on this list"*. The console
is now the only browser surface the project ships, it holds the operator's credential, it lists the
runs — and when the operator finds the run they were looking for, it hands them a link out to raw
JSON they must read in another tab, unauthenticated by the session they are already inside.

ADR-0020's third rejected alternative named the three decisions that stand between here and there:
the document-bytes read, the allow-list widening, and the result renderer inside an application
whose guard forbids one. This ADR makes the second and third, and **declines the first** (§4).

## Decision

### 1. The console fetches and renders the result representation it already links to

`GET /v1/jobs/{processing_id}/result` is added to the console's request allow-list, requested with
the session credential, and rendered read-only: the extracted values, their claimed text, their
grounding status and score, the validation verdict and its findings.

Only a succeeded run has a `processing_id` and therefore a result. A failed run shows what it
already shows — `failed_stage` and `error_class` — and there is nothing else to fetch.

### 2. It is the same representation, not a second one

This is the objection the clarification raised, and it is answered by constraint rather than by
intention. The console renders **what the response sent, as it sent it**:

- **No reformatting of a number.** A `grounding_score` of `0.9337` is printed `0.9337`. No
  `toFixed`, no `toLocaleString`, no percent. The "own rounding" in the original objection is
  exactly this, and forbidding the three calls that do it is greppable.
- **No re-ordering.** Fields render in the response's order. The console holds no opinion about
  which field matters.
- **No derived figure.** No count of fields, no grounded/ungrounded rate, no average score, no
  "12 of 14 validated". This is SC-016's surviving half, and §6 makes it a check for the first
  time.

A renderer that prints and computes nothing cannot disagree with the representation it printed.
That is a narrower claim than "the console shows no result", and unlike the original it is the
claim a script can hold.

### 3. Zero new routes; one new allow-list entry

The route has existed since Milestone 7. `resultHref` in `console/model/client.ts` has been
building the URL the whole time; what changes is that something now requests it. The console's
allow-list goes from eight patterns to nine.

ADR-0019 §3's *"the console introduces no server-side concept"* survives this change intact, and
that is deliberate: it is the strongest property the console has and the cheapest one to lose.

### 4. The document overlay is **not** part of this decision

ADR-0020 said a returning grounding surface *"adds `GET /v1/documents/{blob_id}` serving bytes"*.
This ADR does not add it. The console renders what is **in the result JSON** — including grounding
status, score, and claimed text, which are fields of the result — and does **not** render a
highlighted overlay on the source document, because that needs the document's bytes and that is a
new server-side read.

The line is drawn here and not one step further because §3 is worth more than the overlay. A
milestone that wants the overlay adds the read, restates `specs/011` SC-003's *"exactly one new
server-side concept"* as the historical claim ADR-0020 already said it would become, and argues
for the bytes on their own merits.

### 5. ADR-0004 survives into a surface that can break it

The console becomes a place where confidence is exposed, and ADR-0004 requires that
`model_confidence` *"MUST be documented as untrusted wherever it is exposed"* and that no blended
number is produced. Both apply here and neither is automatic:

- `grounding`, `grounding_score`, `model_confidence` and `calibrated_confidence` render as
  **separate labelled fields**. The console may not combine them, and may not present one where the
  response carried another.
- `model_confidence` carries a visible mark that it is the model's self-report and is untrusted.
  An operator reading two numbers side by side will otherwise take the larger one.

§2's no-derived-figure rule is what keeps a blend from appearing; this section is why it is not
merely tidiness.

### 6. The fence moves, and for the first time it covers *derived*

`check-console-boundary.mjs` keeps both rule sets. The five nouns are untouched — this ADR does not
reopen ADR-0019 §2, and the console still holds no assignment, queue, workload, review state, or
disposition. What changes is rule set 2:

| | Today | After |
|---|---|---|
| Result content (`claimed_text`, `grounding`, `field_path`, `findings`, the result path) | forbidden under all of `src/console/` | forbidden under all of `src/console/` **except two named files** |
| Derived figures | not checked at all | forbidden in those two files, by a second rule set |
| `console-boundary-exempt: link only` | one escape hatch at `client.ts:175` | deleted — `resultHref` moves into the result model, and no other file addresses the endpoint |

The two files are `src/console/model/result.ts` (reads the response, decides nothing about
arithmetic) and `src/console/components/ResultView.tsx` (prints). They are exempt from the first
rule set and subject to a **stricter** second one: no `reduce(`, no `Math.`, no `toFixed(`, no
`toPrecision(`, no `toLocaleString(`, no `filter(…).length`.

**This is not the exemption list ADR-0019 rejected.** That argument — *"an exemption list is a
guard that eventually says nothing"* — is about carving code out of a rule and leaving it
unguarded. Here the carved-out files are the only ones with a rule *about them*, and it is harsher
than the one they escaped. Everywhere else in the console, nothing changed.

**What the new rule set cannot catch**, in the tradition every guard in
`specs/011/contracts/console-guards.md` follows: a hand-rolled `for` loop that counts fields into a
variable and renders it. The word list catches the drift, not the deliberate evasion — and a
smarter check would start reasoning, which is what ADR-0019 §3 refused for the five nouns and
refuses here for the same reason.

### 7. The result is a deliberate navigation, never inline

The run detail does not render the result underneath the metadata list. It keeps offering the
control it has today — now opening a view inside the console's session rather than a tab outside it.

This is the whole of the mitigation for §9's first consequence and it costs one click. An operator
who opened the console to find out why an overnight run failed should not have a customer's invoice
on screen because they scrolled.

### 8. No scope gate

The result view is not gated on `ADMIN_SCOPE`. The key already in the page can fetch the same
result with `curl`, so a gate here would protect nothing and would hide the feature from the
tenant operator it is most useful to. ADR-0016 §6's `404` still governs the administrative areas;
this is not one of them.

### 9. What this supersedes in `specs/011`

Marked retired in place with a pointer here, the pattern ADR-0020 §4 established — a requirement
that quietly disappears is how a spec stops being a record of what was decided.

| Clause | Fate |
|---|---|
| FR-022a | **Amended.** "A way to reach its result" is a view inside the console. The prohibition on *deriving* a count, rate, or summary is kept verbatim and is now checked. |
| FR-037 | **Retired.** Its subject was the absolute no-content rule. FR-038's no-logging rule is untouched and is a different sentence. |
| SC-016, first half | **Retired.** "0% of console views display document text, extracted values…" |
| SC-016, second half | **Kept verbatim, and promoted.** "0 figures shown anywhere in the console are derived from a result's contents." It was unchecked; §6 makes it a check. |

Also to be updated by the implementing change, listed so none is lost:
`ui/scripts/check-console-boundary.mjs`, `ui/src/console/model/README.md`,
`ui/src/console/model/client.ts`, `ui/test/console/client.test.ts` (which asserts today that the
result endpoint is *not* on the allow-list), `docs/concepts/console.md` §6 and its guard table,
`specs/011/contracts/console-http-api.md`, `specs/011/contracts/console-guards.md` §3,
`specs/011/plan.md` gate 2, `specs/011/checklists/requirements.md`, and `src/docdoc/api/models.py`
where `processing_id` is documented as "the link the console is permitted to offer".

## Consequences

**The console becomes a place customer document content appears, and it was not one before.** This
is the real cost. Today an operator can screen-share the console during an incident, and no
extracted value, no claimed text, and no fragment of anybody's invoice is on the screen. That
property is spent here. §7's deliberate-navigation rule is the mitigation and it is a real one —
content appears only when someone asks for it — but it is not the same guarantee.

**The no-persistence guard becomes more load-bearing, and needs no change.**
`check-no-persistence.mjs` already scans all of `ui/src/` for `localStorage`, `sessionStorage`,
IndexedDB, the Cache API, and cookies. It was written for the credential; it now also keeps result
content from coming to rest in a browser. No new mechanism, which is the answer to the obvious
worry — the bytes are in a page that forgets everything on reload, exactly like the key.

**`specs/011` SC-003's "exactly one new server-side concept" stays true**, because this adds zero.
It is the claim the milestone was measured on and this change does not touch it.

**SC-016 gets stronger and smaller.** It stops claiming a thing about every view and starts
claiming a thing that is checked. A success criterion that named an unchecked property for eleven
months was worse than a narrower one that is enforced.

**The grounding overlay is still not available in a browser**, and ADR-0020's recorded cost is only
partly paid. An operator can now read *that* a value grounded fuzzily at 0.87 and *what text* it
matched; they still cannot see where on the page. §4 is why, and the ADR that changes it is a
different one.

**Two files in the console now carry a rule nobody else carries.** A reader of
`ResultView.tsx` who wants to add "3 of 7 fields grounded" will be stopped by a guard and will have
to come here. That is the intended outcome and it is also a cost: the most natural thing to add to
a result view is a summary of it.

## Alternatives considered

**Leave it as a link.** The status quo, and the honest argument for it is §9's first consequence —
the console is currently the only browser surface in this project that cannot leak a document, and
that is worth something. Rejected because the link does not work the way the sentence implies: it
opens an unauthenticated tab against a route that requires the credential the console is holding,
so on every authenticated deployment the operator gets a `401` and has to reach for `curl` anyway.
A link that only functions on an unauthenticated deployment is the same defect ADR-0020 removed the
viewer for.

**Render the result but keep FR-037 by calling it "metadata".** Rejected, and named because it is
the shape this change would take if it were made in a PR instead of here. `grounding_score` is not
metadata. Reinterpreting an existing requirement in order to comply with it is the inversion
Governance's precedence rule exists to prevent, and ADR-0019's own Context records the project
doing it twice before deciding to stop.

**Bring back a separate viewer application for results.** Rejected for the reason ADR-0020 already
gave: a second application that pastes a key, holds it in memory, and loses it on reload means a
second copy of the session model, the failure vocabulary, and the idle bound. *"If a grounding
surface returns, it returns inside the console's session, not beside it."*

**Render the result and add `GET /v1/documents/{blob_id}` for the overlay in one change.** Rejected
as scope, not on principle — see §4. Two decisions with independent arguments, and bundling them
would mean this one could not claim "zero new routes", which is most of why it is cheap.

**Widen `check-console-boundary.mjs` to reason about context instead of exempting two files.**
Rejected on ADR-0019 §3's argument, which was made about route names and applies unchanged: *"a
guard that reasons is one a future route can argue its way past. A word list either matches or does
not, which is the only property that makes it still true in two years."*
