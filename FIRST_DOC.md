# docdoc — MVP Technical Design & Implementation Specification

> **Status:** MVP implementation specification
> **Project:** `docdoc`
> **Goal:** Build a production-oriented Intelligent Document Processing (IDP) engine that is small enough to implement now, but has stable architectural boundaries suitable for becoming a serious open-source project.

---

# 1. Objective

docdoc is an **Intelligent Document Processing engine**.

Its responsibility is:

```text
Document
   ↓
Parse
   ↓
Represent without losing source location
   ↓
Extract structured data
   ↓
Ground extracted values to source
   ↓
Validate
   ↓
Return structured result + evidence
```

docdoc is **not** initially:

- a document management system
- a workflow/BPM platform
- a full RAG platform
- a generic vector database
- an ERP integration platform
- a UI-first product
- a distributed orchestration framework

Those may consume docdoc later.

The MVP must solve one problem extremely well:

> Given a PDF/image document and a versioned schema, extract structured information while preserving exactly where every extracted value came from.

---

# 2. MVP Success Criteria

The MVP is successful when the following workflow works end-to-end:

```text
invoice.pdf
    ↓
Parser
    ↓
Document IR
    ↓
LLM extraction
    ↓
Grounding
    ↓
Validation
    ↓
ExtractionResult
```

For example:

```json
{
  "document_id": "sha256:...",
  "schema": {
    "name": "invoice",
    "version": "1"
  },
  "fields": {
    "invoice_number": {
      "value": "INV-001",
      "confidence": 0.97,
      "method": "llm",
      "grounding": "exact",
      "spans": [
        {
          "page": 1,
          "bbox": [0.72, 0.11, 0.91, 0.14]
        }
      ]
    }
  }
}
```

The MVP must also be able to answer:

> "Where did this value come from?"

without re-running OCR or the LLM.

---

# 3. Design Principles

## 3.1 Source location is a first-class concern

Never reduce a document to:

```python
str
```

The canonical representation must preserve:

- pages
- tokens
- text offsets
- geometry
- blocks
- provenance

---

## 3.2 Core must be provider-independent

The kernel must not know about:

- OpenAI
- Anthropic
- Azure
- AWS
- Google
- PaddleOCR
- Tesseract
- LiteLLM

Providers belong behind adapters.

---

## 3.3 MVP is synchronous internally, asynchronous externally

Do not introduce Kafka or Temporal in the first implementation.

The core pipeline can initially be:

```python
result = await pipeline.process(document, schema)
```

The API can expose:

```text
POST /documents
POST /documents/{id}/extract
GET  /documents/{id}
```

The architecture must nevertheless make processing stages explicit so a queue-based executor can be added later without rewriting the domain.

---

## 3.4 Every processing result is reproducible

Record:

- parser ID
- parser version
- schema version
- model
- prompt hash
- pipeline version
- extraction method
- timestamp

The goal is:

```text
"What produced this result?"
```

must always have an answer.

---

## 3.5 No silent fallback

Missing capability or provider failure must produce an explicit error.

Bad:

```text
geometry requested
    ↓
parser cannot provide geometry
    ↓
silently continue
```

Good:

```text
ParserCapabilityError:
  parser=pdf_text
  required=geometry
  available=false
```

---

# 4. Architecture

The MVP uses the following logical layers:

```text
L5  API
L4  Pipeline
L3  Extraction
L2  Transform
L1  Ingest
L0  Kernel
```

Unlike the original design, evaluation and orchestration infrastructure are initially kept small.

They must remain independent modules, but do not require separate distributed services.

Dependency direction:

```text
API
 ↓
Pipeline
 ↓
Extraction
 ↓
Transform
 ↓
Ingest
 ↓
Kernel
```

The reverse dependency is forbidden.

The kernel must never import:

```text
httpx
openai
anthropic
boto3
pypdfium2
pytesseract
fastapi
```

---

# 5. Repository Structure

Use a Python package layout:

