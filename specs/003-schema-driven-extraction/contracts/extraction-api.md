# Public API Contract: Extraction Layer

The surface `docdoc.extraction` exposes. Pre-1.0 this may change;
`TODO(PRE_1_0_VERSIONING)` in the constitution governs what stability is promised.

Nothing in this contract names a provider or a model. That is the point of it (FR-021).

## 1. Entry point

```python
from docdoc.extraction import ExtractionOptions, extract

result = extract(
    document,                       # docdoc.kernel.Document — read, never modified
    schema="invoice@1",             # a concrete name@version. No "latest" (FR-014)
    registry=registry,              # required — where schemas come from
    adapter=adapter,                # required — which model answers
    options=ExtractionOptions(),    # optional — decoding and budgets
    transport=TransportSettings(),  # optional — attempts, backoff, timeout, deadline
)
```

Returns exactly one `ExtractionResult`, or raises (§7). Never a partial result.

`extract` is synchronous and in-process. No queue, no worker, no batching across documents.

## 2. Schemas and the registry

```python
from docdoc.extraction import SchemaRegistry, default_registry

registry = SchemaRegistry.from_paths(["schemas/"])   # configuration names the paths
registry.identities()          # ('invoice@1', 'invoice@2', 'receipt@1')
registry.describe("invoice@1") # fields, types, cardinality, descriptions — no model call
registry.resolve("invoice@1")  # -> Schema

registry.resolve("invoice")    # SchemaError: no version named
registry.resolve("invoice@9")  # SchemaError: naming @1 and @2 as the ones that exist
```

Two majors of one name are resolvable at the same time and neither shadows the other.

A schema is rejected **when it is registered**, not when it is first used: a malformed file, an unknown
type, a duplicate field name, a repeating group nested inside a repeating group, or a missing prompt all
fail here.

### Identity

```python
schema = registry.resolve("invoice@1")
schema.identity      # 'invoice@1'  — the consumer contract
schema.schema_hash   # 'sha256:…'   — did anything result-affecting change?
```

Both are recorded in every result. The two answer different questions and neither substitutes for the
other; ADR-0008 has the bump rules.

## 3. Results

```python
result.values["total"].value          # Decimal('1240.00')
result.values["total"].claimed_text   # '1,240.00'  — byte-faithful, as the model returned it
result.values["total"].present        # True
result.values["due_date"].present     # False  — an explicit absence, not an error
result.values["due_date"].value       # None

result.values["line_items"]           # a repeating group: one entry per occurrence
result.values["line_items"][0]["description"].claimed_text
```

Every declared field appears, including absent ones. No undeclared field ever appears.

### Confidence and grounding

```python
result.values["total"].model_confidence  # float | None — UNTRUSTED, routes nothing (ADR-0004)
result.values["total"].grounding         # None — unresolved at this milestone
result.values["total"].grounding_score   # None — unresolved at this milestone
```

`grounding` is `None` for every value this layer produces, deliberately. Resolving `claimed_text` to spans
and geometry is Milestone 4's stage, with its own artifact under ADR-0003 — so this layer does not
resolve even the exact-match tier the kernel's search could satisfy cheaply.

## 4. Provenance and identity

```python
p = result.provenance
p.document_id            # the Milestone 1 parse this result reads
p.schema_identity        # 'invoice@1'
p.schema_hash            # 'sha256:…'
p.prompt_hash            # 'sha256:…'
p.projection_id          # 'response-shape@1'
p.adapter_id             # e.g. 'echo' — an adapter id, not a provider name in your code
p.adapter_version        # '1.0.0+…'
p.model_id               # recorded, never named by the caller
p.model_version
p.decoding               # ExtractionOptions as they actually ran
p.extractor_version
p.usage                  # ModelUsage: input/output tokens, or None where an adapter has no tokens

result.artifact_id       # sha256(document_id + extractor_id + extractor_version + options_hash)
```

Change any result-affecting input and `artifact_id` changes. Change only retry, timeout, or deadline and
it does not — those live in `TransportSettings`, a separate type, so this holds by construction rather
than by discipline.

Re-extraction produces a new result with its own provenance. It never mutates a prior one.

## 5. Options

