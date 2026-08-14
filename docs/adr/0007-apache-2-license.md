# ADR-0007: Apache License 2.0

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(LICENSE)`
- **Principles engaged**: XII (Open-Source Quality)

## Context

The constitution deferred the licence choice as an open decision, and task T056 was blocked on it.
Until a licence exists, the default is "all rights reserved": nobody may legally use, fork, or
contribute to the project, which makes the open-source goal unreachable.

The choice is also close to irreversible. Relicensing after external contributions arrive requires
the agreement of every copyright holder, so it is far cheaper to decide before the first public
release than after.

## Decision

**Apache License 2.0.**

- `LICENSE` at the repository root holds the full, unmodified SPDX text, with the appendix
  copyright line filled in as `Copyright 2026 The docdoc Authors`.
- `pyproject.toml` declares `license = "Apache-2.0"` (SPDX expression, PEP 639) and
  `license-files = ["LICENSE"]`, so the licence ships in built wheels and sdists.

## Rationale

**The patent grant is the deciding factor.** Section 3 grants users an explicit, irrevocable
patent licence from every contributor. docdoc is meant to be embedded in enterprise document
pipelines, and corporate legal review routinely blocks patent-silent licences for infrastructure
that touches business records. MIT is shorter and friendlier to read, but its silence on patents is
exactly the thing that gets a library rejected in the review docdoc most needs to pass.

**It is the de-facto standard for this category.** Kubernetes, Kafka, Airflow, and most of the data
infrastructure docdoc would sit beside are Apache-2.0. Matching the ecosystem removes a question
adopters would otherwise have to ask.

**Permissive maximises reach, which the project needs first.** docdoc has no users yet. The
immediate risk is obscurity, not exploitation.

## Alternatives considered

**MIT** — shortest and most permissive, but no patent grant. Rejected for the reason above.

**AGPL-3.0** — would prevent a cloud provider from offering docdoc as a managed service without
publishing their modifications, and would enable a dual-licence commercial model later. Rejected
because many enterprises ban AGPL outright, which would exclude precisely the users this project
is built for. This is a real trade-off rather than a clear-cut call: it trades future commercial
leverage for present adoption. If the project later wants that leverage, the usual path is a
separate commercially-licensed layer above an Apache-2.0 core, not relicensing the core.

## Consequences

- Anyone may use docdoc commercially, including inside closed-source products, without publishing
  their changes.
- Contributors grant a patent licence for their contributions by contributing (Apache §5), so no
  separate CLA is required for basic patent protection.
- Modified distributions must carry the licence, retain notices, and state their changes.
- Relicensing later would require agreement from all copyright holders. Treat this as settled.
- Per-file copyright headers are permitted but not required, and are not used in this repository;
  the root `LICENSE` governs.
