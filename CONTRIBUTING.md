# Contributing to docdoc

Thanks for your interest. This project has a small number of hard rules, and they exist because
the product's value depends on them rather than because of taste.

## The rules that are not negotiable

**1. The kernel's dependency allowlist.** `src/docdoc/kernel/` may import `pydantic` and this
standard-library set only:

```text
bisect  hashlib  json  math  typing  dataclasses  enum  re  unicodedata  collections
```

No file access, no network, no clock, no randomness. `tests/unit/test_kernel_purity.py` enforces
this with an AST scan plus a runtime audit hook, and it will fail your PR. Adding a kernel
dependency requires a constitution amendment, not a code review approval.

**2. The dependency direction.** `API → Pipeline → Extraction → Transform → Ingest → Kernel`, one
way only. `uv run lint-imports` checks it. A provider SDK imported outside an adapter directory is
rejected on sight.

**3. Kernel changes ship property tests.** Anything touching `Span`, `BBox`, `Token`, `Document`,
`locate`, `find`, `slice`, or `merge` needs Hypothesis coverage, not just examples. The invariant
that matters most:

```text
locate(span) == merge(partition(document)).locate(span)
```

If that can break, every grounded value docdoc produces is suspect — and it would surface as a
wrong bounding box in production rather than as a failing test.

**4. No silent fallback.** An unavailable capability raises `CapabilityError`. It never returns an
empty tuple, a default, or `None`. A caller must never be able to confuse "unavailable" with
"nothing there". The same goes for out-of-range spans: raise, do not clamp.

**5. Provenance beats convenience.** Given a choice between an ergonomic API that loses source
information and a slightly clumsier one that preserves it, preserve it. This is
[Principle XI](.specify/memory/constitution.md) and it decides most design arguments here.

## Getting set up

```bash
git clone <repo> && cd docdoc
uv sync --all-extras
uv run pytest tests/unit tests/property
```

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/). Nothing else — no database, no
object storage, no credentials. If a change makes any of those necessary to run the core suite,
that change is wrong.

## Before you open a PR

```bash
HYPOTHESIS_PROFILE=thorough uv run pytest
uv run mypy src/docdoc/kernel
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
```

State in the PR description which constitutional principles your change engages. If it violates
one, say so explicitly and justify it — an unjustified violation is rejected regardless of how
good the code is, and a justified one is usually fine.

## How work is planned here

This repository uses [Spec Kit](https://github.com/github/spec-kit). Substantial features go
through `specify → clarify → plan → tasks → analyze → implement`, and the artifacts live under
`specs/`. You do not need to use that flow for a bug fix or a docs change.

Architectural decisions are recorded as ADRs in [`docs/adr/`](docs/adr/). If your change requires
deciding something the constitution deliberately left open, write an ADR rather than settling it
in code. The constitution's "Open Constitutional Decisions" section lists what is still undecided.

## Commit and review conventions

- One logical change per commit; the test that proves it belongs in the same commit.
- Explain *why* in the commit body. The diff already shows what.
- Comments explain reasoning, not mechanics. If a rule is subtle, say why it exists — most
  comments in this codebase point at the failure mode they prevent.

## Adding or changing a schema

Schemas live in `schemas/` as JSON, with their prompt beside them under `schemas/prompts/`. They are
**data**: adding a document type is adding two files, and no engine change is permitted for it. A
test fails the build if a document-type name appears anywhere under `src/`.

`schemas/` in this repository is fixtures and examples, and is deliberately **not packaged in the
wheel**. A deployment points `DOCDOC_SCHEMA_PATHS` at its own directory (`os.pathsep`-separated, the
way `PATH` reads), or passes paths to `default_registry()` explicitly. With neither, the registry is
empty — docdoc has no schema of its own to fall back to.

```text
schemas/
├── invoice@1.json          # the schema
└── prompts/
    └── invoice@1.md        # its instructions
```

Both files are hashed. The schema's content hash covers every field, type, cardinality, `required`
flag, constraint, and description; the prompt has its own. Both are folded into the extraction
artifact's identity, so a reworded description invalidates cached extractions and reuses the parse.

### What obliges a major bump

[ADR-0008](docs/adr/0008-schema-evolution-policy.md) is the authority. In short:

| MUST bump `name@version` | MUST NOT bump |
|---|---|
| Removing or renaming a field | Adding an **optional** field |
| Changing a field's type or cardinality | Loosening a constraint |
| Optional → required | Rewording a description or prompt |
| **Tightening** a constraint | Reordering fields (the hash does not move either) |
| Changing a field's *meaning* while keeping its name and type | |

The last row is the one no tooling can catch. It is also the one that does the most damage, because
every stored result keeps pointing at a version whose meaning has moved underneath it.

### The snapshot check

`tests/fixtures/snapshots/schema_hashes.json` records the content hash of every registered version.
The build fails when one moves. That is a **change** detector, not a breakage detector — both
bump-worthy and harmless edits trip it, deliberately, so that the classification gets made rather
than skipped.

Clear it one of two ways:

- **Breaking?** Publish a new major. Add `schemas/<name>@<next>.json` and its prompt, and leave the
  old files alone — a stored result naming the old version must stay interpretable.
- **Non-breaking?** Refresh the snapshot and **state the classification in the commit message**:

  ```bash
  uv run python tests/unit/test_schema_snapshot.py
  ```

Do not clear it by editing the assertion.

### Constraints are declared here and enforced elsewhere

A schema may declare `minimum`, `maximum`, `pattern`, `max_length`, and the rest. The extraction
layer stores them, hashes them, and **does not act on them** — Principle VII puts validation in its
own stage with field-addressable output. The chosen model provider would enforce some of them on the
wire if asked; they are deliberately not sent, so that a violated bound is a located validation
failure rather than a provider error whose shape changes when the provider does.
