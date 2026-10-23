# Console Schemas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operators list, view and author schemas in the console. A save always creates a new
version, it is stored in Postgres, and the API and running workers use it straight away.

**Architecture:** A new `console_schemas` table, read by a `PostgresSchemaStore`. The registry is
built from the schema files as today, then merged with the console rows by a new
`SchemaRegistry.with_console`. On a clash, the console row wins and the file is recorded as
shadowed. Files and rows share one validation path, split out of `loader.py`. The console gets three
routes (list, view, editor) built on a pure model, `model/schemas.ts`, which holds the draft tree,
the edits and the diff. That model is the tested surface.

**Tech Stack:** Python 3 / FastAPI / psycopg / pydantic. React 19 + TanStack Router/Query +
shadcn. Node's `node --test` for the console model.

**Spec:** `docs/superpowers/specs/2026-10-23-console-schemas-design.md`

## Global Constraints

- Identities are immutable. A save never updates a row, and nothing deletes one.
- The server assigns the version: `max(file versions of the name, console versions of the name) + 1`. A clash on `(name, version)` is retried, never overwritten.
- When `base` is null, the name must be free in both sources, otherwise `409 schema_name_taken`. When `base` is given, `document.name` must equal the base's name, otherwise `422`.
- Console rows are validated by exactly the code that validates files (`parse_schema`, `prompt_from_text`).
- A bad console row or a file/row collision never fails the whole registry. Skip it, or let the row win, and log it on `logging.getLogger("docdoc.runs")`.
- A deployment without a run database behaves exactly as today.
- UI: decisions live in `ui/src/console/model/` (the only tested surface), and no browser storage (`check-no-persistence`). Avoid ADR-0019's five nouns in identifiers (`ui/scripts/check-console-boundary.mjs`): do not name anything `approve`, `queue`, `assign` or similar.
- While iterating, run `uv run pytest -n0 <files>`. Before reporting done, run `make lint`, `make test` and `make ui`.

## Review Focus

1. **Two saves from the same base at once.** Both succeed, one as `@3` and one as `@4`, and neither overwrites the other. Test: Task 5, `test_concurrent_creates_get_distinct_versions` (Postgres).
2. **A file `invoice@3.json` deployed after the console saved `invoice@3`.** Resolving `invoice@3` returns the console row, `shadowed` names it, and other schemas still resolve. Test: Task 2, `test_console_entry_shadows_a_file_and_is_reported`.
3. **A worker started before the save.** A run naming the new identity executes, with no "not registered" error. Test: Task 4, `test_worker_reloads_registry_on_unknown_identity`.
4. **An invalid nested constraint**, for example `enum` on an integer inside `line_items`. The `422` carries a problem path that maps to that child row. Tests: Task 5, `test_create_rejects_invalid_schema_with_paths`, and Task 6, `rowOfProblem`.
5. **The prompt is whitespace only.** The save returns `422` and nothing is stored. Test: Task 5, `test_create_rejects_blank_prompt`.

---

### Task 1: One validation path for files and console rows

**Files:**
- Modify: `src/docdoc/extraction/loader.py`
- Test: `tests/unit/test_loader_from_data.py` (create)

**Interfaces:**
- Produces: `parse_schema(payload: object, *, path: str | None = None) -> Schema`. It raises `SchemaError` whose `problems: tuple[tuple[str, str], ...]` holds a `(dotted location, message)` pair for each pydantic error.
- Produces: `prompt_from_text(schema: Schema, text: str, *, path: str | None = None) -> PromptTemplate`. It raises `SchemaError` on a blank prompt.
- `load_schema` and `load_prompt` keep their signatures and messages, and become thin wrappers over the two functions above.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_schema_accepts_a_file_shaped_dict():
    doc = json.loads(Path("schemas/invoice@2.json").read_text())
    assert parse_schema(doc).identity == "invoice@2"

def test_parse_schema_reports_each_problem_with_its_location():
    doc = {"name": "x", "version": 1, "fields": [
        {"name": "g", "cardinality": "repeating_group", "fields": [
            {"name": "n", "type": "integer", "constraints": {"pattern": "a"}}]}]}
    with pytest.raises(SchemaError) as e:
        parse_schema(doc)
    assert any(loc.startswith("fields.0") for loc, _ in e.value.problems)