```text
docdoc/
├── pyproject.toml
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── docs/
│   ├── architecture.md
│   ├── concepts/
│   │   ├── document.md
│   │   ├── extraction.md
│   │   └── grounding.md
│   └── adr/
│
├── src/
│   └── docdoc/
│       ├── kernel/
│       │   ├── document.py
│       │   ├── geometry.py
│       │   ├── span.py
│       │   ├── token.py
│       │   ├── block.py
│       │   ├── table.py
│       │   ├── provenance.py
│       │   ├── blob.py
│       │   └── errors.py
│       │
│       ├── ingest/
│       │   ├── base.py
│       │   ├── registry.py
│       │   └── parsers/
│       │       ├── pdf.py
│       │       └── image.py
│       │
│       ├── transform/
│       │   ├── view.py
│       │   └── chunk.py
│       │
│       ├── extraction/
│       │   ├── schema.py
│       │   ├── result.py
│       │   ├── value.py
│       │   ├── extractor.py
│       │   ├── grounding.py
│       │   ├── prompt.py
│       │   └── providers/
│       │       └── openai.py
│       │
│       ├── pipeline/
│       │   ├── pipeline.py
│       │   ├── step.py
│       │   └── context.py
│       │
│       ├── evaluation/
│       │   ├── dataset.py
│       │   └── metrics.py
│       │
│       └── api/
│           ├── app.py
│           └── routes/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── property/
│   └── fixtures/
│
└── examples/
    ├── invoice.py
    └── basic_extraction.py
```

The exact implementation can evolve, but these boundaries must remain.

---

# 6. L0 — Kernel

The kernel is the most important part of the project.

It contains only data structures and deterministic operations.

Dependencies:

```text
pydantic
```

Nothing else.

---

# 7. Span

```python
class Span(NamedTuple):
    start: int
    end: int
```

Rules:

```text
0 <= start <= end
```

Offsets refer to the canonical `Document.text`.

A span is half-open:

```text
[start, end)
```

---

# 8. Geometry

```python
class BBox(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
```

Coordinates are normalized:

```text
0.0 <= coordinate <= 1.0
```

Origin:

```text
top-left
```

Geometry must not depend on the OCR provider's native coordinate system.

Adapters perform conversion.

---

# 9. Token

```python
class Token(NamedTuple):
    text: str
    span: Span
    geometry: Geometry
    ocr_conf: float | None
```

A token must always be traceable to:

```text
Document.text
+
page
+
geometry
```

---

# 10. Document

Canonical document model:

```python
class Document:
    id: DocumentId
    text: str
    pages: tuple[Page, ...]
    tokens: SpanIndex[Token]
    blocks: tuple[Block, ...]
    tables: tuple[Table, ...]
    provenance: IngestProvenance
    source: BlobRef
```

`Document` must be immutable.

Do not store document bytes inside `Document`.

Use:

```python
BlobRef
```

instead.

---

# 11. Required Kernel Operations

The following four operations are mandatory:

```python
doc.locate(span)
doc.find(text)
doc.slice(span)
Document.merge(documents)
```

## `locate`

```python
doc.locate(span)
    -> tuple[Geometry, ...]
```

Returns the physical locations represented by the span.

---

## `find`

```python
doc.find(
    text,
    fuzzy=False
) -> tuple[Span, ...]
```

Used for grounding extracted values.

---

## `slice`

```python
doc.slice(span)
```

Must preserve geometry.

No operation may create a slice that loses source mapping.

---

## `merge`

```python
Document.merge(parts)
```

Must automatically rebase text offsets.

---

# 12. Property Tests

Before implementing extraction, create property tests for:

```text
slice → merge
```

Required invariant:

```text
locate(original_span)
==
locate(remapped_span)
```

Test:

- random documents
- random spans
- page boundaries
- empty spans
- adjacent spans
- multi-page spans

This is a foundational correctness test.

Do not proceed to production extraction until this is stable.

---

# 13. L1 — Ingest

Define a provider-neutral parser interface:

```python
class Parser(Protocol):

    id: str
    version: str
    capabilities: Capabilities

    def parse(
        self,
        blob: Blob,
        options: ParseOptions
    ) -> Document:
        ...
```

Capabilities:

```python
class Capabilities:
    geometry: bool
    tables: bool
    handwriting: bool
    text: bool
```

The parser registry selects based on capabilities.

Example:

```python
parser_registry.select(
    require=Capabilities(geometry=True)
)
```

Do not select parsers by hard-coded provider names.

---

# 14. MVP Parser Strategy

The MVP should have **two parser implementations**.

## Parser A — Local PDF

Purpose:

```text
fast
cheap
offline
```

Use a PDF extraction library capable of providing text and coordinates where possible.

This parser establishes the local development path.

---

## Parser B — Cloud Document Intelligence

Implement one geometry-capable provider adapter.

Recommended first provider:

```text
Azure Document Intelligence
```

or another provider chosen according to actual project infrastructure.

The architecture must make providers interchangeable.

---

# 15. Content-Addressed Identity

Document identity:

```text
document_id = SHA256(original_bytes)
```

Processing artifact identity:

```text
artifact_id =
SHA256(
    input_artifact_id
    + processor_id
    + processor_version
    + options_hash
)
```

This enables deterministic caching.

Do not use in-memory TTL cache as the canonical cache.

---

# 16. L2 — Transform

MVP intentionally keeps Transform small.

Implement:

```text
View
Chunk
```

## View

```python
class View:
    document: Document
    window: Span
```

A view must be zero-copy.

It must not create another independent document string.

---

# 17. Chunking

MVP supports:

```text
Page-based chunking
```

and:

```text
Span-based chunking
```

Do not implement sophisticated semantic chunking initially.

The first objective is correctness.

Later:

```text
Token chunking
Heading chunking
Table-aware chunking
Semantic chunking
```

can be added behind the same interface.

---

# 18. Normalization

Do **not** implement EditMap in MVP.

Initial rule:

> `Document.text` remains source text.

No:

- Unicode normalization
- line joining
- hyphen removal
- table linearization
- whitespace normalization

until the source-map implementation is proven.

If normalization is later added, it must be represented as a reversible transformation.

---

# 19. L3 — Extraction

Extraction is schema-driven.

Example:

```python
class Invoice(Schema):
    invoice_number: str
    invoice_date: date
    vendor: str
    total: Money
```

Every schema must have:

```text
name
version
```

Example:

```text
invoice@1
invoice@2
purchase_order@1
```

Schema version is part of every extraction result.

---

# 20. Extraction Value

Never return raw values alone.

Use:

```python
class Value(Generic[T]):
    value: T | None
    spans: tuple[Span, ...]
    confidence: float | None
    method: ExtractionMethod
    raw: str | None
    alternatives: tuple[Candidate, ...]
```

Example:

```json
{
  "value": "INV-001",
  "spans": [
    {
      "start": 128,
      "end": 135
    }
  ],
  "confidence": 0.96,
  "method": "llm",
  "raw": "INV-001"
}
```

---

# 21. Extraction Provider Interface

Do not expose provider SDK types outside adapters.

Define:

```python
class LLMClient(Protocol):

    async def structured_extract(
        self,
        prompt: str,
        schema: ExtractionSchema
    ) -> ProviderResponse:
        ...
```

Provider-specific code stays in:

```text
extraction/providers/
```

The rest of docdoc sees only the internal interface.

---

# 22. MVP LLM Strategy

Start with one provider.

Do not build a multi-provider abstraction zoo on day one.

The abstraction should exist, but only one implementation is required.

The first implementation must support:

```text
structured output
JSON schema
```

Extraction request:

```text
Document/View
    ↓
Prompt
    ↓
LLM
    ↓
structured response
```

---

# 23. Grounding

Every extracted field should provide:

```text
value
+
quote
```

Example:

```json
{
  "value": "INV-001",
  "quote": "Invoice No: INV-001"
}
```

Grounding algorithm:

```text
quote
  ↓
exact find
  ↓
found?
 ├── yes → grounded_exact
 │
 └── no
      ↓
   fuzzy find
      ↓
   found?
    ├── yes → grounded_fuzzy
    └── no  → ungrounded
```

Grounding must be deterministic.

The LLM must never be responsible for determining whether its own output is grounded.

---

# 24. Grounding Confidence

MVP uses:

```text
exact      → high
fuzzy      → reduced
ungrounded → low
```

Do not implement statistical calibration yet.

The result model should nevertheless reserve:

```python
confidence
calibrator_version
```

for future compatibility.

---

# 25. L4 — Validation / Assure

MVP has three validation levels.

## Structural

```text
required fields
type correctness
schema correctness
```

## Extraction

```text
grounding
confidence
```

## Domain

Examples:

```text
total == sum(line_items)
date is valid
quantity >= 0
```

Validation must produce explicit errors.

Example:

```json
{
  "field": "total",
  "status": "invalid",
  "reason": "does_not_match_line_item_sum"
}
```

---

# 26. L5 — Pipeline

MVP pipeline is a deterministic sequence:

```text
INGEST
  ↓
PARSE
  ↓
EXTRACT
  ↓
GROUND
  ↓
VALIDATE
  ↓
RESULT
```

