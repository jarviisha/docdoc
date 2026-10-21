/**
 * Submitting a file for OCR: the tenant and parser lists this console reads,
 * the choices it offers from them, and the form that drives a submission.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { requestFor } from "../../src/console/model/client.ts";
import {
  AUTOMATIC,
  canSubmit,
  chooseFile,
  languageChoices,
  parserChoices,
  submitIntent,
  submittedRunId,
  toParsers,
  toTenants,
} from "../../src/console/model/submit.ts";

test("tenants are read from the body's own list", () => {
  assert.deepEqual(toTenants({ tenants: ["acme", "mmerp"] }), ["acme", "mmerp"]);
  assert.deepEqual(toTenants({}), []);
  assert.deepEqual(toTenants(null), []);
});

test("parsers default their absent optional fields rather than reading undefined", () => {
  const parsers = toParsers({
    parsers: [
      { id: "paddle-vl", available: true, capabilities: [], media_types: ["image/png"] },
      {
        id: "tesseract",
        available: false,
        unavailable_reason: "disabled",
        capabilities: [],
        media_types: ["image/png"],
        languages: ["en"],
        healthy: false,
      },
    ],
  });
  assert.deepEqual(parsers, [
    { id: "paddle-vl", available: true, healthy: null, languages: [], mediaTypes: ["image/png"] },
    {
      id: "tesseract",
      available: false,
      healthy: false,
      languages: ["en"],
      mediaTypes: ["image/png"],
    },
  ]);
});

test("a row missing its id or available flag is dropped, not guessed at", () => {
  assert.deepEqual(toParsers({ parsers: [{ available: true }, { id: "x" }] }), []);
  assert.deepEqual(toParsers({}), []);
});

test("language choices follow the parser", () => {
  const ps = [
    { id: "paddle-vl", available: true, healthy: true, languages: ["vi", "en"], mediaTypes: [] },
  ];
  assert.deepEqual(languageChoices(ps, AUTOMATIC), [{ value: "", label: "Parser default" }]);
  assert.deepEqual(languageChoices(ps, "paddle-vl").map((c) => c.value), ["", "vi", "en"]);
});

test("a parser no longer in the list offers only the default language", () => {
  assert.deepEqual(languageChoices([], "gone"), [{ value: "", label: "Parser default" }]);
});

test("parser choices mark unavailable and unhealthy", () => {
  const ps = [
    { id: "paddle-vl", available: true, healthy: true, languages: [], mediaTypes: [] },
    { id: "slow", available: true, healthy: false, languages: [], mediaTypes: [] },
    { id: "off", available: false, healthy: null, languages: [], mediaTypes: [] },
  ];
  assert.deepEqual(parserChoices(ps), [
    { value: AUTOMATIC, label: "Automatic", disabled: false },
    { value: "paddle-vl", label: "paddle-vl", disabled: false },
    { value: "slow", label: "slow (not responding)", disabled: false },
    { value: "off", label: "off (unavailable)", disabled: true },
  ]);
});

test("cannot submit while sending", () => {
  const f = { tenant: "acme", parser: "", language: "", file: new File(["x"], "a.png"), key: "k", sending: true };
  assert.equal(canSubmit(f), false);
  assert.equal(canSubmit({ ...f, sending: false }), true);
  assert.equal(canSubmit({ ...f, sending: false, file: null }), false);
  assert.equal(canSubmit({ ...f, sending: false, tenant: "" }), false);
});

test("submit intent omits empty parser and language", () => {
  assert.deepEqual(
    requestFor(submitIntent({ tenant: "acme", parser: "", language: "", file: null, key: "", sending: false }))
      .query,
    {},
  );
  assert.deepEqual(
    requestFor(submitIntent({ tenant: "acme", parser: "paddle-vl", language: "vi", file: null, key: "", sending: false })),
    { method: "POST", path: "/v1/console/tenants/acme/ocr", query: { parser: "paddle-vl", language: "vi" }, hasBody: true },
  );
});

test("a submitted run's id is read from the console detail body", () => {
  assert.equal(submittedRunId({ run_id: "r1", tenant_id: "acme" }), "r1");
  assert.equal(submittedRunId({}), null);
  assert.equal(submittedRunId(null), null);
});

test("a newly chosen file gets a new key; the same file keeps its own", () => {
  const empty = { tenant: "acme", parser: "", language: "", file: null, key: "", sending: false };
  const a = new File(["a"], "a.png");
  const b = new File(["b"], "b.png");

  const first = chooseFile(empty, a, "k1");
  assert.equal(first.key, "k1");
  assert.equal(chooseFile(first, a, "k2").key, "k1");
  assert.equal(chooseFile(first, b, "k2").key, "k2");
  assert.deepEqual(chooseFile(first, null, "k2"), { ...first, file: null, key: "" });
});

test("a submission carries its file's key as Idempotency-Key", () => {
  const form = { tenant: "acme", parser: "", language: "", file: new File(["a"], "a.png"), key: "k1", sending: false };
  assert.deepEqual(requestFor(submitIntent(form)).headers, { "Idempotency-Key": "k1" });
});
