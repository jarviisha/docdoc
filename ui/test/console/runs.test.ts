/**
 * T041 — paging, filtering, and the difference between empty and failed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  back,
  canGoBack,
  currentCursor,
  autoRefreshes,
  detailPolls,
  showsFields,
  showsResult,
  emptyNotice,
  forward,
  isCancellable,
  kindLabel,
  listIntent,
  cancelNotice,
  reasonLabel,
  refetchAfterCancel,
  resultNotice,
  timeline,
  toFailure,
  toPage,
  toRecord,
  type RunRecord,
  type RunSummary,
} from "../../src/console/model/runs.ts";

test("the first page carries no cursor", () => {
  assert.equal(currentCursor([]), undefined);
  assert.equal(canGoBack([]), false);
});

test("forward pushes the cursor and back pops it", () => {
  const second = forward([], "c1");
  assert.deepEqual(second, ["c1"]);
  assert.equal(currentCursor(second), "c1");
  assert.equal(canGoBack(second), true);

  assert.deepEqual(back(["c1", "c2"]), ["c1"]);
  assert.equal(currentCursor(back(second)), undefined);
});

test("forward on a last page does nothing", () => {
  assert.deepEqual(forward(["c1"], null), ["c1"]);
});

test("back from the first page does nothing", () => {
  assert.deepEqual(back([]), []);
});

test("the list request carries only the filters set, and the cursor", () => {
  assert.deepEqual(listIntent({ tenant: "acme", status: "failed" }, "c1"), {
    type: "list-runs",
    tenant: "acme",
    status: "failed",
    cursor: "c1",
  });
  assert.deepEqual(listIntent({}, undefined), { type: "list-runs" });
});

test("an empty list says which emptiness it is", () => {
  assert.match(emptyNotice({}), /no runs/);
  assert.match(emptyNotice({ status: "failed" }), /no failed runs/);
  assert.equal(emptyNotice({ tenant: "acme", status: "failed" }), "Tenant acme has no failed runs.");
  assert.equal(
    emptyNotice({ tenant: "acme", status: "failed", kind: "ocr" }),
    "Tenant acme has no failed OCR runs.",
  );
  assert.equal(emptyNotice({ kind: "extraction" }), "No tenant has no extraction runs.");
});

test("a malformed body becomes an empty page rather than a crash", () => {
  assert.deepEqual(toPage(null), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: "nope", next_cursor: 7 }), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: [], next_cursor: "c" }), { runs: [], next_cursor: "c" });
});

test("a parsed page keeps each row's kind", () => {
  const page = toPage({ runs: [{ run_id: "r1", kind: "extraction" }], next_cursor: null });
  assert.equal(page.runs[0]?.kind, "extraction");
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
  assert.equal(reasonLabel("timeout", "ProviderError"), "The host timed out");
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

// -- cancel, kind label, auto-refresh, detail record ----------------------

test("only a queued or running run can be cancelled", () => {
  assert.equal(isCancellable("queued"), true);
  assert.equal(isCancellable("running"), true);
  assert.equal(isCancellable("succeeded"), false);
  assert.equal(isCancellable("failed"), false);
  assert.equal(isCancellable("cancelled"), false);
});

test("an OCR run's kind names its parser, once one claimed it", () => {
  assert.equal(kindLabel({ kind: "ocr", ocr_parser: "paddle" }), "OCR (paddle)");
  assert.equal(kindLabel({ kind: "ocr", ocr_parser: null }), "OCR");
  assert.equal(kindLabel({ kind: "extraction", ocr_parser: null }), "Extraction");
});

test("the listing refreshes itself only on page one", () => {
  assert.equal(autoRefreshes([]), true);
  assert.equal(autoRefreshes(["c1"]), false);
});

const SUMMARY = {
  run_id: "r1",
  tenant_id: "acme",
  status: "running",
  kind: "ocr",
  created_at: "2026-10-20T10:00:00Z",
  started_at: null,
  finished_at: null,
  blob_id: "sha256:x",
  schema_identity: "s",
  processing_id: null,
  ocr_parser: null,
  error_class: null,
  error_reason: null,
} satisfies RunSummary;

test("a detail body is read as a record", () => {
  const record = toRecord({ ...SUMMARY, worker_id: "w1", attempts: 2, ocr_language: "vie" });
  assert.equal(record?.tenant_id, "acme");
  assert.equal(record?.worker_id, "w1");
  assert.equal(record?.attempts, 2);
  assert.equal(record?.ocr_language, "vie");
});

test("a record's detail-only fields default to null, and a non-object is none", () => {
  const record = toRecord(SUMMARY);
  assert.equal(record?.worker_id, null);
  assert.equal(record?.attempts, null);
  assert.equal(record?.ocr_language, null);
  assert.equal(toRecord(null), null);
  assert.equal(toRecord("nope"), null);
});

test("a detail body without finished_at reads it as null, so the timeline is a number", () => {
  // The console detail body is `RunStateResponse` plus `tenant_id`; an older
  // deployment sends no `finished_at` at all.
  const { finished_at: _omitted, ...detail } = { ...SUMMARY, started_at: "2026-10-20T10:00:06Z" };
  const record = toRecord(detail);
  assert.equal(record?.finished_at, null);
  const times = timeline(record!, Date.parse("2026-10-20T10:01:06Z"));
  assert.deepEqual(times, { waitMs: 6_000, runMs: 60_000 });
});

test("the detail view polls only while a run is still active", () => {
  assert.equal(detailPolls(null), false);
  for (const status of ["queued", "running"] as const) {
    assert.equal(detailPolls({ ...SUMMARY, status } as RunRecord), true);
  }
  for (const status of ["succeeded", "failed", "cancelled"] as const) {
    assert.equal(detailPolls({ ...SUMMARY, status } as RunRecord), false);
  }
});

test("timeline treats a missing finished_at as still running", () => {
  const run = { created_at: "2026-10-20T10:00:00Z", started_at: "2026-10-20T10:00:06Z" } as never;
  assert.deepEqual(timeline(run, Date.parse("2026-10-20T10:01:06Z")), { waitMs: 6_000, runMs: 60_000 });
});

test("a cancel answered with a run still running says the cancel is pending", () => {
  assert.equal(
    cancelNotice({ status: "running" }),
    "Cancellation requested; the run stops at its next stage boundary.",
  );
  assert.equal(cancelNotice({ status: "cancelled" }), null);
});

test("a 409 on cancel means the shown run is stale and is read again", () => {
  assert.equal(refetchAfterCancel({ kind: "reported", status: 409, message: "x" }), true);
  assert.equal(refetchAfterCancel({ kind: "reported", status: 500, message: "x" }), false);
  assert.equal(refetchAfterCancel({ kind: "transport", message: "x" }), false);
});

test("the detail view shows a result for a succeeded run, and fields for an extraction one", () => {
  assert.equal(showsResult(null), false);
  assert.equal(showsResult({ ...SUMMARY, kind: "ocr", status: "succeeded" } as RunRecord), true);
  assert.equal(showsResult({ ...SUMMARY, kind: "extraction", status: "succeeded" } as RunRecord), true);
  assert.equal(showsFields(null), false);
  assert.equal(showsFields({ ...SUMMARY, kind: "ocr", status: "succeeded" } as RunRecord), false);
  assert.equal(showsFields({ ...SUMMARY, kind: "extraction", status: "succeeded" } as RunRecord), true);
  assert.equal(showsFields({ ...SUMMARY, kind: "extraction", status: "failed" } as RunRecord), false);
  for (const status of ["queued", "running", "failed", "cancelled"] as const) {
    assert.equal(showsResult({ ...SUMMARY, kind: "ocr", status } as RunRecord), false);
  }
});

test("resultNotice: an expired result is information, anything else an error", () => {
  assert.deepEqual(
    resultNotice({ kind: "reported", status: 410, message: "this run's result is no longer stored" }),
    { status: "info", title: "This run's result is no longer stored." },
  );
  assert.deepEqual(resultNotice({ kind: "transport", message: "offline" }), {
    status: "error",
    title: "The result could not be shown",
    description: "offline",
  });
});