Do not implement a generic DAG engine yet.

The pipeline must nevertheless use explicit steps:

```python
class PipelineStep(Protocol):
    id: str
    version: str

    async def execute(
        self,
        context: PipelineContext
    ) -> PipelineContext:
        ...
```

This allows the implementation to evolve into DAG execution later.

---

# 27. Pipeline Context

```python
class PipelineContext:
    document: Document
    schema: Schema
    artifacts: ArtifactStore
    metadata: ProcessingMetadata
```

Do not put provider-specific state inside the context.

---

# 28. Idempotency

Every processing request must have:

```text
document_id
pipeline_version
schema_version
```

Identity:

```text
processing_id =
SHA256(
    document_id
    + pipeline_version
    + schema_version
)
```

If the same processing request is executed twice, the second execution should reuse the existing artifact where possible.

---

# 29. Artifact Store

MVP interface:

```python
class ArtifactStore(Protocol):

    async def get(
        self,
        artifact_id: str
    ) -> Artifact | None:
        ...

    async def put(
        self,
        artifact: Artifact
    ) -> ArtifactRef:
        ...
```

Local implementation:

```text
filesystem
```

Production/open-source implementation:

```text
S3-compatible storage
```

Recommended:

```text
MinIO
```

Do not make the core depend directly on MinIO.

---

# 30. Metadata Store

MVP:

```text
PostgreSQL
```

Store:

```text
documents
processing_jobs
processing_steps
schemas
extraction_results
annotations
```

Do not store original document bytes in PostgreSQL.

---

# 31. Database Model

Minimum tables:

```text
documents
----------------
id
sha256
mime_type
size
created_at


processing_jobs
----------------
id
document_id
pipeline_version
schema_name
schema_version
status
created_at
completed_at


processing_steps
----------------
id
job_id
step
version
status
started_at
completed_at
error
artifact_id


schemas
----------------
name
version
definition
created_at


extraction_results
----------------
id
job_id
schema_name
schema_version
result
created_at


annotations
----------------
id
job_id
field
predicted_value
corrected_value
span
annotator
created_at
```

Use JSONB where appropriate.

Do not over-normalize the extraction result in MVP.

---

# 32. API

MVP REST API:

```text
POST /v1/documents
```

Upload a document.

Response:

```json
{
  "document_id": "..."
}
```

---

```text
GET /v1/documents/{id}
```

Returns document metadata.

---

```text
POST /v1/documents/{id}/extract
```

Request:

```json
{
  "schema": "invoice",
  "version": 1
}
```

Response:

```json
{
  "job_id": "..."
}
```

---

```text
GET /v1/jobs/{id}
```

Returns processing status and result.

---

```text
GET /v1/jobs/{id}/result
```

Returns:

```text
ExtractionResult
```

---

# 33. CLI

The project should also provide:

```bash
docdoc parse invoice.pdf
```

```bash
docdoc extract invoice.pdf --schema invoice
```

```bash
docdoc inspect invoice.pdf
```

```bash
docdoc eval ./dataset
```

CLI is important for open-source adoption.

A developer should not need to deploy five services just to test docdoc.

---

# 34. Evaluation

Evaluation is part of the MVP.

Create:

```text
eval/
├── datasets/
│   └── invoices/
├── predictions/
└── reports/
```

Each golden document contains:

```json
{
  "document": "invoice-001.pdf",
  "expected": {
    "invoice_number": "INV-001",
    "vendor": "ABC",
    "total": 125000000
  }
}
```

Minimum metrics:

```text
field accuracy
coverage
missing rate
wrong rate
grounding rate
```

Do not optimize for generic ML metrics that do not map to business behavior.

---

# 35. Golden Dataset

Start with:

```text
50–100 documents
```

Recommended MVP dataset:

```text
30 invoices
20 receipts
20 purchase orders
10 other documents
```

If only invoice extraction is implemented initially, use:

```text
50+ invoices
```

Include:

- clean PDFs
- scanned PDFs
- low-quality scans
- multi-page documents
- different layouts
- missing fields
- rotated pages
- tables

Do not use only perfect demo documents.

---

# 36. Regression Test

Every extraction change should be evaluated against the golden set.

Track:

```text
git_sha
schema_version
prompt_hash
model
parser_version
```

A regression should be visible before merge.

For MVP, the CI gate can initially be advisory.

Later:

```text
accuracy regression > threshold
    ↓
CI failure
```

