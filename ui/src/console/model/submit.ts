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

/** One parser as `GET /v1/console/parsers` describes it. */
export interface ParserOption {
  id: string;
  available: boolean;
  /** `null` when the deployment did not report health at all. */
  healthy: boolean | null;
  languages: string[];
  mediaTypes: string[];
}

/** Read the tenant list from a response body without trusting its shape. */
export function toTenants(body: unknown): string[] {
  if (typeof body !== "object" || body === null) return [];
  const tenants = (body as { tenants?: unknown }).tenants;
  return Array.isArray(tenants) ? (tenants as string[]) : [];
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
    };
    if (typeof record.id !== "string" || typeof record.available !== "boolean") continue;
    options.push({
      id: record.id,
      available: record.available,
      healthy: typeof record.healthy === "boolean" ? record.healthy : null,
      languages: Array.isArray(record.languages) ? (record.languages as string[]) : [],
      mediaTypes: Array.isArray(record.media_types) ? (record.media_types as string[]) : [],
    });
  }
  return options;
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
  sending: boolean;
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
  };
}

/** The run id a successful submission answered with, or `null` if it carried none. */
export function submittedRunId(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const runId = (body as { run_id?: unknown }).run_id;
  return typeof runId === "string" ? runId : null;
}
