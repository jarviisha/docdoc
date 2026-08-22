# Public Contract: The `docdoc` Command

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

Built on `argparse`, so the base install acquires no dependency and everyone who typed
`pip install docdoc` has the command (research R12).

## 1. Commands

```bash
docdoc parse   FILE                       # route, parse, report what the parse produced
docdoc extract FILE --schema invoice@1    # the whole pipeline: parse, extract, ground, validate
docdoc inspect FILE --schema invoice@1    # every value, its verdict, its page, its rectangle
docdoc explain ARTIFACT_ID                # how this identity was derived, and its chain
docdoc eval    MANIFEST --predictions DIR # score a golden set
docdoc store   clear [--stage STAGE]      # all of it, or one stage (FR-019)
```

`extract` and `inspect` are the same run with two renderings: `extract` reports the result,
`inspect` reports where every value came from. Both are the Definition of Done; `inspect` is the half
of it that answers *where did this come from*.

## 2. Output

Every command takes `--json`. Two rules, and they are the whole contract:

- With `--json`, standard output carries **exactly one** JSON document and nothing else.
- Diagnostics, warnings, and progress go to standard error, always, in both forms (FR-027).

A caller piping `--json` into a parser must never have to strip a banner.

## 3. Exit codes

| Code | Meaning |
|---|---|
| `0` | The run completed and the document is valid. |
| `1` | The run completed and the document is **invalid** — a real result, not an error. |
| `2` | The run could not complete: a typed docdoc error. |
| `64` | The invocation itself was wrong — bad arguments, unreadable file. |

The `0`/`1` split is the point (FR-028). A caller must not have to grep output text to tell a failed
validation from a failed run, and a script that treats "this invoice is wrong" as "docdoc is broken"
is the outcome a single non-zero code guarantees.

`2` is also what a run that failed partway returns; the output still names the stage that failed and
carries the stages that succeeded.

## 4. Configuration

Every environment setting that already exists keeps its name and gains a flag of the same meaning
(FR-031). No second vocabulary:

| Setting | Flag |
|---|---|
| `DOCDOC_SCHEMA_PATHS` | `--schema-path` (repeatable) |
| `DOCDOC_MODEL_ADAPTERS` | `--adapter` |
| `DOCDOC_STORE_ROOT` *(new)* | `--store` / `--no-store` |
| — | `--verify-cache` — execute every stage and still write, so a drifted processor surfaces (FR-064) |

There is no default store root, so `--no-store` is the behaviour you already have; `--store` is the
opt-in (FR-017).

An explicit flag beats the environment, which beats the default — the arrangement the library
already uses.

With neither `DOCDOC_SCHEMA_PATHS` nor `--schema-path`, the registry is empty, because docdoc ships
no schema. The error for a schema that cannot be resolved MUST say the registry is empty and name the
setting that fills it, rather than reporting that the schema does not exist (US1, scenario 5).

## 5. Offline

`docdoc parse` on a digital PDF, `docdoc extract` with the echo adapter, `docdoc inspect`, and
`docdoc eval` over the committed golden set all run with **no credentials and no network** (FR-029).
This is the property the offline test suite asserts, with the socket patched to raise.

## 6. What the CLI will not do

- **Contain extraction, grounding, or validation logic** (FR-030). It parses arguments, calls the
  pipeline, and formats a result. A behaviour reachable only through the command line is a bug.
- **Render a page image with boxes drawn on it.** `inspect` reports the page and the rectangle; it
  does not draw them.
- **Print a document's contents to a log.** The rendered result is standard output, for the person
  who asked. The log gets identities (FR-043).
- **Write to the store by default.** `--store` is opt-in, because the artifacts contain extracted
  values and where they land is the operator's decision (FR-044).
