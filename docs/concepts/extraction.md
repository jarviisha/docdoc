# How extraction works

A `Document` plus a versioned schema, in; structured values, out.

```python
from docdoc.extraction import SchemaRegistry, default_adapter, extract

registry = SchemaRegistry.from_paths(["schemas"])
result = extract(document, schema="invoice@1", registry=registry, adapter=default_adapter())

result.value_at("total").value          # Decimal('1240.00')
result.value_at("total").claimed_text   # '1,240.00'  — byte-faithful, as returned
result.value_at("due_date").present     # False — an explicit absence, not an error
```

No provider is named. Which model answers is configuration, and the only observable difference
between two of them is in provenance.

## Choosing an adapter without naming one

`default_adapter()` is the call application code makes. It picks the first usable adapter in the
configured priority order, and raises with **every candidate's reason** when none is usable — so
"why not?" is answerable without reading docdoc's source.

```python
from docdoc.extraction import default_adapter, default_adapter_registry

adapter = default_adapter()                    # configuration decides
default_adapter_registry().candidates()        # inspect what is known, and why each is or is not usable
```

### What "configuration" actually names

Three environment variables, and nothing else. Between them, which schemas exist, which provider
answers, and which model it uses are all deployment decisions rather than code:

| Variable | Decides | Default |
|---|---|---|
| `DOCDOC_SCHEMA_PATHS` | Where `default_registry()` loads schema data from, `os.pathsep`-separated | none — the registry is empty |
| `DOCDOC_MODEL_ADAPTERS` | Adapter preference order, comma-separated | `gemini` |
| `DOCDOC_GEMINI_MODEL` | Which Gemini model answers | the adapter's shipped default |

Credentials come from `GEMINI_API_KEY` or `GOOGLE_API_KEY`, as the adapter's `available()` reports.

An explicit argument always beats configuration — `default_registry(["schemas"])`, or a `model=`
passed straight to an adapter — because a caller who passed one meant it. Configuration is the
default, not a cage; it exists so that the *application* code above never has to name any of these.

`default_registry()` with neither an argument nor `DOCDOC_SCHEMA_PATHS` returns an **empty**
registry, and the next `resolve()` says so. docdoc ships no schema of its own: a schema is a
deployment's data, and `schemas/` in this repository is fixtures and examples, deliberately not
packaged in the wheel.

An adapter whose extra is missing or whose credentials are absent is **recorded with its reason**
rather than omitted. Silence would make "not installed" indistinguishable from "no such thing", and
the resulting error would name nothing. Priority decides between usable candidates, with the adapter
id as a total tie-break, so selection never depends on registration order.

**The echo adapter is never selected automatically**, even when it is registered first and even when
nothing else is usable. This is a safety property rather than an ordering preference. Echo answers
from committed fixtures, so if it were selectable, a forgotten API key would not produce an error —
it would produce a stream of confident, fabricated extractions carrying full provenance,
indistinguishable downstream from real ones. That is the worst outcome this layer can have: not a
failure, but plausible wrong data with a content-addressed identity attesting to it.

It stays fully usable when passed explicitly, which is a decision a caller takes knowingly, and that
is how the offline path and the whole test suite work.

## Two identities, two questions

ADR-0008 splits what one integer was being asked to answer.

| | Answers | Origin |
|---|---|---|
| `schema_version` — the major in `name@version` | *Did the consumer contract change?* | Author-assigned |
| `schema_hash` | *Did anything result-affecting change?* | Derived over canonical JSON |

Both are recorded in every result and both are folded into the extract stage's `options_hash`. That
is what makes a reworded field description invalidate the extraction artifact while **reusing the
parse** — the outcome keying on the integer alone cannot express.

### What forces a major bump

| MUST bump | MUST NOT bump |
|---|---|
| Removing a field | Adding an optional field |
| Renaming a field | Loosening a constraint |
| Changing a field's type | Editing a description, prompt hint, or example |
| Changing cardinality (scalar ↔ repeating) | Reordering fields (the hash does not even move) |
| Optional → required | |
| **Tightening** a constraint | |
| Changing a field's *meaning* while keeping its name and type | |

The last row is the dangerous one: there is no structural signal, and no tooling can catch it.
[ADR-0008](../adr/0008-schema-evolution-policy.md) has the reasoning.

`tests/fixtures/snapshots/schema_hashes.json` is the change detector. When a registered version's
hash moves, the build fails and the contributor either publishes a new major or refreshes the
snapshot with the classification in the commit message. It detects *change*, not *breakage* —
guaranteeing the judgment is made, not making it.

## The schema and the wire are not the same thing