def test_parse_schema_refuses_a_non_object():
    with pytest.raises(SchemaError):
        parse_schema([1])

def test_prompt_from_text_refuses_blank():
    schema = parse_schema({"name": "x", "version": 1})
    with pytest.raises(SchemaError):
        prompt_from_text(schema, "  \n")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest -n0 tests/unit/test_loader_from_data.py`. Expected: ImportError.

- [ ] **Step 3: Implement the change**

Add `problems` as an optional keyword argument to `SchemaError.__init__` in `extraction/errors.py`, defaulting to `()`. Build `problems` from `exc.errors()` using the same `loc` join that `_describe` uses.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest -n0 tests/unit/test_loader_from_data.py tests/unit/test_registry.py`. Expected: PASS.

- [ ] **Step 5: Commit** — `refactor(extraction): validate schema data, not only schema files`

### Task 2: The registry knows each entry's source, and merges console rows

**Files:**
- Modify: `src/docdoc/extraction/registry.py`
- Test: `tests/unit/test_registry_console_merge.py` (create)

**Interfaces:**
- `RegisteredSchema` gains `source: str` (`"file"` or `"console"`) as a constructor keyword, defaulting to `"file"`.
- `SchemaRegistry.register(schema, prompt, *, source: str = "file")`
- Produces: `SchemaRegistry.with_console(entries: Iterable[tuple[Schema, PromptTemplate]]) -> SchemaRegistry`. It returns a **new** registry: a copy of this one, plus each console entry. A console entry whose identity is already a file entry **replaces** it, and that identity is added to `shadowed`. Two console entries with the same identity cannot happen, because the table's primary key prevents it.
- Produces: `SchemaRegistry.entries() -> tuple[RegisteredSchema, ...]`, sorted by identity.
- Produces: `SchemaRegistry.shadowed: tuple[str, ...]`, empty by default.

- [ ] **Step 1: Write the failing tests**

```python
def test_console_entry_is_added_and_marked(file_registry):
    merged = file_registry.with_console([(schema("invoice", 3), prompt("invoice@3"))])
    assert merged.resolve("invoice@3").source == "console"
    assert "invoice@3" not in file_registry      # original untouched

def test_console_entry_shadows_a_file_and_is_reported(file_registry):
    merged = file_registry.with_console([(schema("invoice", 2), prompt("invoice@2"))])
    assert merged.resolve("invoice@2").source == "console"
    assert merged.shadowed == ("invoice@2",)
    assert merged.resolve("receipt@1").source == "file"
```

`file_registry` is `SchemaRegistry.from_paths([Path("schemas")])`. `schema` and `prompt` are local helpers that build a valid `Schema` and a `PromptTemplate`.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the change.**
- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest -n0 tests/unit/test_registry_console_merge.py tests/unit/test_registry.py`

- [ ] **Step 5: Commit** — `feat(extraction): registry merges console schemas over files`

### Task 3: The console schema store

**Files:**
- Create: `src/docdoc/runs/migrations/0014_console_schemas.sql`
- Create: `src/docdoc/runs/console_schemas.py`
- Create: `tests/fixtures/schema_store.py` (`InMemorySchemaStore`, with the same methods)
- Test: `tests/integration/test_console_schemas_postgres.py` (`pytestmark = pytest.mark.postgres`, migrated with `migrations.apply` like `test_corrections_postgres.py`)

**Interfaces:**

The table:

```sql
CREATE TABLE IF NOT EXISTS console_schemas (
    name text NOT NULL,
    version int NOT NULL CHECK (version >= 1),
    document jsonb NOT NULL,
    prompt text NOT NULL,
    base text,
    created_at timestamptz NOT NULL,
    created_by text NOT NULL,
    PRIMARY KEY (name, version)
);
```

The store, in `docdoc.runs.console_schemas`:

- `ConsoleSchema` is a frozen dataclass with `name: str`, `version: int`, `document: dict[str, Any]`, `prompt: str`, `base: str | None`, `created_at: datetime`, `created_by: str`, and a property `identity -> f"{name}@{version}"`.
- `PostgresSchemaStore(execute)` has three methods:
  - `all() -> tuple[ConsoleSchema, ...]`, ordered by name, then version.
  - `get(identity: str) -> ConsoleSchema | None`
  - `insert(row: ConsoleSchema) -> bool`. It runs `INSERT … ON CONFLICT DO NOTHING RETURNING name` and returns `False` when the version is already taken.
- `merged_registry(files: SchemaRegistry, store) -> SchemaRegistry` validates each row with `parse_schema` and `prompt_from_text`. An invalid row is skipped and logged as a warning that names its identity. File identities that a row shadows are logged as warnings that name the identity. It returns `files.with_console(...)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_insert_then_get_and_all(store):
    assert store.insert(row("invoice", 3))
    assert store.get("invoice@3").document["name"] == "invoice"
    assert [r.identity for r in store.all()] == ["invoice@3"]

