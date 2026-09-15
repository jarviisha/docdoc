/**
 * How a column reads, tested because it is a decision and not decoration.
 *
 * The first render of the run table printed a full ISO timestamp with
 * microseconds beside a full UUID, seven rows deep. Everything was correct and
 * nothing was scannable — which is the failure SC-015's three-minute target
 * measures, and the reason these live in the model.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { shortDigest, shortId, when } from "../../src/console/model/format.ts";

test("a timestamp loses its microseconds and keeps its second", () => {
  const rendered = when("2026-09-11T09:17:00.253693+00:00");

  assert.doesNotMatch(rendered, /253693/);
  assert.match(rendered, /\d{2}:\d{2}:\d{2}/);
  assert.match(rendered, /2026/);
});

test("an absent timestamp is a dash, not an empty cell", () => {
  assert.equal(when(null), "—");
  assert.equal(when(""), "—");
});

test("an unparseable timestamp is shown as it arrived", () => {
  // Inventing a date for a value the deployment sent would be this console
  // disagreeing with the deployment about what it said.
  assert.equal(when("not a date"), "not a date");
});

test("a run identifier shortens to its first group", () => {
  assert.equal(shortId("a4e03746-11be-4e79-ab27-f079481fed52"), "a4e03746");
  assert.equal(shortId("no-dashes-here"), "no");
  assert.equal(shortId("plain"), "plain");
});

test("a digest shortens at both ends, keeping the half that differs", () => {
  const digest = "sha256:358dcfa38a862f0c8d3a5cf6131db5cf20f37fa93303d35e8ce0c55f0fe8ef06";

  const rendered = shortDigest(digest);

  assert.equal(rendered, "sha256:358dcfa3…0fe8ef06");
  // The tail is what tells two digests apart when somebody is checking by eye
  // whether two rows name the same document. A head-only truncation hides it.
  assert.ok(rendered.endsWith("0fe8ef06"));
});

test("a short digest is left alone", () => {
  assert.equal(shortDigest("sha256:abc"), "sha256:abc");
  assert.equal(shortDigest("not-a-digest"), "not-a-digest");
});
