<!--
SYNC IMPACT REPORT
==================
Version change: (uninitialized template) → 1.0.0
Bump rationale: MAJOR — initial ratification of the project constitution. All
placeholder tokens replaced with concrete, enforceable governance.

Modified principles: none (initial adoption). Twelve principles established:
  I.    Kernel-First Canonical Document IR
  II.   Source Grounding Is a First-Class Feature
  III.  Deterministic Core, Probabilistic Edges
  IV.   Provider-Agnostic Adapters
  V.    Text-First Document Processing
  VI.   Schema-Driven Generic Extraction
  VII.  Validation Is Separate From Extraction
  VIII. Reproducibility, Provenance, and Versioning
  IX.   Evaluation and Human Correction Are Product Features
  X.    Layered Dependency Direction and Bounded Concepts
  XI.   MVP Discipline and Scale Through Boundaries
  XII.  Open-Source Quality and Kernel Test Rigor

Added sections:
  - MVP Scope Constraints (stack, deferred technology, security, error model)
  - Development Workflow and Quality Gates
  - Open Constitutional Decisions (deferred, blocking-tagged)
  - Governance

Removed sections: none.

Templates requiring updates:
  ✅ .specify/templates/plan-template.md   — Constitution Check gates populated
  ✅ .specify/templates/tasks-template.md  — kernel test mandate overrides "tests optional"
  ✅ .specify/templates/spec-template.md   — reviewed, generic, no change required
  ✅ .claude/skills/speckit-*/SKILL.md     — reviewed, no outdated agent-specific refs
  ⚠  README.md                             — does not exist yet; must state principles
                                              I, II, and XI when authored

Deferred TODOs: see "Open Constitutional Decisions". Six decisions are tagged
BLOCKING and must be resolved before the milestone they gate.

---
AMENDMENT 1.0.0 → 1.1.0 (2026-08-14)
Bump rationale: MINOR — all six BLOCKING open decisions resolved and folded into
binding guidance. Guidance materially expanded; no principle removed or redefined
in a backward-incompatible way.

Decisions adopted (ADR-0001 … ADR-0006, see docs/adr/):
  - Parser strategy: native PDF parser + one cloud document-intelligence parser,
    no standalone OCR engine. `OCRProvider` REMOVED from the Principle IV interface
    list and deferred until an implementation exists (Principle XI compliance).
  - Identity: two-level. blob_id = sha256(bytes); document_id = sha256(blob_id +
    parser_id + parser_version + options_hash). Spans anchor to document_id.
  - Cache: per-stage content-addressed artifact chain; job processing_id is the
    terminal artifact id. Supersedes the reference design's flat formula.
  - Confidence: grounding / grounding_score (trusted) kept separate from
    model_confidence (untrusted); no blended field in MVP.
  - Fuzzy grounding: kernel find() is exact-only; fuzzy lives in the extraction
    layer via rapidfuzz, pinned at threshold 0.90 with a total tie-break order.
  - Normalization: comparison-time match view with an offset map; Document.text
    stays byte-faithful. Full EditMap remains deferred.

Sections amended: Principles I, II, IV, VIII; MVP Scope Constraints (stack,
normalization); Open Constitutional Decisions (six items moved to Resolved).

Templates: no further changes required — the plan-template gate table already
references these principles by number and remains accurate.
-->

# docdoc Constitution

docdoc is an open-source Intelligent Document Processing (IDP) engine. Its differentiator is
not that it can call an LLM. Its differentiator is that it turns unstructured documents into
structured, validated, traceable data while preserving source-level provenance through the
entire pipeline.

The protected capabilities, in priority order, are: document representation, source grounding,
geometry, structured extraction, validation, provenance, and evaluation. When any proposed
change trades one of these away for convenience, performance, or delivery speed, the change is
rejected by default and MUST be justified in the plan's Complexity Tracking table.

## Core Principles

### I. Kernel-First Canonical Document IR

The canonical Document Intermediate Representation is the most important artifact in the
project. The kernel MUST preserve source information rather than reducing a document to text.

