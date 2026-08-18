# docdoc

An open-source **Intelligent Document Processing engine**.

docdoc turns unstructured documents into structured, validated, traceable data while preserving
source-level provenance through the entire pipeline. The differentiator is not that it can call an
LLM — everyone can do that. It is that every extracted value can answer:

> **Where did this come from?**

**Status:** Milestones 1 (kernel), 2 (parsers), and 3 (extraction) implemented. Grounding and
validation are not built yet, so **every extracted value is currently ungrounded** — see
[Roadmap](#roadmap).

## What it does today

Hand it a PDF and ask where a value physically sits:

```python
from docdoc.ingest import CapabilityRequest, parse

document = parse(
    pdf_bytes,
    require=CapabilityRequest(media_type="application/pdf", geometry=True),
)

(span,) = document.find("INV-001")
document.page_for(span)  # (0,)  -> page 1
document.locate(span)  # (Geometry(page_index=0, bbox=BBox(0.45, 0.1, 0.63, 0.13)),)
```

Note that no provider is named. You ask for the capabilities you need; which parser supplies them
is configuration. And the routing decision is on the result, per page:

```python
document.provenance.text_layer.rule_id          # 'text-layer@1'
document.provenance.text_layer.text_layer_usable  # True
document.provenance.text_layer.pages            # per-page verdicts and character counts
```

### Extract structured values against a versioned schema

A schema is **data**, not code. Adding a document type is adding two files; there is no
`InvoiceService` and no `if schema.name == "invoice"` anywhere in the engine.

```python
from docdoc.extraction import SchemaRegistry, default_adapter, extract

registry = SchemaRegistry.from_paths(["schemas/"])
result = extract(
    document,
    schema="invoice@1",
    registry=registry,
    adapter=default_adapter(),   # configuration picks it; this line names no provider
)

result.value_at("total").value          # Decimal('1240.00')  — not a float
result.value_at("total").claimed_text   # '1,240.00'  — byte-faithful, as the model returned it
result.value_at("due_date").present     # False — an explicit absence, not an error
```

Note the identity pair. `invoice@1` is the contract a consumer pins to; `schema_hash` answers a
different question, and both are recorded:

```python
result.provenance.schema_identity   # 'invoice@1'      — did the contract change?
result.provenance.schema_hash       # 'sha256:37a9f6…' — did anything result-affecting change?
result.artifact_id                  # the extraction's own content-addressed identity
```

Rewording a field description moves the hash and not the version, which invalidates the extraction
and **reuses the parse**. [ADR-0008](docs/adr/0008-schema-evolution-policy.md) has the bump rules.

**What extraction deliberately does not do.** It resolves no grounding — every value's `grounding` is
`None` — because that is Milestone 4's stage with its own artifact under
[ADR-0003](docs/adr/0003-content-addressed-artifact-chain.md). What it supplies is the byte-faithful
`claimed_text` that grounding will resolve. And `model_confidence` is stored verbatim, labelled
UNTRUSTED, and routes nothing ([ADR-0004](docs/adr/0004-confidence-semantics.md)).

Run the examples, which need no infrastructure and no credentials at all:

```bash
uv sync --all-extras
uv run python examples/build_document.py                       # kernel only
uv run python examples/parse_pdf.py tests/fixtures/pdf/digital_invoice.pdf
uv run python examples/extract_invoice.py                      # extraction, offline
```

### Installing

```bash
pip install docdoc          # kernel + ingest + extraction contracts; pydantic is the only dependency
pip install docdoc[pdf]     # native PDF text path
pip install docdoc[azure]   # geometry-capable cloud path, for scans and images
pip install docdoc[google]  # the LLM adapter for extraction
```

### Configuration

Which schemas exist, which provider answers, and which model it uses are deployment decisions, so none
of them appears in your code:

```sh
export DOCDOC_SCHEMA_PATHS=/etc/docdoc/schemas   # where default_registry() looks; os.pathsep-separated
export DOCDOC_MODEL_ADAPTERS=gemini              # adapter preference order, comma-separated
export DOCDOC_GEMINI_MODEL=gemini-3.5-flash      # which model answers
export GEMINI_API_KEY=...                        # or GOOGLE_API_KEY
```

Every one has a sensible default and every one can be overridden by an explicit argument — configuration
is the default, not a cage. `DOCDOC_SCHEMA_PATHS` is the exception worth knowing: with neither it nor an
explicit path, the registry is **empty**, because docdoc ships no schema of its own. A schema is your
data, not ours.

> **Licence note.** `docdoc[pdf]` installs [PyMuPDF](https://pymupdf.readthedocs.io/), which is
> **AGPL-3.0** (or a paid commercial licence). docdoc itself is Apache-2.0 and the extra is opt-in,
> so docdoc's own distribution is unaffected — but if you embed `docdoc[pdf]` in a closed-source
> pipeline, the AGPL applies to you. Know this before you install it, not after.
> See [ADR-0001](docs/adr/0001-parser-and-ocr-strategy-in-mvp.md).

## Three principles that shape everything

**1. Source location is a first-class concern.** A document is never reduced to a `str`. The
canonical representation preserves pages, tokens, character spans, normalized geometry, and
ingestion provenance, so any text range resolves back to a page and a bounding box.

**2. Grounding is computed, never self-certified.** An extracted value is not a string — it
carries its source spans, page, geometry, and grounding status. An LLM may propose a quote;
docdoc alone decides whether that quote resolves to a real span. Ungrounded values stay
distinguishable from grounded ones at every layer.

**3. The MVP stays small.** No Kafka, no Temporal, no Kubernetes, no vector database, no workflow
engine. The core library is usable with no database, no object store, and no running service.
Scale comes later through stable boundaries, not premature infrastructure. The execution model
may change; the core contracts must not.

These are three of twenty in the [project constitution](.specify/memory/constitution.md), which
governs every specification, plan, and code change in this repository.

## Architecture

Dependencies flow strictly downward, and the rule is machine-checked in CI:

```text
API → Pipeline → Extraction → Transform → Ingest → Kernel
```

The **kernel** is the bottom layer and depends on nothing above it. Its only runtime dependency is
`pydantic`. It performs no file, network, clock, or random access, so identical inputs always
produce identical outputs — enforced by an AST scan and a runtime audit hook, not by convention.

Identity is two-level ([ADR-0002](docs/adr/0002-blob-and-document-identity.md)):

| | Derived from | Identifies |
|---|---|---|
| `blob_id` | the original bytes | the **source file** |
| `document_id` | blob + parser id + version + options | **one specific parse** of it |

Spans and geometry anchor to `document_id`. Two parsers over the same bytes share a `blob_id` and
get different `document_id`s, so one parse's positions can never be silently applied to another.

## Installation

```bash
pip install docdoc          # kernel only — no provider SDKs, no OCR engine
```

Provider integrations ship as optional extras and are never pulled in unless you ask for them.

## Development

```bash
uv sync --all-extras
uv run pytest tests/unit tests/property      # fast
HYPOTHESIS_PROFILE=thorough uv run pytest    # what CI runs
uv run mypy src/docdoc/kernel                # strict
uv run ruff check . && uv run lint-imports   # lint + layer boundaries
```

The kernel's correctness rests on one property, verified across thousands of generated cases:

```text
locate(span) == merge(partition(document)).locate(span)
```

Cutting a document apart and reassembling it must not change where anything came from. No
higher-layer work merges while that property is failing or absent.

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| 1 | Kernel: Document IR, `locate` / `find` / `slice` / `merge`, identity | **Done** |
| 2 | Parsers: native PDF text path, one geometry-capable cloud provider | **Done** |
| 3 | Schema-driven extraction, one LLM adapter | **Done** |
| 4 | Deterministic grounding: exact → fuzzy → ungrounded | Next |
| 5 | Validation: schema, field, and cross-field rules | Planned |
| 6 | Evaluation: golden dataset, field accuracy, grounding rate | Planned |
| 7 | API and CLI | Planned |

## Documentation

- [Constitution](.specify/memory/constitution.md) — the governing principles
- [Architecture decisions](docs/adr/) — eight accepted ADRs
- [Document concepts](docs/concepts/document.md)
- [Identity model](docs/concepts/identity.md)
- [Kernel API contract](specs/001-kernel-document-ir/contracts/kernel-api.md)
- [Ingest API contract](specs/002-ingest-parser-layer/contracts/ingest-api.md)
- [How ingest works](docs/concepts/ingest.md) — the two paths and the text-layer decision
- [How extraction works](docs/concepts/extraction.md) — the two identities, and the stage boundary with grounding
- [Extraction API contract](specs/003-schema-driven-extraction/contracts/extraction-api.md)
- [Contributing](CONTRIBUTING.md)

## License

[Apache License 2.0](LICENSE). Chosen for its explicit patent grant, which matters for a project
intended to be embedded in enterprise document pipelines — see
[ADR-0007](docs/adr/0007-apache-2-license.md).