```python
ExtractionOptions(
    max_output_tokens=...,      # caps the model's whole output, reasoning included
    temperature=...,            # defaults to 0.0, not the provider's default
    top_p=..., top_k=...,       # optional; None leaves the provider's own default
    seed=...,                   # best-effort reproducibility, recorded either way
    thinking_budget=...,        # None leaves the provider's automatic budget
    input_budget_tokens=...,    # the guard of §6
)
```

Every one of these is folded into the extraction artifact's identity, because every one can change the
answer. `temperature=0.0` plus a recorded `seed` reduces variance; neither makes repeated calls
byte-identical, and §4 does not claim they do.

Retry, timeout, and deadline are configured separately, with `TransportSettings` from `docdoc.ingest`:

```python
from docdoc.ingest import TransportSettings

extract(
    document,
    schema="invoice@1",
    registry=registry,
    adapter=adapter,
    transport=TransportSettings(max_attempts=3, deadline_s=120),
)
```

## 6. Budgets

A document whose text exceeds `input_budget_tokens` is refused **before anything is transmitted**:

```python
extract(huge_document, schema="invoice@1")
# ExtractionError: document exceeds the input budget (estimate ~412,000 > 200,000).
#   Narrow the document with Document.slice and extract from the result.
```

The guard is a deliberate over-estimate, because the only exact count available is an API call that would
transmit the document to answer it — the very thing the guard exists to avoid. A document that would have
fitted can therefore be refused; the ratio is configurable.

The supported way forward is to narrow the document yourself:

```python
part = document.slice(span_over_the_pages_you_want)
result = extract(part, schema="invoice@1")
result.provenance.document_id   # the *narrowed* document — the record says what was read
```

Because `slice` preserves original page numbers and geometry byte-identically, a value extracted from a
narrowed document can still be grounded back to its true page. Windowing and result merging are not
performed for you.

## 7. Errors

| Error | When |
|---|---|
| `SchemaError` | Unknown identity; malformed schema file; unrecognised type or constraint; duplicate field; repetition bound exceeded; missing prompt |
| `ExtractionError` | Response shape mismatch; unparseable value; missing declared field; over-budget input or output; truncated response |
| `ProviderError` | Transport, service, and credential failures, and a content refusal |

All are `DocdocError`. No provider exception crosses the boundary; each is translated with `__cause__`
preserved.

Retries apply to transient network and service failures only. A rejected credential, a malformed request,
an unknown model, and a content refusal all fail on the first attempt. Schema errors are never retried.

There is no fallback: a failed call never switches model, provider, or schema version.

## 8. Adapters

```python
from docdoc.extraction import ModelAdapter, default_adapter   # the protocol, and selection
from docdoc.extraction.adapters import EchoAdapter            # deterministic, offline, no credentials

adapter = default_adapter()   # configuration decides; this line names no provider
```

`default_adapter()` picks the first usable adapter in the configured priority order and raises with
**every candidate's reason** when none is usable — so "why not?" does not require reading docdoc's
source. `default_adapter_registry()` returns the registry itself if you want to inspect or extend it.

It **never selects `EchoAdapter`**, even when explicitly registered and even when nothing else is
usable. Echo answers from fixtures, so auto-selecting it would turn a missing credential into
confident, fabricated extractions carrying full provenance — silently wrong data rather than an
error. Passing it explicitly is fine; that is a decision taken knowingly.

`EchoAdapter` is part of the library, not a test fixture: it is what makes the whole path except the model
call runnable with no credentials and no network, and the contract suite runs against it and the real
adapter alike.

The real adapter installs with `pip install docdoc[google]`. Your application code does not import it
and does not name it — which adapter answers is configuration, and the only observable difference is in
provenance.

## 9. Observability

One structured `extraction.extract` event per extraction, success or failure, carrying document identity,
schema identity, artifact identity where one was produced, adapter and model identity and version, token
usage, duration, attempt count, and outcome.

Document text, extracted values, claimed source text, prompt content, and credentials are **never**
logged. Identifiers, hashes, counts, timings, and token counts only.

## 10. Stability

Pre-1.0. The names in this document are the intended public surface; everything under
`docdoc.extraction.adapters` other than the adapter protocol and `EchoAdapter` is an implementation
detail.