A `Document` MUST be able to carry: canonical text, pages, tokens, blocks, tables where the
parser provides them, character spans, token-to-text mapping, page information, normalized
geometry, ingestion provenance, and source blob identity.

Rules:

- `Document` MUST be immutable. Mutation produces a new value, never an in-place edit.
- `Document` MUST NOT store original document bytes; it references them via `BlobRef`.
- Spans are half-open `[start, end)` offsets into `Document.text`, with `0 <= start <= end`.
- Geometry MUST be normalized to `0.0..1.0` with a top-left origin. Provider-native coordinate
  systems are converted in the adapter, never stored raw in the kernel.
- The kernel's only permitted runtime dependency is `pydantic`. The kernel MUST NOT import
  HTTP clients, provider SDKs, PDF/OCR libraries, database drivers, queue clients, or web
  frameworks. A dependency-boundary test MUST enforce this mechanically.
- `locate()`, `find()`, `slice()`, and `merge()` are mandatory kernel operations. `slice()` and
  `merge()` MUST preserve geometry and rebase offsets without losing source mapping.
- `find()` is **exact-only** and stdlib-implemented; fuzzy matching lives in the extraction layer
  (ADR-0005) because the kernel cannot honor it without breaking its dependency rule.
- Identity is two-level (ADR-0002): `blob_id = sha256(bytes)` identifies the source file;
  `document_id = sha256(blob_id + parser_id + parser_version + options_hash)` identifies one
  specific parse. **All spans and geometry anchor to `document_id`**, so spans from different
  parses of the same bytes can never be silently interchanged.

Rationale: every downstream capability — grounding, evaluation, human review, audit — degrades
to guesswork the moment source location is discarded. Discarded provenance cannot be recovered
later; it can only be re-derived by reprocessing, which defeats reproducibility.

### II. Source Grounding Is a First-Class Feature

An extracted value MUST NOT be represented as a bare string.

Every extracted value MUST be able to carry: value, source span(s), page, geometry, confidence,
extraction method, raw representation, alternatives where applicable, and grounding status.

Rules:

- The system MUST be able to answer "where did this value come from?" without re-running the
  parser, OCR, or the LLM.
- Grounding MUST be computed deterministically by docdoc, in this order:
  exact match → fuzzy match → explicitly ungrounded.
- An LLM MUST NOT determine whether its own output is grounded. A model may supply a quote;
  docdoc alone decides whether that quote resolves to a span.
- Ungrounded values MUST remain machine-distinguishable from grounded values at every layer,
  including API responses, CLI output, and persisted results. Silently emitting an ungrounded
  value as if grounded is a constitutional violation.
- Fuzzy matching MUST use a pinned, versioned algorithm and threshold, and MUST define a
  deterministic tie-break rule when several candidate spans match. Pinned as `grounding_version`
  in ADR-0005: normalized Levenshtein at threshold 0.90, tie-break highest score → earliest start
  → shortest span, runners-up recorded in `alternatives`.
- Matching runs against a derived, versioned **match view**, never against a normalized
  `Document.text` (ADR-0006). Returned spans MUST always be source-text spans.
- Confidence is never a single blended number (ADR-0004). `grounding` and `grounding_score` are
  docdoc-computed and trusted; `model_confidence` is a passthrough and MUST be labeled untrusted
  wherever exposed. MVP routing decisions MUST NOT read `model_confidence`.

Rationale: grounding is the property that makes extraction auditable. It is only trustworthy if
it is produced by deterministic code the project controls.

### III. Deterministic Core, Probabilistic Edges

Document representation, span operations, geometry operations, validation primitives, grounding,
and artifact identity MUST be deterministic: identical inputs produce byte-identical outputs.

AI/ML components belong at the edges, behind adapters. Probabilistic model behavior MUST NOT
define a domain invariant. The system MUST remain useful and correct when the LLM or OCR
provider is swapped.

