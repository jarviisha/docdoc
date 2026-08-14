# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Answer every gate explicitly with PASS / FAIL / N/A and a one-line justification. Any FAIL must
either be designed away or recorded in Complexity Tracking below.

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** — no new kernel dependency beyond `pydantic`; `Document` stays immutable; bytes stay in `BlobRef` | |
| 2 | **Provenance preservation (I, VIII)** — no operation discards spans, geometry, or provenance; results record document/parser/pipeline/schema/model/prompt-hash/extractor identities | |
| 3 | **Grounding integrity (II)** — values carry spans + grounding status; grounding computed deterministically by docdoc, never self-certified by an LLM; ungrounded stays distinguishable | |
| 4 | **Determinism (III)** — no clock, randomness, network, or provider state in kernel/grounding/validation paths | |
| 5 | **Provider isolation (IV)** — no provider SDK type outside an adapter directory; provider errors mapped to the docdoc error model; base install pulls no provider SDK | |
| 6 | **Text-first (V)** — OCR is not forced on text-bearing documents; text-layer decision is explicit and inspectable | |
| 7 | **Schema-driven (VI)** — no document-type-specific service or code path; result references exact schema name@version | |
| 8 | **Validation separation (VII)** — semantic rules (e.g. `sum(line_items) == total`) are deterministic code, not prompt instructions; failures are structured and field-addressable | |
| 9 | **No silent fallback (VIII)** — missing capability or provider failure raises a typed error naming parser, required capability, and availability | |
| 10 | **Measurability (IX)** — quality claims backed by golden-set field/document metrics; annotations remain representable | |
| 11 | **Layer direction (X)** — dependencies flow API → Pipeline → Extraction → Transform → Ingest → Kernel only; domain model free of FastAPI/HTTP/CLI/ORM/SDK | |
| 12 | **MVP discipline (XI)** — nothing from the Deferred Technology list; every new abstraction has a concrete present-tense reason | |
| 13 | **Kernel test rigor (XII)** — kernel/span/geometry changes ship property tests; `locate(original) == locate(remapped)` holds across slice/merge | |
| 14 | **Open decisions (Constitution §Open Constitutional Decisions)** — no BLOCKING TODO gating this milestone is being resolved implicitly in code | |

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
