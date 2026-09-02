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

---
AMENDMENT 1.1.0 → 1.1.1 (2026-08-14)
Bump rationale: PATCH — resolves one deferred decision and records it. No
principle is added, removed, or redefined, and no previously compliant work
becomes non-compliant.

  - TODO(LICENSE) resolved: Apache-2.0, chosen for its explicit patent grant
    (ADR-0007). LICENSE added at the repository root; pyproject declares the
    SPDX expression and ships the file in built distributions.

Sections amended: Open Constitutional Decisions (LICENSE moved to Resolved).
Three non-blocking decisions remain open: SCHEMA_EVOLUTION_POLICY,
GOLDEN_DATASET_LICENSING, PRE_1_0_VERSIONING.

---
AMENDMENT 1.1.1 → 1.2.0 (2026-08-17)
Bump rationale: MINOR — resolves the decision gating Milestone 3 and materially
expands Principle VI with binding schema-identity and version-resolution rules.
No principle is removed or redefined; no previously compliant work becomes
non-compliant, because no extraction code exists yet.

  - TODO(SCHEMA_EVOLUTION_POLICY) resolved (ADR-0008). Schema identity is
    two-level: an author-assigned major `schema_version` in `name@version` that
    moves only on a consumer-contract break, and a derived `schema_hash` over
    canonical JSON that moves on any result-affecting edit. Both are recorded in
    every result and both are folded into the extract stage's `options_hash`,
    refining ADR-0003's Extract row. Concurrent majors are allowed; the library
    core takes concrete versions only, with `latest` permitted at an edge that
    records what it resolved to.

Sections amended: Principle VI (two rules added, rationale extended); Principle
VIII (schema bump rules delegated to ADR-0008); Open Constitutional Decisions
(SCHEMA_EVOLUTION_POLICY moved to Resolved). Two non-blocking decisions remain
open: GOLDEN_DATASET_LICENSING, PRE_1_0_VERSIONING.

Templates: no changes required — the plan-template gate table references
principles by number and remains accurate.

---
AMENDMENT 1.2.0 → 1.3.0 (2026-08-20)
Bump rationale: MINOR — resolves the decision gating Milestone 6, adds two
binding rules to Principle IX, and gives quality gate 5 the target size it
referenced but never stated. No principle is removed or redefined; no previously
compliant work becomes non-compliant, because no evaluation code exists yet.

  - TODO(GOLDEN_DATASET_LICENSING) resolved (ADR-0009). The golden dataset is
    two tiers. A public tier is vendored into the repository — synthetic
    documents from committed generators plus permissively licensed or
    public-domain ones — and MUST be sufficient on its own for a complete
    report, evaluable with no credentials and no network. An optional restricted
    tier is referenced by content hash and never committed; a run without it
    produces a report marked partial, never a smaller full one. Every document
    in either tier records its origin and the basis on which docdoc may use it.
    Predictions follow the tier of the document they describe, because a
    recorded prediction carries the values it extracted.

  - Quality gate 5's target size stated: 50 documents and 500 labeled fields in
    the public tier, across at least two schemas with at least twenty documents
    each. Counted on the public tier because gate 5 is a CI gate and CI cannot
    see the restricted tier. Reaching the size does not flip the gate; flipping
    it remains an amendment.

Sections amended: Principle IX (two rules added); Development Workflow and
Quality Gates (gate 5 given its target size); Open Constitutional Decisions
(GOLDEN_DATASET_LICENSING moved to Resolved). One non-blocking decision remains
open: PRE_1_0_VERSIONING.

Templates: no changes required — the plan-template gate table references
principles by number and remains accurate.

