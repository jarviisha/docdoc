# Console: Schemas, authored in the UI

## Intent

The console's sidebar has carried a disabled "Schemas — soon" entry since ADR-0028. Today a schema
is a `name@version.json` file plus a `prompts/name@version.md` file under a directory named by
`DOCDOC_SCHEMA_PATHS`, mounted read-only (`:ro`) into the API and every worker. Adding or changing a
document type means editing files, rebuilding or remounting, and restarting. The owner wants an
operator to **create and change schemas from the console**, and have them usable straight away.

## What "change" means

A schema identity (`invoice@2`) is immutable: every run records the schema it ran against, and the
registry refuses to register an identity twice. The console keeps that. "Editing" `invoice@2` means
opening it as the **base** of a new version and saving that as `invoice@3`. `invoice@2` is never
modified, and runs that used it keep pointing at it.

## Goals

- **Build / Schemas** becomes a real area: a list of every registered schema, a read-only view of one
  identity, and a form editor that saves a new version.
- The editor starts from an existing identity (any source) or from blank ("New schema", a new name at
  `@1`).
- A saved schema is usable at once by `/v1` extraction, by the run submission routes and by the
  **already-running workers**, with no restart.
- File schemas still work exactly as today, show in the list labelled with their source, and can be
  the base of a new version.

## Non-goals

- No deleting or hiding schemas. Old runs need them to stay readable.
- No per-tenant schemas. Every schema is visible to every tenant, as today.
- No permissions beyond "signed in to the console": every operator can author.
- No writing back to the schema directory. Files stay read-only.
- No server-side drafts. An unsaved edit lives only in the open page.

## Anti-goals: it fails even if it "works" when

- A saved identity can later differ from what a run used, through overwrite, edit-in-place, or two
  saves racing to the same version.
- A schema the file loader would reject gets through the console, or the reverse.
- A worker that started before the save answers "schema not registered" for the new identity.
- An API key, a password, or any other secret appears in a schema endpoint. (None should be there;
  the endpoints are listed below.)

## Decided

1. **Storage: Postgres, in the run database.** A new migration (`0014_…`) adds a table holding, per
   identity: name, version, the schema JSON document (the same shape as a file), the prompt text, the
   base identity it was made from (nullable), `created_at` and `created_by` (operator username).
   `(name, version)` is unique.
2. **One registry, two sources.** The registry seen by the API and workers is the files plus the
   database rows. A save is refused if the identity exists in either source.
   **Our call on a later collision:** a file deployed after a console save can still claim the same
   identity, for example a new `invoice@3.json`. In that case the **console row wins**, because runs
   may already reference it. The file is skipped and logged by identity only, and the
   console list shows the collision as a warning on that identity. It must not fail the registry
   as a whole: one stray file cannot take down `/v1` extraction or the workers. The fix is to rename
   or remove the file.
   **Our call on evaluations:** `runs/evaluations.py` picks the highest version of a name, and it
   keeps doing that across both sources. A console save becomes the evaluation target exactly as a
   newly dropped file would.
3. **The server picks the version.** A save names the schema *name*, and the server assigns
   `max(existing versions of that name across both sources) + 1` inside the insert's transaction.
   Saving from base `invoice@2` while `invoice@3` already exists gives `invoice@4`. The response
   says which identity was created. A new name gets `@1`, and the name must be free in both sources.
4. **Same validation as files.** A console save goes through the same `Schema` model and the same
   prompt checks that `load_schema` and `load_prompt` apply. Refactor the loader so files and DB rows
   share one validation path. A rejection answers `422` with the message and, where the model gives
   one, the field path, so the form can mark the row.
5. **The prompt is required.** A new version copies the base's prompt text into the editor.
6. **Form editor (owner chose this over a raw-JSON editor).** It has three tabs:
   - **Fields:** a table of the field tree. A `group` or `repeating_group` row expands to show its
     children. Each row shows name, type, required, and a short summary of its constraints. Clicking a
     row edits name, description, type or cardinality, required, and the constraints that make sense
     for that type (from `CONSTRAINT_TYPE_DOMAINS`, with min/max length meaning entry count on a
     repeating group). You can add, remove and reorder fields at any level.
   - **Prompt:** the prompt text in a plain textarea.
   - **Changes:** what differs from the base: fields added, removed or changed, and whether the
     prompt changed. There is no Changes tab when there is no base.
   - Save shows "Save as `<name>@<next>`", and the server's answer is final.
7. **Routes.** `/console/schemas` is the list. `/console/schemas/$identity` is the read-only view,
   with a "New version" action. `/console/schemas/new` is the editor, optionally `?base=<identity>`.
   After a save, the editor navigates to the new identity's view.
8. **Console API.** `GET /v1/console/schemas` becomes a list (identity, name, version, source
   `file|console`, field count, created_at/by for console rows) instead of bare identities.
   `GET /v1/console/schemas/{identity}` returns the schema document, prompt and source.
   `POST /v1/console/schemas` saves. The tenant-facing `GET /v1/schemas` lists console schemas too,
   because it reads the same registry.

## Left to the builder

- The exact table and column names, and how the registry merges sources. A `SchemaRegistry` built
  from files plus rows is the obvious shape.
- **How workers see new schemas without a restart.** Workers hold a registry from startup
  (`cli/commands/worker.py`). Any mechanism is fine if a run naming a schema saved after the worker
  started executes correctly. One cheap option: reload the console rows on a `resolve` miss. API
  routes call `deployment.registry()` per request today. Keep that cost acceptable: reading the files
  on every call is the existing cost, and adding one indexed query to it is fine.
- Shape of the 422 error body, beyond "message plus field path where known".
- Leaving the editor with unsaved changes. **Our call:** ask for confirmation, since nothing is kept
  in browser storage (specs/011 FR-007).
- Cross-field `rules` in the editor. **Our call:** a fourth "Rules" tab with a JSON textarea for
  the `rules` array, copied from the base. No shipped schema uses rules today, so a form for them is
  not worth building yet.
- Follow the `console-ui` skill for the screens and ADR-0028 for routing and data loading. Write an
  ADR (next number) recording decisions 1–4.

## Constraints

- A deployment without a run database has no console. Schemas stay file-only there, and nothing
  about that path changes.
- Run the full suite with `make test` before reporting done (AGENTS.md).

## `console_schemas`

One row per immutable identity, primary key `(name, version)`. Columns: `document`
(jsonb, the schema as `parse_schema` reads it), `prompt` (text), `base` (the
identity it was derived from, or null), `created_at`, `created_by`. A row is
inserted once; nothing updates or deletes it.
