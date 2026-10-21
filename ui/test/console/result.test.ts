/**
 * T102 (contract-and-constraints.md) — a `Document` body, sliced by code
 * point and grouped into the pages a result view renders.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  confidenceLabel,
  isTurned,
  pageAfter,
  toResultPages,
  TURNED_NOTICE,
  type ResultPage,
} from "../../src/console/model/result.ts";

test("token text is sliced by code point", () => {
  const doc = {
    text: "😀 Hóa đơn: 1.250.000",
    pages: [{ index: 0, span: [0, 20], width: 100, height: 50, rotation: 0 }],
    tokens: [
      [[2, 9], [0, [0.1, 0.1, 0.5, 0.2]], 0.9],
      [[11, 20], [0, [0.1, 0.3, 0.9, 0.4]], null],
    ],
  };
  const [p] = toResultPages(doc);
  // code points 2..9; a UTF-16 slice would be off by one after the emoji
  assert.equal(p?.lines[0]?.text, "Hóa đơn");
  assert.equal(p?.lines[1]?.text, "1.250.000");
});

test("lines are grouped by page, in token order", () => {
  const doc = {
    text: "ab cd",
    pages: [
      { index: 0, span: [0, 2], width: 100, height: 50, rotation: 0 },
      { index: 1, span: [3, 5], width: 100, height: 50, rotation: 0 },
    ],
    tokens: [
      [[0, 2], [0, [0, 0, 1, 1]], 1],
      [[3, 5], [1, [0, 0, 1, 1]], 1],
    ],
  };
  const [first, second] = toResultPages(doc);
  assert.equal(first?.lines.length, 1);
  assert.equal(first?.lines[0]?.text, "ab");
  assert.equal(second?.lines.length, 1);
  assert.equal(second?.lines[0]?.text, "cd");
});

test("tokens without geometry have no box, and are placed by span", () => {
  const doc = {
    text: "ab cd",
    pages: [
      { index: 0, span: [0, 2], width: 100, height: 50, rotation: 0 },
      { index: 1, span: [3, 5], width: 100, height: 50, rotation: 0 },
    ],
    tokens: [[[3, 5], null, null]],
  };
  const [first, second] = toResultPages(doc);
  assert.equal(first?.lines.length, 0);
  assert.equal(second?.lines.length, 1);
  assert.equal(second?.lines[0]?.text, "cd");
  assert.equal(second?.lines[0]?.bbox, null);
  assert.equal(second?.lines[0]?.confidence, null);
});

test("a token's index is its position in tokens[]", () => {
  const doc = {
    text: "ab cd",
    pages: [{ index: 0, span: [0, 5], width: 100, height: 50, rotation: 0 }],
    tokens: [
      [[0, 2], [0, [0, 0, 1, 1]], 1],
      [[3, 5], [0, [0, 0, 1, 1]], 1],
    ],
  };
  const [p] = toResultPages(doc);
  assert.equal(p?.lines[0]?.index, 0);
  assert.equal(p?.lines[1]?.index, 1);
});

test("a body without text, pages, or tokens arrays throws", () => {
  assert.throws(() => toResultPages({}));
  assert.throws(() => toResultPages({ text: "a" }));
  assert.throws(() => toResultPages({ text: "a", pages: [] }));
  assert.throws(() => toResultPages(null));
});

test("turned page is flagged", () => {
  const page: ResultPage = { index: 0, width: 800, height: 1100, lines: [] };
  assert.equal(isTurned(page, 1100, 800), true);
  assert.equal(isTurned(page, 800, 1100), false);
  assert.equal(isTurned({ ...page, width: 1000, height: 1010 }, 1010, 1000), false);
});

test("the turned notice is fixed copy", () => {
  assert.equal(TURNED_NOTICE, "This page was turned by the OCR host; boxes may not line up.");
});

test("confidence label", () => {
  assert.equal(confidenceLabel(null), "—");
  assert.equal(confidenceLabel(0.936), "94 %");
});

test("the pager stops at either end", () => {
  assert.equal(pageAfter(0, 3, -1), null);
  assert.equal(pageAfter(0, 3, 1), 1);
  assert.equal(pageAfter(2, 3, 1), null);
  assert.equal(pageAfter(2, 3, -1), 1);
  assert.equal(pageAfter(0, 1, 1), null);
});