---

# 37. Human-in-the-loop

HITL is not required for the first API release, but the result model must support it.

A field correction:

```text
prediction
    ↓
human correction
```

must create an annotation:

```json
{
  "field": "vendor",
  "predicted": "ABC Ltd",
  "corrected": "ABC Trading",
  "reason": "wrong_entity"
}
```

This becomes future evaluation data.

---

# 38. Observability

MVP:

```text
structured logging
request ID
processing ID
step ID
latency
provider
model
token usage
```

Use OpenTelemetry where practical.

Every processing step should be observable.

Example:

```text
job=abc
step=extract
model=gpt-...
latency=2.31s
input_tokens=4231
output_tokens=812
```

---

# 39. Error Model

Define stable internal errors:

```text
DocumentError
ParserError
UnsupportedDocumentError
ParserCapabilityError

ExtractionError
ProviderError
SchemaError

GroundingError
ValidationError

PipelineError
ArtifactError
```

Provider exceptions must not leak through the public API.

---

# 40. Retry Policy

MVP:

```text
LLM/network:
    retry 2–3 times
    exponential backoff

validation:
    no retry

grounding:
    no retry

invalid schema:
    no retry
```

Do not add distributed retry infrastructure yet.

---

# 41. Security

MVP must include:

```text
file size limits
allowed MIME types
request size limits
provider secret isolation
temporary file cleanup
```

Never log:

```text
document contents
PII
API keys
full prompts containing sensitive documents
```

Log hashes and identifiers instead.

---

# 42. Open-source Boundary

The core package must be installable without cloud services.

Minimal:

```bash
pip install docdoc
```

should install only:

```text
kernel
transform
core extraction contracts
```

Provider integrations are optional extras:

```bash
pip install docdoc[azure]
pip install docdoc[openai]
pip install docdoc[pdf]
```

The project must not force users to install:

```text
Playwright
AWS SDK
Azure SDK
OCR engine
LLM SDK
```

unless they explicitly use those adapters.

---

# 43. Local Development

A developer should be able to run:

```bash
git clone ...
cd docdoc

uv sync

docdoc extract examples/invoice.pdf \
  --schema invoice
```

Optional infrastructure:

```text
PostgreSQL
MinIO
```

can be started with:

```bash
docker compose up -d
```

But the core extraction library must be usable without them.

---

# 44. Docker

Provide:

```text
Dockerfile
docker-compose.yml
```

Compose should contain only development infrastructure:

```text
docdoc-api
postgres
minio
```

Do not include Kafka, Temporal, Kubernetes, OpenSearch, Grafana, etc. in MVP Compose.

That would make the project look enterprise while making onboarding miserable.

---

# 45. Provider Architecture

Provider adapters:

```text
docdoc/
└── adapters/
    ├── parsers/
    │   ├── azure_di.py
    │   ├── pdf.py
    │   └── ...
    │
    └── llm/
        ├── openai.py
        ├── anthropic.py
        └── ...
```

Provider implementations must conform to internal interfaces.

No provider SDK type may appear in:

```text
kernel/
transform/
extraction/
pipeline/
```

---

# 46. Versioning

Every important artifact is versioned.

At minimum:

```text
Document
Parser
Pipeline
Schema
Prompt
Model
Extractor
Calibrator
```

A result must be reproducible from:

```text
document_id
parser_version
pipeline_version
schema_version
prompt_hash
model
```

---

# 47. Future Scale Path

The MVP must evolve through adapters, not rewrites.

## MVP

```text
FastAPI
Postgres
Filesystem/S3
Local execution
One OCR provider
One LLM provider
```

## Small production

```text
FastAPI
Postgres
S3/MinIO
Worker
Redis or queue
OpenTelemetry
```

## Large deployment

```text
API
  ↓
Queue
  ↓
Workers
  ├── OCR workers
  ├── extraction workers
  ├── validation workers
  └── evaluation workers

Postgres
Object Storage
Observability
```

## Very large deployment

```text
                 API
                  │
                Queue
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
      OCR       Extract    Validate
      pool       pool        pool
        │         │           │
        └─────────┼───────────┘
                  ▼
              Artifacts
                  │
          ┌───────┴───────┐
          ▼               ▼
       Postgres        Object Store
```

The core package does not change.

Only execution infrastructure changes.

---

# 48. What NOT to Build in MVP

Explicitly postpone:

