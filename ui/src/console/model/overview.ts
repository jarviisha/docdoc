/**
 * The overview's four cards, each split by kind, plus the running runs and
 * recent failures an operator reaches for first — `GET /v1/console/overview`
 * in one read, rather than the status-by-status, kind-by-kind listing calls
 * it replaces.
 *
 * `done` counts `succeeded` only: `cancelled` is a thing an operator did, not
 * a thing the pipeline produced, and showing it beside `succeeded` reads as
 * "four outcomes" when the pipeline only has three.
 */

import type { RunKind, RunSummary } from "./runs.ts";

export interface KindSplit {
  total: number;
  ocr: number;
  extraction: number;
}

export interface OverviewCards {
  queued: KindSplit;
  running: KindSplit;
  failed: KindSplit;
  done: KindSplit;
}

export interface Overview {
  windowMinutes: number;
  cards: OverviewCards;
  /** At most 10, `status == "failed"`, newest `updated_at` first. */
  recentFailures: RunSummary[];
  /** At most 10, `status == "running"`, newest `updated_at` first. */
  running: RunSummary[];
}

function emptySplit(): KindSplit {
  return { total: 0, ocr: 0, extraction: 0 };
}

/** Which card a count row lands in. `cancelled` lands in none, by design. */
const CARD_FOR_STATUS: Record<string, keyof OverviewCards> = {
  queued: "queued",
  running: "running",
  failed: "failed",
  succeeded: "done",
};

function isKind(value: unknown): value is RunKind {
  return value === "ocr" || value === "extraction";
}

/**
 * Read an overview from a response body without trusting its shape.
 *
 * Unlike `toPage`, a body with no `counts` array is refused rather than read
 * as zero of everything: `counts` is the one field every card is built from,
 * and a response missing it is not a deployment with nothing running — it is
 * one this console failed to parse.
 */
export function toOverview(body: unknown): Overview {
  if (typeof body !== "object" || body === null) {
    throw new Error("the overview body was not an object");
  }
  const record = body as {
    window_minutes?: unknown;
    counts?: unknown;
    recent_failures?: unknown;
    running?: unknown;
  };
  if (!Array.isArray(record.counts)) {
    throw new Error("the overview body carried no counts array");
  }

  const cards: OverviewCards = {
    queued: emptySplit(),
    running: emptySplit(),
    failed: emptySplit(),
    done: emptySplit(),
  };

  for (const row of record.counts) {
    if (typeof row !== "object" || row === null) continue;
    const { kind, status, count } = row as { kind?: unknown; status?: unknown; count?: unknown };
    if (typeof status !== "string" || typeof count !== "number") continue;
    const card = CARD_FOR_STATUS[status];
    if (card === undefined) continue;

    const split = cards[card];
    split.total += count;
    if (isKind(kind)) split[kind] += count;
  }

  return {
    windowMinutes: typeof record.window_minutes === "number" ? record.window_minutes : 0,
    cards,
    recentFailures: Array.isArray(record.recent_failures)
      ? (record.recent_failures as RunSummary[])
      : [],
    running: Array.isArray(record.running) ? (record.running as RunSummary[]) : [],
  };
}
