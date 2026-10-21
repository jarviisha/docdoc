/**
 * Submitting a file for OCR: which tenants and parsers the console can offer,
 * and the form an operator fills in to pick one.
 *
 * The parser and language choices are derived here rather than in a
 * component, for the reason every decision in this console lives in the
 * model: `check-model-boundary.mjs` makes the model the only tested surface,
 * and a choice list built in a component would be a requirement with no
 * coverage.
 */

import type { Intent } from "./client.ts";
import { joined } from "./format.ts";

/** What a parser can read out of a page: text, its geometry, tables, handwriting. */
export interface ParserCapabilities {
  text: boolean;
  geometry: boolean;
  tables: boolean;
  handwriting: boolean;
}

/** One parser as `GET /v1/console/parsers` describes it. */
export interface ParserOption {
  id: string;
  available: boolean;
  /** `null` when the deployment did not report health at all. */
  healthy: boolean | null;
  languages: string[];
  mediaTypes: string[];
  /** Why it is unavailable, or `null` when it is available or gave no reason. */
  unavailableReason: string | null;
  capabilities: ParserCapabilities;
}

/** Read the tenant list from a response body without trusting its shape. */
export function toTenants(body: unknown): string[] {
  if (typeof body !== "object" || body === null) return [];
  const tenants = (body as { tenants?: unknown }).tenants;
  return Array.isArray(tenants) ? (tenants as string[]) : [];
}

/** A capability row read without trusting its shape; an absent or malformed one reads as none. */
function capabilitiesOf(value: unknown): ParserCapabilities {
  const record =
    typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  return {
    text: record.text === true,
    geometry: record.geometry === true,
    tables: record.tables === true,
    handwriting: record.handwriting === true,
  };
}

/**
 * Read the parser list from a response body without trusting its shape.
 *
 * `languages` and `healthy` are optional keys the contract sends absent, not
 * null, on a parser that has neither (FR per contract-and-constraints.md) —
 * read here as `[]` and `null` so the rest of this file never has to tell
 * "absent" from "empty" apart again.
 */
export function toParsers(body: unknown): ParserOption[] {
  if (typeof body !== "object" || body === null) return [];
  const parsers = (body as { parsers?: unknown }).parsers;
  if (!Array.isArray(parsers)) return [];

  const options: ParserOption[] = [];
  for (const row of parsers) {
    if (typeof row !== "object" || row === null) continue;
    const record = row as {
      id?: unknown;
      available?: unknown;
      healthy?: unknown;
      languages?: unknown;
      media_types?: unknown;
      unavailable_reason?: unknown;
      capabilities?: unknown;
    };
    if (typeof record.id !== "string" || typeof record.available !== "boolean") continue;
    options.push({
      id: record.id,
      available: record.available,
      healthy: typeof record.healthy === "boolean" ? record.healthy : null,
      languages: Array.isArray(record.languages) ? (record.languages as string[]) : [],
      mediaTypes: Array.isArray(record.media_types) ? (record.media_types as string[]) : [],
      unavailableReason:
        typeof record.unavailable_reason === "string" ? record.unavailable_reason : null,
      capabilities: capabilitiesOf(record.capabilities),
    });
  }
  return options;
}

/**
 * A parser's status, in the order that decides it: not `available` beats
 * everything else, an unhealthy one beats a healthy reading, and a parser
 * the deployment said nothing about is `"unknown"` rather than guessed at.
 */
export function parserStatus(
  parser: ParserOption,
): "unavailable" | "not responding" | "healthy" | "unknown" {
  if (!parser.available) return "unavailable";
  if (parser.healthy === false) return "not responding";
  if (parser.healthy === true) return "healthy";
  return "unknown";
}

/**
 * The parsers screen never polls for a fresher reading (plan 4): a health
 * check live-probes every parser, and nobody asked for one on every render.
 * `PARSERS_POLL` pins that decision so a future change has to touch this
 * line rather than quietly wire a poll in `polling.ts` to this route.
 */
export const PARSERS_POLL = false;

/** The parsers screen's own refresh action, since it does not poll. */
export const parsersRefreshLabel = "Refresh";

/** The parsers screen's capability column: the true ones, in the contract's order. */
export function capabilityList(capabilities: ParserCapabilities): string {
  const names = (Object.keys(capabilities) as (keyof ParserCapabilities)[]).filter(
    (name) => capabilities[name],
  );
  return joined(names);
}

/** The parser value that means "let the deployment choose". */
export const AUTOMATIC = "";

/**
 * The parser selector's choices: Automatic first, then every reported
 * parser — disabled with "(unavailable)" when it cannot run at all, or
 * marked "(not responding)", still selectable, when it is available but its
 * last health check failed.
 */
export function parserChoices(
  parsers: ParserOption[],
): { value: string; label: string; disabled: boolean }[] {
  const choices = [{ value: AUTOMATIC, label: "Automatic", disabled: false }];
  for (const parser of parsers) {
    if (!parser.available) {
      choices.push({ value: parser.id, label: `${parser.id} (unavailable)`, disabled: true });
    } else if (parser.healthy === false) {
      choices.push({ value: parser.id, label: `${parser.id} (not responding)`, disabled: false });
    } else {
      choices.push({ value: parser.id, label: parser.id, disabled: false });
    }
  }
  return choices;
}

/**
 * The language selector's choices for the chosen parser: "Parser default"
 * first, then that parser's own languages. A parser left on Automatic, or
 * one this list no longer carries, offers only the default — there is no
 * parser yet whose languages could be listed.
 */
export function languageChoices(
  parsers: ParserOption[],
  parser: string,
): { value: string; label: string }[] {
  const choices = [{ value: "", label: "Parser default" }];
  if (parser === AUTOMATIC) return choices;
  const chosen = parsers.find((candidate) => candidate.id === parser);
  if (chosen === undefined) return choices;
  for (const language of chosen.languages) {
    choices.push({ value: language, label: language });
  }
  return choices;
}

export interface SubmitForm {
  tenant: string;
  parser: string;
  language: string;
  file: File | null;
  /** The chosen file's `Idempotency-Key`; `""` with no file. */
  key: string;
  sending: boolean;
}

/**
 * Choose a file. A different file gets `key`, freshly made by the caller; the
 * same file keeps the key it has, so pressing Submit again after an answer
 * that never arrived replays that run rather than making a second.
 */
export function chooseFile(form: SubmitForm, file: File | null, key: string): SubmitForm {
  if (file === form.file) return form;
  return { ...form, file, key: file === null ? "" : key };
}

/** Whether Submit may be pressed: a tenant, a file, and no request already under way. */
export function canSubmit(form: SubmitForm): boolean {
  return form.tenant !== "" && form.file !== null && !form.sending;
}

/**
 * The intent a submit sends. `parser` and `language` are left off rather than
 * sent empty, as `list-runs` already leaves its own filters off — a route
 * that saw `parser=` would have to decide what nothing after the `=` means.
 */
export function submitIntent(form: SubmitForm): Intent {
  return {
    type: "submit-ocr",
    tenantId: form.tenant,
    ...(form.parser === "" ? {} : { parser: form.parser }),
    ...(form.language === "" ? {} : { language: form.language }),
    ...(form.key === "" ? {} : { idempotencyKey: form.key }),
  };
}

/** The run id a successful submission answered with, or `null` if it carried none. */
export function submittedRunId(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const runId = (body as { run_id?: unknown }).run_id;
  return typeof runId === "string" ? runId : null;
}