---
AMENDMENT 1.3.0 → 1.4.0 (2026-08-20)
Bump rationale: MINOR — Principle X's guidance is materially expanded and its
chain is corrected to the one that exists. No principle is removed and nothing
previously compliant becomes non-compliant: the code already obeyed the real
chain, which is precisely the problem being fixed.

  - Principle X's chain corrected from
    `API -> Pipeline -> Extraction -> Transform -> Ingest -> Kernel` to
    `Evaluation -> Validation -> Grounding -> Extraction -> Ingest -> Kernel`,
    with Recording, Pipeline, and API listed separately as planned and not yet
    built. From Milestone 4 to Milestone 6 this document named layers that did
    not exist (`Transform`, `Pipeline`, `API`) and omitted layers that did
    (`grounding`, `validation`), and the reconciliation lived only in a
    `pyproject.toml` comment and three `research.md` files. Milestone 6 would
    have taken the count of unnamed-but-real layers from two to four.

  - `Transform` is recorded as never built: ADR-0006 placed its transformations
    inside grounding's versioned match view, so the layer it would have been is
    grounding.

  - Two rules added to Principle X: the layers contract in `pyproject.toml` is
    the authoritative form of the chain because it is the one CI checks, and
    this text MUST be amended in the same change that adds a layer to it; and
    the chain MUST name only layers that exist, with planned ones listed
    separately.

  - Principle IV's provider-SDK rule rewritten to name the directories that
    exist rather than `transform/` and `pipeline/`, which do not.

  - Packaging paragraph corrected: the base install carries `pydantic` **and**
    `rapidfuzz`, not `pydantic` alone. Only the kernel is `pydantic`-only.