A schema is hashed whole. What goes to the model is a **projection** of it, versioned
`response-shape@1`.

| | Carries | Drops |
|---|---|---|
| Response shape (on the wire) | types, cardinality, `enum`, string formats, `additionalProperties: false` | numeric bounds, array-length bounds, `minLength`/`maxLength`, `pattern` |
| `schema_hash` | everything, constraints and descriptions included | nothing |

Only `minLength`, `maxLength`, and `pattern` are genuinely unenforceable by the provider. Everything
else in the "drops" column is dropped **by choice** — Gemini would enforce `minimum`, `maximum`, and
array-length bounds if asked.

**The consequence, stated plainly because it looks like a bug.** Editing `minimum` on a field moves
`schema_hash`, which invalidates the extraction cache — while changing nothing the model reads. That
is correct: the constraint changes what the result *means* to the validation stage. But it will look
like a spurious cache miss the first time you hit it, so it is written down here rather than
discovered.

Two decisions here were briefly recorded as *forced* by the provider. They are not — research.md R3
records the correction, because calling a chosen constraint "forced" retires it from review:

- Field constraints are declared in the schema and enforced by Milestone 5. Extraction checks shape
  and type parseability only. This is a decision under Principle VII, not a provider limit: pushing a
  bound onto the wire would make violating it the provider's extraction failure rather than a located
  validation failure, and would change behaviour when the provider changes.
- Repetition is bounded to one level. That bound is ours too — the provider supports `$ref` recursion.
  It is an MVP scope decision about how much of the schema → shape → conformance → result path to make
  recursive, refused at registration rather than at first use.

`decimal` travels as a string. A total that has been through a JSON float is not the total that was
printed.

## Schemas are data

```text
schemas/
├── invoice@1.json
├── invoice@2.json          # a second major, live alongside @1
├── receipt@1.json
└── prompts/
    ├── invoice@1.md
    └── …
```

Adding a document type is adding two files. There is no `if schema.name == "invoice"` anywhere in the
engine, and a test fails the build if one appears. That is Principle VI read literally.

The canonical form for hashing is the one [ADR-0002](../adr/0002-blob-and-document-identity.md)
already defines and the kernel already implements, so `schema_hash` reuses an existing rule rather
than inventing one.

`schemas/` is **not** packaged in the wheel — it is data a deployment supplies. Anything that must
run for a `pip install docdoc` user writes its own schema instead.

## What this layer deliberately does not do

**It resolves no grounding.** Every value carries the [ADR-0004](../adr/0004-confidence-semantics.md)
grounding fields and every one is left unresolved — not even the exact tier the kernel's existing
`find()` could satisfy cheaply.

The reason is structural rather than a matter of effort:
[ADR-0003](../adr/0003-content-addressed-artifact-chain.md) makes grounding its own stage with its own
artifact and its own `grounding_version`, so resolving it here would collapse two stages and fold a
grounding input into this stage's identity. Even the exact tier needs the tie-break rule
[ADR-0005](../adr/0005-fuzzy-grounding-specification.md) specifies for a claimed text appearing more
than once, and a temporary rule would change results under an unchanged `grounding_version`.

**So when this milestone ships, every extracted value is ungrounded.** The product's central claim is
not demonstrable end to end until Milestone 4. That is the price of the stage boundary.

What extraction *does* supply is the `claimed_text` Milestone 4 will resolve: the model's verbatim
source text, preserved byte for byte. No trimming, no case folding, no Unicode normalisation — text
this layer altered could not be located afterwards.

**It trusts no self-report.** `model_confidence` is stored verbatim, labelled UNTRUSTED in the field's
own schema description, and routes nothing.

## Identity and reproducibility

```text
options_hash = hash of {schema_identity, schema_hash, prompt_hash, projection_id,
                        model_id, model_version, max_output_tokens, temperature,
                        top_p, top_k, seed, thinking_budget, input_budget_tokens}
artifact_id  = hash of {document_id, extractor_id, extractor_version, options_hash}
```

`extractor_version` embeds the adapter and its SDK — `1.0.0+gemini-1.0.0+google-genai-2.18.1` — the
way ingest's `parser_version` embeds a library version, and for the same reason: the adapter builds
the request and maps the response, so an adapter fix changes results. Recording a change makes it
visible; only folding it makes it invalidating.

Every parameter ADR-0003's `Extract` row names exists here — `temperature`, `top_p`, `seed`, and the
output cap — so the row is followed literally rather than refined. `top_k` and `thinking_budget` are
folded as well, because the ADR's list is a minimum and both change the answer.