def test_insert_refuses_a_taken_version(store):
    assert store.insert(row("invoice", 3))
    assert store.insert(row("invoice", 3)) is False

def test_merged_registry_skips_an_invalid_row(store, caplog):
    store.insert(row("bad", 1, document={"name": "bad", "version": 1, "fields": "nope"}))
    merged = merged_registry(SchemaRegistry.from_paths([Path("schemas")]), store)
    assert "bad@1" not in merged and "invoice@1" in merged
    assert "bad@1" in caplog.text
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `make test` with only this file, or `DOCDOC_TEST_DATABASE_URL=… uv run pytest -n0 tests/integration/test_console_schemas_postgres.py`

- [ ] **Step 3: Implement the migration, the store, `merged_registry` and `InMemorySchemaStore`.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Commit** — `feat(runs): store console-authored schemas`

### Task 4: The API deployment and workers see console schemas without a restart

**Files:**
- Modify: `src/docdoc/api/app.py` (`_Deployment.__init__`, add `console_schemas()`, change `registry()`)
- Modify: `src/docdoc/cli/config.py` (`Settings.registry`)
- Modify: `src/docdoc/cli/commands/worker.py`, `src/docdoc/runs/worker.py` (`Worker.__init__` and `_execute_with_heartbeat`)
- Test: `tests/unit/test_worker_registry_reload.py` (create), plus extending `tests/contract/test_console_extraction_submit.py`

**Interfaces:**
- `_Deployment(…, schemas: Any = None)` takes an explicit store, which tests use with `InMemorySchemaStore`. `console_schemas()` returns that store, or else builds `PostgresSchemaStore(execute=self._runs._execute)` once (the same pattern as `evaluations()`), or returns `None`.
- `_Deployment.registry()` builds the base exactly as today (the injected registry or the files). If `console_schemas()` is not `None`, it returns `merged_registry(base, store)`.
- `Settings.registry(console: Any = None)` follows the same rule.
- `Worker(…, reload_registry: Callable[[], Any] | None = None)`. In `_execute_with_heartbeat`, before `execute_one`, the worker runs the following check:

```python
if self._reload_registry is not None and run.schema_identity and run.schema_identity not in self._registry:
    self._registry = self._reload_registry()
```

  In `cli/commands/worker.py`, build `store = PostgresSchemaStore(execute=queue._execute)` and pass `registry=settings.registry(store)` and `reload_registry=lambda: settings.registry(store)`.

- [ ] **Step 1: Write the failing tests**
  - `test_worker_reloads_registry_on_unknown_identity`: build a `Worker` with an in-memory queue whose `registry` lacks `invoice@3`, and a `reload_registry` that returns one with it. Enqueue an extraction for `invoice@3` and execute it once. Assert the run succeeded, and that `reload_registry` was called once.
  - `test_console_schema_is_extractable_through_v1`: the deployment has `schemas=InMemorySchemaStore()` holding `invoice@3` (a copy of `@2`). Assert that `GET /v1/schemas` lists it.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the change.**
- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest -n0 tests/unit/test_worker_registry_reload.py tests/contract/test_console_extraction_submit.py`

- [ ] **Step 5: Commit** — `feat: API and workers resolve console schemas without a restart`

### Task 5: Console schema routes

**Files:**
- Modify: `src/docdoc/api/console.py` (replace `list_schemas`, add two routes)
- Modify: `src/docdoc/runs/console_schemas.py` (add `create_version`)
- Modify: `ui/src/console/model/submit.ts` (`toSchemas` reads `entries[].identity`) and `ui/test/console/` (its test)
- Test: `tests/contract/test_console_schemas.py` (create, using the `signed_in` pattern from `test_console_extraction_submit.py` with `schemas=InMemorySchemaStore()`)

**Interfaces:**
- `create_version(store, registry, *, document: dict, prompt: str, base: str | None, created_by: str, now: datetime) -> ConsoleSchema` follows these rules:
  - The name comes from `document["name"]`, and `document["version"]` is overwritten.
  - `next = max(registry versions of the name, store versions of the name, default=0) + 1`.
  - It validates with `parse_schema` and `prompt_from_text`, then calls `store.insert`. If the insert returns `False`, it recomputes and retries, up to 3 attempts. After that it raises `SchemaVersionTaken`.
  - When `base` is null and the name exists, it raises `SchemaNameTaken`.
  - When `base` is given and does not resolve, or its name differs from `document["name"]`, it raises `SchemaError`.
- `GET /v1/console/schemas` returns `{"entries": [{identity, name, version, source, field_count, created_at, created_by, shadows_file}]}`. The last three are `null` or `false` for file entries. Read `field_count` from `registry.describe(identity).fields` and count only top-level fields: `len(entry.schema.fields)`.
- `GET /v1/console/schemas/{identity}` returns `{identity, source, document, prompt, base, created_at, created_by}`. `document` is `schema.model_dump(mode="json", exclude_defaults=True)`. Prefer a file entry's raw JSON when it round-trips, and otherwise use the dump. The route answers `404` when the identity is unknown.
- `POST /v1/console/schemas` takes a body of `{document: object, prompt: string, base: string | null}` and answers `201` with the detail shape above. The error answers are:
  - `422 {"error": "invalid_schema", "message": str, "problems": [{"path": str, "message": str}]}`
  - `409 {"error": "schema_name_taken" | "schema_version_taken", "message": str}`
  - `503`, the existing console-unavailable answer, when the deployment has no run database. `created_by` is the operator's username.

- [ ] **Step 1: Write the failing tests**
  - `test_list_has_sources`: `invoice@1` has `source == "file"` and `field_count > 0`.
  - `test_create_from_base_gets_next_version`: POST from base `invoice@2` returns `201` with `identity == "invoice@3"`, `created_by == "alice"`, and the list now includes it.
  - `test_create_twice_from_same_base`: the second POST returns `invoice@4`.
  - `test_new_name_starts_at_one` and `test_new_name_taken_is_409`, for `name = "invoice"` with `base = None`.
  - `test_create_rejects_invalid_schema_with_paths`: posting `enum` on an integer inside a repeating group returns `422`, with a `problems[0].path` that starts with `fields.`.
  - `test_create_rejects_blank_prompt` returns `422`, and the list is unchanged.
  - `test_concurrent_creates_get_distinct_versions`, in `tests/integration/test_console_schemas_postgres.py`: two threads each call `create_version` with base `invoice@2` against the Postgres store. The resulting identities are `{"invoice@3", "invoice@4"}`.
  - `test_detail_unknown_is_404`, and `test_schema_routes_need_a_session` returns `401`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the routes and `create_version`, and update `toSchemas` and its test.**
- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest -n0 tests/contract/test_console_schemas.py tests/contract/test_console_extraction_submit.py tests/integration/test_console_schemas_postgres.py` and `cd ui && npm test`

- [ ] **Step 5: Commit** — `feat(console): list, read and create schemas over HTTP`

### Task 6: The console model for schemas