Any function in the kernel that would need a clock, a random source, a network call, or
provider state to produce its result does not belong in the kernel.

Rationale: a probabilistic foundation makes every layer above it untestable and every result
unreproducible.

### IV. Provider-Agnostic Adapters

docdoc MUST NOT be coupled to any single OCR engine, PDF parser, LLM, or cloud provider.

Rules:

- Capability boundaries are internal interfaces. MVP interfaces are `Parser`, `LLMClient`, and
  `ArtifactStore`. `OCRProvider` is **deferred** (ADR-0001): a document-intelligence provider
  satisfies the `Parser` contract, and an interface with no implementation would violate
  Principle XI. It is introduced only when a local OCR engine proves the contracts diverge.
- Provider SDK types MUST NOT appear in `kernel/`, `transform/`, `extraction/` (outside
  `providers/`), or `pipeline/`. Provider exceptions MUST be translated to docdoc's error model
  and MUST NOT leak through the public API.
- Parsers are selected by declared capabilities, never by hard-coded provider name.
- Local/open-source components MUST remain a viable path; cloud providers are optional extras.
- The base install MUST NOT force installation of any provider SDK or OCR engine. Provider
  integrations ship as optional extras.

Rationale: provider coupling is the fastest way to convert an open-source engine into an
unmaintainable vendor wrapper.

### V. Text-First Document Processing

OCR is a capability and an adapter, not the definition of IDP. docdoc MUST NOT assume every
document requires OCR.

The preferred path is: PDF with a usable text layer → native text parser → Document IR. Only
documents that actually require OCR enter the OCR path.

The architecture MUST support text PDFs, scanned PDFs, images, and mixed documents, and MUST
make the text-layer usability decision explicit and inspectable rather than implicit.

Rationale: forcing OCR on text-bearing PDFs destroys accurate native geometry, multiplies cost
and latency, and lowers extraction quality.

### VI. Schema-Driven Generic Extraction

Extraction MUST be driven by explicit, versioned schemas with stable identities such as
`invoice@1`, `purchase_order@1`, `receipt@1`.

Rules:

- The engine operates generically: Document → Document Type → Schema → Extraction → Validation
  → Extraction Result.
- Document-type-specific services (`InvoiceService`, `PurchaseOrderService`, and equivalents)
  MUST NOT exist. Document-type knowledge lives in schema and prompt data, not in code paths.
- Every extraction result MUST reference the exact schema name and version used.

Rationale: hard-coded per-document-type services are how IDP engines become unmaintainable
enterprise services instead of reusable engines.

### VII. Validation Is Separate From Extraction

Extraction answers "what did the model find?". Validation answers "is this result structurally
and semantically acceptable?". These MUST be separate stages with separate outputs.

docdoc MUST support at least: schema/type validation, grounding validation, field-level
validation, and cross-field validation.

Cross-field rules such as `sum(line_items) == total` MUST be implemented as deterministic
validation code. Delegating such a rule to a prompt instruction is a violation.

Validation MUST produce explicit, structured, field-addressable failures — never a silent
correction and never a bare boolean.

Rationale: validation implemented in a prompt is unverifiable, non-reproducible, and cannot be
regression-tested.

### VIII. Reproducibility, Provenance, and Versioning

Every processing result MUST be explainable and reproducible.

Rules:

- Results MUST record: document identity, parser id and version, pipeline version, schema
  version, model, prompt hash, extractor version, processing options, and artifact identity.
- Anything that can change a result MUST be versionable: schemas, parsers, pipelines, prompts,
  models, calibrators, and relevant processing configuration.
- Provenance MUST NOT be silently overwritten. Reprocessing produces a new result with new
  provenance; it does not mutate the prior one.
- Artifacts MUST be immutable and content-addressed, using the per-stage chain in ADR-0003:
  `artifact_id = sha256(input_artifact_id + processor_id + processor_version + options_hash)`.
  Every processor MUST expose a stable `id` and `version` and MUST bump `version` whenever its
  output changes for fixed inputs. Any cache key that omits an input which can change the output
  is a correctness bug, not a performance tradeoff.
