# Quickstart & Validation Guide: Milestone 7

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

Eight scenarios that, run in order, demonstrate every success criterion in the spec. All but
scenario 7 run offline with no credentials. Fixtures and datasets referenced here already exist in
the repository.

## Prerequisites

```bash
uv sync --all-extras
export DOCDOC_SCHEMA_PATHS="$PWD/schemas"
export DOCDOC_MODEL_ADAPTERS=echo          # the offline adapter
export DOCDOC_STORE_ROOT="$(mktemp -d)"    # a throwaway store
```

---

## 1. The Definition of Done — a PDF in, a located value out

**Proves**: SC-001, US1.

```bash
docdoc inspect tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1
```

Expected: one line per schema field carrying its value, its verdict, its page, and its bounding box.
Exit code `0` if the document validates, `1` if it does not — both are successful runs.

```bash
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 --json | jq .processing_id
```

Expected: one JSON document on standard output and nothing else; `processing_id` is the terminal
artifact id.

---

## 2. Reuse — the second run executes nothing

**Proves**: SC-002, SC-004, US2.

```bash
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 \
  --store "$DOCDOC_STORE_ROOT" --json > run1.json
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 \
  --store "$DOCDOC_STORE_ROOT" --json > run2.json

jq -r '.outcomes[] | "\(.stage) \(.status)"' run2.json     # every line: REUSED
diff <(jq 'del(.outcomes)' run1.json) <(jq 'del(.outcomes)' run2.json) && echo IDENTICAL
```

Expected: four `REUSED`, and results identical apart from the outcomes block that reports the reuse.

---

## 3. Partial reuse — change the prompt, keep the parse

**Proves**: SC-003, FR-013.

Edit a field description in `schemas/invoice@1.json` (a change ADR-0008 says moves `schema_hash` and
not `schema_version`), then:

```bash
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 \
  --store "$DOCDOC_STORE_ROOT" --json | jq -r '.outcomes[] | "\(.stage) \(.status)"'
```

Expected: `PARSE REUSED`, then `EXECUTED` for extract, ground, and validate. This is ADR-0003's
central promise, executing for the first time in the project's history.

---

## 4. Explain an identity

**Proves**: SC-007, US4.

```bash
docdoc explain "$(jq -r .processing_id run1.json)" --chain
```

Expected: the validate stage, its input artifact id, the validator and its version, the names of the
folded options — then the same walked back through ground, extract, and parse to a `blob_id`. No
document content, no extracted value, no credential anywhere in the output.

---

## 5. Integrity — a corrupted artifact is refused, not returned

**Proves**: SC-005, FR-014.

```bash
# Corrupt one stored payload byte, then re-run.
f=$(find "$DOCDOC_STORE_ROOT/artifacts" -name '*.json' | head -1)
python - "$f" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
d["payload"]["artifact_id"] = "sha256:" + "0"*64
json.dump(d, open(p, "w"))
PY
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 \
  --store "$DOCDOC_STORE_ROOT"; echo "exit=$?"
```

Expected: an `ArtifactError` naming the store and the artifact id, exit `2`. **Not** a silent
recompute — that would hide a failing disk behind a slower run.

Then the drift check, which is the other half of SC-005:

```bash
docdoc store clear --stage validate --store "$DOCDOC_STORE_ROOT"
docdoc extract tests/fixtures/pdf/digital_invoice.pdf --schema invoice@1 \
  --store "$DOCDOC_STORE_ROOT" --verify-cache; echo "exit=$?"
```

Expected: exit `0`. `--verify-cache` executes every stage and writes every result, so a stage whose
output has drifted without its version moving collides with what is already stored and raises
(FR-062, FR-064). A clean tree is the only way this passes.

---

## 6. Limits — refused before anything expensive happens

**Proves**: SC-009, FR-039, FR-040.

```bash
head -c 200000000 /dev/zero > /tmp/huge.pdf
docdoc extract /tmp/huge.pdf --schema invoice@1; echo "exit=$?"
```

Expected: `UnsupportedDocumentError` with `reason=size_limit` (or `mime_type`, since zeroes are not a
PDF), exit `2`, zero parses, zero provider calls, and no temporary file left behind.

---

## 7. Over HTTP — the same result, the same identity

**Proves**: SC-010, US3. The only scenario needing the `docdoc[api]` extra.

```bash
uvicorn --factory docdoc.api.app:create_app --port 8000 &
blob=$(curl -sS --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  localhost:8000/v1/documents | jq -r .blob_id)
job=$(curl -sS -XPOST "localhost:8000/v1/documents/$blob/extract?schema=invoice@1" | jq -r .job_id)
curl -sS localhost:8000/v1/jobs/$job/result | jq .job_id
```

Expected: `job_id` equals `processing_id` equals the value scenario 1 printed for the same inputs.

> **Three corrections, made when this scenario was first run end to end.** It had
> been written before the interface existed and did not run as drafted.
>
> `--factory docdoc.api.app:create_app`, not `docdoc.api:app`. There is no
> module-level application on purpose: building one at import time would make
> merely importing `docdoc.api.app` — to read the error table, say — read the
> environment and construct a deployment as a side effect.
>
> `--data-binary @file`, not `-F file=@file`. Submission takes the raw body.
> Multipart would put the identity of the *envelope* in `blob_id` rather than of
> the PDF, and accepting it would add `python-multipart` to the `api` extra for a
> second way to say the same thing.
>
> `?schema=invoice@1` as a query parameter, not a JSON body. One required scalar
> does not need a document wrapped around it, and a JSON body would have been a
> second request shape for the endpoint to validate.

The extract call returns the result as well as the identity (FR-067), so that last
fetch is a *re-read* rather than the only way to see the answer. Its `job_id` is
the same value, which is the point of the identity.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  "localhost:8000/v1/jobs/sha256:$(printf '0%.0s' {1..64})/result"
curl -sS "localhost:8000/v1/jobs/not-an-identity" | jq -r .status
```

Expected: `404` for the well-formed-but-absent identity, and `unknown` for the
malformed one — never `pending`. The status endpoint answers `200` with a status
body; only `/result` 404s, because there a caller asked for a thing and there is
no thing.

---

## 8. The golden set through the command line, twice

**Proves**: SC-015, SC-014, and the whole cost argument.

```bash
docdoc eval datasets/mvp/manifest.json --predictions datasets/mvp/predictions --json \
  | jq '.metrics.micro.field_accuracy.value, .partial.covered_labels'
```

Expected: the same numbers `examples/evaluate_golden_set.py` prints today — the CLI is a front end,
not a second implementation.

Then regenerate the committed prediction set with the rewritten recorder and compare bytes:

```bash
git status --porcelain datasets/mvp/predictions   # expected: empty
```

Expected: no diff. The recorder now calls `pipeline.run()` instead of sequencing the stages itself,
and if that changed any prediction, this is where it shows.

---

## What "done" looks like

| Scenario | Criteria |
|---|---|
| 1 | SC-001 |
| 2 | SC-002, SC-004 |
| 3 | SC-003 |
| 4 | SC-007 |
| 5 | SC-005 |
| 6 | SC-009 |
| 7 | SC-010 |
| 8 | SC-014, SC-015 |

The remaining criteria are asserted by the suite rather than by hand: SC-006 (identity recomputable
from recorded inputs) and SC-008 (nothing leaks into logs) as integration tests, SC-011 and SC-012
(typed errors, partial results preserved) as unit tests over injected failures at each stage, SC-013
(base install acquires nothing) as a CI job that installs without extras and runs the offline suite,
and SC-016 (zero open constitutional decisions) as a documentation task.
