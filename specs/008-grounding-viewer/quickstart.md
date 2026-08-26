# Quickstart & Validation Guide: Milestone 8

**Feature**: `008-grounding-viewer` | **Date**: 2026-08-25

Ten scenarios that, run in order, demonstrate every success criterion in the spec. All of them run
offline with no credentials. Scenario 5 is the only one confirmed by a person rather than by a
command, and that is the milestone's recorded deviation rather than a gap in this guide — see
[the view-model contract](./contracts/view-model.md) §4.

## Prerequisites

```bash
uv sync --all-extras
export DOCDOC_SCHEMA_PATHS="$PWD/schemas"
export DOCDOC_MODEL_ADAPTERS=echo                          # the offline adapter
export DOCDOC_ECHO_FIXTURES="$PWD/tests/fixtures/echo"     # and its canned answers
unset DOCDOC_STORE_ROOT                                    # deliberately unconfigured
```

`DOCDOC_STORE_ROOT` is left unset on purpose: most of what this milestone adds is only observable on a
deployment that has no store, because that is the deployment the interface previously could not serve
at all.

`DOCDOC_ECHO_FIXTURES` is not optional either, and an earlier draft of this guide omitted it — the run
then failed with a `ModelProviderError` at the extract stage, which reads like a broken endpoint and is
actually an adapter with nothing to answer from. Both settings are named here because the offline path
is deliberately explicit: `DOCDOC_MODEL_ADAPTERS=echo` says *use fabricated answers* and
`DOCDOC_ECHO_FIXTURES` says *these ones*, and neither is inferred.

```bash
uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
BASE=http://127.0.0.1:8000/v1
```

---

## 1. An extraction with no store configured

**Proves**: FR-001, FR-002, SC-007, US1 scenario 1.

```bash
curl -sS -X POST "$BASE/extract?schema=invoice@1" \
  --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  | jq '{counts: .grounding.counts, verdict, job: .job_id}'
```

Expected, on this fixture: `counts` reading
`{"exact": 5, "fuzzy": 0, "ungrounded": 8, "not_applicable": 2, "truncated": 0}`, a verdict of
`valid`, and `job: null` — there is no job, because a storeless run produces no terminal artifact and
ADR-0003's `processing_id` *is* the terminal artifact id.

`not_applicable` is **2**, not 0: two schema fields were reported absent by the model, and a correctly
reported absence is not a grounding failure — which is why it is counted separately and outside the
rate. 15 leaf values in total: 13 asserted (5 located, 8 not) and 2 absent.

> The field names matter and were wrong here once. A run response carries `extraction`, `grounding`,
> `validation`, and `outcomes` — there is no top-level `values` array, and a reader who ran an earlier
> draft of this scenario saw `null` and had every reason to think the endpoint was broken. Counts live
> on `grounding.counts` precisely so the grounding rate needs no second pass.

Before this milestone the same deployment answered this request with a refusal naming
`DOCDOC_STORE_ROOT`, and could not be made to answer it any other way.

**Nothing was written.** Confirm it, rather than assuming:

```bash
find / -newer tests/fixtures/pdf/digital_invoice.pdf -name '*.docdoc*' 2>/dev/null | head
```

Expected: no output.

---

## 2. The same run, with a store configured, still writes nothing

**Proves**: FR-008, SC-008.

```bash
export DOCDOC_STORE_ROOT="$(mktemp -d)"
# restart uvicorn so the deployment re-reads its environment
curl -sS -X POST "$BASE/extract?schema=invoice@1" \
  --data-binary @tests/fixtures/pdf/digital_invoice.pdf > /dev/null
find "$DOCDOC_STORE_ROOT" -type f | wc -l
```

Expected: `0`. The endpoint decides whether a run persists, not the configuration. This is the
property most likely to be lost to a later change that reuses the deployment's store because it
happens to be there.

---

## 3. Storeless and store-backed agree

**Proves**: FR-004, SC-006.

```bash
BLOB=$(curl -sS --data-binary @tests/fixtures/pdf/digital_invoice.pdf "$BASE/documents" | jq -r .blob_id)
curl -sS -X POST "$BASE/documents/$BLOB/extract?schema=invoice@1" | jq 'del(.job_id)' > /tmp/stored.json
curl -sS -X POST "$BASE/extract?schema=invoice@1" \
  --data-binary @tests/fixtures/pdf/digital_invoice.pdf | jq 'del(.job_id)' > /tmp/storeless.json
diff /tmp/stored.json /tmp/storeless.json && echo IDENTICAL
```

Expected: `IDENTICAL`. The two routes differ in what they persist and in nothing else.

---

## 4. The deployment lists its schemas

**Proves**: FR-009 through FR-012.