- Silent fallback is forbidden. A missing capability or provider failure MUST raise an explicit,
  typed error naming the parser, the required capability, and its availability.

Rationale: a result whose origin cannot be reconstructed after the system evolves has no
evidentiary value, which is the entire point of the product.

### IX. Evaluation and Human Correction Are Product Features

Quality MUST be measurable, not asserted.

Rules:

- The project MUST support a golden dataset and regression evaluation over it.
- Minimum metrics: field accuracy, coverage, missing rate, incorrect rate, grounding rate.
- Metrics MUST be reported at both document level and field level. Vague "AI quality" claims are
  not acceptable evidence in any plan or PR.
- Evaluation runs MUST record git sha, schema version, prompt hash, model, and parser version.
- The result model MUST support human corrections as annotations carrying at minimum: field,
  predicted value, corrected value, source span, reason, annotator, and timestamp.
- Corrections MUST be reusable as evaluation and dataset signal. Supporting corrections MUST NOT
  turn the MVP into a workflow or review platform.
- Confidence MUST eventually support routing decisions (high → automatic, low → human review),
  and confidence semantics MUST be documented and versioned rather than passed through from a
  model's self-report unexamined.

Rationale: an IDP engine without measurement cannot be improved safely, and cannot prove it did
not regress.

### X. Layered Dependency Direction and Bounded Concepts

The dependency direction MUST be, strictly downward:

```text
API → Pipeline → Extraction → Transform → Ingest → Kernel
```

Rules:

- The kernel MUST NOT depend on any layer above it.
- The domain model MUST NOT depend on FastAPI, HTTP, CLI, ORM/database models, or provider SDKs.
- `Document` is the canonical root but MUST NOT become a god object. These concepts stay
  conceptually separate even when their MVP implementation is small: Document, Content, Layout,
  Provenance, Extraction, Validation, Annotations, Artifacts.
- Persistence and transport are outer layers. The core library MUST be usable with no database,
  no object store, and no running service.

Rationale: the execution model will change several times; the domain model must survive those
changes untouched.

### XI. MVP Discipline and Scale Through Boundaries

The first implementation MUST stay intentionally small. Scale is bought with clean boundaries,
not with premature infrastructure.

Rules:

- Local synchronous execution MUST be able to evolve into API → queue → workers → specialized
  OCR/extraction/validation workers without rewriting the domain model. The execution model may
  change; the core contracts MUST NOT.
- Pipeline stages MUST be explicit, identified, and versioned steps, so a queue-based or DAG
  executor can be introduced later without touching domain types. A generic DAG engine MUST NOT
  be built in the MVP.
- Infrastructure listed under "Deferred Technology" MUST NOT be introduced without a concrete,
  documented requirement that a simpler option provably cannot meet.
- Given a choice between a clever abstraction for hypothetical future needs and a simple
  abstraction with a stable boundary, choose the simple one.
- Given a choice between convenience that destroys provenance and slightly more complexity that
  preserves it, preserve provenance.
- Given a choice between a provider-specific implementation in the core and a small provider
  adapter, choose the adapter.
- Every abstraction MUST have a concrete, present-tense reason to exist. Abstractions justified
  only by speculative future use are rejected.

Rationale: most IDP projects fail either by shipping a demo with no boundaries or by building
distributed infrastructure before they have a correct document model.

### XII. Open-Source Quality and Kernel Test Rigor

docdoc is treated as a real open-source project from the first commit.

Rules:

- Public APIs MUST be small, documented, and accompanied by runnable examples.
- The kernel MUST have exhaustive tests. Property-based tests (Hypothesis) are REQUIRED for
  `Span`, `BBox`, `Token`, `Document`, `locate()`, `find()`, `slice()`, and `merge()`.
- These invariants MUST hold and MUST be tested:
  - source spans remain valid across all operations;
  - geometry remains traceable to page and bounding box;
  - `slice()` and `merge()` lose no provenance, and
    `locate(original_span) == locate(remapped_span)`;
  - document identity is stable;
  - provider failure never corrupts the canonical document.
