# Quickstart: Schema-Driven Extraction

How to set the feature up and how to convince yourself it works. Five validation scenarios, one per user
story plus one for the boundaries. Four of the five need no credentials and no network.

## Prerequisites

- Python 3.11+
- `uv`
- Nothing else for V1–V3 and V5. V4 needs credentials for the model provider and will cost money.

## Setup

```bash
uv sync --all-extras
```

The base install stays `pydantic` alone. `uv sync --all-extras` adds the provider SDK behind the
`google` extra, plus the Milestone 2 extras. To confirm the base install pulls no provider SDK:

```bash
uv run --no-project --with . python -c "import docdoc.extraction, importlib.util as u; \
  assert u.find_spec('google.genai') is None; print('base install: no provider SDK')"
```

## Run the suites

```bash
uv run pytest -m 'not provider and not perf'   # everything a contributor can run offline
uv run pytest -m perf                          # SC-021, the deterministic budget
uv run pytest -m provider                       # live model calls — credentials, network, money
uv run mypy src
uv run ruff check .
uv run lint-imports                             # Principle X + the SDK containment rules
```

`-m 'not provider'` must be green with no credentials configured and no network. A skipped
provider test must state its reason.

## Validation scenarios

### V1 — Extract the fields a schema declares, offline (US1, P1)

```python
from docdoc.extraction import SchemaRegistry, extract
from docdoc.extraction.adapters import EchoAdapter

registry = SchemaRegistry.from_paths(["schemas/"])
echo = EchoAdapter.from_fixtures("tests/fixtures/echo")
result = extract(document, schema="invoice@1", registry=registry, adapter=echo)

# Every declared field is present, including the ones the document does not contain
assert set(result.values) == set(registry.describe("invoice@1").field_names)

assert result.values["total"].value == Decimal("1240.00")
assert result.values["total"].claimed_text == "1,240.00"   # byte-faithful, as returned

assert result.values["due_date"].present is False          # an absence, not an error
assert result.values["due_date"].value is None

assert len(result.values["line_items"]) == 3               # a repeating group
assert result.values["line_items"][0]["description"].claimed_text
```

**Expected**: no credentials, no network, no database, no object storage. One entry per declared field;
zero undeclared fields.

Then confirm the model cannot bully the result into a different shape:

```python
extract(document, schema="invoice@1", registry=registry, adapter=EchoAdapter.malformed())
# ExtractionError naming the offending field path — no coercion, no default
```

### V2 — Depend on a schema version that means something (US2, P2)

```python
registry.identities()                       # ('invoice@1', 'invoice@2', 'receipt@1')

r1 = extract(document, schema="invoice@1", registry=registry, adapter=echo)
r2 = extract(document, schema="invoice@2", registry=registry, adapter=echo)
assert r1.provenance.schema_identity == "invoice@1"
assert r1.artifact_id != r2.artifact_id     # two majors, two artifacts

registry.resolve("invoice")                 # SchemaError: no version named
registry.resolve("invoice@9")               # SchemaError: names @1 and @2 as the ones that exist
```

Now the hash. Reorder the fields in `schemas/invoice@1.json` and reload:

```bash
uv run pytest tests/property/test_schema_hash.py -q
```

**Expected**: reordering fields leaves `schema_hash` unchanged; editing any field, type, constraint, or
description changes it. Then edit a field description in `schemas/invoice@1.json` without bumping the
version and run:

```bash
uv run pytest tests/unit/test_schema_snapshot.py -q
```

**Expected**: the build fails, naming the version whose hash moved. Clearing it means either publishing a
new major or refreshing `tests/fixtures/snapshots/schema_hashes.json` with the classification stated in the
commit message — the change detector of FR-017. Revert the edit afterwards.

### V3 — A second document type, with zero engine changes (US1 scenario 8, SC-014)

`schemas/receipt@1.json` and `schemas/prompts/receipt@1.md` are the whole of what a new document type
requires. Confirm nothing in the engine knows about it:

```bash
uv run pytest tests/unit/test_no_provider_names.py -q   # also asserts no document-type code path
rg -n 'invoice|receipt' src/docdoc/                      # expect: no matches
```