Sections amended: Principle IV (one rule); Principle X (chain, three rules,
Transform's fate); MVP Scope Constraints (Packaging). No decision moved; one
non-blocking decision remains open: PRE_1_0_VERSIONING.

Templates: no changes required — the plan-template gate table references
principles by number and remains accurate.

---
AMENDMENT 1.4.0 → 1.5.0 (2026-08-22)
Bump rationale: MINOR — Principle X's chain gains four layers and the guidance
around two of their positions. No principle is removed and nothing previously
compliant becomes non-compliant.

  - Principle X's chain extended to
    `API, CLI -> Recording -> Evaluation -> Pipeline -> Validation -> Grounding
    -> Extraction -> Ingest -> Artifacts -> Kernel`, the form the `import-linter`
    contract in `pyproject.toml` now enforces. This amendment lands in the same
    change as that contract, which is what the rule added at 1.4.0 requires.

  - `Artifacts` is recorded as sitting directly ABOVE the kernel. It stores whole
    result models without importing one — the caller names the model at the call
    site — so its dependencies are `pydantic` and two kernel hashing helpers, and
    the lowest position that is true is the honest one.

  - `Pipeline` is recorded as sitting directly ABOVE `Validation`, the highest
    stage it drives, rather than above `Evaluation` where its milestone number
    might suggest. This leaves the `Recording > Evaluation` edge of 1.3.0
    untouched and yields `Recording > Pipeline`, which is what makes the recorder
    a caller of the pipeline rather than a second definition of the stage order.

  - `API` and `CLI` are siblings, not a stack, separated by an `independence`
    contract. Neither may import the other; a position in an ordered list would
    have implied a permission.

  - The "planned and not yet built" list is now empty. Every layer this document
    names exists.

Sections amended: Principle X (chain, one rule clarified). No decision moved; one
non-blocking decision remains open: PRE_1_0_VERSIONING, which Milestone 7
resolves separately via ADR-0011.

Templates: no changes required — the plan-template gate table references
principles by number and remains accurate. Note that gate 11's parenthetical in
`plan-template.md` still quotes the pre-1.4.0 chain and should be corrected when
that template is next touched; it is advisory text, not the enforced contract.

---
AMENDMENT 1.5.0 → 1.5.1 (2026-08-24)
Bump rationale: PATCH — the last open decision moves to Resolved. No principle is
added, removed, or redefined, and no previously compliant work becomes
non-compliant; the policy recorded describes what the project already did.

  - TODO(PRE_1_0_VERSIONING) resolved (ADR-0011). While the major version is `0`,
    a minor bump may break any public API and a patch bump may not, and every
    breaking change ships a changelog entry naming what moved and what to do
    about it. Two surfaces get a deprecation path instead of a silent break —
    the kernel's identity derivations and the on-disk artifact format — because a
    change to either invalidates data that already exists on somebody else's
    disk, which is the one class of breakage upgrading cannot repair. Nothing
    else is promised stable before `1.0.0`.

  - The mechanism that path relies on already runs: `FileArtifactStore.get`
    treats an incompatible `artifact_format_version` as a logged miss, so an
    artifact written under an older format is recomputed rather than misread.

Sections amended: Open Constitutional Decisions (PRE_1_0_VERSIONING moved to
Resolved; the "Still open" list is now empty and says so). Principle XII is
untouched — ADR-0011 states what "follows semantic versioning" means below
`1.0.0` rather than changing the requirement.

Templates: no changes required.

---
AMENDMENT 1.5.1 → 1.5.2 (2026-08-24)
Bump rationale: PATCH — a clarification, not a widening. `pydantic_core` is
recorded as being `pydantic` rather than admitted as a second dependency, so
nothing previously compliant becomes non-compliant and nothing previously
forbidden becomes permitted.

  - Principle I gains a sub-clause stating that `pydantic_core` counts as
    `pydantic`. It is pydantic's own compiled runtime: installing pydantic
    installs it, pydantic cannot function without it, and it is not separately
    declared in `pyproject.toml`. The kernel reaches it for one purpose —
    `SpanIndex.__get_pydantic_core_schema__`, without which a `Document` cannot
    be serialised and the parse stage has no storable artifact.

  - Recorded here because it had been argued **only in a comment inside
    `tests/unit/test_kernel_purity.py`**, whose own failure message says that
    adding a kernel dependency requires a constitution amendment. A permission
    that lives in the test enforcing the prohibition is not a governed decision,
    and `/speckit-converge` was right to raise it as CRITICAL.

Sections amended: Principle I (one sub-clause). No decision moved; the open list
stays empty.

Templates: no changes required.

---
AMENDMENT 1.5.2 → 1.6.0 (2026-08-28)
Bump rationale: MINOR — guidance is materially widened. A process type that a
literal reading of the Development Compose sentence excluded is now permitted, so
this is not a clarification and must not be recorded as one.

Principles affected: XI (MVP Discipline and Scale Through Boundaries), via the
MVP Scope Constraints "Deferred technology" paragraph. No principle is removed or
redefined; nothing previously compliant becomes non-compliant.

  - Development Compose gains `worker`. Principle XI itself mandates that local
    synchronous execution be able to become "API → queue → workers", and the
    composition sentence as written permitted three containers, so the document
    required an evolution its own scope constraint forbade demonstrating. A
    worker is the docdoc image already in the composition at a different entry
    point — it acquires no dependency the api container does not already have.

  - Recorded as an amendment rather than settled by reading. `specs/009`'s plan
    argued that "only" governs third-party infrastructure and not docdoc's own
    processes. That argument is sound and was made in the open, and it is still
    the wrong instrument: Governance says the constitution wins where a plan
    conflicts with it, so a plan that reinterprets a constitutional sentence to
    make itself compliant inverts the precedence. `/speckit-analyze` raised it as
    CRITICAL on exactly that ground.

  - The "multi-tenant billing" entry gains a sentence distinguishing billing from
    tenant isolation. Milestone 9 scopes runs, blobs, and artifacts per tenant;
    it meters and invoices nothing. The two are one phrase apart and the deferral
    should not be read to forbid the isolation.

Sections amended: MVP Scope Constraints (Deferred technology). No decision moved;
the open list stays empty.

Migration note: no artifact is invalidated. `specs/009-asynchronous-runs/plan.md`
retains its Gate 12 argument for the other two constraints and now cites this
amendment for the third rather than carrying the reinterpretation.

Templates: no changes required — the plan-template gate table references
Principle XI by number and remains accurate.

---
AMENDMENT 1.6.0 → 1.7.0 (2026-08-28)
Bump rationale: MINOR — a layer is added to the chain in Principle X. Same class
of change as 1.4.0 → 1.5.0, which added four.

Principles affected: X (Layered Dependency Direction and Bounded Concepts).

  - `Runs` joins the chain as a sibling of `Recording`, above `Pipeline`. It is
    Milestone 9's transport layer: it accepts a request, records it, and hands it
    to a worker that calls `pipeline.run()` unchanged.

  - Recorded here because Principle X requires it in as many words: "This text
    MUST be amended in the same change that adds a layer to it." That rule exists
    because from Milestone 4 to Milestone 6 the prose and the contract disagreed
    and nobody noticed. `tests/unit/test_layer_boundaries.py` is what now
    notices, and it is what raised this — the layer landed in `pyproject.toml`
    and the suite went red in the same run.

Sections amended: Principle X (the chain, and one paragraph on the new sibling
pair). No decision moved; the open list stays empty.

Migration note: no artifact is invalidated. The `pyproject.toml` layers contract
remains the authoritative form and already carries the change.

Templates: no changes required.
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
  - `pydantic_core` counts as `pydantic` and not as a second dependency. It is pydantic's own
    compiled runtime: `pip install pydantic` installs it, pydantic cannot function without it, and
    it is not separately declared in `pyproject.toml`. The kernel reaches it for exactly one
    purpose — `SpanIndex.__get_pydantic_core_schema__`, which is the documented way to make a
    non-pydantic class serialisable and is what lets a `Document` survive a round trip through the
    artifact store. Without it the parse stage has no storable artifact and ADR-0003's central
    promise is unkeepable. It is named here because the boundary test reads top-level module names
    and cannot know the two ship together, and because a permission argued only in a test comment
    is not recorded.
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
- Provider SDK types MUST NOT appear in any layer outside an adapter directory — today that means
  `kernel/`, `grounding/`, `validation/`, `evaluation/`, and `extraction/` outside
  `extraction/adapters/`, with `ingest/` restricted to `ingest/parsers/`. Provider exceptions MUST
  be translated to docdoc's error model and MUST NOT leak through the public API.
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
- Schema identity is two-level per ADR-0008: an author-assigned major `schema_version` inside
  `name@version`, which changes only when the consumer contract breaks, and a derived
  `schema_hash` over the schema's canonical JSON. Both MUST be recorded in every result and both
  MUST be folded into the extract stage's `options_hash`. The bump rules are fixed by ADR-0008 and
  MUST NOT be reinterpreted per schema.
- An extraction request MUST name a concrete `name@version`. `latest` resolution is permitted only
  at an API or CLI edge, and only when the resolved version is recorded in the result. Multiple
  majors of one schema MAY be served concurrently.

Rationale: hard-coded per-document-type services are how IDP engines become unmaintainable
enterprise services instead of reusable engines. A schema version that moves for reasons a consumer
cannot predict is not a contract.

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
  models, calibrators, and relevant processing configuration. For schemas, which edits move the
  version and which move only the content hash is fixed by ADR-0008.
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
- The golden dataset MUST have a **public tier** vendored into the repository that any contributor
  can evaluate with no credentials and no network, and that is sufficient on its own to produce a
  complete report. An optional **restricted tier** MAY exist, referenced by content hash and never
  committed; a run without it MUST produce a report marked partial, naming what it skipped, and
  MUST NOT produce a smaller full one (ADR-0009).
- Every golden-set document MUST record its origin and the basis on which docdoc may use it. A
  document whose provenance cannot be stated MUST NOT be admitted.
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

The dependency direction MUST be, strictly downward. The layers that **exist**, in order:

```text
API, CLI → Recording, Runs → Evaluation → Pipeline → Validation → Grounding
         → Extraction → Ingest → Artifacts → Kernel
```

Nothing is planned and unbuilt: every layer named here exists. `Transform` was named as a layer in
this document until v1.4.0 and was never built — ADR-0006 put its transformations inside grounding's
versioned match view instead, so the layer it would have been is grounding.

Two positions in that chain are not the ones a reader would guess, and both are deliberate.
**`Artifacts` sits directly above the kernel**, because it stores whole result models without
importing one — the caller names the model — so it depends on `pydantic` and two kernel helpers and
nothing else. **`Pipeline` sits directly above `Validation`**, the highest stage it drives, which
leaves `Recording → Evaluation` as it was and yields `Recording → Pipeline`: the recorder *calls* the
pipeline rather than holding a second copy of the stage order.

**`API` and `CLI` are siblings, not a stack.** Neither may import the other, which an ordered
position cannot express; an `independence` contract states it instead.

**`Recording` and `Runs` are siblings for the same reason.** `Recording` drives the pipeline to
produce a prediction set; `Runs` drives it to serve an accepted request. Neither uses the other, so
an ordered position between them would grant a permission neither needs. A second `independence`
contract states it.

Rules:

- The kernel MUST NOT depend on any layer above it.
- **The layers contract in `pyproject.toml` is the authoritative form of this chain**, because it
  is the one CI checks. This text MUST be amended in the same change that adds a layer to it. From
  Milestone 4 to Milestone 6 the two disagreed — grounding and validation shipped as layers this
  document never named — and the reconciliation lived only in a `pyproject.toml` comment and three
  `research.md` files. A dependency graph a reader must reconstruct from three research documents
  is not a governing one.
- **This chain MUST name only layers that exist**, with planned ones listed separately. A principle
  that names `Transform` and `Pipeline` alongside `Kernel` invites a designer to build against a
  graph that is partly aspiration, which is the specific failure this rule now prevents. As of
  v1.5.0 the separate list is empty, and it MUST stay empty rather than becoming a wish list.
- ADR-0003's per-document stage chain — parse → extraction → grounding → validation — is finer
  grained than this list and consistent with it. Where they overlap, both hold; neither overrides
  the other.
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

**Packaging.** `pip install docdoc` installs the deterministic layers — kernel, ingest, extraction,
grounding, validation, and evaluation contracts — and no provider SDK. Provider integrations are
optional extras (for example `docdoc[google]`, `docdoc[pdf]`). The base install's runtime
dependencies are `pydantic` and `rapidfuzz`; the latter is sanctioned by the stack line above and by
ADR-0005, and the **kernel** alone depends on `pydantic` only.

**Deferred technology.** The following are postponed, not architecturally rejected, and MUST NOT
appear in the MVP without an approved amendment: Kafka, Temporal, Kubernetes, multi-region
deployment, distributed DAG engines, vector databases, RAG infrastructure, workflow/BPM engines,
semantic chunking, EditMap-style normalization, automatic model training, multi-tenant billing,
and a full review UI. Development Compose contains only api, worker, postgres, and object storage.

"Multi-tenant billing" above forbids **billing**, not tenant isolation. Scoping runs, blobs, and
artifacts to a tenant so that one customer cannot read another's is permitted and, from Milestone 9,
required; metering, invoicing, and per-tenant pricing remain deferred. The distinction is stated
because the two are one phrase apart and a reviewer would otherwise have to guess which was meant.

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
   golden-set metrics. The CI gate is advisory during the MVP. Its target size is **50 documents
   and 500 labeled fields in the public tier**, across at least two schemas with at least twenty
   documents each (ADR-0009) — counted on the public tier because this is a CI gate and CI cannot
   see the restricted one. Reaching that size does not flip the gate; flipping it is an amendment,
   made on evidence that the metrics are stable enough to block a merge on.
6. **Provider changes stay in adapters.** A PR that adds a provider SDK import outside an
   adapter directory is rejected on sight.
7. **Every feature ships with documentation and at least one example.**

## Open Constitutional Decisions

These are tensions in the founding principles. Each MUST be decided — via an ADR under
`docs/adr/` — before the work it gates begins. Items marked BLOCKING MUST NOT be resolved
implicitly by an implementation choice.

**Resolved** (see [`docs/adr/`](../../docs/adr/)). The six original BLOCKING items were all resolved
on 2026-08-14:

| Decision | Outcome | ADR |
|----------|---------|-----|
| `OCR_IN_MVP` | Native PDF parser + one cloud document-intelligence parser; no standalone OCR engine; `OCRProvider` deferred | [0001](../../docs/adr/0001-parser-and-ocr-strategy-in-mvp.md) |
| `DOCUMENT_IDENTITY` | Two-level identity; spans anchor to `document_id`, not `blob_id` | [0002](../../docs/adr/0002-blob-and-document-identity.md) |
| `PROCESSING_CACHE_KEY` | Per-stage content-addressed chain; job id is the terminal artifact id | [0003](../../docs/adr/0003-content-addressed-artifact-chain.md) |
| `CONFIDENCE_SEMANTICS` | Separate trusted/untrusted fields; no blended confidence in MVP | [0004](../../docs/adr/0004-confidence-semantics.md) |
| `FUZZY_GROUNDING_SPEC` | Kernel `find()` exact-only; fuzzy in extraction via `rapidfuzz`, pinned as `grounding_version` | [0005](../../docs/adr/0005-fuzzy-grounding-specification.md) |
| `NORMALIZATION_VS_GROUNDING` | Comparison-time match view with offset map; `Document.text` stays byte-faithful | [0006](../../docs/adr/0006-comparison-time-match-view.md) |
| `LICENSE` | Apache-2.0, chosen for its explicit patent grant | [0007](../../docs/adr/0007-apache-2-license.md) |
| `SCHEMA_EVOLUTION_POLICY` | Major `schema_version` for contract breaks, derived `schema_hash` for cache invalidation; concurrent majors allowed; no `latest` in the core (2026-08-17) | [0008](../../docs/adr/0008-schema-evolution-policy.md) |
| `GOLDEN_DATASET_LICENSING` | Two tiers: a vendored public tier sufficient on its own for a complete report, plus an optional hash-referenced restricted tier whose absence makes a report partial; gate 5's target size stated (2026-08-20) | [0009](../../docs/adr/0009-golden-dataset-licensing.md) |
| `PRE_1_0_VERSIONING` | While the major is `0`, a minor may break any public API and a patch may not; the kernel's identity derivations and the on-disk artifact format get a deprecation path rather than a silent break, because a change to either invalidates data on somebody else's disk; nothing else is promised stable before `1.0.0` (2026-08-24) | [0011](../../docs/adr/0011-pre-1.0-versioning.md) |
| `STORELESS_EXTRACTION` | Extraction over HTTP no longer requires a configured store: `POST /v1/extract` takes the bytes, returns the result, and persists nothing — unconditionally, so the endpoint decides and never the deployment. Such a run writes no terminal artifact and therefore has no job identity to retrieve. Raised because the interface forced every document to come to rest on disk, which is the objection the `gcv` adapter used to decline Vision's asynchronous API, and because Milestone 7's contract already claimed the opposite (2026-08-25) | [0012](../../docs/adr/0012-storeless-extraction-over-http.md) |

**Still open:** none.

Every decision this document has ever raised is resolved and points at an accepted ADR. That is a
statement about *this* list, not a claim that no decision will ever be needed again — a later
question is raised here, decided, and recorded, exactly as these were. What it does mean is that no
implementer is currently working around an unresolved constitutional question, which is the
condition the precedence rule below exists to keep true.

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

**Version**: 1.7.0 | **Ratified**: 2026-08-14 | **Last Amended**: 2026-08-28
