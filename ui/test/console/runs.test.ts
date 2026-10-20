/**
 * T041 — paging, filtering, and the difference between empty and failed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  back,
  byKind,
  canGoBack,
  canGoForward,
  currentCursor,
  emptyNotice,
  failed,
  filtered,
  forTenant,
  forward,
  initialListing,
  isEmpty,
  loaded,
  reasonLabel,
  reloaded,
  timeline,
  toFailure,
  toPage,
} from "../../src/console/model/runs.ts";

const PAGE = { runs: [{ run_id: "r1" }] as never, next_cursor: "c1" };

test("the first page carries no cursor", () => {
  const state = initialListing();
  assert.equal(currentCursor(state), undefined);
  assert.equal(canGoBack(state), false);
});

test("forward pushes the cursor and back pops it", () => {
  const first = loaded(initialListing(), PAGE);
  assert.equal(canGoForward(first), true);

  const second = forward(first);
  assert.equal(currentCursor(second), "c1");
  assert.equal(canGoBack(second), true);
  assert.equal(second.listing.state, "loading");

  const returned = back(second);
  assert.equal(currentCursor(returned), undefined);
  assert.equal(canGoBack(returned), false);
});

test("forward on a last page does nothing", () => {
  const last = loaded(initialListing(), { runs: [], next_cursor: null });
  assert.equal(canGoForward(last), false);
  assert.deepEqual(forward(last), last);
});

test("back from the first page does nothing", () => {
  const first = loaded(initialListing(), PAGE);
  assert.deepEqual(back(first), first);
});

test("changing the filter returns to the first page", () => {
  // A cursor is a position in one ordering; a filter produces a different one,
  // and carrying the cursor across pages into a sequence it was never issued for.
  const deep = forward(loaded(initialListing(), PAGE));
  const refiltered = filtered(deep, "failed");

  assert.deepEqual(refiltered.stack, []);
  assert.equal(refiltered.status, "failed");
  assert.equal(currentCursor(refiltered), undefined);
});

test("a failure resets the stack, so there is no back into a sequence that failed", () => {
  const deep = forward(loaded(initialListing(), PAGE));
  const broken = failed(deep, { kind: "transport", message: "gone" });

  assert.equal(canGoBack(broken), false);
  assert.equal(broken.listing.state, "failed");
});

test("empty and failed are different states", () => {
  const empty = loaded(initialListing(), { runs: [], next_cursor: null });
  const broken = failed(initialListing(), { kind: "transport", message: "gone" });

  assert.equal(isEmpty(empty), true);
  assert.equal(isEmpty(broken), false, "a failed request is not an empty tenant");
});

test("an empty list says which emptiness it is", () => {
  const all = loaded(initialListing(), { runs: [], next_cursor: null });
  assert.match(emptyNotice(all), /no runs/);

  const filteredEmpty = filtered(all, "failed");
  assert.match(emptyNotice(filteredEmpty), /no failed runs/);

  const oneTenant = loaded(forTenant(filteredEmpty, "acme"), { runs: [], next_cursor: null });
  assert.equal(emptyNotice(oneTenant), "Tenant acme has no failed runs.");
});

test("a tenant filter returns to page one and keeps the state filter", () => {
  const deep = forward(loaded(filtered(initialListing(), "failed"), PAGE));
  const narrowed = forTenant(deep, "  acme ");
  assert.equal(narrowed.tenant, "acme");
  assert.equal(narrowed.status, "failed");
  assert.deepEqual(narrowed.stack, []);
  assert.equal(narrowed.listing.state, "loading");

  assert.equal(forTenant(narrowed, "   ").tenant, null, "a blank filter means every tenant");
  assert.equal(filtered(narrowed, null).tenant, "acme", "a state filter keeps the tenant");
});

test("a reload keeps its place", () => {
  const deep = forward(loaded(initialListing(), PAGE));
  const again = reloaded(loaded(deep, PAGE));
  assert.deepEqual(again.stack, deep.stack);
  assert.equal(again.listing.state, "loading");
});

test("a malformed body becomes an empty page rather than a crash", () => {
  assert.deepEqual(toPage(null), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: "nope", next_cursor: 7 }), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: [], next_cursor: "c" }), { runs: [], next_cursor: "c" });
});

// -- why a run failed ---------------------------------------------------------
//
// The detail view read one field of the detail body — `routing` — and showed a
// failed run as a red badge over four timestamps. US2 scenario 7 and SC-015 both
// need the reason, and the route has always sent it.

test("a failed run reports its stage and its error class", () => {
  const body = { status: "failed", failed_stage: "parse", error_class: "ParserCapabilityError" };
  assert.deepEqual(toFailure(body), { stage: "parse", errorClass: "ParserCapabilityError" });
});

test("a run that failed before reaching a stage has no stage, and that is a fact", () => {
  // An unresolvable schema fails a run with no stage having run. `null` is the
  // distinction the API's own model draws, not a missing value to paper over.
  const body = { status: "failed", failed_stage: null, error_class: "SchemaNotFound" };
  assert.deepEqual(toFailure(body), { stage: null, errorClass: "SchemaNotFound" });
});

test("a succeeded run reports no failure", () => {
  assert.equal(toFailure({ status: "succeeded", processing_id: "sha256:abc" }), null);
});

test("a malformed or empty body reports no failure rather than an empty label", () => {
  assert.equal(toFailure(null), null);
  assert.equal(toFailure("nope"), null);
  assert.equal(toFailure({ error_class: "" }), null);
  assert.equal(toFailure({ error_class: 7 }), null);
});

// -- the kind filter ----------------------------------------------------------

test("a kind filter returns to page one and keeps the other filters", () => {
  const deep = forward(loaded(filtered(initialListing(), "failed"), PAGE));
  const narrowed = byKind(deep, "ocr");

  assert.equal(narrowed.kind, "ocr");
  assert.equal(narrowed.status, "failed");
  assert.deepEqual(narrowed.stack, []);
  assert.equal(narrowed.listing.state, "loading");
});

test("a fresh listing has no kind filter", () => {
  assert.equal(initialListing().kind, null);
});

// -- timeline -------------------------------------------------------------
//
// waitMs and runMs are durations a card and a detail view both read from the
// same four timestamps, rather than each computing its own.

const T0 = "2026-10-20T00:00:00.000Z";
const T0_PLUS_6S = "2026-10-20T00:00:06.000Z";
const T1 = "2026-10-20T00:01:00.000Z";

test("a run never started has no wait or run time", () => {
  assert.deepEqual(timeline({ created_at: T0, started_at: null, finished_at: T1 }, Date.parse(T1)), {
    waitMs: null,
    runMs: null,
  });
});

test("a running run's run time grows with now", () => {
  const now = Date.parse(T0) + 20_000;
  const result = timeline({ created_at: T0, started_at: T0_PLUS_6S, finished_at: null }, now);
  assert.equal(result.runMs, 14_000);
});

test("wait time runs from creation to the claim", () => {
  const result = timeline(
    { created_at: T0, started_at: T0_PLUS_6S, finished_at: null },
    Date.parse(T0_PLUS_6S),
  );
  assert.equal(result.waitMs, 6_000);
});

test("a finished run's run time stops at finish, not at now", () => {
  const result = timeline(
    { created_at: T0, started_at: T0_PLUS_6S, finished_at: T1 },
    Date.parse(T1) + 999_999,
  );
  assert.equal(result.runMs, Date.parse(T1) - Date.parse(T0_PLUS_6S));
});

test("a negative duration clamps to zero", () => {
  // Clock skew between a claim and its own creation record should never read
  // as a negative wait or run time.
  const result = timeline({ created_at: T1, started_at: T0, finished_at: T0 }, Date.parse(T0));
  assert.equal(result.waitMs, 0);
  assert.equal(result.runMs, 0);
});

// -- reasonLabel ------------------------------------------------------------

test("a known reason carries its fixed copy", () => {
  assert.equal(reasonLabel("timeout", "ProviderError"), "The OCR host timed out");
  assert.equal(reasonLabel("mime_type", null), "This file type is not supported");
});

test("the three document-shape reasons share one label", () => {
  assert.equal(reasonLabel("invalid_order", null), "The parser produced an invalid document");
  assert.equal(reasonLabel("wrong_document", null), "The parser produced an invalid document");
  assert.equal(reasonLabel("internal", null), "The parser produced an invalid document");
});

test("an unknown reason falls back to the class, or to itself", () => {
  assert.equal(reasonLabel(null, "ProviderError"), "ProviderError");
  assert.equal(reasonLabel("brand_new", "X"), "brand_new");
});

test("no reason and no class is nothing to report", () => {
  assert.equal(reasonLabel(null, null), null);
});
