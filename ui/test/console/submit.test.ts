/**
 * Submitting a file for OCR: the tenant and parser lists this console reads,
 * the choices it offers from them, and the form that drives a submission.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { requestFor } from "../../src/console/model/client.ts";
import {
  AUTOMATIC,
  PARSERS_POLL,
  canSubmit,
  capabilityList,
  chooseFile,
  languageChoices,
  parserChoices,
  parserStatus,
  parsersRefreshLabel,
  submitIntent,
  toSchemas,
  switchKind,
  chooseSchema,
  chooseParser,
  submittedRunId,
  toParsers,
  toTenants,
} from "../../src/console/model/submit.ts";

const NO_CAPABILITIES = { text: false, geometry: false, tables: false, handwriting: false };

test("tenants are read from the body's own list", () => {
  assert.deepEqual(toTenants({ tenants: ["acme", "mmerp"] }), ["acme", "mmerp"]);
  assert.deepEqual(toTenants({}), []);
  assert.deepEqual(toTenants(null), []);
});

test("parsers default their absent optional fields rather than reading undefined", () => {
  const parsers = toParsers({
    parsers: [
      {
        id: "paddle-vl",
        available: true,
        capabilities: { text: true, geometry: true, tables: false, handwriting: false },
        media_types: ["image/png"],
      },
      {
        id: "tesseract",
        available: false,
        unavailable_reason: "disabled",
        capabilities: { text: true, geometry: false, tables: false, handwriting: false },
        media_types: ["image/png"],
        languages: ["en"],
        healthy: false,
      },
    ],
  });
  assert.deepEqual(parsers, [
    {
      id: "paddle-vl",
      available: true,
      healthy: null,
      languages: [],
      mediaTypes: ["image/png"],
      unavailableReason: null,
      capabilities: { text: true, geometry: true, tables: false, handwriting: false },
    },
    {
      id: "tesseract",
      available: false,
      healthy: false,
      languages: ["en"],
      mediaTypes: ["image/png"],
      unavailableReason: "disabled",
      capabilities: { text: true, geometry: false, tables: false, handwriting: false },
    },
  ]);
});

test("a parser with no capabilities reported reads as none, not undefined", () => {
  const parsers = toParsers({
    parsers: [{ id: "x", available: true, media_types: [] }],
  });
  assert.deepEqual(parsers[0]?.capabilities, NO_CAPABILITIES);
  assert.equal(parsers[0]?.unavailableReason, null);
});

test("parser status order: unavailable beats unhealthy; healthy; otherwise unknown", () => {
  const base = {
    id: "x",
    available: true,
    healthy: null as boolean | null,
    languages: [],
    mediaTypes: [],
    unavailableReason: null,
    capabilities: NO_CAPABILITIES,
  };
  assert.equal(parserStatus({ ...base, available: false, healthy: true }), "unavailable");
  assert.equal(parserStatus({ ...base, healthy: false }), "not responding");
  assert.equal(parserStatus({ ...base, healthy: true }), "healthy");
  assert.equal(parserStatus({ ...base, healthy: null }), "unknown");
});

test("parsers never poll", () => {
  assert.equal(PARSERS_POLL, false);
  assert.equal(parsersRefreshLabel, "Refresh");
});

test("a row missing its id or available flag is dropped, not guessed at", () => {
  assert.deepEqual(toParsers({ parsers: [{ available: true }, { id: "x" }] }), []);
  assert.deepEqual(toParsers({}), []);
});

test("language choices follow the parser", () => {
  const ps = [
    {
      id: "paddle-vl",
      available: true,
      healthy: true,
      languages: ["vi", "en"],
      mediaTypes: [],
      unavailableReason: null,
      capabilities: NO_CAPABILITIES,
    },
  ];
  assert.deepEqual(languageChoices(ps, AUTOMATIC), [{ value: "", label: "Parser default" }]);
  assert.deepEqual(languageChoices(ps, "paddle-vl").map((c) => c.value), ["", "vi", "en"]);
});

test("a parser no longer in the list offers only the default language", () => {
  assert.deepEqual(languageChoices([], "gone"), [{ value: "", label: "Parser default" }]);
});

test("parser choices mark unavailable and unhealthy", () => {
  const ps = [
    {
      id: "paddle-vl",
      available: true,
      healthy: true,
      languages: [],
      mediaTypes: [],
      unavailableReason: null,
      capabilities: NO_CAPABILITIES,
    },
    {
      id: "slow",
      available: true,
      healthy: false,
      languages: [],
      mediaTypes: [],
      unavailableReason: null,
      capabilities: NO_CAPABILITIES,
    },
    {
      id: "off",
      available: false,
      healthy: null,
      languages: [],
      mediaTypes: [],
      unavailableReason: "disabled",
      capabilities: NO_CAPABILITIES,
    },
  ];
  assert.deepEqual(parserChoices(ps), [
    { value: AUTOMATIC, label: "Automatic", disabled: false },
    { value: "paddle-vl", label: "paddle-vl", disabled: false },
    { value: "slow", label: "slow (not responding)", disabled: false },
    { value: "off", label: "off (unavailable)", disabled: true },
  ]);
});

test("cannot submit while sending", () => {
  const f = { kind: "ocr" as const, schema: "", tenant: "acme", parser: "", language: "", file: new File(["x"], "a.png"), key: "k", sending: true };
  assert.equal(canSubmit(f), false);
  assert.equal(canSubmit({ ...f, sending: false }), true);
  assert.equal(canSubmit({ ...f, sending: false, file: null }), false);
  assert.equal(canSubmit({ ...f, sending: false, tenant: "" }), false);
});

test("submit intent omits empty parser and language", () => {
  assert.deepEqual(
    requestFor(submitIntent({ kind: "ocr" as const, schema: "", tenant: "acme", parser: "", language: "", file: null, key: "", sending: false }))
      .query,
    {},
  );
  assert.deepEqual(
    requestFor(submitIntent({ kind: "ocr" as const, schema: "", tenant: "acme", parser: "paddle-vl", language: "vi", file: null, key: "", sending: false })),
    { method: "POST", path: "/v1/console/tenants/acme/ocr", query: { parser: "paddle-vl", language: "vi" }, hasBody: true },
  );
});

test("a submitted run's id is read from the console detail body", () => {
  assert.equal(submittedRunId({ run_id: "r1", tenant_id: "acme" }), "r1");
  assert.equal(submittedRunId({}), null);
  assert.equal(submittedRunId(null), null);
});

test("a newly chosen file gets a new key; the same file keeps its own", () => {
  const empty = { kind: "ocr" as const, schema: "", tenant: "acme", parser: "", language: "", file: null, key: "", sending: false };
  const a = new File(["a"], "a.png");
  const b = new File(["b"], "b.png");

  const first = chooseFile(empty, a, "k1");
  assert.equal(first.key, "k1");
  assert.equal(chooseFile(first, a, "k2").key, "k1");
  assert.equal(chooseFile(first, b, "k2").key, "k2");
  assert.deepEqual(chooseFile(first, null, "k2"), { ...first, file: null, key: "" });
});

test("a submission carries its file's key as Idempotency-Key", () => {
  const form = { kind: "ocr" as const, schema: "", tenant: "acme", parser: "", language: "", file: new File(["a"], "a.png"), key: "k1", sending: false };
  assert.deepEqual(requestFor(submitIntent(form)).headers, { "Idempotency-Key": "k1" });
});

test("capabilities read as the true ones, comma-joined, or a dash when none", () => {
  assert.equal(
    capabilityList({ text: true, geometry: true, tables: false, handwriting: true }),
    "text, geometry, handwriting",
  );
  assert.equal(capabilityList(NO_CAPABILITIES), "—");
});

const FILE = new File(["x"], "a.pdf");
const FORM = {
  kind: "ocr" as const,
  tenant: "acme",
  parser: "",
  language: "",
  schema: "",
  file: FILE,
  key: "k",
  sending: false,
};

test("schemas are read from the listing, ignoring anything that is not an identity", () => {
  assert.deepEqual(toSchemas({ schemas: ["invoice@1", 3, null, "receipt@2"] }), [
    "invoice@1",
    "receipt@2",
  ]);
  assert.deepEqual(toSchemas({}), []);
  assert.deepEqual(toSchemas({ schemas: "x" }), []);
  assert.deepEqual(toSchemas(null), []);
});

test("an extraction needs a schema and a file", () => {
  const extraction = { ...FORM, kind: "extraction" as const };
  assert.equal(canSubmit(extraction), false);
  assert.equal(canSubmit({ ...extraction, schema: "invoice@1", file: null }), false);
  assert.equal(canSubmit({ ...extraction, schema: "invoice@1" }), true);
  assert.equal(canSubmit({ ...extraction, schema: "invoice@1", sending: true }), false);
  assert.equal(canSubmit({ ...extraction, schema: "invoice@1", tenant: "" }), false);
});

test("OCR does not need a schema", () => {
  assert.equal(canSubmit(FORM), true);
});

test("switching kind clears the other kind's choices and keeps tenant and file", () => {
  const ocr = { ...FORM, parser: "paddle", language: "vi", schema: "invoice@1" };
  const extraction = switchKind(ocr, "extraction", "k2");
  assert.equal(extraction.kind, "extraction");
  assert.equal(extraction.parser, "");
  assert.equal(extraction.language, "");
  assert.equal(extraction.schema, "");
  assert.equal(extraction.tenant, "acme");
  assert.equal(extraction.file, FILE);
  assert.equal(extraction.key, "k2");
  assert.equal(switchKind(ocr, "ocr", "k2"), ocr);
  assert.equal(switchKind({ ...ocr, file: null, key: "" }, "extraction", "k2").key, "");
});

test("a different schema or parser gets a fresh key; the same one keeps it", () => {
  const form = { ...FORM, kind: "extraction" as const, schema: "invoice@1" };
  assert.equal(chooseSchema(form, "invoice@1", "k2"), form);
  assert.equal(chooseSchema(form, "receipt@1", "k2").key, "k2");
  assert.equal(chooseParser(FORM, FORM.parser, "k2"), FORM);
  const next = chooseParser({ ...FORM, language: "vi" }, "paddle", "k2");
  assert.deepEqual([next.key, next.language], ["k2", ""]);
});

test("an extraction submit carries the schema and key, and no parser", () => {
  assert.deepEqual(submitIntent({ ...FORM, kind: "extraction", schema: "invoice@1" }), {
    type: "submit-extraction",
    tenantId: "acme",
    schema: "invoice@1",
    idempotencyKey: "k",
  });
});
