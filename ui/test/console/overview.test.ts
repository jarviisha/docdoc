/**
 * The overview's four cards, each split by kind, plus the two short lists
 * underneath them — the running runs and the recent failures an operator
 * reaches for first.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { cardTone, toOverview } from "../../src/console/model/overview.ts";

test("cards of empty counts are zero", () => {
  const body = { window_minutes: 60, counts: [], recent_failures: [], running: [] };
  const overview = toOverview(body);

  assert.equal(overview.windowMinutes, 60);
  assert.deepEqual(overview.cards.queued, { total: 0, ocr: 0, extraction: 0 });
  assert.deepEqual(overview.cards.running, { total: 0, ocr: 0, extraction: 0 });
  assert.deepEqual(overview.cards.failed, { total: 0, ocr: 0, extraction: 0 });
  assert.deepEqual(overview.cards.done, { total: 0, ocr: 0, extraction: 0 });
});

test("cards split by kind", () => {
  const body = {
    window_minutes: 60,
    counts: [
      { kind: "ocr", status: "queued", count: 10 },
      { kind: "extraction", status: "queued", count: 2 },
      { kind: "ocr", status: "succeeded", count: 218 },
    ],
    recent_failures: [],
    running: [],
  };
  const overview = toOverview(body);

  assert.deepEqual(overview.cards.queued, { total: 12, ocr: 10, extraction: 2 });
  assert.equal(overview.cards.done.total, 218);
});

test("done counts succeeded only; cancelled is not a card", () => {
  const body = {
    window_minutes: 60,
    counts: [
      { kind: "ocr", status: "succeeded", count: 3 },
      { kind: "ocr", status: "cancelled", count: 9 },
    ],
    recent_failures: [],
    running: [],
  };
  const overview = toOverview(body);

  assert.equal(overview.cards.done.total, 3);
  const total = Object.values(overview.cards).reduce((sum, split) => sum + split.total, 0);
  assert.equal(total, 3, "the cancelled row must not land in any card");
});

test("recentFailures and running pass through as rows, kind included", () => {
  const body = {
    window_minutes: 60,
    counts: [],
    recent_failures: [{ run_id: "r1", kind: "ocr" }],
    running: [{ run_id: "r2", kind: "extraction" }],
  };
  const overview = toOverview(body);

  assert.equal(overview.recentFailures.length, 1);
  assert.equal(overview.recentFailures[0]?.kind, "ocr");
  assert.equal(overview.running.length, 1);
  assert.equal(overview.running[0]?.kind, "extraction");
});

test("a body without a counts array is refused rather than read as empty", () => {
  assert.throws(() => toOverview(null));
  assert.throws(() => toOverview({}));
  assert.throws(() => toOverview({ counts: "nope" }));
});

test("only a failed card with failures in it reads as danger", () => {
  const none = { total: 0, ocr: 0, extraction: 0 };
  const some = { total: 3, ocr: 1, extraction: 2 };
  assert.equal(cardTone("failed", some), "danger");
  assert.equal(cardTone("failed", none), "neutral");
  assert.equal(cardTone("queued", some), "neutral");
  assert.equal(cardTone("done", some), "neutral");
});