```text
Temporal
Kafka
Kubernetes
multi-region
distributed DAG engine
semantic chunking
EditMap normalization
vector database
RAG
automatic model training
complex classification trees
complex document splitting
multi-tenant billing
full review UI
workflow engine
```

These are not rejected architecturally.

They are deliberately postponed.

---

# 49. MVP Milestones

## Milestone 1 — Kernel

Implement:

```text
Span
BBox
Geometry
Token
Block
Page
Document
BlobRef
SpanIndex
```

and:

```text
locate
find
slice
merge
```

Acceptance criteria:

```text
100% unit coverage for core operations
property tests for slice/merge
no external I/O
```

---

## Milestone 2 — Parser

Implement:

```text
local PDF parser
one geometry-capable provider parser
```

Acceptance:

```text
PDF → Document
```

with:

```text
text
pages
tokens
geometry
```

---

## Milestone 3 — Extraction

Implement:

```text
Schema
Value
ExtractionResult
LLMClient
one LLM provider
```

Acceptance:

```text
invoice.pdf
    ↓
Invoice schema
    ↓
structured result
```

---

## Milestone 4 — Grounding

Implement:

```text
exact grounding
fuzzy grounding
```

Acceptance:

```text
every extracted field
    ↓
source span
    ↓
page + bbox
```

---

## Milestone 5 — Validation

Implement:

```text
schema validation
field validation
cross-field validation
```

---

## Milestone 6 — Evaluation

Implement:

```text
golden dataset
field accuracy
coverage
wrong
missing
grounding
```

and CLI:

```bash
docdoc eval ./dataset
```

---

## Milestone 7 — API

Implement:

```text
upload
extract
job status
result
```

---

## Milestone 8 — Packaging

Provide:

```text
PyPI package
Docker image
CLI
documentation
examples
tests
CI
```

At this point docdoc is a legitimate MVP open-source project.

---

# 50. Definition of Done

The MVP is considered complete when this works:

```text
                     invoice.pdf
                          │
                          ▼
                     docdoc CLI
                          │
                          ▼
                       Parser
                          │
                          ▼
                      Document
                          │
                          ▼
                    Invoice Schema
                          │
                          ▼
                         LLM
                          │
                          ▼
                      Grounding
                          │
                          ▼
                      Validation
                          │
                          ▼
                  ExtractionResult
```

And the user can inspect:

```text
Invoice Number
    INV-001
       │
       └── Page 1
           └── Bounding Box

Vendor
    ABC Trading
       │
       └── Page 1
           └── Bounding Box

Total
    125,000,000 VND
       │
       └── Page 2
           └── Bounding Box
```

The system must be able to answer:

```text
What did the model extract?
Where did it come from?
How confident was it?
Which model produced it?
Which schema version?
Which parser?
Can I reproduce it?
Was it subsequently corrected by a human?
```

---

# 51. Long-term Open-source Vision

docdoc should eventually become:

```text
                docdoc
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
    Parsing    Extraction   Evaluation
       │          │          │
       ▼          ▼          ▼
      OCR       LLM/VLM    Golden Sets
       │          │          │
       └──────────┼──────────┘
                  ▼
              IDP Engine
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
       SDK       API       Workers
        │         │         │
        └─────────┼─────────┘
                  ▼
          Enterprise Systems
          ERP / CRM / EAM
```

The core differentiator is not:

> "docdoc can call an LLM."

Everyone can do that.

The differentiator is:

> **docdoc turns unstructured documents into structured, validated, traceable data while preserving source-level provenance throughout the entire processing pipeline.**

That means:

```text
Extraction
+
Grounding
+
Geometry
+
Validation
+
Reproducibility
+
Evaluation
```

are the product's core primitives.

---

# 52. First Implementation Task

Start with exactly this scope:

```text
src/docdoc/kernel/

Span
BBox
Geometry
Token
Page
Block
Document
BlobRef
SpanIndex

Document.locate()
Document.find()
Document.slice()
Document.merge()
```

Then implement:

```text
tests/property/test_document_invariants.py
```

with Hypothesis.

Do not implement:

```text
Kafka
Temporal
API
UI
LLM
OCR
```

until the kernel invariants pass.

The first deliverable should be a small, dependency-light package that can construct a document manually and prove:

```text
Document
   ↓ slice
Document
   ↓ merge
Document
   ↓ locate
original geometry
```

Once that invariant is solid, every higher-level IDP feature can build on it without compromising source traceability.
