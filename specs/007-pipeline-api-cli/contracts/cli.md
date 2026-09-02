# Public Contract: The `docdoc` Command

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

Built on `argparse`, so the base install acquires no dependency and everyone who typed
`pip install docdoc` has the command (research R12).

## 1. Commands

```bash
docdoc parse   FILE                       # route, parse, report what the parse produced
docdoc extract FILE --schema invoice@1    # the whole pipeline: parse, extract, ground, validate
docdoc inspect FILE --schema invoice@1    # every value, its verdict, and where it was found
docdoc inspect --result PROCESSING_ID     # the same, read back from the store
docdoc explain ARTIFACT_ID                # how this identity was derived, and its chain
docdoc eval    MANIFEST --predictions DIR # score a golden set
docdoc store   clear [--stage STAGE]      # all of it, or one stage (FR-019)
docdoc migrate [--check]                  # apply the run-state schema (Milestone 9, FR-078)
```

`migrate` was added by Milestone 9 and is the only command that touches a database. It is explicit
because a schema change applied at process start is several workers racing to alter one table, and
the winner decides what the deployment ends up with (FR-078). `--check` applies nothing and exits
non-zero when anything is pending, which is the form a rollout gates on.

`extract` and `inspect` are the same run with two renderings: `extract` reports the result,
`inspect` reports where every value came from. Both are the Definition of Done; `inspect` is the half
of it that answers *where did this come from*.

**`inspect` takes a file or an identity, and exactly one of them.** With `--result`, it reads a
completed run out of the store rather than performing one — three lookups, walking the chain the way
`GET /v1/jobs/{id}/result` does, because each artifact records the identity of its input. FR-026 asks
for a command that inspects *a result*, and until this existed somebody holding a `processing_id` from
a log had an HTTP path to it and no command-line one.

A result that is not in the store is reported absent and **never recomputed** (FR-036): the inputs may
have moved since, and producing a different result under the same identity would break the one promise
that identity makes. That is not a failure — the command was asked a question and answered it — so it
exits `0`.

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

Every environment setting that can change what an invocation does keeps its name and gains a flag of
the same meaning (FR-031, as amended 2026-08-25). No second vocabulary:

| Setting | Flag |
|---|---|
| `DOCDOC_SCHEMA_PATHS` | `--schema-path` (repeatable) |
| `DOCDOC_MODEL_ADAPTERS` | `--adapter` |
| `DOCDOC_ECHO_FIXTURES` | `--echo-fixtures` |
| `DOCDOC_STORE_ROOT` *(new)* | `--store` / `--no-store` |
| `DOCDOC_MAX_DOCUMENT_BYTES` | `--max-document-bytes` |
| `DOCDOC_MAX_PAGES` | `--max-pages` |
| — | `--verify-cache` — execute every stage and still write, so a drifted processor surfaces (FR-064) |
| — | `--json` — the machine form, and the only thing then written to standard output (FR-027) |

**Three settings deliberately have no flag**, and the exclusions are listed because an unlisted one
reads as an oversight — which is how two rows above went missing until 2026-08-25, while this
sentence claimed the table was complete:

| Setting | Why it has no flag |
|---|---|
| `DOCDOC_MAX_REQUEST_BYTES` | Caps an HTTP request body while it is read. The command line reads none. |
| `DOCDOC_MATCH_VIEW_CACHE` | A process-level memory bound. One run grounds one document once, so every bound above zero behaves identically. |
| `DOCDOC_GEMINI_MODEL`, and the provider credentials | Per-provider. A vendor's vocabulary does not belong on a provider-agnostic command line (Principle IV), and `argv` is readable by every process on the host, which is a worse place for a credential than the environment (FR-042). |

The two lists are checked against the parser, and against every setting the code reads, by
`tests/unit/test_cli_config_vocabulary.py`. A setting added later lands on one of them or fails that
check.

There is no default store root, so `--no-store` is the behaviour you already have; `--store` is the
opt-in (FR-017).

An explicit flag beats the environment, which beats the default — the arrangement the library
already uses, applied **per setting**: `--max-pages` on the command line and
`DOCDOC_MAX_DOCUMENT_BYTES` in the environment both take effect.

A size limit of zero or less is an invocation error (exit `64`) naming the flag that was typed,
rather than a validation failure naming a field that was not.

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
