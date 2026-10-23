# ADR-0029: Schemas Are Authored in the Console and Stored in the Run Database

- **Status**: Accepted
- **Date**: 2026-10-23
- **Relates to**: [ADR-0023](0023-console-login-and-cross-tenant-operations.md) (the session and who
  may author), [ADR-0028](0028-console-routing-and-data-layer.md) (routes and data loading)
- **Design**: `docs/superpowers/specs/2026-10-23-console-schemas-design.md`

## Context

A schema was a `name@version.json` file plus a `prompts/name@version.md` file under
`DOCDOC_SCHEMA_PATHS`, mounted read-only into the API and every worker. Adding or changing a
document type meant editing files, remounting and restarting. The owner wants operators to create
and change schemas from the console and use them straight away. Runs record the schema they ran
against and the registry refuses an identity twice, so an identity must stay immutable.

## Decision

1. **Storage is Postgres, in the run database.** Migration `0014_console_schemas.sql` adds
   `console_schemas`: primary key `(name, version)`, `document` (jsonb, the schema as
   `parse_schema` reads it), `prompt`, `base` (nullable identity it was derived from),
   `created_at`, `created_by`. A row is inserted once; nothing updates or deletes it.
2. **One registry, two sources.** The registry the API and workers see is the files plus the rows.
   A save is refused if the identity exists in either. Workers see rows saved after they started
   (a `resolve` miss reloads the console rows), so no restart is needed.
3. **The server picks the version.** A save names the schema name; the server assigns
   `max(versions of that name across both sources) + 1`, computed from a fresh read of both
   sources and claimed by the `(name, version)` primary key with `INSERT ... ON CONFLICT DO NOTHING`.
   A lost race recomputes, up to 3 attempts, then `409 schema_version_taken`. Saving from
   base `invoice@2` when `invoice@3` exists gives `invoice@4`; a new name gets `@1` and must be
   free in both sources; a `base=None` create gets one attempt, and a loss is `409 schema_name_taken`.
   The response says which identity was created.
4. **Same validation as files.** A save goes through the same `Schema` model and prompt checks as
   `load_schema` and `load_prompt`; the loader was refactored so files and rows share one path. A
   rejection is `422` with the message and, where known, the field path. The prompt is required.
   Enum values are not type-checked, same as files.

### What the implementation settled

- **Collision (our call).** A file deployed later that claims a saved identity loses: the console
  row wins, because runs may reference it. The file is skipped and logged by identity only (not the path),
  the console list warns on that identity, and the registry as a whole never fails over one stray
  file.
- **Evaluations (our call).** `runs/evaluations.py` keeps picking the highest version of a name
  across both sources, so a console save becomes the evaluation target as a dropped file would.
- **Database unreachable.** The API falls back to file schemas and logs a warning on
  `docdoc.runs`; extraction does not go down with the database. While it is unreachable, a
  shadowed identity briefly resolves to the file on `/v1` synchronous paths. Workers instead
  wait and retry: a failed reload leaves the run unfinished so its lease lapses.
- **Row naming another identity.** A row whose stored document names a different identity than its
  key is skipped.
- **Racing creates.** A `base=None` create that loses a race on a new name gets `409`, not a later
  version; the operator sees the name is taken.

## Alternatives rejected

- **Editing an identity in place.** Breaks the record of what a run used.
- **Writing back to the schema directory.** Files stay read-only; they are mounted `:ro`.
- **A raw-JSON editor.** The owner chose a form (Fields, Prompt, Changes tabs, plus a JSON Rules tab,
  as no shipped schema uses rules).
- **Client-chosen versions.** Two saves could race to the same identity.

## Consequences

- A deployment without a run database has no console; schemas stay file-only there.
- Schemas are visible to every tenant and every signed-in operator may author. No delete or hide.
- Every registry build reads all console rows: a worker on each registry miss, the API on each
  request on top of reading the files.
- Unsaved edits live only in the open page; leaving asks for confirmation (specs/011 FR-007).
