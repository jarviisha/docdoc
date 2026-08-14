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
