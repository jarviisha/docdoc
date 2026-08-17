# Implementation Plan: Ingest Parser Layer

**Branch**: `002-ingest-parser-layer` | **Date**: 2026-08-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-ingest-parser-layer/spec.md`

## Summary

Build docdoc's L1 ingest layer: the thing that turns bytes into the Milestone 1 `Document`. Two
parsers behind one contract — a native PDF text reader and a geometry-capable cloud
document-intelligence service — plus the three pieces of machinery that keep them interchangeable: a
versioned text-layer assessment that decides which path a document takes, a capability-based registry
that selects a parser without anyone naming a provider, and a translation boundary that stops
provider types and provider exceptions from escaping their adapter.

The approach is deliberately thin. The ingest layer owns routing, identity, validation, and error
translation; it owns no layout intelligence. Reading order, coordinate conversion, and page rotation
are each an adapter's responsibility, declared and recorded rather than reconstructed. What the layer
does insist on is that a parser's output be *provably* valid before it becomes a `Document`: token
order and capability honesty are checked, and a violation names the parser rather than being quietly
repaired. Geometry bounds are enforced earlier still, when a `BBox` is constructed, so an
out-of-range box never reaches validation at all.

The one Milestone 1 change is additive: `IngestProvenance` gains the full text-layer verdict and the
parser's declared reading order, because Principle I puts provenance inside the document, not beside
it.

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestone 1)

**Primary Dependencies**: Base install unchanged — `pydantic` only. Two new optional extras:
`docdoc[pdf]` → PyMuPDF (AGPL-3.0, opt-in — see [research.md R1](research.md)); `docdoc[azure]` →
the Azure Document Intelligence SDK. Stdlib used by the layer: `hashlib`, `logging`, `time`
(monotonic deadlines), `dataclasses`, `typing`.

**Storage**: N/A — bytes in, `Document` out. No persistence, no cache, no artifact store.

**Testing**: `pytest` + `hypothesis`, `mypy --strict`, `ruff`, `import-linter`. Three tiers
(research.md R14): offline unit/property/contract tests; adapter tests against recorded, scrubbed
service responses; live tests behind a new `provider` marker, skipped with a stated reason when
credentials are absent.

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows. The native path
must behave identically on all three.

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals** — revised against measurement after implementation, as Milestone 1's table was.
The originals were unmeasured estimates written before PyMuPDF was installed. Enforced by
`tests/perf/test_ingest_perf.py` (marked `perf`).

| Operation | Target | Measured | Basis |
|---|---|---|---|
| Text-layer assessment, 20-page PDF | < 500 ms | ~7 ms | Page text extraction only, no IR construction |
| Native parse, 20-page text PDF | < 1 s | ~23 ms | SC-012 allows 5 s, so there is two orders of magnitude of headroom |
| Native parse, 200-page text PDF | < 8 s | ~256 ms | Linear in page count, as hoped |
| Geometry normalization | negligible | — | Two divisions per box |
| Remote parse | bounded, not targeted | — | Dominated by the service; governed by `deadline_s`, not by us |

The targets are left far above the measurements on purpose. A perf test that trips on ordinary
machine noise gets disabled, and a disabled test protects nothing; what these catch is an accidental
quadratic, not a constant-factor drift.

**Constraints**: The native path must run with no network, no credentials, and no provider SDK
(FR-033). Provider SDK types confined to `docdoc/ingest/parsers/` and enforced by `import-linter`
(FR-026). Transport settings must not influence document identity (FR-039). No clock, randomness, or
I/O may be introduced into `docdoc/kernel/` — the ingest layer may use all three.

**Scale/Scope**: Documents up to the configured 1,000-page limit. Roughly 12 new modules plus two
adapters, one additive kernel change, and a fixture set of 9 small PDFs, 1 image, and 2 recorded
service responses.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.1.1. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — the only kernel change is two optional plain-data fields on `IngestProvenance` (data-model §7). No new kernel dependency, no I/O, no clock. `tests/unit/test_kernel_purity.py` must keep passing untouched |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every produced document records parser id, parser version, options, options hash, capabilities, the full text-layer verdict per page, and the declared reading order. `parser_version` embeds the library version so an upgrade is visible in identity (research.md, final section) |
| 3 | **Grounding integrity (II)** | **N/A** — no extraction or grounding at this milestone. This feature supplies the geometry that grounding will later resolve against |
| 4 | **Determinism (III)** | **PASS** — the native path and the assessment are deterministic; the remote path is explicitly not, and records everything needed to explain a result instead (FR-023). Retry jitter and deadlines live only in the transport layer, never in the kernel |
| 5 | **Provider isolation (IV)** | **PASS** — SDK imports confined to two adapter modules, enforced by a forbidden-imports contract (research.md R13); provider exceptions translated with `__cause__` preserved; base install pulls no provider SDK (SC-010) |
| 6 | **Text-first (V)** | **PASS** — this gate is the feature. The verdict is computed before routing, versioned as `text-layer@1`, recorded per page, and overridable only explicitly and on the record |
| 7 | **Schema-driven (VI)** | **N/A** — no extraction, no schemas. No document-type-specific code path is introduced; `pdf-text` and `azure-di` are capability adapters, not document types |
| 8 | **Validation separation (VII)** | **N/A** — domain validation is Milestone 5. The checks here are structural preconditions for constructing a valid `Document`, not result validation |
| 9 | **No silent fallback (VIII)** | **PASS** — `ParserCapabilityError` names the capability and every candidate's availability; a failed parse never retries on a different parser (ING-17); a missing native reader raises rather than guessing (R4) |
| 10 | **Measurability (IX)** | **PASS** — all 18 success criteria are countable, and the sample set that the text-layer thresholds are validated against is committed. Golden-set field metrics are Milestone 6 and are not claimed here |
| 11 | **Layer direction (X)** | **PASS** — `docdoc.ingest` sits directly above `docdoc.kernel` and imports nothing else of docdoc's; added to the `import-linter` layers contract in the same change |
| 12 | **MVP discipline (XI)** | **PASS** — nothing from the Deferred Technology list. No layout engine, no plugin discovery, no cache, no queue. Every new type traces to a spec requirement, listed in data-model.md |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or operation semantics change, so the Milestone 1 property suite applies unchanged and must stay green. The additive provenance fields get their own construction tests, and geometry normalization gets a new property test (bounds and round-trip within tolerance) |
| 14 | **Open decisions** | **PASS** — `TODO(OCR_IN_MVP)`, the only BLOCKING item gating this milestone, was resolved by ADR-0001 and is followed rather than reinterpreted. The remaining open TODOs gate Milestones 3 and 6 and pre-1.0, and none is touched. One *new* decision is surfaced rather than resolved silently — see item 1 below |

### Design decisions that refine the spec

Recorded so reviewers see them here rather than discovering them in code. None is a constitution
violation.

1. **PyMuPDF's licence is a consequence the spec did not name.** It is AGPL-3.0; docdoc is
   Apache-2.0. Confining it to an opt-in `docdoc[pdf]` extra keeps docdoc's own distribution
   Apache-2.0, but a user embedding that extra in a closed-source pipeline inherits the AGPL
   obligation. The constitution's sanctioned stack names PyMuPDF, so this plan follows it and makes
   the consequence loud — README, extra documentation, quickstart. If the project judges that
   unacceptable, the fix is an ADR plus a constitution amendment naming a permissive reader
   (`pypdfium2` is the obvious candidate), **not** a quiet substitution in this milestone.
2. **Canonical text is assembled from the parser's tokens** rather than taken from the reader's page
   text blob (R6). This makes the token-to-text correspondence exact by construction instead of by
   search. The consequence: `Document.text` is not byte-identical to a viewer's copy-paste output.
   FR-007 is still satisfied — the text *is* what the parser emitted, unnormalized.
3. **Geometry outside the page is tolerated up to 1% and rejected beyond it** (R7). The spec said
   only that geometry must be normalized. Clamping everything would hide coordinate-system bugs;
   rejecting everything would fail on ordinary documents that place glyphs a hair outside the
   MediaBox.
4. **`ParseOptions` and `TransportSettings` are separate types** (data-model §3). The spec required
   transport settings not to affect identity (FR-039); keeping them out of the type that feeds
   `options_hash` makes that true by construction rather than by discipline.
5. **Two library behaviours were assumed, and both assumptions were wrong.** Resolved by measurement
   during implementation (T030, T031), which is what those tasks existed for:
   - PyMuPDF's `sort=True` extraction sorts by vertical position across the *whole page*, so a
     two-column page comes back interleaved line by line. The adapter therefore uses unsorted
     content-stream order and declares `pymupdf-stream@1` — describing what it delivers rather than
     what was hoped for. docdoc claims no layout reconstruction, and
     `tests/unit/test_pdf_text_parser.py` asserts both the column order it does produce and the
     interleaving `sort=True` would produce, so a future library change surfaces the choice again.
   - Word coordinates arrive in **unrotated** page space while `page.rect` is the *displayed* size.
     Normalizing one against the other would misplace every box on a rotated page, so each box is
     mapped through `page.rotation_matrix` first. The regression test compares the same word upright
     (x0 ≈ 0.17) and rotated 90° (x0 ≈ 0.90); skipping the rotation would give ≈ 0.07.

### Reconciliation after implementation

Recorded here rather than left as drift between the artifacts and the code. Each is a place the design
as written turned out not to be implementable, or turned out to have a simpler form.

6. **`TextLayerAssessment` is the kernel's `TextLayerRecord`, not a second type.** data-model §5
   described a mirror pair. Two classes with identical fields have no present-tense reason to exist
   (Principle XI) and are how a verdict and its copy drift apart, so the ingest name is an alias.
7. **`Parser.parse` takes a fourth argument, `text_layer`.** The routing verdict is known only to the
   caller of the ingest layer, and `Document` is immutable with provenance inside it — so there is no
   later moment at which the verdict could be attached without rebuilding the document.
8. **`parse()` takes `rule`.** FR-010 requires both thresholds to be configurable and `parse()` is the
   only entry point; without this the rule could only be reconfigured by bypassing it.
9. **The Azure adapter takes text from the service's own `content` and offsets** rather than
   re-assembling it from words as R6 prescribes for the native path. The service supplies offsets for
   every word, line, and table cell, and those offsets are what make a table cell resolvable to a span
   at all. Re-assembly would have discarded them and left tables unplaceable.
10. **`assess.py` names the native reader in an error message.** The text-layer question *is* "what can
    the native reader extract from this file?", so an install without it cannot answer, and naming what
    is missing is the whole value of the error. `tests/unit/test_no_provider_names.py` bounds the
    exception: that module may not import a parser class and may not select one.
11. **The page limit is checked only once the rule has run.** A skipped rule leaves the page count
    unknown, and asserting a made-up count would be worse than not checking.
12. **`register_unavailable()` was added to the registry.** When an extra is not installed there is no
    parser object to register, and FR-018 still requires the caller to be told the difference between
    "not installed" and "no such capability".
13. **One scoped mypy relaxation.** PyMuPDF's functions are unannotated, so `--strict`'s
    no-untyped-call rule fires on every library call. It is disabled for exactly
    `docdoc.ingest.parsers.pdf_text` — the one module whose job is to talk to that library. This is the
    practical reason adapters exist: the untyped world stops there.

### After the convergence pass

A second assessment against the spec (`/speckit-converge`) found ten gaps the first pass left, closed
in tasks T061–T070. Four changed behaviour and are recorded here:

14. **The page limit is now checked twice, at whichever point the count first exists.** Before, it ran
    only from the assessment's page list, so a forced parse in a deployment without the native reader
    skipped it entirely — the exact configuration that `force` was added to support. It now also runs
    after the parse when the rule was skipped. That still cannot undo a transmission a remote parse has
    already made; the size limit, enforced before anything leaves the process, is what bounds cost.
15. **`UnsupportedDocumentError` carries `parser_id`.** An encrypted PDF is refused by a *named* parser,
    and SC-007 requires the failure to name it. It stays `None` when the refusal precedes any parser
    choice, which is itself the honest answer.
16. **Service page numbers are reconciled explicitly.** Token page indices were derived by position and
    table page indices from `pageNumber - 1`; those agree only for a response starting at page 1 and
    running contiguously. A page-ranged analyze produced a misleading "geometry outside page N". Both
    now resolve through one map, and an unknown page number says so.
17. **A zero-page PDF is refused as `corrupt` rather than routed.** Left to the rule it came out "text
    layer not usable", which sent the caller looking for a recognition parser for a document with
    nothing to recognize.

One finding was closed by correcting an artifact rather than the code: this plan previously said
`validate.py` checks geometry bounds. It does not, and should not — `BBox` construction in
`normalize.py` makes an out-of-range box unconstructable, so a second check would be unreachable.

`handwriting` remains the one declared capability that is not verified against output, because the IR
carries no marker distinguishing recognized handwriting from print. That is stated in
`check_capability_honesty` rather than implied by omission.

## Project Structure

### Documentation (this feature)

```text
specs/002-ingest-parser-layer/
├── plan.md              # This file
├── spec.md              # Feature specification (40 FR, 18 SC, 5 clarifications)
├── research.md          # Phase 0 output — 14 resolved decisions
├── data-model.md        # Phase 1 output — entities, ING-1…ING-22, error model
├── quickstart.md        # Phase 1 output — setup and 5 validation scenarios
├── contracts/
│   └── ingest-api.md    # Phase 1 output — public API contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