**Expected**: zero matches under `src/`. Document-type knowledge lives in `schemas/`, which is Principle VI
read literally.

### V4 — Reach a real model, without naming it in application code (US3, P3) — credentials required

```bash
export GEMINI_API_KEY=...             # or GOOGLE_API_KEY
uv run pytest -m provider -q
```

```python
# `default_adapter()` selects from whatever is installed and configured. Nothing
# here names a provider, which is FR-021. It raises with every candidate's reason
# if none is usable, and it never selects the echo adapter -- a fixture adapter
# answering a real request would fabricate confidently (FR-028, FR-029).
adapter = default_adapter()
result = extract(document, schema="invoice@1", registry=registry, adapter=adapter)
result.provenance.adapter_id                     # 'gemini'
result.provenance.model_id, result.provenance.model_version
result.provenance.usage.input_tokens, result.provenance.usage.output_tokens
```

**Expected**: a result of exactly the shape V1 produced. Nothing downstream can tell which adapter
produced it except by reading provenance. Repoint configuration at a different model and confirm the same
application code runs and only provenance changes.

Then confirm the prompt cache is actually working — this is the difference between paying for the schema
instructions once per document and once per schema:

```python
first = extract(doc_a, schema="invoice@1", registry=registry, adapter=adapter)
second = extract(doc_b, schema="invoice@1", registry=registry, adapter=adapter)
second.provenance.usage.cache_read_input_tokens   # 0 today — see below
```

**Expected today: zero.** A Gemini cache hit needs the shared prefix to clear a per-model minimum of
2,048–4,096 tokens, and the current per-schema prefix is a few hundred (research.md R15). The ordering
is still right and buys nothing yet. Once the prefix does clear the threshold, a zero would mean
something volatile precedes the breakpoint — `tests/unit/test_prompt_assembly.py` guards that from the
offline side, and `tests/integration/test_gemini_live.py` asserts the threshold arithmetic rather than
a hit that cannot happen.

### V5 — Failure, safety, and the boundaries

```python
# Over-budget input: refused before anything is transmitted
extract(huge_document, schema="invoice@1", registry=registry, adapter=adapter)
# ExtractionError naming the document, the bound, the estimate, and Document.slice as the way forward

part = huge_document.slice(span_over_pages_1_to_5)
ok = extract(part, schema="invoice@1", registry=registry, adapter=adapter)
ok.provenance.document_id      # the narrowed document — the record says what was actually read
```

```bash
uv run pytest tests/unit/test_no_transmission.py -q    # SC-016
```

**Expected**: zero bytes transmitted for a request that fails schema resolution, credential availability,
or the budget guard — asserted against a transport that records every call attempt.

```bash
uv run pytest tests/unit/test_provider_errors.py -q
```

**Expected**: transient failures (timeout, rate limit, server error) retried within the configured limit;
a rejected credential, an unknown model, and **a content refusal** all fail on the first attempt with zero
retries. The refusal case is the one worth reading the test for: it arrives as a *successful* HTTP
response, so an adapter that reads the content without checking the stop reason would treat a refusal as
an answer.

```bash
uv run pytest tests/unit/test_observe.py tests/unit/test_grounding_untouched.py -q
```

**Expected**: one structured event per extraction carrying identifiers, model, adapter, usage, duration,
attempts, and outcome — and zero occurrences of document text, extracted values, claimed source text,
prompt content, or credentials anywhere in the log output. Every grounding field on every value is
unresolved.

```bash
uv run lint-imports
```

**Expected**: `docdoc.extraction` above `docdoc.ingest` above `docdoc.kernel`, and the provider SDK
importable only from `docdoc/extraction/adapters/gemini.py`.

## What "done" looks like

- `uv run pytest -m 'not provider and not perf'` is green with no credentials and no network.
- `mypy --strict`, `ruff`, and `lint-imports` are clean.
- V1, V2, V3, and V5 pass offline; V4 passes with credentials.
- `schemas/receipt@1.json` proves a document type is data: `rg 'invoice|receipt' src/docdoc/` finds
  nothing.
- Every value in every result has an unresolved grounding status. That is not an omission — it is what
  Milestone 4 is for, and this milestone deliberately does not pre-empt it.