Retry, timeout, and deadline live in `TransportSettings`, a separate type reused from the ingest
layer. They cannot change the content of a successful result, so they are absent from identity **by
construction** rather than by discipline.

Extraction does not promise byte-identical results across repeated model calls. `temperature` defaults
to `0.0` and a `seed` can be set — both are recorded — but the provider guarantees no bit-exactness, so
the claim stays unavailable. What it promises instead is that any single result is fully explainable.

## Long documents

A document whose text exceeds the input budget is refused, and the error names the way forward:

```python
part = document.slice(span_over_the_pages_you_want)
result = extract(part, schema="invoice@1", registry=registry, adapter=adapter)
result.provenance.document_id   # the *narrowed* document — the record says what was read
```

Because `slice` preserves original page numbers and geometry byte-identically, a value extracted from
a narrowed document can still be grounded back to its true page. Windowing and result merging are not
performed for you; Milestone 1 built `slice`, `merge`, and the `origin` ranges that survive both, so a
later milestone can.

**The budget guard is a deliberate over-estimate.** The only exact token count available is an API
call that transmits the document in order to answer, which is the very thing the guard exists to
avoid. So the guard estimates locally, and a document that would actually have fitted can be refused.

The ratio is **measured, not assumed**, against the provider's own `count_tokens` across every
committed fixture plus deliberately dense content. It ranges from 5.13 characters per token for English
prose down to a floor of **1.10** for numeric tables and emoji. `CHARS_PER_TOKEN` is **1.20** with a
1.15 safety margin, which sits below the 1.26 that floor and margin together allow — so the estimate is
high everywhere, and highest exactly where it is cheapest to be wrong.

That calibration replaced a guess of 2.5, and the correction is worth knowing because it ran the wrong
way: at 2.5 the guard **under-estimated dense tabular invoice text by 1.72×** — the precise content this
engine reads — and would have passed an over-budget document through to be transmitted. An
over-estimate refuses a document that would have fitted; an under-estimate defeats the guard. Only one
of those is a bug.

The cost is real and quantified rather than waved at: a single linear ratio cannot serve both 1.10 and
5.13, so English prose is over-estimated roughly 5×, and about 215k characters get refused against a
200k-token budget whose true cost is nearer 42k. `Document.slice` is the escape hatch, and it is
friction. The alternative is transmitting the document to find out. See research.md R5 for the table.

**The per-schema prefix is not cached today.** The assembly order is right, but a Gemini cache hit
needs the shared prefix to clear a per-model minimum of 2,048–4,096 tokens, and the `invoice@1` prefix
measures **817**. So the ordering costs nothing and buys nothing yet; it buys something free the moment
schemas or instructions grow past roughly 2.5× their current size. Padding the prefix to become
cache-eligible is a cost decision — paying for tokens whose only purpose is to qualify for a discount —
and is deliberately not taken as a default.

## Failure

| Error | When | Retried |
|---|---|---|
| `SchemaError` | Unknown identity; malformed file; unrecognised type or constraint; duplicate field; repetition bound exceeded; missing prompt | Never |
| `ExtractionError` | Shape mismatch; unparseable value; missing declared field; over-budget input or output; truncated response | Never |
| `ModelProviderError` | Transport, service, and credential failures, and a content refusal | Transient causes only |

A **refusal** is the one that does not look like a failure on the wire: the provider answers with a
*successful* response whose stop reason says it declined. An adapter that reads the content without
checking the stop reason reports a refusal as an answer. Every refusal is permanent — re-sending the
same content gets the same decision.

There is more than one, and they do not mean the same thing:

| Stop reason | What it means |
|---|---|
| `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST` | The output was blocked |
| `RECITATION` | The output resembled copyrighted material. **Not misconduct** — an invoice quoting standard payment terms can trip it |
| `SPII` | Sensitive personal information. For an engine whose job is documents full of names and account numbers, a reason to expect rather than an edge case |
| `promptFeedback.blockReason` | The *prompt* was blocked before generation, so no candidate exists at all |

Reporting `RECITATION` or `SPII` as a safety refusal sends the reader after a problem that is not
there, which is why each keeps its own category in provenance.

Nothing falls back. A failed call never switches model, provider, or schema version; `extract()`
takes one adapter and has no argument a fallback could be threaded through.

## Observability

One `extraction.extract` event per extraction, success and failure alike, carrying identifiers,
model and adapter identity and version, token usage, duration, attempt count, and outcome.

Document text, extracted values, claimed source text, prompt content, and credentials **never** reach
a log. The event's key set is closed in `observe.py`, so a content-bearing field cannot be added at a
call site — that turns "remember not to log the payload" into a build failure.