**Files:**
- Create: `ui/src/console/model/schemas.ts`
- Modify: `ui/src/console/model/client.ts`. Add the intents `{type: "schema-detail"; identity: string}`, mapped to `GET /v1/console/schemas/{identity}`, and `{type: "create-schema"}`, mapped to `POST /v1/console/schemas` with `hasBody: true`. Add both to the exhaustive intent list.
- Modify: `ui/src/console/model/queries.ts`. `create-schema` invalidates `[["list-schemas"], ["schema-detail"]]`, and `schema-detail` is a read.
- Modify: `ui/src/console/model/failure.ts`. `reported` gains `problems?: {path: string; message: string}[]`, parsed when `body.error === "invalid_schema"`.
- Modify: `ui/src/console/model/navigation.ts`. Add `Area "schemas"` and `AreaPath "/schemas"`.
- Test: `ui/test/console/schemas.test.ts` (create), plus updating `navigation.test.ts` and `failure.test.ts`

**Interfaces (all exported from `schemas.ts`):**
- `FIELD_TYPES = ["string","integer","number","boolean","date","datetime","decimal"] as const`. `CARDINALITIES = ["scalar","group","repeating_group"] as const`.
- `interface SchemaEntry {identity; name; version: number; source: "file" | "console"; fieldCount: number; createdAt: string | null; createdBy: string | null; shadowsFile: boolean}`. `toSchemaEntries(body: unknown): SchemaEntry[]`.
- `interface SchemaGroup {name; versions: SchemaEntry[] /* ascending */; latest: SchemaEntry}`. `groupByName(entries): SchemaGroup[]`, sorted by name.
- `interface FieldDraft {name: string; type: FieldType | null; cardinality: Cardinality; required: boolean; description: string; constraints: Record<string, unknown>; fields: FieldDraft[]}`
- `interface SchemaDraft {name: string; fields: FieldDraft[]; rulesText: string; prompt: string}`
- `toSchemaDetail(body: unknown): {identity; source; document: Record<string, unknown>; prompt: string; base: string | null; createdAt; createdBy} | null`
- `draftFrom(document, prompt): SchemaDraft`. It fills defaults: `required` false, `description` `""`, `constraints` `{}`, `fields` `[]`, and `rulesText` = `JSON.stringify(rules ?? [], null, 2)`. `emptyDraft(): SchemaDraft`.
- `documentOf(draft): {document: Record<string, unknown>} | {error: string}`. It omits keys at their default, so `documentOf(draftFrom(file))` deep-equals the file minus `version`. A `rulesText` that is not a JSON array produces an `error`.
- Edits are by tree path (`number[]`), and each returns a new draft:
  - `updateField(draft, path, patch: Partial<FieldDraft>)`
  - `addField(draft, parentPath: number[])`, which appends `{name: "", type: "string", cardinality: "scalar", …}`
  - `removeField(draft, path)`
  - `moveField(draft, path, delta: -1 | 1)`. Moving past either end is a no-op.
  - Changing `cardinality` to a group clears `type` and constraints. Changing it to scalar clears `fields` and sets `type` to `"string"`.
- `constraintsFor(field: FieldDraft): string[]` mirrors `CONSTRAINT_TYPE_DOMAINS`. For a scalar, `enum` and `const` are always present, plus `pattern`, `min_length` and `max_length` for a string, `minimum` and `maximum` for numeric and temporal types, and `multiple_of` for numeric types. A repeating group gets `["min_length", "max_length"]`, and a group gets `[]`.
- `changesBetween(base: SchemaDraft, draft: SchemaDraft): {added: string[]; removed: string[]; changed: string[]; promptChanged: boolean; rulesChanged: boolean}`. It is keyed by dotted field name path, for example `line_items.quantity`.
- `isDirty(base, draft): boolean`
- `rowOfProblem(path: string): number[] | null` maps `"fields.3.fields.0.constraints"` to `[3, 0]` and `"name"` to `null`.
- `saveLabel(groups: SchemaGroup[], name: string): string` gives `"Save as invoice@3"`, or `"Save as foo@1"` for an unknown name.

- [ ] **Step 1: Write the failing tests.** There is one test per export above, with these assertions:
  - The round trip `documentOf(draftFrom(invoice@2 JSON))` equals the file without `version`.
  - `moveField` at index 0 with `-1` leaves the draft unchanged.
  - `changesBetween` reports `line_items.quantity` as changed after its type changes.
  - `rowOfProblem("fields.3.fields.0.constraints")` deep-equals `[3, 0]`.
  - `saveLabel` gives `"Save as invoice@3"` given versions 1 and 2.
