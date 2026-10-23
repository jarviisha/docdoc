import { when } from "./format.ts";

/**
 * Schemas: the list the console reads, and the draft an operator edits before
 * saving it as a new version.
 *
 * A draft is the server's schema document with every default made explicit, so
 * a form can bind to it; `documentOf` strips the defaults again, so an untouched
 * draft saves exactly the document it was read from. Validation stays on the
 * server — this file only mirrors which constraint keys a type can carry, so a
 * form does not offer one the server would reject.
 */

export const FIELD_TYPES = [
  "string",
  "integer",
  "number",
  "boolean",
  "date",
  "datetime",
  "decimal",
] as const;
export type FieldType = (typeof FIELD_TYPES)[number];

export const CARDINALITIES = ["scalar", "group", "repeating_group"] as const;
export type Cardinality = (typeof CARDINALITIES)[number];

export interface SchemaEntry {
  identity: string;
  name: string;
  version: number;
  source: "file" | "console";
  fieldCount: number;
  createdAt: string | null;
  createdBy: string | null;
  shadowsFile: boolean;
}

export interface SchemaGroup {
  name: string;
  /** Ascending by version. */
  versions: SchemaEntry[];
  latest: SchemaEntry;
}

export interface FieldDraft {
  name: string;
  /** `null` for a group, which has no type of its own. */
  type: FieldType | null;
  cardinality: Cardinality;
  required: boolean;
  description: string;
  constraints: Record<string, unknown>;
  fields: FieldDraft[];
}

export interface SchemaDraft {
  name: string;
  fields: FieldDraft[];
  /** The `rules` array as JSON text; edited as text, parsed on save. */
  rulesText: string;
  prompt: string;
}