Single-project Python library. Only the paths below are created or touched by this feature; sibling
packages (`transform/`, `extraction/`, `pipeline/`, `api/`) arrive in later milestones.

```text
pyproject.toml               # + [pdf] and [azure] extras, + `provider` marker, + import-linter contracts
uv.lock                      # regenerated and committed

src/docdoc/
├── kernel/
│   └── provenance.py        # MODIFIED: + TextLayerRecord, + IngestProvenance.text_layer/.reading_order
└── ingest/                  # NEW LAYER
    ├── __init__.py          # the public surface in contracts/ingest-api.md
    ├── errors.py            # IngestError → UnsupportedDocument/ParserCapability/Parser/Provider
    ├── source.py            # SourceFile, byte-signature detection, Limits
    ├── capabilities.py      # ParserCapabilities, CapabilityRequest
    ├── parser.py            # Parser protocol
    ├── options.py           # ParseOptions helpers, TransportSettings
    ├── assess.py            # TextLayerRule `text-layer@1`, TextLayerAssessment
    ├── registry.py          # ParserRegistry, priority order, default_registry()
    ├── validate.py          # token order + capability honesty → ParserError
    │                        #   (geometry bounds are enforced earlier, by BBox
    │                        #    construction in normalize.py — a second check
    │                        #    here would be unreachable)
    ├── normalize.py         # coordinate normalization, rotation, text assembly
    ├── observe.py           # the single `ingest.parse` structured event
    ├── parse.py             # parse() — the entry point that composes the above.
    │                        #   Named `parse`, not `pipeline`, to avoid colliding with the
    │                        #   Pipeline layer of Milestone 7
    └── parsers/
        ├── __init__.py
        ├── pdf_text.py      # PyMuPDF adapter        (extra: pdf)
        └── azure_di.py      # Azure DI adapter       (extra: azure)

tests/
├── unit/
│   ├── test_source_detection.py, test_limits.py
│   ├── test_assess_text_layer.py      # incl. the sparse-text-layer fixture
│   ├── test_registry_selection.py     # ING-14…ING-17
│   ├── test_validate.py               # ING-4, ING-8
│   ├── test_normalize.py              # R7 tolerance, R8 rotation, no-normalization assertions
│   ├── test_ingest_boundaries.py      # layer direction + no SDK leak (constitution mandate)
│   ├── test_pdf_text_parser.py        # incl. the two-column and rotated fixtures
│   ├── test_parse_native.py           # end-to-end native path, identity, determinism
│   ├── test_provenance_recording.py   # per-page verdict, override
│   ├── test_provider_errors.py        # transient vs permanent, deadline, no fallback
│   ├── test_no_provider_names.py      # SC-011
│   ├── test_temp_file_cleanup.py      # SC-014
│   ├── test_observe.py                # event schema + content-leak assertion
│   ├── test_provenance_fields.py      # kernel change, incl. Milestone 1 back-compat
│   └── test_azure_mapping.py          # recorded, scrubbed responses (R14)
├── contract/
│   └── test_parser_contract.py        # every Parser must satisfy ING-4/7/8/9
├── property/
│   └── test_geometry_normalization.py
├── integration/
│   └── test_azure_live.py             # marked `provider`
├── perf/
│   └── test_ingest_perf.py            # marked `perf`
└── fixtures/
    ├── pdf/                           # 9 synthetic PDFs: digital, scanned, sparse, mixed,
    │                                  #   two-column, rotated, encrypted, unicode, zero-page
    ├── image/sample_page.png          # required by SC-004's sample set and by ING-13
    ├── azure/                         # 2 recorded, scrubbed service responses (R14)
    └── make_fixtures.py               # committed generator, so fixtures are reproducible

examples/
└── parse_pdf.py             # the SC-015 example
```

**Structure Decision**: `src/` layout unchanged. `ingest/` is one flat package with one module per
concept, plus a `parsers/` sub-package — the only place provider SDKs may be imported, which is what
makes the `import-linter` containment rule expressible as a directory boundary rather than a
convention. Adapters are siblings under one contract precisely so that neither is privileged in code;
the offline-first preference lives in configured priority, not in an `if`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No gate fails. Two items are recorded not as violations but because a reviewer would reasonably ask
why they exist.

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| Modifying kernel `IngestProvenance` from a later milestone | Principle I requires the document to *carry* its ingestion provenance, and FR-011 requires the text-layer verdict to be readable without re-reading the source | A side-car record keyed by `document_id` would be separable from the document it explains — which is precisely how provenance gets lost. The change is additive, defaults to `None`, and cannot affect `document_id`, which reads only blob, parser, version, and options hash |
| Recorded-response fixtures for the remote adapter | Without them the response-to-IR mapping is exercised only when credentials exist, so CI would never test the adapter that produces every scanned-document result | Relying on live integration tests alone. Rejected: SC-009 requires the suite to be meaningful offline, and an adapter tested only in credentialed environments regresses silently everywhere else |
