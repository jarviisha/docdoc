/**
 * Providers: what the console reads about the model adapters, and the bodies
 * it sends to change them.
 *
 * Scope `null` is the deployment default; a string is one tenant's override.
 * The server never returns a key, only its last four characters, and refuses
 * what it cannot honour (an empty priority, an unsealed key) — this file only
 * shapes the view and the request bodies.
 */

export type Source = "tenant" | "default" | "env" | "shipped";

export interface ProviderRow {
  id: string;
  /** The echo adapter: no saved settings, so nothing to edit. */
  testOnly: boolean;
  model: string | null;
  modelSource: Source;
  keyLast4: string | null;
  keySource: Source;
  usable: boolean;
  reason: string | null;
  updatedAt: string | null;
  updatedBy: string | null;
}

export interface ProvidersView {
  /** `null` is the deployment default. */
  scope: string | null;
  /** The adapter a run would use now. */
  selected: string | null;
  secretsConfigured: boolean;
  priority: string[];
  prioritySource: Source;
  /** In priority order, then the rest by id. */
  rows: ProviderRow[];
}

export const SOURCE_LABEL: Record<Source, string> = {
  tenant: "this tenant",
  default: "deployment default",
  env: "environment",
  shipped: "built-in",
};

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const isSource = (v: unknown): v is Source =>
  v === "tenant" || v === "default" || v === "env" || v === "shipped";

function rowFrom(raw: unknown): ProviderRow | null {
  if (!isObj(raw) || typeof raw.id !== "string") return null;
  const { model, key, status } = raw;
  if (!isObj(model) || !isObj(key) || !isObj(status)) return null;
  if (!isSource(model.source) || !isSource(key.source)) return null;
  return {
    id: raw.id,
    testOnly: raw.test_only === true,
    model: strOrNull(model.value),
    modelSource: model.source,
    keyLast4: strOrNull(key.last4),
    keySource: key.source,
    usable: status.usable === true,
    reason: strOrNull(status.reason),
    updatedAt: strOrNull(raw.updated_at),
    updatedBy: strOrNull(raw.updated_by),
  };
}

export function toProvidersView(body: unknown): ProvidersView | null {
  if (!isObj(body) || !isObj(body.priority) || !Array.isArray(body.adapters)) return null;
  const { value, source } = body.priority;
  if (!Array.isArray(value) || !isSource(source)) return null;
  const priority = value.filter((v): v is string => typeof v === "string");
  const rows: ProviderRow[] = [];
  for (const raw of body.adapters) {
    const row = rowFrom(raw);
    if (row === null) return null;
    rows.push(row);
  }
  const rank = (id: string) => {
    const i = priority.indexOf(id);
    return i === -1 ? Infinity : i;
  };
  rows.sort((a, b) => rank(a.id) - rank(b.id) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return {
    scope: strOrNull(body.scope),
    selected: strOrNull(body.selected),
    secretsConfigured: body.secrets_configured === true,
    priority,
    prioritySource: source,
    rows,
  };
}

export const keyDisplay = (row: ProviderRow): string =>
  row.keyLast4 === null ? "not set" : `••••${row.keyLast4}`;

export const editable = (row: ProviderRow): boolean => !row.testOnly;

/** Swap `id` with its neighbour; a no-op at the ends or when absent. */
export function movePriority(priority: string[], id: string, delta: -1 | 1): string[] {
  const i = priority.indexOf(id);
  const j = i + delta;
  if (i === -1 || j < 0 || j >= priority.length) return priority;
  const next = [...priority];
  [next[i], next[j]] = [next[j]!, next[i]!];
  return next;
}

export const withAdapter = (priority: string[], id: string): string[] =>
  priority.includes(id) ? priority : [...priority, id];

/** The server refuses an empty list, so the last entry stays. */
export const withoutAdapter = (priority: string[], id: string): string[] =>
  priority.length <= 1 ? priority : priority.filter((p) => p !== id);

export function adapterUpdate(form: {
  model: string;
  apiKey: string;
  clearKey: boolean;
}): { model: string | null; api_key?: string | null } {
  const model = form.model.trim();
  const body: { model: string | null; api_key?: string | null } = { model: model || null };
  if (form.clearKey) body.api_key = null;
  else if (form.apiKey.trim()) body.api_key = form.apiKey.trim();
  return body;
}

/** A test sends only what was typed: the server rejects blank strings. */
export function testBody(form: { model: string; apiKey: string }): {
  model?: string;
  api_key?: string;
} {
  const body: { model?: string; api_key?: string } = {};
  if (form.model.trim()) body.model = form.model.trim();
  if (form.apiKey.trim()) body.api_key = form.apiKey.trim();
  return body;
}

export const isOverride = (view: ProvidersView): boolean =>
  view.prioritySource === "tenant" ||
  view.rows.some((r) => r.modelSource === "tenant" || r.keySource === "tenant");

/** What the status column says: "in use" is the adapter a run would take now. */
export function statusOf(
  view: ProvidersView,
  row: ProviderRow,
): { label: string; variant: "success" | "neutral" | "warning"; reason: string | null } {
  if (view.selected === row.id) return { label: "in use", variant: "success", reason: null };
  if (row.usable) return { label: "usable", variant: "neutral", reason: null };
  return { label: "unavailable", variant: "warning", reason: row.reason };
}

/** The source a save in this scope writes. */
const ownSource = (view: ProvidersView): Source => (view.scope === null ? "default" : "tenant");

/** The edit form's starting model: only this scope's own, so saving never copies an inherited one down. */
export const savedModel = (view: ProvidersView, row: ProviderRow): string =>
  row.modelSource === ownSource(view) ? (row.model ?? "") : "";

/** Whether this scope holds a key of its own, which "Remove saved key" would drop. */
export const keySaved = (view: ProvidersView, row: ProviderRow): boolean =>
  row.keySource === ownSource(view) && row.keyLast4 !== null;

/** An empty key input keeps what is there; the placeholder says what that is. */
export function keyPlaceholder(view: ProvidersView, row: ProviderRow): string {
  if (keySaved(view, row)) return `••••${row.keyLast4} saved — type to replace`;
  if (row.keyLast4 !== null) return `••••${row.keyLast4} from ${SOURCE_LABEL[row.keySource]} — type to override`;
  return "not set — type to save";
}

export const SECRETS_NOTE = "set DOCDOC_SECRETS_KEY to store API keys";

/** A typed key cannot be stored without the secrets key; clearing one can. */
export const canSave = (
  form: { model: string; apiKey: string; clearKey: boolean },
  secretsConfigured: boolean,
  busy: boolean,
): boolean => !busy && (secretsConfigured || form.clearKey || form.apiKey.trim() === "");

export function toTestResult(body: unknown): { ok: boolean; message: string } | null {
  if (!isObj(body) || typeof body.ok !== "boolean" || typeof body.message !== "string") return null;
  return { ok: body.ok, message: body.message };
}

/** A scope's own saved priority can be dropped to inherit again; the env and built-in ones are not saved. */
export const canInheritPriority = (view: ProvidersView): boolean =>
  view.prioritySource === (view.scope === null ? "default" : "tenant");

/** `null` clears the scope's saved priority. */
export const inheritPriorityBody = (): string => JSON.stringify({ priority: null });