- Property tests MUST cover random documents, random spans, page boundaries, empty spans,
  adjacent spans, and multi-page spans.
- Provider adapters MUST have integration tests; those tests MUST NOT be required to run the
  unit and property suites.
- Dependency boundaries MUST be enforced by an automated test, not by convention.
- The project follows semantic versioning and MUST NOT ship a feature without documentation.

Rationale: the kernel invariants are the foundation every other guarantee rests on. If
`slice`/`merge` can silently lose a mapping, every grounded value in the system is suspect.

## MVP Scope Constraints

**Sanctioned stack.** Python; local filesystem or S3-compatible object storage; PostgreSQL only
where persistence is genuinely required; one native PDF parser (PyMuPDF, text path); one
geometry-capable cloud document-intelligence parser (scanned/image/mixed path); one LLM adapter;
`rapidfuzz` in the extraction layer for grounding. No standalone OCR engine in the MVP
(ADR-0001). The core library MUST remain installable and usable without any of the optional
infrastructure.

**Packaging.** `pip install docdoc` installs kernel, transform, and core extraction contracts
only. Provider integrations are optional extras (for example `docdoc[openai]`, `docdoc[pdf]`).

**Deferred technology.** The following are postponed, not architecturally rejected, and MUST NOT
appear in the MVP without an approved amendment: Kafka, Temporal, Kubernetes, multi-region
deployment, distributed DAG engines, vector databases, RAG infrastructure, workflow/BPM engines,
semantic chunking, EditMap-style normalization, automatic model training, multi-tenant billing,
and a full review UI. Development Compose contains only api, postgres, and object storage.

**Normalization.** `Document.text` is byte-faithful source text. No Unicode normalization, line
joining, hyphen removal, whitespace normalization, or table linearization is applied to the
canonical IR. Normalization for **matching only** is permitted via the versioned, offset-mapped
match view of ADR-0006, whose output is never exposed as `Document.text` and whose returned spans
are always source-text spans. The full EditMap over `Document.text` remains deferred.

**Security.** File size limits, allowed MIME types, request size limits, provider secret
isolation, and temporary file cleanup are MVP requirements. Document contents, PII, API keys,
and prompts containing sensitive documents MUST NOT be logged. Log hashes and identifiers only.

**Error model.** Errors are stable, typed, and provider-neutral: `DocumentError`, `ParserError`,
`UnsupportedDocumentError`, `ParserCapabilityError`, `ExtractionError`, `ProviderError`,
`SchemaError`, `GroundingError`, `ValidationError`, `PipelineError`, `ArtifactError`. Retries
are permitted for LLM/network calls only; validation, grounding, and schema errors MUST NOT be
retried.

**Observability.** Structured logging with request id, processing id, step id, latency,
provider, model, and token usage. OpenTelemetry where practical.

## Development Workflow and Quality Gates

1. **Constitution Check precedes design.** Every `/speckit-plan` MUST evaluate the gates in
   `plan-template.md` before Phase 0 and again after Phase 1. Violations are either removed or
   recorded in Complexity Tracking with a rejected simpler alternative.
2. **Kernel invariants gate everything.** No extraction, grounding, API, or provider work merges
   while kernel property tests are failing or absent.
3. **Layer discipline is machine-checked.** The dependency-direction test runs in CI.
4. **Grounding regressions are blocking.** A change that lowers grounding rate on the golden set
   MUST be justified explicitly; it is not an acceptable side effect of an unrelated change.
5. **Evaluation gate.** Changes to parsers, prompts, models, schemas, or grounding MUST report
   golden-set metrics. The CI gate is advisory during the MVP and becomes blocking once the
   golden dataset reaches its target size.
6. **Provider changes stay in adapters.** A PR that adds a provider SDK import outside an
   adapter directory is rejected on sight.
7. **Every feature ships with documentation and at least one example.**

