/**
 * What this viewer can ask for, exhaustively.
 *
 * SC-013 says the viewer issues zero requests that write to a store and
 * constructs zero corrections — a claim about the *absence* of behaviour, which
 * only an exhaustive pass over the user's intents can carry. It is measurable
 * without a browser only because FR-041 puts request construction in the model;
 * a component calling `fetch` directly would put it out of reach.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { allIntents, requestFor, writesToStore, type Intent } from "../src/model/client.ts";

const DOCUMENT = new Uint8Array([0x25, 0x50, 0x44, 0x46]);

describe("every intent a user can express", () => {
  it("constructs no request that writes to a store", () => {
    for (const intent of allIntents(DOCUMENT, "invoice@1")) {
      assert.equal(writesToStore(requestFor(intent)), false, `${intent.type} must not write`);
    }
  });

  it("touches only the two paths this viewer knows", () => {
    const paths = allIntents(DOCUMENT, "invoice@1").map((intent) => requestFor(intent).path);

    assert.deepEqual(new Set(paths), new Set(["/v1/schemas", "/v1/extract"]));
  });

  it("never submits a document for storage", () => {
    // `POST /v1/documents` stores the bytes, and the blob-shaped extract route
    // requires them stored first. Either is a write; this viewer performs none,
    // which is why it uses the storeless route (ADR-0012).
    const paths = allIntents(DOCUMENT, "invoice@1").map((intent) => requestFor(intent).path);

    assert.ok(!paths.includes("/v1/documents"));
    assert.ok(!paths.some((path) => path.includes("/documents/")));
  });

  it("constructs no correction", () => {
    // FR-030. Principle IX's `Correction` model exists and this milestone does
    // not import it — there is no intent that would produce one, which is the
    // fence around the spec's argument that this is not a review platform.
    const intents = allIntents(DOCUMENT, "invoice@1").map((intent) => intent.type);

    assert.ok(!intents.some((type) => /correct|annotat|review|approve/i.test(type)));
  });
});

describe("the storeless route", () => {
  it("carries the schema and the document, and nothing else", () => {
    const plan = requestFor({ type: "extract", schema: "invoice@1", document: DOCUMENT });

    assert.equal(plan.method, "POST");
    assert.equal(plan.path, "/v1/extract");
    assert.deepEqual(plan.query, { schema: "invoice@1" });
    assert.equal(plan.hasBody, true);
  });

  it("sends the listing request with no body at all", () => {
    const plan = requestFor({ type: "list-schemas" });

    assert.equal(plan.method, "GET");
    assert.equal(plan.hasBody, false);
  });
});

describe("the predicate itself", () => {
  it("calls an unknown path a write", () => {
    // The guard on the guard. `writesToStore` returning `false` for everything
    // would make every test above pass and mean nothing.
    const invented = { method: "POST", path: "/v1/documents", query: {}, hasBody: true } as const;

    assert.equal(writesToStore(invented), true);
    assert.equal(writesToStore({ ...invented, path: "/v1/anything" }), true);
  });
});

describe("the document", () => {
  it("is never named as a destination other than the extraction endpoint", () => {
    // FR-032 — the bytes go to exactly one place, and nothing here writes them
    // to browser-persistent storage.
    const withBody = allIntents(DOCUMENT, "invoice@1")
      .map((intent: Intent) => requestFor(intent))
      .filter((plan) => plan.hasBody);

    assert.equal(withBody.length, 1);
    assert.equal(withBody[0]?.path, "/v1/extract");
  });
});