- [ ] **Step 2: Run them and confirm they fail.** Run: `cd ui && npm test`
- [ ] **Step 3: Implement the change.**
- [ ] **Step 4: Run the checks and confirm they pass.** Run: `cd ui && npm test && npx tsc --noEmit && npm run lint:boundaries`
- [ ] **Step 5: Commit** — `feat(console): schema draft model`

### Task 7: The Schemas screens

**Files:**
- Create: `ui/src/console/components/SchemasArea.tsx` (the list), `SchemaView.tsx` (read-only detail) and `SchemaEditor.tsx` (the form)
- Modify: `ui/src/console/routes.tsx`. Replace `{ soon: "Schemas" }` with `{ name: "schemas", label: "Schemas" }`, and add the routes `/schemas`, `/schemas/new` (`validateSearch` → `{base?: string}`, adding a parser to `model/search.ts` with a test) and `/schemas/$identity`. `PLACES` maps `/schemas/*` to "Build / Schemas".

**Behaviour, following the `console-ui` skill. Invoke it before writing the screens:**
- **List.** `useIntent({type: "list-schemas"}, {select: toSchemaEntries})` feeds `groupByName`. There is one row per name, showing the name, the versions as links, the latest version, its source (`file`, or `console · date · user`) and the field count, with a "New version" link to `/schemas/new?base=<latest>`. A "New schema" button links to `/schemas/new`. Rows with `shadowsFile` show a warning badge reading "a file with this identity is ignored".
- **View.** Shows the identity, source and created info, a read-only field tree, and the prompt in a `<pre>`, with a "New version" button.
- **Editor.** Loads the base through `schema-detail` when `base` is set, and otherwise starts from `emptyDraft()`. It has a name input, which is read-only when there is a base. It has four tabs:
  - **Fields:** the tree table. A row expands for groups. Clicking a row opens an inline editor with name, description, cardinality, type, required, and one input per `constraintsFor` entry. `enum` is a comma list, and numbers are number inputs. There are add, remove and up/down controls.
  - **Prompt:** a textarea.
  - **Rules:** a JSON textarea.
  - **Changes:** shown only when there is a base, rendering `changesBetween`.
- **Save.** The button reads `saveLabel` and calls `useAct()({type: "create-schema"}, JSON.stringify({document, prompt, base}))`. On a `201`, navigate to `/schemas/<identity>`. On a `422`, show the message, and mark rows using `rowOfProblem` for each problem. On a `409`, show the message.
- **Leaving with unsaved changes.** When `isDirty` is true, TanStack Router's `useBlocker` asks for confirmation through `ConfirmDialog`.

- [ ] **Step 1: Build the screens and the routes.**
- [ ] **Step 2: Run the checks.** Run `make ui`, which covers the typecheck, the tests, the boundaries including a11y, the licences and the build. Expected: all pass.
- [ ] **Step 3: Check it by hand in the running app** (`make dev`, through the `run` skill):
  - The sidebar shows Schemas, and the list shows `invoice` with @1 and @2.
  - New version from `invoice@2`: add a field, change one in `line_items`, and check that the Changes tab lists both. Saving creates `invoice@3` and lands on its view.
  - Submit's schema picker now offers `invoice@3`. Run one extraction against it through the worker.
  - Putting `enum` on an integer marks that row.
- [ ] **Step 4: Commit** — `feat(console): Schemas area — list, view, author new versions`

### Task 8: Record it

**Files:**
- Create: `docs/adr/0029-console-authored-schemas.md`, recording spec decisions 1–4 and the collision and evaluation calls. Add it to `docs/adr/README.md`.
- Modify: `CHANGELOG.md`, adding an entry under Unreleased in the file's existing style.
- Commit both spec files and this plan if they are not already committed.

- [ ] **Step 1: Write the ADR and the changelog entry.**
- [ ] **Step 2: Run the full checks.** Run `make lint && make test && make ui`. Expected: all green.
- [ ] **Step 3: Commit** — `docs: ADR-0029 console-authored schemas`