## Open Constitutional Decisions

These are tensions in the founding principles. Each MUST be decided — via an ADR under
`docs/adr/` — before the work it gates begins. Items marked BLOCKING MUST NOT be resolved
implicitly by an implementation choice.

**Resolved 2026-08-14** (all six original BLOCKING items; see [`docs/adr/`](../../docs/adr/)):

| Decision | Outcome | ADR |
|----------|---------|-----|
| `OCR_IN_MVP` | Native PDF parser + one cloud document-intelligence parser; no standalone OCR engine; `OCRProvider` deferred | [0001](../../docs/adr/0001-parser-and-ocr-strategy-in-mvp.md) |
| `DOCUMENT_IDENTITY` | Two-level identity; spans anchor to `document_id`, not `blob_id` | [0002](../../docs/adr/0002-blob-and-document-identity.md) |
| `PROCESSING_CACHE_KEY` | Per-stage content-addressed chain; job id is the terminal artifact id | [0003](../../docs/adr/0003-content-addressed-artifact-chain.md) |
| `CONFIDENCE_SEMANTICS` | Separate trusted/untrusted fields; no blended confidence in MVP | [0004](../../docs/adr/0004-confidence-semantics.md) |
| `FUZZY_GROUNDING_SPEC` | Kernel `find()` exact-only; fuzzy in extraction via `rapidfuzz`, pinned as `grounding_version` | [0005](../../docs/adr/0005-fuzzy-grounding-specification.md) |
| `NORMALIZATION_VS_GROUNDING` | Comparison-time match view with offset map; `Document.text` stays byte-faithful | [0006](../../docs/adr/0006-comparison-time-match-view.md) |

**Still open:**

- **TODO(SCHEMA_EVOLUTION_POLICY)** — Milestone 3. Define which schema changes require a version
  bump (additive fields, constraint changes, renames) and whether multiple schema versions may
  be served simultaneously.
- **TODO(GOLDEN_DATASET_LICENSING)** — Milestone 6. A public repository cannot ship real
  customer invoices. Decide the sourcing strategy — synthetic, public-domain, or a private
  dataset referenced by hash — and how contributors run evaluation without it.
- **TODO(LICENSE)** — before first public release. Choose the OSS license (Apache-2.0
  recommended for patent grant in an enterprise-adjacent project).
- **TODO(PRE_1_0_VERSIONING)** — before first public release. Principle XII mandates semantic
  versioning while the kernel API is expected to churn. Confirm the `0.x` policy and what
  stability, if any, is promised before `1.0.0`.

## Governance

This constitution supersedes all other development practices, conventions, and preferences in
this repository. Where a plan, spec, task list, review comment, or generated code conflicts with
it, the constitution wins.

**Amendment procedure.** Amendments are proposed as a change to this file, accompanied by: the
principle(s) affected, the rationale, the version bump and its justification, and a migration
note for any artifact the change invalidates. An amendment is adopted when the change is merged.
Templates and dependent artifacts MUST be updated in the same change.

**Versioning policy.** This document uses semantic versioning:

- **MAJOR** — a principle is removed or redefined in a backward-incompatible way, or governance
  changes such that previously compliant work becomes non-compliant.
- **MINOR** — a principle or section is added, or existing guidance is materially expanded.
- **PATCH** — clarification, rewording, typo, or non-semantic refinement.

**Compliance review.** Every plan runs the Constitution Check gate. Every PR that touches the
kernel, a layer boundary, a provider adapter, or the grounding path MUST state which principles
it engages. Complexity that violates a principle is permitted only when recorded in the plan's
Complexity Tracking table with the simpler alternative and the concrete reason it is
insufficient. Unjustified violations are rejected regardless of the code's quality.

**Precedence for unresolved items.** Where an "Open Constitutional Decision" is unresolved,
implementers MUST NOT resolve it silently in code. Raise it, decide it, record it.

**Version**: 1.1.0 | **Ratified**: 2026-08-14 | **Last Amended**: 2026-08-14