export interface SchemaDetail {
  identity: string;
  source: "file" | "console";
  document: Record<string, unknown>;
  prompt: string;
  base: string | null;
  createdAt: string | null;
  createdBy: string | null;
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const isSource = (v: unknown): v is "file" | "console" => v === "file" || v === "console";

/** Read `GET /v1/console/schemas` without trusting its shape; bad rows are dropped. */
export function toSchemaEntries(body: unknown): SchemaEntry[] {
  const listed = isObj(body) ? body.entries : undefined;
  if (!Array.isArray(listed)) return [];
  const entries: SchemaEntry[] = [];
  for (const row of listed) {
    if (
      !isObj(row) ||
      typeof row.identity !== "string" ||
      typeof row.name !== "string" ||
      typeof row.version !== "number" ||
      !isSource(row.source)
    ) {
      continue;
    }
    entries.push({
      identity: row.identity,
      name: row.name,
      version: row.version,
      source: row.source,
      fieldCount: typeof row.field_count === "number" ? row.field_count : 0,
      createdAt: strOrNull(row.created_at),
      createdBy: strOrNull(row.created_by),
      shadowsFile: row.shadows_file === true,
    });
  }
  return entries;
}

export function groupByName(entries: SchemaEntry[]): SchemaGroup[] {
  const byName = new Map<string, SchemaEntry[]>();
  for (const entry of entries) byName.set(entry.name, [...(byName.get(entry.name) ?? []), entry]);
  return [...byName.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([name, rows]) => {
      const versions = [...rows].sort((a, b) => a.version - b.version);
      return { name, versions, latest: versions[versions.length - 1] as SchemaEntry };
    });
}

/** The label of the save button: the version the server will assign next. */
export function saveLabel(groups: SchemaGroup[], name: string): string {
  const latest = groups.find((g) => g.name === name)?.latest.version ?? 0;
  return `Save as ${name}@${latest + 1}`;
}

/** Where a schema came from: `file`, or `console · when · who`. */
export function sourceLabel(entry: Pick<SchemaEntry, "source" | "createdAt" | "createdBy">): string {
  if (entry.source === "file") return "file";
  return `console · ${when(entry.createdAt)} · ${entry.createdBy ?? "—"}`;
}

/** Read `GET /v1/console/schemas/{identity}` (or the 201 of a create). */
export function toSchemaDetail(body: unknown): SchemaDetail | null {
  if (
    !isObj(body) ||
    typeof body.identity !== "string" ||
    !isSource(body.source) ||
    !isObj(body.document)
  ) {
    return null;
  }
  return {
    identity: body.identity,
    source: body.source,
    document: body.document,
    prompt: typeof body.prompt === "string" ? body.prompt : "",
    base: strOrNull(body.base),
    createdAt: strOrNull(body.created_at),
    createdBy: strOrNull(body.created_by),
  };
}

function fieldFrom(raw: unknown): FieldDraft {
  const f = isObj(raw) ? raw : {};
  const cardinality = CARDINALITIES.find((c) => c === f.cardinality) ?? "scalar";
  return {
    name: typeof f.name === "string" ? f.name : "",
    type: FIELD_TYPES.find((t) => t === f.type) ?? null,
    cardinality,
    required: f.required === true,
    description: typeof f.description === "string" ? f.description : "",
    constraints: isObj(f.constraints) ? f.constraints : {},
    fields: Array.isArray(f.fields) ? f.fields.map(fieldFrom) : [],
  };
}

export function draftFrom(document: Record<string, unknown>, prompt: string): SchemaDraft {
  return {
    name: typeof document.name === "string" ? document.name : "",
    fields: Array.isArray(document.fields) ? document.fields.map(fieldFrom) : [],
    rulesText: JSON.stringify(document.rules ?? [], null, 2),
    prompt,
  };
}

export function emptyDraft(): SchemaDraft {
  return { name: "", fields: [], rulesText: "[]", prompt: "" };
}

function fieldDocument(f: FieldDraft): Obj {
  const out: Obj = { name: f.name };
  if (f.type !== null) out.type = f.type;
  if (f.cardinality !== "scalar") out.cardinality = f.cardinality;
  if (f.required) out.required = true;
  if (f.description !== "") out.description = f.description;
  if (Object.keys(f.constraints).length > 0) out.constraints = f.constraints;
  if (f.fields.length > 0) out.fields = f.fields.map(fieldDocument);
  return out;
}

/** The schema document a draft saves as, defaults omitted; no `version` (the server assigns it). */
export function documentOf(draft: SchemaDraft): { document: Obj } | { error: string } {
  let rules: unknown;
  try {
    rules = JSON.parse(draft.rulesText.trim() === "" ? "[]" : draft.rulesText);
  } catch {
    return { error: "Rules must be valid JSON." };
  }
  if (!Array.isArray(rules)) return { error: "Rules must be a JSON array." };
  const document: Obj = { name: draft.name, fields: draft.fields.map(fieldDocument) };
  if (rules.length > 0) document.rules = rules;
  return { document };
}

// --- edits, by tree path ---------------------------------------------------

/** Apply `fn` to the sibling list under `parent` (`[]` is the schema's own list). */
function withList(
  fields: FieldDraft[],
  parent: number[],
  fn: (list: FieldDraft[]) => FieldDraft[],
): FieldDraft[] {
  const [i, ...rest] = parent;
  if (i === undefined) return fn(fields);
  return fields.map((f, k) => (k === i ? { ...f, fields: withList(f.fields, rest, fn) } : f));
}

function edit(
  draft: SchemaDraft,
  parent: number[],
  fn: (list: FieldDraft[]) => FieldDraft[],
): SchemaDraft {
  return { ...draft, fields: withList(draft.fields, parent, fn) };
}

function patched(field: FieldDraft, patch: Partial<FieldDraft>): FieldDraft {
  const next = { ...field, ...patch };
  if (patch.cardinality === undefined || patch.cardinality === field.cardinality) return next;
  return patch.cardinality === "scalar"
    ? { ...next, fields: [], type: "string" }
    : { ...next, type: null, constraints: {} };
}

export function updateField(draft: SchemaDraft, path: number[], patch: Partial<FieldDraft>): SchemaDraft {
  const at = path[path.length - 1];
  return edit(draft, path.slice(0, -1), (list) => list.map((f, i) => (i === at ? patched(f, patch) : f)));
}

export function addField(draft: SchemaDraft, parentPath: number[]): SchemaDraft {
  const blank: FieldDraft = {
    name: "",
    type: "string",
    cardinality: "scalar",
    required: false,
    description: "",
    constraints: {},
    fields: [],
  };
  return edit(draft, parentPath, (list) => [...list, blank]);
}

export function removeField(draft: SchemaDraft, path: number[]): SchemaDraft {
  const at = path[path.length - 1];
  return edit(draft, path.slice(0, -1), (list) => list.filter((_, i) => i !== at));
}

export function moveField(draft: SchemaDraft, path: number[], delta: -1 | 1): SchemaDraft {
  const at = path[path.length - 1] as number;
  return edit(draft, path.slice(0, -1), (list) => {
    const to = at + delta;
    if (at < 0 || at >= list.length || to < 0 || to >= list.length) return list;
    const next = [...list];
    [next[at], next[to]] = [next[to] as FieldDraft, next[at] as FieldDraft];
    return next;
  });
}

/** The constraint keys a field can carry, mirroring `CONSTRAINT_TYPE_DOMAINS` on the server. */
export function constraintsFor(field: FieldDraft): string[] {
  if (field.cardinality === "repeating_group") return ["min_length", "max_length"];
  if (field.cardinality === "group" || field.type === null) return [];
  const keys = ["enum", "const"];
  if (field.type === "string") keys.push("pattern", "min_length", "max_length");
  if (field.type !== "string" && field.type !== "boolean") keys.push("minimum", "maximum");
  if (["integer", "number", "decimal"].includes(field.type)) keys.push("multiple_of");
  return keys;
}

const NUMERIC: readonly (FieldType | null)[] = ["integer", "number", "decimal"];

/** Whether a constraint's value is a number, so the form offers a number input. */
export function isNumberConstraint(field: FieldDraft, key: string): boolean {
  if (["min_length", "max_length", "multiple_of"].includes(key)) return true;
  return (key === "minimum" || key === "maximum") && NUMERIC.includes(field.type);
}

/** A typed value as the field's type reads it; text that does not parse stays text for the server to name. */
function valueOf(field: FieldDraft, text: string): unknown {
  if (NUMERIC.includes(field.type) && text !== "" && Number.isFinite(Number(text))) return Number(text);
  if (field.type === "boolean" && (text === "true" || text === "false")) return text === "true";
  return text;
}

/** A constraint's value as its input shows it; an `enum` is a comma list. */
export function constraintText(value: unknown): string {
  if (value === undefined || value === null) return "";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** `field`'s constraints with `key` set from its input's text; empty text removes the key. */
export function withConstraint(field: FieldDraft, key: string, text: string): Record<string, unknown> {
  const { [key]: _old, ...rest } = field.constraints;
  const trimmed = text.trim();
  if (trimmed === "") return rest;
  if (key === "enum") {
    const items = trimmed.split(",").map((t) => t.trim()).filter((t) => t !== "");
    return { ...rest, enum: items.map((t) => valueOf(field, t)) };
  }
  if (isNumberConstraint(field, key)) {
    const n = Number(trimmed);
    return { ...rest, [key]: Number.isFinite(n) ? n : trimmed };
  }
  return { ...rest, [key]: key === "const" ? valueOf(field, trimmed) : trimmed };
}

// --- showing the tree -----------------------------------------------------

export interface TreeRow {
  field: FieldDraft;
  path: number[];
  /** `path` joined by dots: what `expanded` and `problemRows` hold. */
  key: string;
  depth: number;
  expandable: boolean;
}

/** The rows a tree table shows: every top-level field, and the children of expanded groups. */
export function visibleRows(fields: FieldDraft[], expanded: Set<string>, parent: number[] = []): TreeRow[] {
  return fields.flatMap((field, i) => {
    const path = [...parent, i];
    const key = path.join(".");
    const row = { field, path, key, depth: parent.length, expandable: field.cardinality !== "scalar" };
    return expanded.has(key) ? [row, ...visibleRows(field.fields, expanded, path)] : [row];
  });
}

// --- comparing -------------------------------------------------------------

function flatten(fields: FieldDraft[], prefix = "", into = new Map<string, string>()) {
  for (const { fields: children, ...own } of fields) {
    const path = prefix + own.name;
    into.set(path, JSON.stringify(own));
    flatten(children, `${path}.`, into);
  }
  return into;
}

const rulesOf = (text: string): string => {
  try {
    return JSON.stringify(JSON.parse(text));
  } catch {
    return text;
  }
};

/** What differs between two drafts, keyed by dotted field name path (`line_items.quantity`). */
export function changesBetween(base: SchemaDraft, draft: SchemaDraft) {
  const was = flatten(base.fields);
  const now = flatten(draft.fields);
  return {
    added: [...now.keys()].filter((p) => !was.has(p)),
    removed: [...was.keys()].filter((p) => !now.has(p)),
    changed: [...now.keys()].filter((p) => was.has(p) && was.get(p) !== now.get(p)),
    promptChanged: base.prompt !== draft.prompt,
    rulesChanged: rulesOf(base.rulesText) !== rulesOf(draft.rulesText),
  };
}

export function isDirty(base: SchemaDraft, draft: SchemaDraft): boolean {
  return JSON.stringify(base) !== JSON.stringify(draft);
}

/**
 * The tree path of the field a server problem names, or null when it names none.
 * Pydantic paths are by index (`fields.3.fields.0.constraints`); schema-level
 * checks use field names (`line_items.quantity`), looked up in `draft`.
 */
export function rowOfProblem(draft: SchemaDraft, path: string): number[] | null {
  const parts = path.split(".");
  const found: number[] = [];
  let list = draft.fields;
  if (parts[0] === "fields" && /^\d+$/.test(parts[1] ?? "")) {
    for (let k = 0; parts[k] === "fields" && /^\d+$/.test(parts[k + 1] ?? ""); k += 2) {
      const i = Number(parts[k + 1]);
      const field = list[i];
      if (field === undefined) return found.length > 0 ? found : null;
      found.push(i);
      list = field.fields;
    }
    return found;
  }
  for (const name of parts) {
    const i = list.findIndex((f) => f.name === name);
    if (i < 0) return null;
    found.push(i);
    list = (list[i] as FieldDraft).fields;
  }
  return found;
}

/** The keys (`TreeRow.key`) of every row the server's problems name. */
export function problemRows(draft: SchemaDraft, problems: { path: string; message: string }[]): Set<string> {
  const rows = new Set<string>();
  for (const { path } of problems) {
    const row = rowOfProblem(draft, path);
    if (row !== null) rows.add(row.join("."));
  }
  return rows;
}