```bash
curl -sS "$BASE/schemas" | jq .
DOCDOC_SCHEMA_PATHS="" uvicorn --factory docdoc.api.app:create_app --port 8001 &
curl -sS http://127.0.0.1:8001/v1/schemas | jq .
```

Expected: first, the identities under `schemas/` — `invoice@1`, `invoice@2`, `receipt@1` — sorted,
each a string `POST /v1/extract` accepts verbatim, and **no filesystem paths anywhere in the
response**. Second, `{"schemas": []}` with a `200`: a deployment with nothing configured is validly
configured, not broken.

---

## 5. The viewer, opened by a person

**Proves**: US1, US2 — and it is the one scenario no test covers.

```bash
open http://127.0.0.1:8000/
```

Pick `tests/fixtures/pdf/digital_invoice.pdf`, choose `invoice@1`, and run it. Expected:

- **15 values listed** — 13 the model asserted, of which 5 carry rectangles and 8 do not, plus 2 it
  reported absent. That ratio is the correct output on this fixture, not a failure: the eight are real
  extracted values the grounder could not locate (`1240.00` against a page rendering `1,240.00`, and
  so on), and Principle II forbids inventing a page for them. A viewer drawing thirteen rectangles
  would be the bug, and so would one listing thirteen rows.
- **One located value carries two rectangles** (`line_items[0].description`, which wraps). Both are
  drawn. Drawing the first only is the failure SC-001 is worded to catch.
- Each of the eight is **visible and marked**, not omitted (FR-016, FR-017).
- Selecting a field distinguishes its rectangles; selecting a rectangle names its field.
- The interface states that it is showing pages selectively (FR-054).

Confirm by eye, once: that each rectangle sits over the text the value was read from, and that
`unavailable` and `empty` geometry do not read as "not found". **Nothing automated checks these**, by
the decision recorded in the spec's first clarification.

---

## 6. The view model, which is what is actually tested

**Proves**: SC-001, SC-002, SC-003, SC-004, SC-011, SC-013, SC-015, SC-016, SC-017.

```bash
cd ui && npm test
```

Expected: green, with a case per invariant in
[the view-model contract](./contracts/view-model.md) §3 — including the ones that are easy to pass
accidentally and easy to lose silently: a value carrying three boxes yields three entries and not one;
`unavailable` and `empty` produce different labels; a result arriving under a stale token is
discarded; and `pagesToRender` does not grow with document length.

```bash
cd ui && npm run lint:boundaries
```

Expected: green. `src/model/**` imports nothing from `src/components/**`, React, or the renderer — the
check that keeps FR-043 from being a matter of review (R9).

---

## 7. The base install acquires nothing

**Proves**: FR-035, FR-036, SC-005.

```bash
python -m venv /tmp/base && /tmp/base/bin/pip install -q .
/tmp/base/bin/python -c "import docdoc; print(docdoc.__name__)"
find /tmp/base/lib -path '*docdoc*' \( -name '*.js' -o -name '*.css' -o -name '*.html' \) | wc -l
uv run pytest -q
```

Expected: the import succeeds, the asset count is `0`, and the offline suite passes with no web
framework, no command-line framework, no provider SDK, and no viewer installed.

---

## 8. The contract no longer disagrees with the code

**Proves**: FR-007, SC-012.

```bash
grep -n "need a store" specs/007-pipeline-api-cli/contracts/http-api.md
uv run pytest tests/contract/test_http_ui_endpoints.py -q
```

Expected: §1 names which routes need a store and why, `POST /v1/extract` is the one that needs none,
and the contract test asserts the behaviour the corrected text describes. The false sentence is
replaced rather than deleted — the system it described is the one this milestone built.

---

## 9. Every sentence the requirements oblige us to write

**Proves**: SC-018, FR-044, FR-050, FR-054, FR-059, FR-061, FR-062, FR-063.

Confirm the documentation states all seven: that the rendered interface carries no automated test;
that a run continues after the page is closed and what that demands of a proxy; that pages are shown
selectively; that no accessibility conformance level is claimed; that the interface is unauthenticated
and every visitor spends the provider budget; that it belongs on a trusted network; and that a
store-less deployment now serves extractions it used to refuse.

These are counted together because they share a failure mode: each costs nothing to omit and is
discovered missing only by the person it would have warned.

---

## 10. Licences and the build

**Proves**: FR-038, FR-039, SC-014.

```bash
cd ui && npm run build && git status --porcelain -- ui/
cd ui && npm run licenses
```

Expected: the build succeeds from a clean checkout, `git status` reports **nothing** — no build output
is committed — and every dependency's licence is compatible with Apache-2.0. `pdfjs-dist` is
Apache-2.0, which is the reason it is here rather than a server-side renderer under AGPL.
